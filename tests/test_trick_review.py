import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from viz.trick_review import (
    audio_features, byte_range, cached_video_timing, extract_audio, make_server, probe, validate_review, video_timing,
)

HAS_SCIENCE = all(importlib.util.find_spec(name) for name in ("numpy", "scipy"))


class FrameTimingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_indexes_120fps_media_with_its_actual_timestamp_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "120fps.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=120000/1001",
                "-frames:v", "60", "-c:v", "libx264", "-output_ts_offset", "0.125", str(media),
            ], check=True, capture_output=True)
            info = probe(media)["format"]
            duration = float(info["start_time"]) + float(info["duration"])
            timing = video_timing(media, duration)
            self.assertAlmostEqual(timing["fps"], 120000 / 1001)
            self.assertEqual(len(timing["frame_times_s"]), 60)
            self.assertAlmostEqual(timing["frame_times_s"][0], .125, delta=.001)
            self.assertAlmostEqual(timing["frame_times_s"][2] - timing["frame_times_s"][1],
                                   1001 / 120000, delta=1e-6)

    def test_preserves_variable_frame_intervals_and_rejects_empty_index(self):
        stream = dict(streams=[dict(codec_type="video", avg_frame_rate="0/0")])
        frames = dict(frames=[dict(best_effort_timestamp_time=t) for t in
                             (".031", "-.01", ".008", ".016342", ".031", "N/A", "nan", "1.1")])
        with patch("viz.trick_review.probe", return_value=stream), patch("viz.trick_review.run", return_value=json.dumps(frames).encode()):
            result = video_timing(Path("clip.webm"), 1)
        self.assertEqual(result, dict(fps=None, frame_times_s=[.008, .016342, .031]))
        with patch("viz.trick_review.probe", return_value=stream), patch("viz.trick_review.run", return_value=b'{"frames":[]}'):
            with self.assertRaisesRegex(ValueError, "No video frame timestamps"):
                video_timing(Path("clip.webm"), 1)

    def test_cached_index_is_rebuilt_only_when_review_media_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            media = directory / "media.mp4"
            media.write_bytes(b"first")
            timing = dict(fps=120, frame_times_s=[0, 1 / 120])
            with patch("viz.trick_review.video_timing", return_value=timing) as index:
                self.assertEqual(cached_video_timing(directory, media, 1), timing)
                self.assertEqual(cached_video_timing(directory, media, 1), timing)
                self.assertEqual(index.call_count, 1)
                media.write_bytes(b"replacement")
                cached_video_timing(directory, media, 1)
                self.assertEqual(index.call_count, 2)


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
        with self.request("/api/data") as response:
            self.assertEqual(json.load(response)["api_version"], 2)
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

    def aligned(self):
        self.data["source_pts_origin_s"] = -.25
        (self.session / "metadata.json").write_text(json.dumps(dict(closed_utc="2026-09-10", duration_s=30)))
        (self.session / "video_sync.jsonl").write_text("\n".join(json.dumps(dict(
            video="example.webm", sync_id=i, video_s=t, t_s=1.001 * t + 10))
            for i, t in [(1, 0), (2, 8)]))

    def decision(self, identity="one", outcome="make", start=1, end=4, decision="label"):
        with self.request("/api/review") as response:
            current = json.load(response)
        return dict(revision=current["revision"], labels_revision=current["labels_revision"],
                    mapping=current["label_context"]["mapping"], settings=self.valid()["settings"],
                    decision=decision, interval=dict(id=identity, start_s=2, end_s=2.5,
                        label_start_s=start, label_end_s=end, status="reviewed", outcome=outcome,
                        trick="ollie", note="watched roll-away"))

    def test_ui_labels_map_full_attempt_and_revise_without_duplicates(self):
        self.aligned()
        with self.request("/api/label", self.decision()) as response:
            saved = json.load(response)
        pair = saved["intervals"][0]
        self.assertEqual((pair["status"], pair["outcome"], pair["start_s"], pair["label_start_s"]),
                         ("labeled", "make", 2, 1))
        self.assertFalse(pair["label_stale"])
        with self.request("/api/label", self.decision(outcome="bail")) as response:
            revised = json.load(response)
        rows = [json.loads(line) for line in (self.session / "labels.jsonl").read_text().splitlines()]
        self.assertEqual([row["outcome"] for row in rows], ["make", "bail"])
        self.assertEqual(rows[0]["id"], rows[1]["id"])
        self.assertAlmostEqual(rows[0]["start_s"], 1.001 * .75 + 10)
        self.assertAlmostEqual(rows[0]["end_s"], 1.001 * 3.75 + 10)
        self.assertEqual(rows[0]["video_start_s"], .75)
        self.assertEqual(rows[0]["review_source"], self.data["source"])
        self.assertEqual(len(revised["intervals"]), 1)
        with self.request("/api/labels") as response:
            exported = json.load(response)["labels"]
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["outcome"], "bail")

    def test_not_trick_is_background_and_skip_is_unlabeled(self):
        self.aligned()
        with self.request("/api/label", self.decision(outcome="background")) as response:
            pair = json.load(response)["intervals"][0]
        self.assertEqual((pair["outcome"], pair["trick"]), ("background", ""))
        with self.request("/api/label", self.decision(identity="duplicate", decision="skip")) as response:
            self.assertEqual(len(json.load(response)["intervals"]), 2)
        with self.request("/api/labels") as response:
            self.assertEqual(len(json.load(response)["labels"]), 1)
        with self.assertRaises(HTTPError):
            self.request("/api/label", self.decision(decision="skip"))

    def test_background_range_touches_saved_attempt_and_round_trips_as_one_label(self):
        self.aligned()
        with self.request("/api/label", self.decision()):
            pass
        value = self.decision(identity="manual-background", outcome="background", start=4, end=9)
        value["interval"].update(start_s=4, end_s=9)
        with self.request("/api/label", value) as response:
            saved = json.load(response)
        background = next(row for row in saved["intervals"] if row["id"] == "manual-background")
        self.assertEqual((background["start_s"], background["end_s"], background["label_start_s"],
                          background["label_end_s"], background["outcome"], background["trick"]),
                         (4, 9, 4, 9, "background", ""))
        with self.request("/api/review") as response:
            self.assertEqual(len(json.load(response)["intervals"]), 2)
        with self.request("/api/labels") as response:
            labels = json.load(response)["labels"]
        self.assertEqual([row["outcome"] for row in labels], ["make", "background"])
        self.assertAlmostEqual(labels[0]["end_s"], labels[1]["start_s"])
        self.assertEqual(labels[1]["video_end_s"], 8.75)

    def test_legacy_rejections_are_not_automatically_background(self):
        value = self.valid()
        value["intervals"][0]["status"] = "rejected"
        with self.request("/api/review", value):
            pass
        with self.request("/api/labels") as response:
            self.assertEqual(json.load(response)["labels"], [])
        self.assertFalse((self.session / "labels.jsonl").exists())

    def test_label_validation_is_atomic_and_requires_alignment(self):
        with self.assertRaises(HTTPError):
            self.request("/api/label", self.decision())
        self.aligned()
        for changes in (dict(outcome="fail"), dict(outcome=None), dict(label_start_s=True),
                        dict(label_end_s=11), dict(label_start_s=5, label_end_s=4), dict(trick="x" * 201)):
            value = self.decision()
            value["interval"].update(changes)
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/label", value)
            self.assertEqual(caught.exception.code, 400)
            self.assertFalse((self.session / "labels.jsonl").exists())
            self.assertFalse((self.directory / "review.json").exists())
        with self.request("/api/label", self.decision()):
            pass
        before = (self.session / "labels.jsonl").read_bytes()
        with self.assertRaises(HTTPError):
            self.request("/api/label", self.decision(identity="overlap"))
        self.assertEqual((self.session / "labels.jsonl").read_bytes(), before)

    def test_changed_alignment_flags_labels_and_stale_tabs_cannot_save_or_export(self):
        self.aligned()
        stale = self.decision()
        with self.request("/api/label", stale):
            pass
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/label", stale)
        self.assertEqual(caught.exception.code, 409)
        old_mapping = self.decision()
        sync_path = self.session / "video_sync.jsonl"
        points = [json.loads(line) for line in sync_path.read_text().splitlines()]
        for point in points:
            point["t_s"] += .02
        sync_path.write_text("\n".join(json.dumps(point) for point in points))
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/label", old_mapping)
        self.assertEqual(caught.exception.code, 409)
        with self.request("/api/review") as response:
            self.assertTrue(json.load(response)["intervals"][0]["label_stale"])
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/labels")
        self.assertEqual(caught.exception.code, 409)
        with self.request("/api/label", self.decision()) as response:
            self.assertFalse(json.load(response)["intervals"][0]["label_stale"])

    def test_label_file_changes_invalidate_open_tabs(self):
        self.aligned()
        stale = self.decision()
        (self.session / "labels.jsonl").write_text(json.dumps(dict(id="elsewhere", start_s=25, end_s=26,
                                                    video="other.mp4", outcome="background")) + "\n")
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/label", stale)
        self.assertEqual(caught.exception.code, 409)

    def test_failed_sidecar_write_does_not_save_label(self):
        self.aligned()
        value = self.decision()
        with patch("viz.trick_review.write_json", side_effect=OSError("disk full")):
            with self.assertRaises(HTTPError):
                self.request("/api/label", value)
        self.assertFalse((self.session / "labels.jsonl").exists())

    def test_labels_restore_even_if_review_sidecar_is_lost(self):
        self.aligned()
        with self.request("/api/label", self.decision()):
            pass
        (self.directory / "review.json").unlink()
        with self.request("/api/review") as response:
            pair = json.load(response)["intervals"][0]
        self.assertEqual((pair["id"], pair["outcome"], pair["label_start_s"]), ("one", "make", 1))


if __name__ == "__main__":
    unittest.main()
