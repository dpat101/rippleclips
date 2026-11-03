"""The hourly job (cron). Finds new episode videos in Drive, cuts the funny clips, saves them to a Clips folder.

    python -m clips.run                 # the normal run (cron)
    python -m clips.run --status        # what has been done
    python -m clips.run --local FILE    # try one video file on the droplet, results in ~/rippleclips-work/local-out

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
from datetime import datetime, timezone
from pathlib import Path

from . import config, state as st
from .captions import write_ass
from .cut import cut_clip, probe
from .drive import Drive, now_utc, is_settled, safe_name
from .pick import Clip, pick_clips
from .transcribe import transcribe

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


def make_clips(video: Path, episode: int, work: Path, first_number: int = 1) -> list[dict]:
    """Transcribe, pick and cut. Returns one record per finished clip (files are in `work`)."""
    info = probe(video)
    log.info("%s: %.0f s, %dx%d", video.name, info.duration, info.width, info.height)
    words = transcribe(video, work)
    log.info("%s: %d words transcribed", video.name, len(words))
    clips, usage = pick_clips(words, video.name, episode, info.duration)
    log.info("%s: %d clips kept", video.name, len(clips))
    done = []
    for i, clip in enumerate(clips):
        number = first_number + i
        base = f"{number:02d} - {safe_name(clip.title)}"
        out = work / f"{base}.mp4"
        layout = cut_clip(video, info, clip.start, clip.end,
                          lambda margin: write_ass(words, clip.start, clip.end, work / f"{base}.ass", margin), out)
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


def process_drive_video(drive: Drive, s: dict, episode: int, folder, f) -> None:
    work = config.WORK_DIR / f.id
    work.mkdir(parents=True, exist_ok=True)
    rec = s["videos"].setdefault(f.id, {"name": f.name, "episode": episode})
    try:
        video = drive.download(f.id, work / safe_name(f.name, 100))
        ep_state = s.setdefault("episodes", {}).setdefault(str(episode), {"next_number": 1})
        made = make_clips(video, episode, work, ep_state["next_number"])
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
    except Exception as e:  # noqa: BLE001 - keep going with other videos, try again next run
        rec["failures"] = st.failures(s, f.id) + 1
        rec["status"] = "failed"
        rec["last_error"] = f"{type(e).__name__}: {e}"[:500]
        log.error("FAILED %s (attempt %d): %s\n%s", f.name, rec["failures"], e, traceback.format_exc())
    finally:
        st.save(s)
        shutil.rmtree(work, ignore_errors=True)


def run() -> None:
    check_disk()
    drive = Drive()
    s = st.load()
    now = now_utc()
    for episode, folder in drive.episodes():
        videos = [f for f in drive.children(folder.id) if f.mime_type.startswith("video/")]
        todo = [f for f in videos if not st.is_done(s, f.id) and st.failures(s, f.id) < MAX_FAILURES]
        if not todo:
            continue
        if not is_settled(videos, now, config.SETTLE_HOURS):
            log.info("TRE Ep%d: still uploading, waiting", episode)
            continue
        for f in sorted(todo, key=lambda x: x.name):
            process_drive_video(drive, s, episode, folder, f)


def status() -> None:
    s = st.load()
    for vid, rec in sorted(s.get("videos", {}).items(), key=lambda kv: (kv[1].get("episode", 0), kv[1].get("name", ""))):
        line = f"TRE Ep{rec.get('episode')}  {rec.get('name')}: {rec.get('status')}"
        if rec.get("status") == "done":
            line += f", {rec.get('clips')} clips"
        if rec.get("last_error") and rec.get("status") != "done":
            line += f" ({rec.get('failures')} failures, last: {rec['last_error'][:120]})"
        print(line)
    if not s.get("videos"):
        print("Nothing processed yet.")


def local(path: str, episode: int) -> None:
    out = config.WORK_DIR / "local-out"
    out.mkdir(parents=True, exist_ok=True)
    made = make_clips(Path(path), episode, out)
    for c in made:
        print(f"{c['number']:02d}  {fmt(c['start'])}-{fmt(c['end'])}  {c['title']}  ({c['layout']})")
    print(f"Clips are in {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--status", action="store_true")
    p.add_argument("--local")
    p.add_argument("--episode", type=int, default=0)
    a = p.parse_args()
    if a.status:
        status()
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
