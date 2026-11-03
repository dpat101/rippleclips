"""Cut a clip to vertical 1080x1920 with burnt-in captions.

Framing: faces are found in frames sampled across the clip (OpenCV). If one person (or people close together)
is on screen, the frame is cropped around them. If two people sit far apart, the clip is split: one person on
top, one below, a common podcast layout.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float


def probe(video: Path) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_side_data=rotation:format=duration", "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    s = data["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    for sd in s.get("side_data_list", []) or []:
        if abs(int(sd.get("rotation", 0))) in (90, 270):
            w, h = h, w
    return VideoInfo(w, h, float(data["format"]["duration"]))


def face_centres(video: Path, start: float, end: float, samples: int = 16) -> list[tuple[float, float, float]]:
    """(x centre, y centre, face width) of faces found in frames sampled across the clip, in pixels."""
    import cv2

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(video))
    found = []
    try:
        for i in range(samples):
            t = start + (end - start) * (i + 0.5) / samples
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            h = grey.shape[0]
            faces = cascade.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=6, minSize=(h // 12, h // 12))
            for (x, y, fw, fh) in faces:
                found.append((x + fw / 2, y + fh / 2, float(fw)))
    finally:
        cap.release()
    return found


def median(values: list[float]) -> float:
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def plan_layout(info: VideoInfo, faces: list[tuple[float, float, float]]) -> dict:
    """Decide how to frame the clip. Returns {'mode': 'single', 'x': ...} or {'mode': 'stack', 'xs': [...], 'y': ...}."""
    w, h = info.width, info.height
    if w / h <= config.OUT_WIDTH / config.OUT_HEIGHT + 0.01:  # already vertical
        return {"mode": "fit"}
    crop_w = h * 9 / 16
    if not faces:
        return {"mode": "single", "x": w / 2}
    xs = sorted(f[0] for f in faces)
    # Split the faces at the biggest gap between them.
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    if gaps:
        gap, i = max(gaps)
        left, right = xs[: i + 1], xs[i + 1:]
        enough = min(len(left), len(right)) >= max(2, len(xs) // 6)
        if gap > crop_w * 0.7 and enough:
            ys = [f[1] for f in faces]
            return {"mode": "stack", "xs": [median(left), median(right)], "y": median(ys)}
    return {"mode": "single", "x": median(xs)}


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def filter_graph(info: VideoInfo, layout: dict, ass: Path) -> tuple[str, int]:
    """ffmpeg filter for the layout, and the caption margin from the bottom."""
    w, h = info.width, info.height
    ow, oh = config.OUT_WIDTH, config.OUT_HEIGHT
    subs = "subtitles=filename='{}'".format(str(ass).replace("'", r"\'").replace(":", r"\:"))
    if layout["mode"] == "fit":
        return (f"[0:v]scale={ow}:{oh}:force_original_aspect_ratio=decrease,"
                f"pad={ow}:{oh}:(ow-iw)/2:(oh-ih)/2,setsar=1,{subs}[v]"), 520
    if layout["mode"] == "single":
        cw = int(h * 9 / 16) // 2 * 2
        x = int(clamp(layout["x"] - cw / 2, 0, w - cw))
        return f"[0:v]crop={cw}:{h}:{x}:0,scale={ow}:{oh},setsar=1,{subs}[v]", 520
    # stack: two panels of 1080x960 (9:8), each centred on one person
    ch = int(min(h, w / 2 * 8 / 9) // 2 * 2)
    cw = int(ch * 9 / 8) // 2 * 2
    y = int(clamp(layout["y"] - ch / 2, 0, h - ch))
    panels = []
    for i, cx in enumerate(layout["xs"]):
        x = int(clamp(cx - cw / 2, 0, w - cw))
        panels.append(f"[s{i}]crop={cw}:{ch}:{x}:{y},scale={ow}:{oh // 2},setsar=1[p{i}]")
    graph = "[0:v]split=2[s0][s1];" + ";".join(panels) + f";[p0][p1]vstack=inputs=2,{subs}[v]"
    return graph, oh // 2 - 135  # captions sit across the join


def cut_clip(video: Path, info: VideoInfo, start: float, end: float, ass_writer, out: Path) -> dict:
    """Make the clip. ass_writer(margin_v) writes the captions file and returns its path."""
    layout = plan_layout(info, face_centres(video, start, end))
    margin = 520 if layout["mode"] != "stack" else config.OUT_HEIGHT // 2 - 135
    ass = ass_writer(margin)
    graph, _ = filter_graph(info, layout, ass)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(video), "-t", f"{end - start:.2f}",
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out),
    ]
    subprocess.run(cmd, check=True)
    log.info("cut %s (%s)", out.name, layout["mode"])
    return layout
