"""The full episode: the warm-up cut off, the sound brought up to podcast loudness, Dave's intro in front.

Claude reads the transcript and decides where the real conversation starts and ends, and writes the episode's
title and descriptions for Spotify and YouTube. ffmpeg then puts Intro.mp4 (from the main Drive folder) in front
of the trimmed episode, and Outro.mp4 (if there is one) after it.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .cut import VideoInfo, probe
from .pick import call_tool, style_examples
from .transcribe import Word, transcript_text

log = logging.getLogger(__name__)

SYSTEM = """You edit "The Ripple Effect", an Australian podcast, into a finished full episode for Spotify and
YouTube.

1. Find where the show really starts. Recordings often begin with the hosts getting ready: mic checks, "are we
   rolling", chit-chat before the conversation, someone sitting down. Start at the first sentence of the real
   conversation or the hosts' welcome. If the show starts straight away, start at 0. Usually it is within the
   first minute or two; never skip part of the real conversation.
2. Find where it really ends: just after the last proper line or sign-off, before any off-air chatter ("is it
   still recording?", packing up). If it runs to the end, end at the last word. When unsure, keep more.
3. Write the episode title and descriptions in the house style of the examples, Australian voice. Never invent
   anything that was not said. Never make fun of a named person who is not on the show, and nothing about anyone's
   private life, health or family. On serious topics, talk about the issue, never an identifiable person. No
   swearing in the written text.

Every time you are given is in seconds from the start of the video, as in the transcript."""

TOOL = {
    "name": "save_episode",
    "description": "Save where the episode starts and ends, and its title and descriptions.",
    "input_schema": {
        "type": "object",
        "properties": {
            "start_seconds": {"type": "number", "description": "First word of the real show"},
            "end_seconds": {"type": "number", "description": "Last word of the real show"},
            "why": {"type": "string", "description": "One line, for the team: what was cut and why"},
            "title": {"type": "string", "description": "Episode title, under 70 characters"},
            "spotify_description": {"type": "string", "description": "Spotify / podcast description, a few short paragraphs"},
            "youtube_title": {"type": "string", "description": "YouTube title, under 90 characters"},
            "youtube_description": {"type": "string", "description": "YouTube description with a few hashtags at the end"},
        },
        "required": ["start_seconds", "end_seconds", "why", "title", "spotify_description",
                     "youtube_title", "youtube_description"],
    },
}

MAX_TRIM_START = 300.0  # never cut more than 5 minutes off the front
MAX_TRIM_END = 300.0    # or off the end
LOUDNESS = "loudnorm=I=-16:TP=-1.5:LRA=11"  # the podcast standard (Spotify and Apple ask for about -16 LUFS)


@dataclass
class EpisodePlan:
    start: float
    end: float
    why: str
    text: dict = field(default_factory=dict)


def build_prompt(words: list[Word], video_name: str, episode: int, duration: float) -> str:
    parts = [f"Episode: TRE Ep{episode}. Video file: {video_name}. Length: {duration:.0f} seconds."]
    examples = style_examples()
    if examples:
        parts.append("House style, past descriptions the host liked:\n<examples>\n" + examples + "\n</examples>")
    parts.append("Transcript (times are minutes:seconds; give your answer in seconds):\n<transcript>\n"
                 + transcript_text(words) + "\n</transcript>")
    parts.append("Give your answer by calling the save_episode tool.")
    return "\n\n".join(parts)


def snap_trim(words: list[Word], start: float, end: float, duration: float) -> tuple[float, float]:
    """Put the cut points on word boundaries with a little air, and never trim more than the limits allow."""
    if not words:
        return 0.0, duration
    start = max(0.0, min(start, MAX_TRIM_START))
    end = min(duration, max(end, duration - MAX_TRIM_END))
    first = min(words, key=lambda w: abs(w.start - start))
    last = min(words, key=lambda w: abs(w.end - end))
    s = max(0.0, first.start - 0.4)
    e = min(duration, last.end + 1.5)
    if e - s < duration * 0.5:  # something went wrong: keep the whole recording rather than lose the show
        return 0.0, duration
    return round(s, 2), round(e, 2)


def plan_episode(words: list[Word], video_name: str, episode: int, duration: float, client=None) -> tuple[EpisodePlan, dict]:
    answer, usage = call_tool(SYSTEM, build_prompt(words, video_name, episode, duration), TOOL, client)
    s, e = snap_trim(words, float(answer.get("start_seconds", 0)), float(answer.get("end_seconds", duration)), duration)
    text = {k: str(answer.get(k, "")).strip() for k in ("title", "spotify_description", "youtube_title", "youtube_description")}
    return EpisodePlan(s, e, str(answer.get("why", "")).strip(), text), usage


def has_audio(video: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "json", str(video)], check=True, capture_output=True, text=True).stdout
    return bool(json.loads(out).get("streams"))


def render(video: Path, info: VideoInfo, intro: Path | None, start: float, end: float, out: Path,
           outro: Path | None = None) -> None:
    """Intro (if any), the trimmed episode, then the outro (if any), at the episode's own size, sound levelled."""
    w, h = info.width // 2 * 2, info.height // 2 * 2
    length = end - start
    norm_v = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p"
    norm_a = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    fade_out = max(0.0, length - 1.5)
    inputs = ["-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(video)]
    parts = [
        f"[0:v]{norm_v},fade=t=out:st={fade_out:.2f}:d=1.5[v0]",
        f"[0:a]{LOUDNESS},{norm_a},afade=t=in:d=0.3,afade=t=out:st={fade_out:.2f}:d=1.5[a0]",
    ]
    order = ["0"]
    n = 0
    for role, extra in (("intro", intro), ("outro", outro)):
        if not extra:
            continue
        n += 1
        inputs += ["-i", str(extra)]
        parts.append(f"[{n}:v]{norm_v}[v{n}]")
        if has_audio(extra):
            parts.append(f"[{n}:a]{LOUDNESS},{norm_a}[a{n}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={probe(extra).duration:.2f},{norm_a}[a{n}]")
        if role == "intro":
            order.insert(0, str(n))
        else:
            order.append(str(n))
    if len(order) > 1:
        parts.append("".join(f"[v{i}][a{i}]" for i in order) + f"concat=n={len(order)}:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        maps = ["-map", "[v0]", "-map", "[a0]"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts), *maps,
           "-c:v", "libx264", "-preset", "faster", "-crf", "21", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)


def description_text(plan: EpisodePlan, episode: int, video_name: str, intro: bool, outro: bool = False) -> str:
    t = plan.text
    m = lambda x: f"{int(x // 60)}:{int(x % 60):02d}"  # noqa: E731
    return "\n".join([
        f"TRE Ep{episode}: {t.get('title', '')}",
        f"From {video_name}: kept {m(plan.start)} to {m(plan.end)}" + (" with the intro in front" if intro else "") + (" and the outro at the end" if outro else ""),
        f"What was cut: {plan.why}",
        "",
        "SPOTIFY / PODCAST TITLE", t.get("title", ""), "",
        "SPOTIFY / PODCAST DESCRIPTION", t.get("spotify_description", ""), "",
        "YOUTUBE TITLE", t.get("youtube_title", ""), "",
        "YOUTUBE DESCRIPTION", t.get("youtube_description", ""), "",
    ])
