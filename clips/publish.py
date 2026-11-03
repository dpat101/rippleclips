"""Stage 2: post the clips to Instagram, Facebook, YouTube Shorts and TikTok through ContentStudio.

ContentStudio does the posting at the scheduled time; this only hands each clip over, a few days ahead.

How a clip becomes a post:
- Each episode's Clips folder in Drive gets two subfolders: "Approved" and "Don't post".
- For the first 2 weeks after posting is switched on, only clips Dave has dragged into Approved are posted.
- After that every clip is posted unless Dave drags it into "Don't post".
- Two clips a day (CLIPS_PER_DAY), oldest episode first, from tomorrow on, at most POST_DAYS_AHEAD days ahead, so a
  clip can still be stopped in ContentStudio or by dragging it into "Don't post" before it is handed over.
- Each platform has its own time for clip 1 and clip 2 (SLOT_TIMES, Brisbane), and every post goes up at a random
  minute within POST_JITTER_MINUTES of it, picked once per post and kept, so no two days look the same.

POSTING in ~/.rippleclips-env: "off" (default, nothing is sent), "draft" (posts are made as drafts in
ContentStudio, for checking; nothing is published) or "on".
"""
from __future__ import annotations

import logging
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

log = logging.getLogger(__name__)

API = os.environ.get("CONTENTSTUDIO_API_URL", "https://api.contentstudio.io/api/v1")
PLATFORMS = ("instagram", "facebook", "youtube", "tiktok")
APPROVED_FOLDER = "Approved"
SKIP_FOLDER = "Don't post"
BRISBANE = ZoneInfo("Australia/Brisbane")
# ContentStudio's post_type for each platform. Checked against draft posts before going live.
POST_TYPES = {"instagram": "reel", "facebook": "reel", "youtube": "shorts", "tiktok": "feed"}


class CSError(RuntimeError):
    pass


class ContentStudio:
    def __init__(self, key: str | None = None, http=None):
        self.key = key or os.environ.get("CONTENTSTUDIO_API_KEY", "")
        if not self.key:
            raise CSError("No CONTENTSTUDIO_API_KEY in ~/.rippleclips-env")
        if http is None:
            import httpx
            http = httpx.Client(timeout=600)
        self.http = http
        self._ws = os.environ.get("CONTENTSTUDIO_WORKSPACE_ID", "")

    def _call(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, API + path, headers={"X-API-Key": self.key, "Accept": "application/json"}, **kw)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or body.get("status") is False:
            # Only the status and ContentStudio's message, never the key or headers.
            raise CSError(f"ContentStudio {method} {path.split('?')[0]}: HTTP {r.status_code} {str(body.get('message', ''))[:300]}")
        return body

    @staticmethod
    def _items(body: dict) -> list[dict]:
        data = body.get("data", body)
        if isinstance(data, dict):
            for k in ("data", "items", "accounts", "workspaces"):
                if isinstance(data.get(k), list):
                    return data[k]
            return [data]
        return data if isinstance(data, list) else []

    @staticmethod
    def _id(item: dict) -> str:
        for k in ("_id", "id", "account_id", "platform_identifier", "workspace_id"):
            if item.get(k):
                return str(item[k])
        return ""

    def workspaces(self) -> list[dict]:
        return [{"id": self._id(w), "name": w.get("name", "")} for w in self._items(self._call("GET", "/workspaces?per_page=50"))]

    def workspace_id(self) -> str:
        if not self._ws:
            ws = self.workspaces()
            if len(ws) != 1:
                raise CSError(f"Found {len(ws)} ContentStudio workspaces: set CONTENTSTUDIO_WORKSPACE_ID in ~/.rippleclips-env")
            self._ws = ws[0]["id"]
        return self._ws

    def accounts(self) -> list[dict]:
        body = self._call("GET", f"/workspaces/{self.workspace_id()}/accounts?per_page=100")
        out = []
        for a in self._items(body):
            platform = str(a.get("platform") or a.get("platform_type") or a.get("type") or "").lower()
            out.append({"id": self._id(a), "platform": platform,
                        "name": a.get("name") or a.get("account_name") or a.get("platform_name") or ""})
        return out

    def account_for(self, platform: str, accounts: list[dict]) -> str:
        override = os.environ.get(f"CONTENTSTUDIO_ACCOUNT_{platform.upper()}", "")
        if override:
            return override
        found = [a for a in accounts if a["platform"].startswith(platform)]
        if len(found) != 1:
            raise CSError(f"Found {len(found)} {platform} accounts in ContentStudio: set "
                          f"CONTENTSTUDIO_ACCOUNT_{platform.upper()} in ~/.rippleclips-env")
        return found[0]["id"]

    def upload_video(self, path: Path) -> str:
        with open(path, "rb") as fh:
            body = self._call("POST", f"/workspaces/{self.workspace_id()}/media",
                              files={"file": (path.name, fh, "video/mp4")})
        url = (body.get("data") or {}).get("url")
        if not url:
            raise CSError("ContentStudio did not return a link for the uploaded video")
        return url

    def create_post(self, account: str, platform: str, text: str, video_url: str, when: datetime | None, draft: bool,
                    post_type: str | None = None) -> str:
        payload = {
            "content": {"text": text, "media": {"video": video_url}},
            "accounts": [account],
            "post_type": post_type or POST_TYPES.get(platform, "feed"),
            "scheduling": {"publish_type": "draft"} if draft or when is None
            else {"publish_type": "scheduled", "scheduled_at": when.strftime("%Y-%m-%d %H:%M:%S")},
        }
        if draft and when is not None:
            payload["scheduling"]["scheduled_at"] = when.strftime("%Y-%m-%d %H:%M:%S")
        body = self._call("POST", f"/workspaces/{self.workspace_id()}/posts", json=payload)
        data = body.get("data") or {}
        return str(data.get("id") or data.get("_id") or "")


def platform_text(clip: dict, platform: str) -> str:
    t = clip.get("text", {})
    if platform == "youtube":
        return "\n\n".join(x for x in (t.get("youtube_title", ""), t.get("youtube_description", "")) if x)
    return t.get(platform, "")


def posting_mode() -> str:
    mode = os.environ.get("POSTING", "off").strip().lower()
    return mode if mode in ("off", "draft", "on") else "off"


def pick_time(platform: str, slot: int, rng=random) -> str:
    """The slot's time for this platform, moved a random number of minutes either way."""
    hh, mm = (int(x) for x in config.SLOT_TIMES[platform][slot - 1].split(":"))
    j = config.POST_JITTER_MINUTES
    m = min(23 * 60 + 59, max(0, hh * 60 + mm + rng.randint(-j, j)))
    return f"{m // 60:02d}:{m % 60:02d}"


def next_slot(day: date, slot: int) -> tuple[date, int]:
    return (day, slot + 1) if slot < config.CLIPS_PER_DAY else (day + timedelta(days=1), 1)


def post_time(day: date, hhmm: str) -> datetime:
    hh, mm = (int(x) for x in hhmm.split(":"))
    local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=BRISBANE)
    # ContentStudio reads the time in the workspace's own timezone (Dave's is Brisbane); UTC if set so.
    return local.astimezone(timezone.utc).replace(tzinfo=None) if config.CS_TIMES_IN_UTC else local.replace(tzinfo=None)


def candidates(s: dict) -> list[dict]:
    """Every clip made so far, oldest episode first, with where it came from."""
    out = []
    for vid, rec in s.get("videos", {}).items():
        if rec.get("status") != "done" or int(rec.get("episode", 0)) < config.POST_FROM_EPISODE:
            continue
        for c in rec.get("clip_list", []):
            if c.get("drive_id"):
                out.append({**c, "episode": int(rec["episode"]), "video": rec.get("name", "")})
    return sorted(out, key=lambda c: (c["episode"], c["number"]))


def run(drive, s: dict, cs: ContentStudio | None = None, today: date | None = None, rng=random) -> None:
    mode = posting_mode()
    if mode == "off":
        return
    p = s.setdefault("posting", {"posts": {}})
    today = today or datetime.now(BRISBANE).date()
    if "approval_until" not in p and mode == "on":
        p["approval_until"] = (today + timedelta(days=config.APPROVAL_DAYS)).isoformat()
    approval = mode == "draft" or today < date.fromisoformat(p.get("approval_until", "9999-12-31"))
    cs = cs or ContentStudio()

    # Where each clip sits now: Approved, Don't post, or still in Clips.
    folders = {n: f for n, f in drive.episodes()}
    approved, skipped = set(), set()
    for ep in {c["episode"] for c in candidates(s)}:
        if ep not in folders:
            continue
        clips_id = drive.folder(folders[ep].id, config.CLIPS_FOLDER_NAME)
        approved |= {f.id for f in drive.children(drive.folder(clips_id, APPROVED_FOLDER))}
        skipped |= {f.id for f in drive.children(drive.folder(clips_id, SKIP_FOLDER))}

    posts = p.setdefault("drafts" if mode == "draft" else "posts", {})  # practice drafts never count as posted
    # The last slot handed over so far; new clips go in the slots after it, never before tomorrow.
    last = max([(date.fromisoformat(v["day"]), int(v.get("slot", 1))) for v in posts.values() if v.get("day")]
               + [(today, config.CLIPS_PER_DAY)])
    accounts = None
    for clip in candidates(s):
        key = clip["drive_id"]
        rec = posts.get(key, {})
        if rec and all(rec.get("ids", {}).get(pl) for pl in PLATFORMS):
            continue  # handed over already
        if key in skipped or (approval and key not in approved):
            continue
        if rec.get("day"):
            day, slot = date.fromisoformat(rec["day"]), int(rec.get("slot", 1))
        else:
            day, slot = next_slot(*last)
            if (day - today).days > config.POST_DAYS_AHEAD:
                break
        accounts = accounts if accounts is not None else cs.accounts()
        video = None
        try:
            if config.CS_VIDEO == "upload":
                work = config.WORK_DIR / "posting"
                work.mkdir(parents=True, exist_ok=True)
                video = drive.download(key, work / f"{key}.mp4")
                url = cs.upload_video(video)
            else:
                url = drive.public_link(key)
            rec = posts.setdefault(key, {"day": day.isoformat(), "slot": slot, "title": clip["title"],
                                         "episode": clip["episode"], "mode": mode, "ids": {}, "errors": {}})
            times = rec.setdefault("times", {})
            last = max(last, (day, slot))
            for platform in PLATFORMS:
                if rec["ids"].get(platform):
                    continue
                try:
                    acct = cs.account_for(platform, accounts)
                    times.setdefault(platform, pick_time(platform, slot, rng))
                    rec["ids"][platform] = cs.create_post(acct, platform, platform_text(clip, platform), url,
                                                          post_time(day, times[platform]), draft=mode == "draft") or "ok"
                    rec["errors"].pop(platform, None)
                except Exception as e:  # noqa: BLE001 - try this platform again next run
                    rec["errors"][platform] = str(e)[:300]
                    log.error("POSTING %s to %s failed: %s", clip["title"], platform, e)
            log.info("Handed %r to ContentStudio for %s, clip %d of the day (%s): %s", clip["title"], day.isoformat(),
                     slot, mode, ", ".join(f"{k} {v}" for k, v in sorted(times.items())))
        finally:
            if video:
                video.unlink(missing_ok=True)


def check() -> None:
    """Print the workspaces and accounts ContentStudio sees (names and ids only)."""
    cs = ContentStudio()
    for w in cs.workspaces():
        print(f"workspace  {w['id']}  {w['name']}")
    for a in cs.accounts():
        print(f"account    {a['platform']:<22} {a['id']:<24} {a['name']}")
