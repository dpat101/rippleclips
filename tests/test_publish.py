import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from clips import config, publish


class FakeResp:
    def __init__(self, body, code=200):
        self.body, self.status_code = body, code

    def json(self):
        return self.body


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers=None, **kw):
        self.calls.append((method, url, kw))
        if url.endswith("/workspaces?per_page=50"):
            return FakeResp({"status": True, "data": [{"_id": "ws1", "name": "Ripple"}]})
        if "/accounts" in url:
            return FakeResp({"status": True, "data": [
                {"_id": "ig1", "platform": "instagram", "name": "IG"}, {"_id": "fb1", "platform": "facebook", "name": "FB"},
                {"_id": "yt1", "platform": "youtube", "name": "YT"}, {"_id": "tt1", "platform": "tiktok", "name": "TT"}]})
        if url.endswith("/media"):
            return FakeResp({"status": True, "data": {"_id": "m1", "url": "https://cdn/x.mp4"}})
        if url.endswith("/posts"):
            return FakeResp({"status": True, "data": {"id": f"p{len(self.calls)}"}})
        return FakeResp({"status": False, "message": "nope"}, 404)


class FakeDrive:
    def __init__(self, approved=(), skipped=()):
        self.approved, self.skipped = set(approved), set(skipped)

    def episodes(self):
        return [(52, SimpleNamespace(id="ep52"))]

    def folder(self, parent, name):
        return name

    def children(self, folder_id):
        ids = self.approved if folder_id == publish.APPROVED_FOLDER else self.skipped if folder_id == publish.SKIP_FOLDER else set()
        return [SimpleNamespace(id=i) for i in ids]

    def download(self, file_id, dest):
        dest.write_bytes(b"video")
        return dest


def state():
    clips = [{"number": n, "title": f"Clip {n}", "drive_id": f"c{n}",
              "text": {"instagram": "ig text", "facebook": "fb", "youtube_title": "YT #shorts",
                       "youtube_description": "desc", "tiktok": "tt"}} for n in (1, 2, 3, 4, 5)]
    return {"videos": {"v1": {"status": "done", "episode": 52, "name": "TRE Ep52-1.mp4", "clip_list": clips},
                       "v0": {"status": "done", "episode": 51, "name": "old.mp4",
                              "clip_list": [{"number": 1, "title": "old", "drive_id": "old1", "text": {}}]}}}


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = config.WORK_DIR
        config.WORK_DIR = Path(self.tmp.name)

    def tearDown(self):
        config.WORK_DIR = self.saved
        self.tmp.cleanup()
        os.environ.pop("POSTING", None)

    def run_once(self, s, mode, drive, today=date(2026, 11, 4)):
        os.environ["POSTING"] = mode
        http = FakeHTTP()
        publish.run(drive, s, publish.ContentStudio("k", http), today)
        return http

    def test_off_sends_nothing(self):
        s = state()
        os.environ["POSTING"] = "off"
        publish.run(FakeDrive(approved={"c1"}), s, cs=None)
        self.assertNotIn("posting", s)

    def test_approval_period_only_approved_one_a_day(self):
        s = state()
        http = self.run_once(s, "on", FakeDrive(approved={"c2", "c4", "old1"}))
        posts = s["posting"]["posts"]
        self.assertEqual(sorted(posts), ["c2", "c4"])  # Ep51 never posted, unapproved clips wait
        self.assertEqual([posts["c2"]["day"], posts["c4"]["day"]], ["2026-11-05", "2026-11-06"])
        self.assertEqual(set(posts["c2"]["ids"]), set(publish.PLATFORMS))
        self.assertEqual(s["posting"]["approval_until"], "2026-11-18")
        sent = [kw["json"] for m, u, kw in http.calls if u.endswith("/posts")]
        self.assertEqual(len(sent), 8)
        self.assertEqual(sent[0]["scheduling"], {"publish_type": "scheduled", "scheduled_at": "2026-11-05 18:00:00"})
        yt = [j for j in sent if j["accounts"] == ["yt1"]][0]
        self.assertEqual(yt["content"]["text"], "YT #shorts\n\ndesc")
        # Run again the same day: nothing new is sent.
        again = self.run_once(s, "on", FakeDrive(approved={"c2", "c4"}))
        self.assertFalse([1 for m, u, kw in again.calls if u.endswith("/posts")])

    def test_after_approval_all_but_dont_post_and_days_ahead(self):
        s = state()
        s["posting"] = {"approval_until": "2026-11-01", "posts": {}}
        self.run_once(s, "on", FakeDrive(skipped={"c1"}))
        posts = s["posting"]["posts"]
        self.assertEqual(sorted(posts), ["c2", "c3", "c4"])  # c1 skipped, c5 beyond 3 days ahead

    def test_drafts_kept_apart(self):
        s = state()
        http = self.run_once(s, "draft", FakeDrive(approved={"c1"}))
        self.assertIn("c1", s["posting"]["drafts"])
        self.assertNotIn("c1", s["posting"].get("posts", {}))
        sent = [kw["json"] for m, u, kw in http.calls if u.endswith("/posts")]
        self.assertEqual(sent[0]["scheduling"]["publish_type"], "draft")

    def test_error_message_never_has_key(self):
        cs = publish.ContentStudio("secret-key", FakeHTTP())
        with self.assertRaises(publish.CSError) as e:
            cs._call("GET", "/nothing")
        self.assertNotIn("secret-key", str(e.exception))


if __name__ == "__main__":
    unittest.main()
