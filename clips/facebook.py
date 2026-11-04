"""The Facebook version of a full episode: cut down to fit ContentStudio's 20-minute limit.

ContentStudio sends a Facebook video as a "feed" post, which stops at 1200 seconds (found 4 November 2026; it has no
other Facebook post type for a long video). Dave chose (4 November 2026) to have Claude take the least important
stretches out of a longer episode so it fits, rather than upload Facebook by hand. The opening and the sign-off are
never cut. The cut version is saved next to the full episode in Drive, so Dave can watch it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import config
from .cut import probe
from .episode import keep_parts, render
from .pick import call_tool
from .transcribe import Word, transcript_text

log = logging.getLogger(__name__)

FB_MAX_SECONDS = 1200.0
MARGIN = 15.0         # finish this far under the limit
KEEP_EDGES = 90.0     # never cut the first or last minute and a half of the show

SYSTEM = """You edit "The Ripple Effect", an Australian podcast. Facebook only takes videos up to 20 minutes, so
this episode must be made shorter for Facebook. Choose one to three stretches of the conversation to take out:
the least interesting parts (tangents, repetition, slow patches, admin talk). Never take out the opening or the
sign-off, never take out the punchline of a story without its set-up (or the set-up without the punchline), and
start and end each stretch between sentences, so the conversation still flows. Together the stretches must add up
to at least the time asked for, and not much more.

Every time you are given is in seconds from the start of the video, as in the transcript."""

TOOL = {
    "name": "cut_for_facebook",
    "description": "Save the stretches to take out for the Facebook version.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cuts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_seconds": {"type": "number", "description": "First word taken out"},
                        "end_seconds": {"type": "number", "description": "First word kept after the stretch"},
                        "why": {"type": "string", "description": "A few words: what this stretch is"},
                    },
                    "required": ["start_seconds", "end_seconds", "why"],
                },
            },
        },
        "required": ["cuts"],
    },
}


def seconds_to_cut(full_duration: float, start: float, end: float) -> float:
    """How much of the show must go. The intro and outro stay, so they count against the limit."""
    extras = max(0.0, full_duration - (end - start))
    return max(0.0, (end - start) - (FB_MAX_SECONDS - MARGIN - extras))


def snap(cuts, words: list[Word], start: float, end: float, need: float) -> list[tuple[float, float]]:
    """Keep the cuts inside the show (not its first or last 90 s), on word edges, and long enough in total."""
    lo, hi = start + KEEP_EDGES, end - KEEP_EDGES
    starts = sorted(w.start for w in words if lo <= w.start <= hi)
    out = []
    for a, b in sorted(cuts):
        a, b = max(a, lo), min(b, hi)
        if not starts or b - a < 1:
            continue
        a = next((x for x in starts if x >= a - 0.01), hi)  # begin on a word
        b = next((x for x in starts if x >= b - 0.01), hi)  # and keep the word the stretch ends on
        if b - a >= 1:
            out.append((a, b))
    merged: list[tuple[float, float]] = []
    for a, b in out:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    short = need - sum(b - a for a, b in merged)
    if short > 0:
        # Claude fell short: take the rest from just before the sign-off rather than miss the limit.
        for x in reversed(starts):
            x = min([x] + [a for a, b in merged if b > x])  # a stretch running past x joins the last cut
            if sum(b - a for a, b in merged if b <= x) + (hi - x) >= need:
                merged = [c for c in merged if c[1] <= x] + [(x, hi)]
                break
        log.warning("Facebook cut: Claude's stretches were %.0f s short, the rest came off before the sign-off", short)
    return merged


def plan_cuts(words: list[Word], start: float, end: float, need: float, client=None) -> tuple[list, dict]:
    show = [w for w in words if start <= w.start <= end]
    prompt = (f"The show runs from {start:.0f} to {end:.0f} seconds. Take out at least {need:.0f} seconds "
              f"(and no more than {need + 60:.0f}). Nothing before {start + KEEP_EDGES:.0f} or after "
              f"{end - KEEP_EDGES:.0f} may be cut.\n\nTranscript:\n{transcript_text(show)}")
    answer, usage = call_tool(SYSTEM, prompt, TOOL, client, max_tokens=4000)
    raw = [(float(c.get("start_seconds", 0)), float(c.get("end_seconds", 0))) for c in answer.get("cuts", [])]
    for c in answer.get("cuts", []):
        log.info("Facebook cut: %s", c.get("why", ""))
    return snap(raw, words, start, end, need), usage


def make(drive, ep: dict, work: Path, client=None) -> dict:
    """Make and save the Facebook version if the full episode is too long. Returns {"drive_id", ...} or
    {"short_enough": True}."""
    from .run import get_bookend, get_words  # here, not at the top: run imports release, which imports this

    work.mkdir(parents=True, exist_ok=True)
    full = drive.download(ep["drive_id"], work / f"{ep['drive_id']}.mp4")
    try:
        full_duration = probe(full).duration
    finally:
        full.unlink(missing_ok=True)
    start, end = float(ep["start"]), float(ep["end"])
    need = seconds_to_cut(full_duration, start, end)
    if need <= 0:
        return {"short_enough": True}

    raw = drive.download(ep["video"], work / f"{ep['video']}-raw.mp4")
    out = work / f"TRE Ep{ep['episode']} - Facebook {ep['drive_id'][:8]}.mp4"
    try:
        words = get_words(raw, work, config.WORK_DIR / "transcripts" / f"{ep['video']}.json")
        cuts, usage = plan_cuts(words, start, end, need, client)
        intro = get_bookend(drive, config.INTRO_NAME) if ep.get("intro") else None
        outro = (get_bookend(drive, config.OUTRO_NAME) or intro) if ep.get("outro") else None
        render(raw, probe(raw), intro, start, end, out, outro, cuts)
        made = probe(out).duration
        if made > FB_MAX_SECONDS:
            raise RuntimeError(f"the Facebook version is still {made:.0f} s long")
        name = f"TRE Ep{ep['episode']} - {Path(ep.get('name', '')).stem} - Facebook (under 20 min).mp4"
        up = drive.upload(out, drive.parent(ep["drive_id"]), "video/mp4", name=name)
        kept = keep_parts(start, end, cuts)
        log.info("Facebook version of TRE Ep%d: %.0f s (took out %.0f s); Claude usage %s", ep["episode"], made,
                 (end - start) - sum(b - a for a, b in kept), usage)
        return {"drive_id": up["id"], "seconds": round(made), "cuts": [[round(a, 1), round(b, 1)] for a, b in cuts]}
    finally:
        raw.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
