import csv
import json
from pathlib import Path
import tempfile
import unittest

from capture.dataset import build, collect, inspect_session


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.path = self.make_session("a7s-001")

    def tearDown(self):
        self.temp.cleanup()

    def write_jsonl(self, path, rows):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def make_session(self, name):
        path = self.sessions / name
        path.mkdir(parents=True)
        (path / "metadata.json").write_text(json.dumps(dict(
            synthetic=False, closed_utc="2026-09-20", transport="onboard_flash",
            rider="rider-01", board="deck-01", onboard_quality=dict(usable=True, issues=[]))))
        (path / "clip.mp4").write_bytes(b"source identity fixture")
        self.write_jsonl(path / "video_sync.jsonl", [
            dict(video="clip.mp4", sync_id=1, video_s=10, t_s=0),
            dict(video="clip.mp4", sync_id=2, video_s=12, t_s=2)])
        self.write_jsonl(path / "labels.jsonl", [self.label()])
        self.write_samples(path)
        return path

    def write_samples(self, path, times=None, channel_changes=None):
        with (path / "samples.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("t_s", "ax", "ay", "az", "gx", "gy", "gz"))
            for time in times if times is not None else [i / 100 for i in range(201)]:
                channels = (channel_changes or {}).get(time, [0, 0, 9.8, .1, .2, .3])
                writer.writerow([time, *channels])

    def label(self, **changes):
        label = dict(id="same-id", source="video_human", video="clip.mp4", trick="ollie",
                     outcome="make", start_s=.5, end_s=1, video_start_s=10.5, video_end_s=11)
        label.update(changes)
        return label

    def test_latest_revision_and_session_scoped_ids(self):
        self.write_jsonl(self.path / "labels.jsonl", [self.label(), self.label(outcome="bail")])
        self.make_session("a7s-002")
        self.make_session("bench-001")
        report, labels, windows = collect(self.sessions)
        self.assertEqual(report["unique_labels"], 2)
        self.assertEqual(report["eligible_attempts"], 2)
        self.assertEqual(report["sessions"][0]["label_revisions"], 2)
        self.assertEqual([r["label"]["outcome"] for r in labels], ["bail", "make"])
        self.assertEqual(set(windows), {"a7s-001/same-id", "a7s-002/same-id"})

    def test_snapshot_contains_windows_provenance_and_cannot_be_overwritten(self):
        before = {p.name: p.read_bytes() for p in self.path.iterdir()}
        output = self.root / "dataset-v1"
        report, labels, windows = collect(self.sessions)
        build(output, report, labels, windows)
        exported = json.loads((output / "manifest.jsonl").read_text())
        self.assertEqual(exported["session_id"], "a7s-001")
        self.assertTrue(exported["outcome_training"])
        self.assertEqual(exported["sample_count"], 50)
        with (output / "samples.csv").open() as stream:
            samples = list(csv.DictReader(stream))
        self.assertEqual(len(samples), 50)
        self.assertEqual(float(samples[0]["relative_s"]), 0)
        self.assertEqual(float(samples[-1]["t_s"]), .99)
        self.assertEqual(len(report["sessions"][0]["source_sha256"]["labels.jsonl"]), 64)
        with self.assertRaisesRegex(ValueError, "already exists"):
            build(output, report, labels, windows)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.iterdir()})

    def test_background_and_unknown_do_not_inflate_attempt_count(self):
        self.write_jsonl(self.path / "labels.jsonl", [
            self.label(outcome="unknown"),
            self.label(id="bg", outcome="background", trick="", start_s=1, end_s=1.5,
                       video_start_s=11, video_end_s=11.5)])
        report, labels, windows = collect(self.sessions)
        self.assertEqual((report["eligible_attempts"], report["background"], report["unknown"]), (0, 1, 1))
        unknown = next(r for r in labels if r["label"]["outcome"] == "unknown")
        self.assertFalse(unknown["outcome_training"])
        self.assertIn(unknown["example_id"], windows)
        all_times = [sample[0] for window in windows.values() for sample in window]
        self.assertEqual(len(all_times), len(set(all_times)))

    def test_stale_alignment_is_excluded_until_label_is_resaved(self):
        with (self.path / "video_sync.jsonl").open("a") as stream:
            for sync_id, t in ((1, 0), (2, 2)):
                stream.write(json.dumps(dict(video="clip.mp4", sync_id=sync_id,
                                             video_s=10 + t, t_s=t + .1)) + "\n")
        _, labels, windows = collect(self.sessions)
        self.assertFalse(windows)
        self.assertIn("stale alignment", labels[0]["exclusion_reasons"][0])
        self.write_jsonl(self.path / "labels.jsonl", [self.label(start_s=.6, end_s=1.1)])
        self.assertEqual(collect(self.sessions)[0]["eligible_attempts"], 1)

    def test_synthetic_open_and_flagged_sessions_are_excluded(self):
        original = json.loads((self.path / "metadata.json").read_text())
        for updates in (dict(synthetic=True), dict(closed_utc=None),
                        dict(onboard_quality=dict(usable=False, issues=["flash_capacity_reached"]))):
            with self.subTest(updates=updates):
                (self.path / "metadata.json").write_text(json.dumps(original | updates))
                report, labels, windows = collect(self.sessions)
                self.assertEqual(report["excluded"], 1)
                self.assertTrue(labels[0]["exclusion_reasons"])
                self.assertFalse(windows)

    def test_unlabeled_session_remains_visible_without_inventing_background(self):
        (self.path / "labels.jsonl").unlink()
        report, labels, windows = collect(self.sessions)
        self.assertEqual(len(report["sessions"]), 1)
        self.assertEqual(report["background"], 0)
        self.assertEqual(labels, [])
        self.assertEqual(windows, {})

    def test_sd_recordings_receive_the_same_quality_gate_as_flash(self):
        meta = json.loads((self.path / "metadata.json").read_text())
        meta["transport"] = "onboard_sd"
        (self.path / "metadata.json").write_text(json.dumps(meta))
        self.assertEqual(collect(self.sessions)[0]["eligible_attempts"], 1)
        for quality in ({}, dict(usable=False, issues=["storage_buffer_full"])):
            meta["onboard_quality"] = quality
            (self.path / "metadata.json").write_text(json.dumps(meta))
            self.assertFalse(collect(self.sessions)[2])

    def test_gaps_inside_and_across_window_boundaries_are_excluded(self):
        for lo, hi in ((.7, .8), (.48, .52), (.98, 1.02), (0, .05)):
            with self.subTest(gap=(lo, hi)):
                if lo == 0:
                    self.write_jsonl(self.path / "labels.jsonl", [
                        self.label(start_s=0, video_start_s=10)])
                self.write_samples(self.path, [i / 100 for i in range(201) if not lo < i / 100 < hi])
                _, labels, windows = collect(self.sessions)
                self.assertFalse(windows)
                self.assertIn("sensor gap over 30 ms", labels[0]["exclusion_reasons"])

    def test_invalid_sensor_channels_zero_accel_and_clipping_are_not_exported(self):
        for values in ([float("nan"), 0, 9.8, 0, 0, 0], [0, 0, 0, 0, 0, 0],
                       [320, 0, 9.8, 0, 0, 0], [0, 0, 9.8, 41, 0, 0]):
            with self.subTest(values=values):
                self.write_samples(self.path, channel_changes={.75: values})
                _, _, windows = collect(self.sessions)
                self.assertFalse(windows)

    def test_gap_before_window_does_not_reject_a_complete_window(self):
        self.write_samples(self.path, [i / 100 for i in range(201) if not .4 < i / 100 < .5])
        self.assertEqual(collect(self.sessions)[0]["eligible_attempts"], 1)

    def test_overlaps_and_unreviewed_labels_are_excluded(self):
        self.write_jsonl(self.path / "labels.jsonl", [self.label(), self.label(id="duplicate")])
        _, labels, windows = collect(self.sessions)
        self.assertFalse(windows)
        self.assertTrue(all("overlapping labels" in row["exclusion_reasons"] for row in labels))
        self.write_jsonl(self.path / "labels.jsonl", [self.label(source="live_human")])
        self.assertFalse(collect(self.sessions)[2])

    def test_video_identity_and_missing_alignment_are_checked(self):
        stat = (self.path / "clip.mp4").stat()
        self.write_jsonl(self.path / "labels.jsonl", [self.label(review_source=dict(
            name="clip.mp4", bytes=stat.st_size, mtime_ns=stat.st_mtime_ns))])
        self.assertEqual(inspect_session(self.path)[0]["eligible_attempts"], 1)
        (self.path / "clip.mp4").write_bytes(b"replaced")
        self.assertFalse(collect(self.sessions)[2])
        self.write_jsonl(self.path / "labels.jsonl", [self.label()])
        (self.path / "video_sync.jsonl").unlink()
        self.assertFalse(collect(self.sessions)[2])

    def test_unreadable_sessions_are_reported_and_block_snapshot_build(self):
        other = self.make_session("a7s-002")
        (other / "labels.jsonl").write_text('{"id":')
        report, labels, windows = collect(self.sessions)
        self.assertEqual(report["eligible_attempts"], 1)
        self.assertTrue(report["sessions"][1]["error"])
        output = self.root / "bad-snapshot"
        with self.assertRaisesRegex(ValueError, "unreadable"):
            build(output, report, labels, windows)
        self.assertFalse(output.exists())

    def test_no_matching_sessions_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "No sessions match"):
            collect(self.sessions, "missing-*")


if __name__ == "__main__":
    unittest.main()
