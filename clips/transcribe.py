"""Speech to text on the droplet itself (faster-whisper, free), with the time of every word."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass
class Word:
    start: float
    end: float
    text: str


def extract_audio(video: Path, wav: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
        check=True,
    )
    return wav


def read_wav(wav: Path):
    """16 kHz mono wav -> float32 samples (avoids faster-whisper's own decoder and its PyAV version issues)."""
    import wave

    import numpy as np

    with wave.open(str(wav), "rb") as f:
        frames = f.readframes(f.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(video: Path, work: Path) -> list[Word]:
    from faster_whisper import WhisperModel

    wav = extract_audio(video, work / (video.stem + ".wav"))
    model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=2)
    segments, _ = model.transcribe(read_wav(wav), language="en", word_timestamps=True, vad_filter=True)
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(round(w.start, 2), round(w.end, 2), text))
    wav.unlink(missing_ok=True)
    return words


def as_lines(words: list[Word], max_gap: float = 0.8, max_words: int = 18) -> list[tuple[float, float, str]]:
    """Group words into short timed lines for Claude to read: (start, end, text)."""
    lines, cur = [], []
    for w in words:
        if cur and (w.start - cur[-1].end > max_gap or len(cur) >= max_words or cur[-1].text.endswith((".", "?", "!"))):
            lines.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        lines.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
    return lines


def fmt_time(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m):02d}:{s:05.2f}"


def transcript_text(words: list[Word]) -> str:
    return "\n".join(f"[{fmt_time(a)} - {fmt_time(b)}] {t}" for a, b, t in as_lines(words))
