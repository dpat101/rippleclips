"""Full episodes go out on their own, one a week (Dave, 4 November 2026: no approval needed).

- YouTube and Facebook get the full video through ContentStudio, by Drive link (like the clips).
- Podbean gets an MP3 of the same full episode through Podbean's API; Apple Podcasts and the other apps take it
  from Podbean's feed. Spotify is not sent anything: Dave uploads the video there himself, because Podbean can only
  send audio to Spotify and his Spotify show is his own.

Release day and time are RELEASE_DAY / RELEASE_TIME (Brisbane), moved by a random number of minutes up to
POST_JITTER_MINUTES either way. Oldest episode first, at most one release handed over ahead.
POSTING in the env file applies here too: off / draft (ContentStudio and Podbean drafts) / on.
"""
from __future__ import annotations

import logging
import os
import random
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config
from .publish import BRISBANE, ContentStudio, CSError, post_time, posting_mode

log = logging.getLogger(__name__)

PODBEAN_API = os.environ.get("PODBEAN_API_URL", "https://api.podbean.com/v1")
CS_PLATFORMS = ("youtube", "facebook")
TARGETS = CS_PLATFORMS + ("podbean",)
# ContentStudio's post_type for a full-length video. Checked against drafts before going live.
FULL_POST_TYPES = {"youtube": "video", "facebook": "feed"}


class PodbeanError(RuntimeError):
    pass


class Podbean:
    """Podbean's API for our own podcast (client credentials; keys in ~/.rippleclips-env)."""

    def __init__(self, http=None):
        self.cid = os.environ.get("PODBEAN_CLIENT_ID", "")
        self.secret = os.environ.get("PODBEAN_CLIENT_SECRET", "")
        if not (self.cid and self.secret):
            raise PodbeanError("No PODBEAN_CLIENT_ID / PODBEAN_CLIENT_SECRET in ~/.rippleclips-env")
        if http is None:
            import httpx
            http = httpx.Client(timeout=1800)
        self.http = http
        self._token = ""

    def _check(self, r, what: str) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
            # Podbean's own message only, never the keys or the token.
            msg = body.get("error_description") or body.get("error") or "" if isinstance(body, dict) else ""
            raise PodbeanError(f"Podbean {what}: HTTP {r.status_code} {str(msg)[:300]}")
        return body

    def token(self) -> str:
        if not self._token:
            r = self.http.post(f"{PODBEAN_API}/oauth/token", data={"grant_type": "client_credentials"},
                               auth=(self.cid, self.secret))
            self._token = self._check(r, "sign-in").get("access_token", "")
            if not self._token:
                raise PodbeanError("Podbean sign-in gave no access token")
        return self._token

    def upload(self, path: Path, content_type: str = "audio/mpeg") -> str:
        r = self.http.get(f"{PODBEAN_API}/files/uploadAuthorize",
                          params={"access_token": self.token(), "filename": path.name,
                                  "filesize": path.stat().st_size, "content_type": content_type})
        auth = self._check(r, "upload authorise")
        if not auth.get("presigned_url") or not auth.get("file_key"):
            raise PodbeanError("Podbean did not give an upload address")
        with open(path, "rb") as fh:
            put = self.http.put(auth["presigned_url"], content=fh.read(), headers={"Content-Type": content_type})
        if put.status_code >= 400:
            raise PodbeanError(f"Podbean upload: HTTP {put.status_code}")
        return auth["file_key"]

    def publish(self, title: str, content: str, media_key: str, when: datetime, draft: bool) -> str:
        data = {"access_token": self.token(), "title": title, "content": content, "media_key": media_key,
                "status": "draft" if draft else "future", "type": "public",
                "publish_timestamp": int(when.timestamp())}
        body = self._check(self.http.post(f"{PODBEAN_API}/episodes", data=data), "publish")
        ep = body.get("episode") or {}
        return str(ep.get("id") or ep.get("permalink_url") or "ok")


def to_mp3(video: Path, out: Path) -> Path:
    """The full episode's sound only, as a podcast MP3 (it is already levelled to -16 LUFS)."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-c:a", "libmp3lame",
                    "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)], check=True)
    return out


def candidates(s: dict) -> list[dict]:
    """Every full episode made so far, oldest first."""
    out = []
    for vid, rec in s.get("videos", {}).items():
        full = rec.get("full_episode") or {}
        if (rec.get("episode_status") == "done" and full.get("drive_id")
                and int(rec.get("episode", 0)) >= config.POST_FROM_EPISODE):
            out.append({"video": vid, "episode": int(rec["episode"]), "name": rec.get("name", ""), **full})
    return sorted(out, key=lambda c: (c["episode"], c["name"]))


def next_release(after: date) -> date:
    """The first release day strictly after `after`."""
    d = after + timedelta(days=1)
    while d.weekday() != config.RELEASE_DAY:
        d += timedelta(days=1)
    return d


def pick_release_time(rng=random) -> str:
    hh, mm = (int(x) for x in config.RELEASE_TIME.split(":"))
    j = config.POST_JITTER_MINUTES
    m = min(23 * 60 + 59, max(0, hh * 60 + mm + rng.randint(-j, j)))
    return f"{m // 60:02d}:{m % 60:02d}"


def texts(ep: dict, target: str) -> tuple[str, str]:
    """(title, description) for each place."""
    t = ep.get("text", {})
    if target == "podbean":
        return t.get("title", ""), t.get("spotify_description", "")
    return t.get("youtube_title", "") or t.get("title", ""), t.get("youtube_description", "")


def run(drive, s: dict, cs: ContentStudio | None = None, pb: Podbean | None = None, today: date | None = None,
        rng=random) -> None:
    mode = posting_mode()
    if mode == "off" or not config.RELEASES:
        return
    r = s.setdefault("releases", {})
    done = r.setdefault("drafts" if mode == "draft" else "episodes", {})
    today = today or datetime.now(BRISBANE).date()
    last = max((date.fromisoformat(v["day"]) for v in done.values() if v.get("day")), default=today)

    for ep in candidates(s):
        rec = done.get(ep["video"], {})
        if rec and all(rec.get("ids", {}).get(t) for t in TARGETS):
            continue
        if rec.get("day"):
            day = date.fromisoformat(rec["day"])
        else:
            day = next_release(max(last, today))
            if (day - today).days > 7:
                break  # one release handed over ahead is enough; the rest wait their turn
        rec = done.setdefault(ep["video"], {"day": day.isoformat(), "time": pick_release_time(rng),
                                            "episode": ep["episode"], "title": ep.get("text", {}).get("title", ""),
                                            "mode": mode, "ids": {}, "errors": {}})
        last = max(last, day)
        when_local = post_time(day, rec["time"])  # what ContentStudio is given
        local = datetime.combine(day, datetime.strptime(rec["time"], "%H:%M").time(), tzinfo=BRISBANE)
        draft = mode == "draft"

        missing_cs = [p for p in CS_PLATFORMS if not rec["ids"].get(p)]
        if missing_cs:
            try:
                cs = cs or ContentStudio()
                accounts = cs.accounts()
                url = drive.public_link(ep["drive_id"])
                for p in missing_cs:
                    try:
                        title, desc = texts(ep, p)
                        text = "\n\n".join(x for x in (title, desc) if x)
                        rec["ids"][p] = cs.create_post(cs.account_for(p, accounts), p, text, url, when_local, draft,
                                                       post_type=FULL_POST_TYPES[p]) or "ok"
                        rec["errors"].pop(p, None)
                    except Exception as e:  # noqa: BLE001 - this place is tried again next run
                        rec["errors"][p] = str(e)[:300]
                        log.error("RELEASE TRE Ep%d to %s failed: %s", ep["episode"], p, e)
            except (CSError, Exception) as e:  # noqa: BLE001
                for p in missing_cs:
                    rec["errors"][p] = str(e)[:300]
                log.error("RELEASE TRE Ep%d to ContentStudio failed: %s", ep["episode"], e)

        if not rec["ids"].get("podbean"):
            work = config.WORK_DIR / "release"
            work.mkdir(parents=True, exist_ok=True)
            video = work / f"{ep['drive_id']}.mp4"
            mp3 = work / f"TRE Ep{ep['episode']} {ep['drive_id'][:8]}.mp3"
            try:
                pb = pb or Podbean()
                drive.download(ep["drive_id"], video)
                to_mp3(video, mp3)
                title, desc = texts(ep, "podbean")
                rec["ids"]["podbean"] = pb.publish(title, desc.replace("\n", "<br>"), pb.upload(mp3), local, draft)
                rec["errors"].pop("podbean", None)
            except Exception as e:  # noqa: BLE001
                rec["errors"]["podbean"] = str(e)[:300]
                log.error("RELEASE TRE Ep%d to Podbean failed: %s", ep["episode"], e)
            finally:
                video.unlink(missing_ok=True)
                mp3.unlink(missing_ok=True)
        log.info("Release TRE Ep%d %r set for %s %s (%s): %s", ep["episode"], rec["title"], rec["day"], rec["time"],
                 mode, ", ".join(sorted(rec["ids"])) or "nothing yet")


def check() -> None:
    """Sign in to Podbean and print the podcast(s) the keys reach (names only)."""
    pb = Podbean()
    body = pb._check(pb.http.get(f"{PODBEAN_API}/podcasts", params={"access_token": pb.token()}), "podcasts")
    pods = body.get("podcasts") or ([body["podcast"]] if body.get("podcast") else [])
    print("Signed in to Podbean.")
    for pod in pods:
        print(f"podcast  {pod.get('title', '')}  {pod.get('website', '')}")
