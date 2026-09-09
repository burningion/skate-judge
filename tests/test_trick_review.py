import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from viz.trick_review import (
    audio_features, byte_range, extract_audio, make_server, validate_review,
)

HAS_SCIENCE = all(importlib.util.find_spec(name) for name in ("numpy", "scipy"))


class ReviewValidationTests(unittest.TestCase):
    def valid(self):
        return dict(revision=0, intervals=[dict(id="one", start_s=1., end_s=1.5,
                    status="reviewed", note="pop/contact")], settings=dict(
                    threshold=.65, min_gap=.18, max_gap=.9, offset=334., scale=1.))

    def test_review_rejects_bad_bounds_and_nonfinite_settings(self):
        for start, end in [(-1, 1), (2, 1), (1, 11), (float("nan"), 2), (True, 2)]:
            value = self.valid()
            value["intervals"][0].update(start_s=start, end_s=end)
            with self.assertRaises(ValueError):
                validate_review(value, 10)
        value = self.valid()
        value["settings"]["offset"] = float("inf")
        with self.assertRaises(ValueError):
            validate_review(value, 10)

    def test_review_rejects_duplicate_ids_and_inverted_gap(self):
        value = self.valid()
        value["intervals"] *= 2
        with self.assertRaises(ValueError):
            validate_review(value, 10)
        value = self.valid()
        value["settings"]["min_gap"] = 2
        with self.assertRaises(ValueError):
            validate_review(value, 10)

    def test_byte_ranges_for_seeking(self):
        self.assertEqual(byte_range(None, 100), (0, 99, False))
        self.assertEqual(byte_range("bytes=10-19", 100), (10, 19, True))
        self.assertEqual(byte_range("bytes=95-", 100), (95, 99, True))
        self.assertEqual(byte_range("bytes=-7", 100), (93, 99, True))
        self.assertEqual(byte_range("bytes=95-999", 100), (95, 99, True))
        for value in ("bytes=100-", "bytes=5-3", "bytes=-0", "bytes=0-1,4-5", "bytes=-"):
            with self.assertRaises(ValueError):
                byte_range(value, 100)


@unittest.skipUnless(HAS_SCIENCE, "audio analysis requires numpy/scipy (use uv run --with)")
class AudioTests(unittest.TestCase):
    def test_detects_two_impacts_and_preserves_silence(self):
        import numpy as np

        rate = 16000
        audio = np.zeros(rate * 4, dtype=np.float32)
        rng = np.random.default_rng(42)
        for time in (1.0, 1.45, 3.0):
            impact = rng.normal(size=640) * np.exp(-np.arange(640) / 50)
            start = round(time * rate)
            audio[start:start + len(impact)] = impact
        features = audio_features(audio)
        detected = [row["time_s"] for row in features["onsets"] if row["strength"] >= .65]
        for expected in (1.0, 1.45, 3.0):
            self.assertTrue(any(abs(actual - expected) <= .02 for actual in detected), detected)
        self.assertFalse(any(1.7 < actual < 2.8 for actual in detected))

    def test_silence_does_not_produce_onsets_or_nan(self):
        import numpy as np

        features = audio_features(np.zeros(16000))
        self.assertEqual(features["onsets"], [])
        self.assertEqual(max(features["strength"]), 0)
        json.dumps(features, allow_nan=False)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_delayed_audio_keeps_media_clock(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "delayed.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=10:duration=2",
                "-itsoffset", "0.6", "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=16000:duration=1",
                "-c:v", "ffv1", "-c:a", "pcm_s16le", str(media),
            ], check=True, capture_output=True)
            audio = extract_audio(media)
            active = np.flatnonzero(np.abs(audio) > .01)
            self.assertAlmostEqual(active[0] / 16000, .6, delta=.01)
            self.assertGreater(len(audio) / 16000, 1.59)


class ReviewHttpTests(unittest.TestCase):
    valid = ReviewValidationTests.valid
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.session = Path(self.temporary.name)
        self.directory = self.session / "review" / "example"
        self.directory.mkdir(parents=True)
        self.media = self.directory / "media.webm"
        self.media.write_bytes(bytes(range(100)))
        self.data = dict(duration_s=10, source=dict(name="example.webm", bytes=100, mtime_ns=1),
                         video="example.webm", source_pts_origin_s=0, algorithm="test",
                         media_url="/media.webm", sensor=dict(syncs=[
                             dict(id=1, session_s=11), dict(id=2, session_s=19)]))
        self.server = make_server(self.directory, self.media, self.data, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, path, value=None, headers=None, method=None):
        headers = dict(headers or {})
        if value is not None:
            headers["Content-Type"] = "application/json"
        return urlopen(Request(self.url + path, data=json.dumps(value).encode() if value else None,
                               headers=headers, method=method), timeout=5)

    def test_save_reload_and_stale_revision(self):
        with self.request("/api/review", self.valid()) as response:
            self.assertEqual(json.load(response)["revision"], 1)
        with self.request("/api/review") as response:
            self.assertEqual(json.load(response)["intervals"][0]["start_s"], 1)
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/review", self.valid())
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(json.loads((self.directory / "review.json").read_text())["revision"], 1)

    def test_range_and_head(self):
        with self.request("/media.webm", headers={"Range": "bytes=10-19"}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), bytes(range(10, 20)))
            self.assertEqual(response.headers["Content-Range"], "bytes 10-19/100")
        with self.request("/media.webm", method="HEAD") as response:
            self.assertEqual(response.headers["Content-Length"], "100")
            self.assertEqual(response.read(), b"")

    def test_forbidden_origins_hosts_and_arbitrary_paths(self):
        for path, headers in [("/api/review", {"Origin": "https://example.com"}),
                              ("/api/data", {"Host": "example.com"}),
                              ("/../metadata.json", {})]:
            with self.assertRaises(HTTPError) as caught:
                self.request(path, headers=headers)
            self.assertIn(caught.exception.code, (403, 404))

    def test_invalid_save_does_not_create_sidecar(self):
        value = self.valid()
        value["intervals"][0]["end_s"] = 11
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/review", value)
        self.assertEqual(caught.exception.code, 400)
        self.assertFalse((self.directory / "review.json").exists())

    def test_flash_matches_preserve_source_clock_and_reject_bad_fit_before_writing(self):
        self.data["source_pts_origin_s"] = -.25
        (self.session / "metadata.json").write_text(json.dumps(dict(
            closed_utc="2026-09-09", boot_id="abcd1234", device_origin_us=1000000)))
        (self.session / "events.jsonl").write_text("\n".join(json.dumps(dict(
            kind="sync", sync_id=identity, edge=1, led_enabled=True,
            boot_id="abcd1234", device_us=stamp)) for identity, stamp in [(1, 12000000), (2, 20000000)]))
        with self.request("/api/sync", dict(sync_id=1, review_s=1)) as response:
            self.assertEqual(json.load(response)["points"], 1)
        with self.request("/api/sync", dict(sync_id=2, review_s=9)) as response:
            mapping = json.load(response)
            self.assertEqual(mapping, dict(scale=1, offset_s=10.25, points=2))
        sync_path = self.session / "video_sync.jsonl"
        original = sync_path.read_text()
        self.assertEqual(json.loads(original.splitlines()[0])["video_s"], .75)
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/sync", dict(sync_id=2, review_s=8))
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(sync_path.read_text(), original)

    def test_flash_cannot_use_missing_event_or_open_session(self):
        for value in (dict(sync_id=99, review_s=1), dict(sync_id=1, review_s=1)):
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/sync", value)
            self.assertEqual(caught.exception.code, 400)
        self.assertFalse((self.session / "video_sync.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
