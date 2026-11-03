"""Claude reads the transcript, picks the funny moments and writes a description for each clip."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from . import config
from .transcribe import Word, transcript_text

log = logging.getLogger(__name__)

SYSTEM = """You cut short vertical clips from "The Ripple Effect", an Australian podcast, for Instagram Reels,
Facebook Reels, YouTube Shorts and TikTok. The goal of every clip is views: people should laugh, share it or
comment.

Picking clips:
- Choose only genuinely funny moments: a punchline, a story with a payoff, banter, a ridiculous admission,
  a reaction that lands. Quality over quantity. Return none if nothing is funny enough.
- Decide how many the video deserves (at most {max_clips}). Clips must not overlap.
- Each clip is {min_s:.0f} to {max_s:.0f} seconds, starts on the first word of a sentence and ends just after the
  laugh or punchline lands. It must make sense to someone who has not heard the rest of the episode.
- The first 3 seconds must hook the viewer.
- You only have the words, not the picture, so do not rely on a visual gag.

Writing:
- Australian spelling and voice. Funny, cheeky and short. Write like a person, not a marketer.
- Never invent anything that was not said. Never make fun of a named person who is not on the show, and
  nothing about anyone's private life, health or family.
- No swearing in the written text (swearing in the clip itself is fine).
- Match the house style in the examples if any are given.

Every time you are given is in seconds from the start of the video, as in the transcript."""

TOOL = {
    "name": "save_clips",
    "description": "Save the chosen clips with their descriptions.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_seconds": {"type": "number"},
                        "end_seconds": {"type": "number"},
                        "title": {"type": "string", "description": "Short name for the file, 2 to 6 words"},
                        "why_funny": {"type": "string", "description": "One line, for the team"},
                        "instagram": {"type": "string", "description": "Reel caption: 1 to 3 short lines plus 3 to 6 hashtags"},
                        "facebook": {"type": "string", "description": "Reel caption, may be a little longer, 0 to 3 hashtags"},
                        "youtube_title": {"type": "string", "description": "Shorts title, under 90 characters, ending with #shorts"},
                        "youtube_description": {"type": "string"},
                        "tiktok": {"type": "string", "description": "Caption plus 3 to 5 hashtags, under 300 characters"},
                    },
                    "required": ["start_seconds", "end_seconds", "title", "why_funny", "instagram",
                                 "facebook", "youtube_title", "youtube_description", "tiktok"],
                },
            }
        },
        "required": ["clips"],
    },
}


@dataclass
class Clip:
    start: float
    end: float
    title: str
    why_funny: str
    text: dict = field(default_factory=dict)  # platform -> description

    @property
    def length(self) -> float:
        return self.end - self.start


def style_examples() -> str:
    try:
        text = config.STYLE_FILE.read_text().strip()
    except FileNotFoundError:
        return ""
    # The placeholder file has no examples yet.
    return text


def build_prompt(words: list[Word], video_name: str, episode: int) -> str:
    parts = [f"Episode: TRE Ep{episode}. Video file: {video_name}."]
    examples = style_examples()
    if examples:
        parts.append("House style, past descriptions the host liked:\n<examples>\n" + examples + "\n</examples>")
    parts.append("Transcript (times are minutes:seconds; give your answer in seconds):\n<transcript>\n"
                 + transcript_text(words) + "\n</transcript>")
    parts.append("Pick the clips and call save_clips.")
    return "\n\n".join(parts)


def ask_claude(words: list[Word], video_name: str, episode: int, client=None) -> tuple[list[dict], dict]:
    import anthropic

    client = client or anthropic.Anthropic()
    msg = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=8000,
        system=SYSTEM.format(max_clips=config.MAX_CLIPS_PER_VIDEO, min_s=config.MIN_CLIP_SECONDS, max_s=config.MAX_CLIP_SECONDS),
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "save_clips"},
        messages=[{"role": "user", "content": build_prompt(words, video_name, episode)}],
    )
    usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
    for block in msg.content:
        if getattr(block, "type", "") == "tool_use" and block.name == "save_clips":
            return list(block.input.get("clips", [])), usage
    raise RuntimeError("Claude did not return any clips")


def snap(words: list[Word], start: float, end: float, duration: float) -> tuple[float, float] | None:
    """Move start and end onto word boundaries, add a little air, and keep the length in range."""
    if not words or end <= start:
        return None
    first = min(words, key=lambda w: abs(w.start - start))
    last = min(words, key=lambda w: abs(w.end - end))
    s, e = first.start, last.end
    if e <= s:
        return None
    if e - s > config.MAX_CLIP_SECONDS:  # trim back to the last word that fits
        fitting = [w for w in words if s < w.end <= s + config.MAX_CLIP_SECONDS]
        if not fitting:
            return None
        e = fitting[-1].end
    s = max(0.0, s - 0.15)
    e = min(duration, e + 0.4)
    if e - s < config.MIN_CLIP_SECONDS:
        return None
    return round(s, 2), round(e, 2)


def clean(raw: list[dict], words: list[Word], duration: float) -> list[Clip]:
    """Validate Claude's answer: snap times, drop short or overlapping clips, cap the count."""
    out: list[Clip] = []
    for c in raw:
        try:
            times = snap(words, float(c["start_seconds"]), float(c["end_seconds"]), duration)
        except (KeyError, TypeError, ValueError):
            continue
        if not times:
            log.info("dropped clip %r: too short or bad times", c.get("title"))
            continue
        s, e = times
        if any(s < o.end and o.start < e for o in out):
            log.info("dropped clip %r: overlaps another", c.get("title"))
            continue
        text = {k: str(c.get(k, "")).strip() for k in ("instagram", "facebook", "youtube_title", "youtube_description", "tiktok")}
        out.append(Clip(s, e, str(c.get("title", "clip")).strip(), str(c.get("why_funny", "")).strip(), text))
        if len(out) >= config.MAX_CLIPS_PER_VIDEO:
            break
    return sorted(out, key=lambda c: c.start)


def pick_clips(words: list[Word], video_name: str, episode: int, duration: float, client=None) -> tuple[list[Clip], dict]:
    if len(words) < 20:
        return [], {}
    raw, usage = ask_claude(words, video_name, episode, client)
    log.info("Claude proposed %d clips (%s)", len(raw), json.dumps(usage))
    return clean(raw, words, duration), usage
