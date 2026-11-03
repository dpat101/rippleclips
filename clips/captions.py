"""Bold word-by-word captions as an .ass subtitle file, burnt into the clip by ffmpeg."""
from __future__ import annotations

from pathlib import Path

from .transcribe import Word

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,82,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

HIGHLIGHT = r"{\c&H00D7FF&}"  # yellow, ASS colours are BGR
PLAIN = r"{\c&HFFFFFF&}"


def ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def chunks(words: list[Word], max_words: int = 3, max_seconds: float = 1.4) -> list[list[Word]]:
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w.end - cur[0].start > max_seconds or w.start - cur[-1].end > 0.6
                    or cur[-1].text.endswith((".", "?", "!", ","))):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def build_ass(words: list[Word], clip_start: float, clip_end: float, margin_v: int = 520) -> str:
    """Captions for the words inside [clip_start, clip_end], timed from the start of the clip.

    Each chunk of up to 3 words is shown together, with the word being spoken in yellow.
    """
    inside = [w for w in words if w.end > clip_start and w.start < clip_end]
    lines = [HEADER.format(margin_v=margin_v)]
    for group in chunks(inside):
        for i, w in enumerate(group):
            start = w.start - clip_start
            end = (group[i + 1].start if i + 1 < len(group) else group[-1].end + 0.15) - clip_start
            end = min(end, clip_end - clip_start)
            if end <= start:
                continue
            text = " ".join((HIGHLIGHT + escape(x.text.upper()) + PLAIN) if j == i else escape(x.text.upper())
                            for j, x in enumerate(group))
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def write_ass(words: list[Word], clip_start: float, clip_end: float, path: Path, margin_v: int = 520) -> Path:
    path.write_text(build_ass(words, clip_start, clip_end, margin_v), encoding="utf-8")
    return path
