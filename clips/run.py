"""The daily job (cron, 3am Brisbane). Finds new episode videos in Drive, cuts the funny clips into a Clips folder, and makes
the full episode (warm-up cut, sound levelled, intro in front) in a Full episode folder.

    python -m clips.run                 # the normal run (cron)
    python -m clips.run --status        # what has been done
    python -m clips.run --local FILE    # try one video file on the droplet, results in ~/rippleclips-work/local-out
    python -m clips.run --drafts-were-published   # Dave published the practice drafts by hand: count them as done
    python -m clips.run --test-post-after 13:00   # one-off: post the next approved clip today, in the hour after 1pm

Stage 1: nothing is posted anywhere. Clips and descriptions go into "<episode folder>/Clips" in Drive.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import shutil
import sys
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import config, publish, release, state as st
from .captions import write_ass
from .cut import cut_clip, probe
from .drive import Drive, now_utc, is_settled, safe_name
from .episode import description_text as episode_text, plan_episode, render
from .pick import Clip, pick_clips
from .transcribe import Word, transcribe

log = logging.getLogger("clips")
MAX_FAILURES = 3


def setup_logging(to_file: bool = True) -> None:
    handlers = [logging.StreamHandler(sys.stdout)]
    if to_file:
        handlers.append(logging.FileHandler(config.LOG_FILE))
    quiet = QuietLibraries()
    for h in handlers:
        h.addFilter(quiet)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    for noisy in QuietLibraries.NAMES:
        logging.getLogger(noisy).setLevel(logging.WARNING)


class QuietLibraries(logging.Filter):
    """Drop the libraries' chatter (every web request, model download lines); keep their warnings and errors."""
    NAMES = ("googleapiclient", "httpx", "httpcore", "huggingface_hub", "faster_whisper", "urllib3", "filelock")

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or not record.name.startswith(self.NAMES)


def check_disk() -> None:
    usage = shutil.disk_usage(config.HOME)
    pct = usage.used / usage.total * 100
    if pct >= config.DISK_WARN_PERCENT:
        log.warning("DISK %.0f%% full (warning level %d%%)", pct, config.DISK_WARN_PERCENT)


def description_text(clip: Clip, number: int, episode: int, video_name: str) -> str:
    t = clip.text
    return "\n".join([
        f"Clip {number:02d}: {clip.title}",
        f"From TRE Ep{episode}, {video_name}, {fmt(clip.start)} to {fmt(clip.end)} ({clip.length:.0f} seconds)",
        f"Why it's funny: {clip.why_funny}",
        "",
        "INSTAGRAM", t.get("instagram", ""), "",
        "FACEBOOK", t.get("facebook", ""), "",
        "YOUTUBE SHORTS TITLE", t.get("youtube_title", ""), "",
        "YOUTUBE SHORTS DESCRIPTION", t.get("youtube_description", ""), "",
        "TIKTOK", t.get("tiktok", ""), "",
    ])


def fmt(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def get_words(video: Path, work: Path, cache: Path | None) -> list[Word]:
    """Transcribe, or reuse the transcript saved by an earlier attempt (transcribing takes half an hour)."""
    if cache and cache.exists():
        words = [Word(**w) for w in json.loads(cache.read_text())]
        log.info("%s: reusing saved transcript (%d words)", video.name, len(words))
        return words
    words = transcribe(video, work)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps([asdict(w) for w in words]))
    return words


def make_clips(video: Path, episode: int, work: Path, first_number: int = 1, cache: Path | None = None) -> list[dict]:
    """Transcribe, pick and cut. Returns one record per finished clip (files are in `work`)."""
    info = probe(video)
    log.info("%s: %.0f s, %dx%d", video.name, info.duration, info.width, info.height)
    words = get_words(video, work, cache)
    log.info("%s: %d words transcribed", video.name, len(words))
    clips, usage = pick_clips(words, video.name, episode, info.duration)
    log.info("%s: %d clips kept", video.name, len(clips))
    done = []
    for i, clip in enumerate(clips):
        number = first_number + i
        base = f"{number:02d} - {safe_name(clip.title)}"
        # The captions file has a plain name: ffmpeg's filter cannot open a path with an apostrophe in it
        # ("Everyone's A Cop With A Phone", 10 November 2026).
        out = work / f"{base}.mp4"
        layout = cut_clip(video, info, clip.start, clip.end,
                          lambda margin: write_ass(words, clip.start, clip.end, work / f"{number:02d}.ass", margin), out)
        txt = work / f"{base}.txt"
        txt.write_text(description_text(clip, number, episode, video.name), encoding="utf-8")
        done.append({
            "number": number, "title": clip.title, "start": clip.start, "end": clip.end,
            "why_funny": clip.why_funny, "text": clip.text, "layout": layout["mode"],
            "mp4": str(out), "txt": str(txt),
        })
    if usage:
        log.info("Claude usage for %s: %s", video.name, json.dumps(usage))
    return done


def needs_clips(s: dict, file_id: str) -> bool:
    return not st.is_done(s, file_id) and st.failures(s, file_id) < MAX_FAILURES


def needs_episode(s: dict, file_id: str) -> bool:
    rec = s["videos"].get(file_id, {})
    return (config.FULL_EPISODES and rec.get("episode_status") != "done"
            and int(rec.get("episode_failures", 0)) < MAX_FAILURES)


def do_clips(drive: Drive, s: dict, rec: dict, episode: int, folder, f, video: Path, work: Path, cache: Path) -> None:
    ep_state = s.setdefault("episodes", {}).setdefault(str(episode), {"next_number": 1})
    made = make_clips(video, episode, work, ep_state["next_number"], cache)
    clips_folder = drive.folder(folder.id, config.CLIPS_FOLDER_NAME)
    for c in made:
        up = drive.upload(Path(c["mp4"]), clips_folder, "video/mp4")
        drive.upload(Path(c["txt"]), clips_folder, "text/plain")
        c["drive_id"], c["drive_link"] = up["id"], up.get("webViewLink", "")
        c.pop("mp4"), c.pop("txt")
    manifest = work / f"{safe_name(Path(f.name).stem)} clips.json"
    manifest.write_text(json.dumps({"episode": episode, "video": f.name, "clips": made}, indent=2))
    drive.upload(manifest, clips_folder, "application/json")
    ep_state["next_number"] += len(made)
    rec.update(status="done", clips=len(made), finished=datetime.now(timezone.utc).isoformat(), clip_list=made)
    log.info("DONE TRE Ep%d %s: %d clips saved to Drive", episode, f.name, len(made))


def make_episode(video: Path, episode: int, work: Path, intro: Path | None, cache: Path | None = None,
                 outro: Path | None = None) -> dict:
    """Trim, level and put the intro in front. Returns the record, with the files in `work`."""
    info = probe(video)
    words = get_words(video, work, cache)
    plan, usage = plan_episode(words, video.name, episode, info.duration)
    log.info("%s: full episode keeps %s to %s of %s (%s)", video.name, fmt(plan.start), fmt(plan.end),
             fmt(info.duration), plan.why)
    stem = safe_name(f"TRE Ep{episode} - {Path(video.name).stem} - full episode", 100)
    out, txt = work / f"{stem}.mp4", work / f"{stem}.txt"
    render(video, info, intro, plan.start, plan.end, out, outro)
    txt.write_text(episode_text(plan, episode, video.name, intro is not None, outro is not None), encoding="utf-8")
    log.info("Claude usage for %s episode: %s", video.name, json.dumps(usage))
    return {"start": plan.start, "end": plan.end, "why": plan.why, "text": plan.text, "intro": intro is not None, "outro": outro is not None,
            "mp4": str(out), "txt": str(txt)}


def do_episode(drive: Drive, rec: dict, episode: int, folder, f, video: Path, work: Path, cache: Path,
               intro: Path | None, outro: Path | None = None) -> None:
    made = make_episode(video, episode, work, intro, cache, outro)
    target = drive.folder(folder.id, config.FULL_EPISODE_FOLDER_NAME)
    up = drive.upload(Path(made["mp4"]), target, "video/mp4")
    drive.upload(Path(made["txt"]), target, "text/plain")
    made["drive_id"], made["drive_link"] = up["id"], up.get("webViewLink", "")
    made.pop("mp4"), made.pop("txt")
    rec.update(episode_status="done", full_episode=made)
    log.info("DONE TRE Ep%d %s: full episode saved to Drive", episode, f.name)


def fail(rec: dict, what: str, f, e: Exception) -> None:
    key = "failures" if what == "clips" else "episode_failures"
    rec[key] = int(rec.get(key, 0)) + 1
    rec["status" if what == "clips" else "episode_status"] = "failed"
    rec["last_error" if what == "clips" else "episode_error"] = f"{type(e).__name__}: {e}"[:500]
    log.error("FAILED %s %s (attempt %d): %s\n%s", what, f.name, rec[key], e, traceback.format_exc())


def process_drive_video(drive: Drive, s: dict, episode: int, folder, f, intro: Path | None = None,
                        outro: Path | None = None) -> None:
    work = config.WORK_DIR / f.id
    work.mkdir(parents=True, exist_ok=True)
    rec = s["videos"].setdefault(f.id, {"name": f.name, "episode": episode})
    # Transcripts are small and kept, so a retry or a later job never transcribes the same video twice.
    cache = config.WORK_DIR / "transcripts" / f"{f.id}.json"
    try:
        video = drive.download(f.id, work / safe_name(f.name, 100))
        if needs_clips(s, f.id):
            try:
                do_clips(drive, s, rec, episode, folder, f, video, work, cache)
            except Exception as e:  # noqa: BLE001 - keep going, try again next run
                fail(rec, "clips", f, e)
            st.save(s)
        if needs_episode(s, f.id):
            try:
                do_episode(drive, rec, episode, folder, f, video, work, cache, intro, outro)
            except Exception as e:  # noqa: BLE001
                fail(rec, "full episode", f, e)
    except Exception as e:  # noqa: BLE001 - the download failed
        fail(rec, "clips", f, e)
    finally:
        st.save(s)
        shutil.rmtree(work, ignore_errors=True)


def get_bookend(drive: Drive, name: str) -> Path | None:
    """Intro.mp4 or Outro.mp4 (any video with that name) from the main Drive folder, downloaded once per version."""
    if not config.FULL_EPISODES:
        return None
    files = [f for f in drive.children(drive.root_id())
             if f.mime_type.startswith("video/") and Path(f.name).stem.strip().lower() == name.lower()]
    if not files:
        log.info("No %s video in the main folder: full episodes will have none", name)
        return None
    f = max(files, key=lambda x: x.modified)
    dest = config.WORK_DIR / name.lower() / f"{f.id}-{int(f.modified.timestamp())}{Path(f.name).suffix or '.mp4'}"
    if not dest.exists():
        shutil.rmtree(dest.parent, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        drive.download(f.id, dest)
        log.info("downloaded %s", f.name)
    return dest


def run() -> None:
    check_disk()
    drive = Drive()
    s = st.load()
    now = now_utc()
    intro = outro = None
    bookends = False
    for episode, folder in drive.episodes():
        videos = [f for f in drive.children(folder.id) if f.mime_type.startswith("video/")]
        todo = [f for f in videos if needs_clips(s, f.id) or needs_episode(s, f.id)]
        if not todo:
            continue
        if not is_settled(videos, now, config.SETTLE_HOURS):
            log.info("TRE Ep%d: still uploading, waiting", episode)
            continue
        if not bookends:
            intro, outro, bookends = get_bookend(drive, config.INTRO_NAME), get_bookend(drive, config.OUTRO_NAME), True
            outro = outro or intro  # Dave, 3 Nov 2026: with no Outro video, the intro is played at the end too
        for f in sorted(todo, key=lambda x: x.name):
            process_drive_video(drive, s, episode, folder, f, intro, outro)
    try:
        publish.run(drive, s)
    except Exception as e:  # noqa: BLE001 - posting problems never stop the clips
        log.error("POSTING failed: %s\n%s", e, traceback.format_exc())
    try:
        st.save(s)
        release.run(drive, s)
    except Exception as e:  # noqa: BLE001
        log.error("RELEASE failed: %s\n%s", e, traceback.format_exc())
    finally:
        st.save(s)


def status() -> None:
    s = st.load()
    for vid, rec in sorted(s.get("videos", {}).items(), key=lambda kv: (kv[1].get("episode", 0), kv[1].get("name", ""))):
        line = f"TRE Ep{rec.get('episode')}  {rec.get('name')}: {rec.get('status')}"
        if rec.get("status") == "done":
            line += f", {rec.get('clips')} clips"
        if rec.get("last_error") and rec.get("status") != "done":
            line += f" ({rec.get('failures')} failures, last: {rec['last_error'][:120]})"
        if config.FULL_EPISODES:
            line += f"; full episode: {rec.get('episode_status') or 'to do'}"
            if rec.get("episode_error") and rec.get("episode_status") != "done":
                line += f" ({rec.get('episode_failures')} failures, last: {rec['episode_error'][:120]})"
        print(line)
    if not s.get("videos"):
        print("Nothing processed yet.")
    print(f"Posting: {publish.posting_mode()}")
    p = s.get("posting", {})
    if p.get("approval_until") and config.APPROVAL_DAYS > 0:
        print(f"Only clips in Approved are posted until {p['approval_until']}")
    for key in ("posts", "drafts"):
        for rec in sorted(p.get(key, {}).values(), key=lambda r: (r.get("day", ""), r.get("slot", 1))):
            sent = ", ".join(f"{pl} {rec.get('times', {}).get(pl, '')}".strip() for pl in sorted(rec.get("ids", {}))) or "none yet"
            line = f"  {rec.get('day')} clip {rec.get('slot', 1)}  TRE Ep{rec.get('episode')}  {rec.get('title')}  ({key[:-1]}: {sent})"
            if rec.get("errors"):
                line += f"  problems: {rec['errors']}"
            print(line)
    r = s.get("releases", {})
    for key in ("episodes", "drafts"):
        for rec in sorted(r.get(key, {}).values(), key=lambda x: x.get("day", "")):
            sent = ", ".join(sorted(rec.get("ids", {}))) or "none yet"
            line = f"  {rec.get('day')} {rec.get('time')}  full episode TRE Ep{rec.get('episode')}  {rec.get('title')}  ({key.rstrip('s')}: {sent})"
            if rec.get("errors"):
                line += f"  problems: {rec['errors']}"
            print(line)


def local(path: str, episode: int) -> None:
    out = config.WORK_DIR / "local-out"
    out.mkdir(parents=True, exist_ok=True)
    made = make_clips(Path(path), episode, out)
    for c in made:
        print(f"{c['number']:02d}  {fmt(c['start'])}-{fmt(c['end'])}  {c['title']}  ({c['layout']})")
    print(f"Clips are in {out}")


def drafts_were_published(s: dict, today) -> list[str]:
    """Dave published the practice drafts himself (4 November 2026). Record them as posted today, so nothing in them
    goes out a second time and the next clip and the next weekly episode follow on from today."""
    done = []
    p = s.setdefault("posting", {"posts": {}})
    for key, rec in p.pop("drafts", {}).items():
        rec.update(day=today.isoformat(), mode="published by hand")
        p.setdefault("posts", {})[key] = rec
        done.append(f"clip {rec.get('title', key)}")
    r = s.setdefault("releases", {})
    for key, rec in r.pop("drafts", {}).items():
        rec.update(day=today.isoformat(), mode="published by hand")
        r.setdefault("episodes", {})[key] = rec
        done.append(f"full episode TRE Ep{rec.get('episode', '?')} {rec.get('title', '')}")
    return done


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--status", action="store_true")
    p.add_argument("--contentstudio-check", action="store_true")
    p.add_argument("--podbean-check", action="store_true")
    p.add_argument("--drafts-were-published", action="store_true")
    p.add_argument("--test-post-after", help="HH:MM: post the next clip today, in the hour after this time")
    p.add_argument("--local")
    p.add_argument("--episode", type=int, default=0)
    a = p.parse_args()
    if a.status:
        status()
        return
    if a.contentstudio_check:
        publish.check()
        return
    if a.podbean_check:
        release.check()
        return
    if a.drafts_were_published:
        s = st.load()
        done = drafts_were_published(s, datetime.now(publish.BRISBANE).date())
        st.save(s)
        print("\n".join(f"Recorded as published: {d}" for d in done) or "No drafts to record")
        return
    if a.test_post_after:
        setup_logging()
        s = st.load()
        try:
            publish.run(Drive(), s, test_after=a.test_post_after)
        finally:
            st.save(s)
        return
    setup_logging()
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(config.LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # a run is already going
    if a.local:
        local(a.local, a.episode)
    else:
        run()


if __name__ == "__main__":
    main()
