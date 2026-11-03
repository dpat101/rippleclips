import os
import random
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from clips import config, release
from tests.test_publish import FakeHTTP, FakeResp


class FakeDrive:
    def public_link(self, file_id):
        return f"https://drive/{file_id}"

    def download(self, file_id, dest):
        dest.write_bytes(b"video")
        return dest


class FakePodbean:
    def __init__(self):
        self.published = []

    def upload(self, path, content_type="audio/mpeg"):
        return "key-" + path.name

    def publish(self, title, content, media_key, when, draft):
        self.published.append((title, media_key, when, draft))
        return f"pb{len(self.published)}"


def state():
    def full(n):
        return {"episode_status": "done", "episode": 52, "name": f"TRE Ep52-{n}.mp4",
                "full_episode": {"drive_id": f"f{n}", "text": {"title": f"Title {n}", "spotify_description": "spot",
                                                               "youtube_title": f"YT {n}", "youtube_description": "yt"}}}
    return {"videos": {"v1": full(1), "v2": full(2), "v0": {**full(0), "episode": 51}}}


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = config.WORK_DIR
        config.WORK_DIR = Path(self.tmp.name)
        self.mp3 = mock.patch.object(release, "to_mp3", side_effect=lambda v, out: out.write_bytes(b"mp3") or out)
        self.mp3.start()

    def tearDown(self):
        self.mp3.stop()
        config.WORK_DIR = self.saved
        self.tmp.cleanup()
        os.environ.pop("POSTING", None)

    def go(self, s, mode, today=date(2026, 11, 4)):  # a Wednesday
        os.environ["POSTING"] = mode
        http, pb = FakeHTTP(), FakePodbean()
        release.run(FakeDrive(), s, release.ContentStudio("k", http), pb, today, rng=random.Random(3))
        return http, pb

    def test_one_a_week_on_release_day(self):
        s = state()
        http, pb = self.go(s, "on")
        eps = s["releases"]["episodes"]
        self.assertEqual(list(eps), ["v1"])  # one handed over; Ep51 never, Ep52-2 waits a week
        rec = eps["v1"]
        self.assertEqual(date.fromisoformat(rec["day"]).weekday(), config.RELEASE_DAY)
        self.assertEqual(rec["day"], "2026-11-10")
        self.assertEqual(set(rec["ids"]), {"youtube", "facebook", "podbean"})
        sent = [kw["json"] for m, u, kw in http.calls if u.endswith("/posts")]
        self.assertEqual({j["post_type"] for j in sent}, {"video"})
        self.assertTrue(all(j["content"]["media"] == {"media_ids": ["m1"]} for j in sent))
        yt = [j for j in sent if j["accounts"] == ["yt1"]][0]
        self.assertEqual(yt["youtube_options"]["title"], "YT 1")
        self.assertEqual(yt["content"]["text"], "yt")
        self.assertEqual(pb.published[0][0], "Title 1")
        self.assertFalse(pb.published[0][3])
        # A week later the next one is handed over, a week after the first.
        http, pb = self.go(s, "on", today=date(2026, 11, 10))
        self.assertEqual(s["releases"]["episodes"]["v2"]["day"], "2026-11-17")

    def test_draft_mode_kept_apart(self):
        s = state()
        _, pb = self.go(s, "draft")
        self.assertIn("v1", s["releases"]["drafts"])
        self.assertNotIn("episodes", s["releases"])
        self.assertTrue(pb.published[0][3])

    def test_time_within_window(self):
        rng = random.Random(5)
        to_min = lambda x: int(x[:2]) * 60 + int(x[3:])  # noqa: E731
        for _ in range(30):
            self.assertLessEqual(abs(to_min(release.pick_release_time(rng)) - to_min(config.RELEASE_TIME)),
                                 config.POST_JITTER_MINUTES)

    def test_podbean_errors_never_show_keys(self):
        os.environ["PODBEAN_CLIENT_ID"], os.environ["PODBEAN_CLIENT_SECRET"] = "id-123", "secret-456"
        try:
            class H:
                def post(self, *a, **kw):
                    return FakeResp({"error": "invalid_client", "error_description": "bad client"}, 401)
            with self.assertRaises(release.PodbeanError) as e:
                release.Podbean(H()).token()
            self.assertNotIn("secret-456", str(e.exception))
            self.assertIn("bad client", str(e.exception))
        finally:
            os.environ.pop("PODBEAN_CLIENT_ID"), os.environ.pop("PODBEAN_CLIENT_SECRET")


if __name__ == "__main__":
    unittest.main()
