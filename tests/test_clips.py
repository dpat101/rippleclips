import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from clips import config
from clips.captions import build_ass, chunks
from clips.cut import VideoInfo, cut_clip, plan_layout, probe
from clips.drive import DriveFile, episode_number, is_settled, safe_name
from clips.pick import clean, pick_clips, snap
from clips.transcribe import Word, as_lines, transcript_text


def words_every(seconds, step=0.5, start=0.0):
    out, t, i = [], start, 0
    while t + 0.4 <= seconds:
        out.append(Word(round(t, 2), round(t + 0.4, 2), f"word{i}" + ("." if i % 10 == 9 else "")))
        t += step
        i += 1
    return out


class DriveTests(unittest.TestCase):
    def test_episode_number(self):
        self.assertEqual(episode_number("TRE Ep52"), 52)
        self.assertEqual(episode_number("tre ep 7"), 7)
        self.assertIsNone(episode_number("Clips"))
        self.assertIsNone(episode_number("TRE Episode notes"))

    def test_settled(self):
        now = datetime(2026, 11, 3, 6, tzinfo=timezone.utc)
        f = lambda h: DriveFile("1", "a.mp4", "video/mp4", now - timedelta(hours=h))
        self.assertFalse(is_settled([], now, 2))
        self.assertFalse(is_settled([f(5), f(1)], now, 2))
        self.assertTrue(is_settled([f(5), f(3)], now, 2))

    def test_safe_name(self):
        self.assertEqual(safe_name('Marshall: "the ute"  story/2'), "Marshall the ute story 2")


class TranscriptTests(unittest.TestCase):
    def test_lines_break_on_sentence(self):
        lines = as_lines(words_every(10))
        self.assertTrue(lines[0][2].endswith("word9."))
        self.assertIn("[00:00.00 - 00:04.90]", transcript_text(words_every(10)))


class PickTests(unittest.TestCase):
    def setUp(self):
        self.words = words_every(300)

    def test_snap_to_words_and_limits(self):
        s, e = snap(self.words, 10.13, 40.2, 300)
        self.assertAlmostEqual(s, 9.85)   # word at 10.0, minus 0.15
        self.assertAlmostEqual(e, 40.8)   # word ending 40.4, plus 0.4
        self.assertIsNone(snap(self.words, 10, 15, 300))  # too short
        s, e = snap(self.words, 10, 200, 300)
        self.assertLessEqual(e - s, config.MAX_CLIP_SECONDS + 0.6)

    def test_clean_drops_overlaps(self):
        raw = [
            {"start_seconds": 10, "end_seconds": 40, "title": "A", "why_funny": "x", "instagram": "i"},
            {"start_seconds": 30, "end_seconds": 60, "title": "B", "why_funny": "x"},
            {"start_seconds": 100, "end_seconds": 130, "title": "C", "why_funny": "x"},
            {"start_seconds": "bad", "end_seconds": 1, "title": "D"},
        ]
        got = clean(raw, self.words, 300)
        self.assertEqual([c.title for c in got], ["A", "C"])
        self.assertEqual(got[0].text["instagram"], "i")

    def test_pick_with_fake_claude(self):
        block = SimpleNamespace(type="tool_use", name="save_clips", input={"clips": [
            {"start_seconds": 50, "end_seconds": 80, "title": "Ute story", "why_funny": "punchline",
             "instagram": "ig", "facebook": "fb", "youtube_title": "yt #shorts", "youtube_description": "d", "tiktok": "tt"}]})
        sent = {}

        class Msgs:
            def create(self, **kw):
                sent.update(kw)
                return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=100, output_tokens=50))

        clips, usage = pick_clips(self.words, "a.mp4", 52, 300, client=SimpleNamespace(messages=Msgs()))
        self.assertEqual(len(clips), 1)
        self.assertEqual(sent["tool_choice"], {"type": "auto"})
        self.assertEqual(sent["tools"][0]["name"], "save_clips")
        self.assertIn("Healing Through Laughter", sent["messages"][0]["content"])
        self.assertEqual(usage["output_tokens"], 50)


    def test_nudge_when_no_tool_call(self):
        text = SimpleNamespace(type="text", text="Here are my picks...")
        block = SimpleNamespace(type="tool_use", name="save_clips", input={"clips": []})
        calls = []

        class Msgs:
            def create(self, **kw):
                calls.append(kw)
                content = [text] if len(calls) == 1 else [block]
                return SimpleNamespace(content=content, usage=SimpleNamespace(input_tokens=10, output_tokens=5))

        clips, usage = pick_clips(self.words, "a.mp4", 52, 300, client=SimpleNamespace(messages=Msgs()))
        self.assertEqual(clips, [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(usage["input_tokens"], 20)


class TranscriptCacheTests(unittest.TestCase):
    def test_reuses_saved_transcript(self):
        from clips import run
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "t" / "x.json"
            orig = run.transcribe
            try:
                run.transcribe = lambda video, work: [Word(1.0, 1.5, "hi")]
                first = run.get_words(Path("v.mp4"), Path(d), cache)
                run.transcribe = lambda video, work: self.fail("transcribed twice")
                second = run.get_words(Path("v.mp4"), Path(d), cache)
            finally:
                run.transcribe = orig
            self.assertEqual(first, second)


class EpisodeTests(unittest.TestCase):
    def test_snap_trim_limits(self):
        from clips.episode import snap_trim
        words = words_every(900)
        s, e = snap_trim(words, 31.2, 880.0, 900)
        self.assertTrue(30 <= s <= 31.2)
        self.assertTrue(880 <= e <= 882)
        s, e = snap_trim(words, 700, 800, 900)  # never more than 5 minutes off the front
        self.assertLessEqual(s, 300)
        self.assertEqual(snap_trim([], 10, 20, 900), (0.0, 900))

    def test_plan_with_fake_claude(self):
        from clips.episode import plan_episode
        block = SimpleNamespace(type="tool_use", name="save_episode", input={
            "start_seconds": 42, "end_seconds": 290, "why": "mic check at the start", "title": "T",
            "spotify_description": "S", "youtube_title": "Y", "youtube_description": "YD"})

        class Msgs:
            def create(self, **kw):
                return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=1, output_tokens=1))

        plan, _ = plan_episode(words_every(300), "a.mp4", 52, 300, client=SimpleNamespace(messages=Msgs()))
        self.assertTrue(41 <= plan.start <= 42)
        self.assertEqual(plan.text["title"], "T")

    def test_render_with_and_without_intro_sound(self):
        from clips.episode import render
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src, intro, silent = d / "src.mp4", d / "intro.mp4", d / "silent.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "12", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25",
                            "-f", "lavfi", "-i", "sine=frequency=880", "-t", "3", "-c:v", "libx264", "-c:a", "aac", str(intro)], check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=30",
                            "-t", "2", "-c:v", "libx264", str(silent)], check=True)
            info = probe(src)
            for name, ip, op, want in (("a", intro, None, 3 + 8), ("b", silent, None, 2 + 8), ("c", None, None, 8),
                                       ("d", intro, silent, 3 + 8 + 2), ("e", None, intro, 8 + 3), ("f", intro, intro, 3 + 8 + 3)):
                out = d / f"{name}.mp4"
                render(src, info, ip, 2.0, 10.0, out, op)
                got = probe(out)
                self.assertEqual((got.width, got.height), (1280, 720))
                self.assertAlmostEqual(got.duration, want, delta=0.3)


class ProcessTests(unittest.TestCase):
    """One Drive video through clips and full episode, with Drive, Whisper and Claude faked."""

    def test_clips_then_full_episode(self):
        from clips import run
        from clips.episode import EpisodePlan
        from clips.pick import Clip
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "src.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "30", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
            uploads = []

            class FakeDrive:
                def download(self, file_id, dest):
                    dest.write_bytes(src.read_bytes())
                    return dest

                def folder(self, parent, name):
                    return name

                def upload(self, path, parent, mime):
                    uploads.append((parent, path.name))
                    return {"id": str(len(uploads)), "webViewLink": "link"}

            saved = dict(WORK_DIR=config.WORK_DIR, STATE_FILE=config.STATE_FILE)
            patched = dict(transcribe=run.transcribe, pick_clips=run.pick_clips, plan_episode=run.plan_episode,
                           face_centres=run.cut_clip.__globals__["face_centres"])
            try:
                config.WORK_DIR, config.STATE_FILE = d / "work", d / "state.json"
                run.transcribe = lambda video, work: words_every(30)
                run.pick_clips = lambda *a, **k: ([Clip(2.0, 16.0, "Ute story", "why", {"instagram": "ig"})], {})
                run.plan_episode = lambda *a, **k: (EpisodePlan(3.0, 28.0, "warm-up", {"title": "T"}), {})
                run.cut_clip.__globals__["face_centres"] = lambda *a, **k: []
                s = {"videos": {}}
                f = SimpleNamespace(id="vid1", name="TRE Ep52-1.mp4")
                run.process_drive_video(FakeDrive(), s, 52, SimpleNamespace(id="ep"), f, None)
            finally:
                config.WORK_DIR, config.STATE_FILE = saved["WORK_DIR"], saved["STATE_FILE"]
                run.transcribe, run.pick_clips, run.plan_episode = patched["transcribe"], patched["pick_clips"], patched["plan_episode"]
                run.cut_clip.__globals__["face_centres"] = patched["face_centres"]
            rec = s["videos"]["vid1"]
            self.assertEqual(rec["status"], "done")
            self.assertEqual(rec["episode_status"], "done", rec.get("episode_error"))
            folders = {p for p, _ in uploads}
            self.assertEqual(folders, {"Clips", "Full episode"})
            self.assertIn("TRE Ep52 - TRE Ep52-1 - full episode.mp4", [n for _, n in uploads])


class CaptionTests(unittest.TestCase):
    def test_chunks_and_times(self):
        w = words_every(6)
        self.assertTrue(all(len(c) <= 3 for c in chunks(w)))
        ass = build_ass(w, 2.0, 5.0)
        first = [l for l in ass.splitlines() if l.startswith("Dialogue")][0]
        self.assertIn("0:00:00.00", first)
        self.assertNotIn("{word", ass)

    def test_escapes_braces(self):
        ass = build_ass([Word(0, 1, "{hack}")], 0, 2)
        self.assertNotIn("{HACK}", ass)


class LayoutTests(unittest.TestCase):
    info = VideoInfo(1920, 1080, 600)

    def test_single_and_stack(self):
        self.assertEqual(plan_layout(self.info, [])["mode"], "single")
        one = [(900, 400, 150)] * 10
        self.assertEqual(plan_layout(self.info, one)["x"], 900)
        two = [(500, 400, 150)] * 8 + [(1450, 420, 150)] * 8
        lay = plan_layout(self.info, two)
        self.assertEqual(lay["mode"], "stack")
        self.assertEqual(lay["xs"], [500, 1450])
        self.assertEqual(plan_layout(VideoInfo(1080, 1920, 10), one)["mode"], "fit")


class CutTests(unittest.TestCase):
    """Real ffmpeg run on a made-up landscape video."""

    def test_cut_all_layouts(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "src.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "20", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
            info = probe(src)
            self.assertEqual((info.width, info.height), (1920, 1080))
            words = words_every(20)
            import clips.cut as cutmod
            for name, faces in (("single", []), ("stack", [(500, 400, 150)] * 8 + [(1450, 420, 150)] * 8)):
                orig = cutmod.face_centres
                cutmod.face_centres = lambda *a, **k: faces
                try:
                    out = d / f"{name}.mp4"
                    lay = cut_clip(src, info, 2.0, 16.0,
                                   lambda m: (d / f"{name}.ass").write_text(build_ass(words, 2.0, 16.0, m)) and d / f"{name}.ass", out)
                finally:
                    cutmod.face_centres = orig
                self.assertEqual(lay["mode"], name)
                got = probe(out)
                self.assertEqual((got.width, got.height), (1080, 1920))
                self.assertAlmostEqual(got.duration, 14.0, delta=0.3)


if __name__ == "__main__":
    unittest.main()
