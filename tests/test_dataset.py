import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capture.dataset import build, collect, inspect_session
from test_board_motion import motion_event, motion_metadata


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

    def configure_events(self, rows=()):
        meta = json.loads((self.path / "metadata.json").read_text())
        config = motion_metadata()
        config["onboard_quality"] = dict(complete=True, usable=True, issues=[])
        (self.path / "metadata.json").write_text(json.dumps(meta | config))
        self.write_jsonl(self.path / "events.jsonl", rows)

    def test_event_export_joins_adjacent_windows_preserving_full_bounds_and_raw_flags(self):
        event = motion_event(earliest_device_us=1950000, device_us=2040000, latest_device_us=2050000)
        self.configure_events([event])
        self.write_jsonl(self.path / "labels.jsonl", [self.label(), self.label(
            id="next", start_s=1, end_s=1.5, video_start_s=11, video_end_s=11.5, outcome="unknown")])
        before = {p.name: p.read_bytes() for p in self.path.iterdir()}
        report, labels, windows = collect(self.sessions)
        output = self.root / "events-export"
        build(output, report, labels, windows)
        events = [json.loads(line) for line in (output / "motion_events.jsonl").read_text().splitlines()]
        self.assertEqual(len(events), 2)
        self.assertEqual(len({r["event_id"] for r in events}), 1)
        indexed = {r["example_id"].split("/")[-1]: r for r in events}
        self.assertFalse(indexed["same-id"]["available_by_window_end"])
        self.assertTrue(indexed["next"]["available_by_window_end"])
        self.assertAlmostEqual(indexed["next"]["relative_earliest_s"], -.05)
        self.assertTrue(all(e["earliest_s"] == .95 and e["latest_s"] == 1.05 for e in events))
        self.assertTrue(all(e["d6d_src"] == 96 and e["profile"] == 2 for e in events))
        manifest = [json.loads(line) for line in (output / "manifest.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["board_motion"]["count"] == 1 for row in manifest))
        self.assertFalse(next(row for row in manifest if row["label"]["outcome"] == "unknown")["outcome_training"])
        self.assertNotIn("events", manifest[0]["board_motion"])
        self.assertEqual(report["sessions"][0]["source_sha256"]["events.jsonl"], hashlib.sha256(before["events.jsonl"]).hexdigest())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.iterdir()})

    def test_event_absence_is_not_a_negative_feature_and_excluded_windows_have_no_events(self):
        _, rows, _ = collect(self.sessions)
        self.assertEqual(rows[0]["board_motion"]["availability"], "not_recorded")
        self.assertIsNone(rows[0]["board_motion"]["count"])
        self.configure_events()
        _, rows, _ = collect(self.sessions)
        self.assertEqual(rows[0]["board_motion"]["count"], 0)
        (self.path / "events.jsonl").unlink()
        _, rows, windows = collect(self.sessions)
        self.assertEqual(rows[0]["board_motion"]["availability"], "missing")
        self.assertIsNone(rows[0]["board_motion"]["count"])
        self.assertTrue(windows)
        self.configure_events([motion_event()])
        self.write_jsonl(self.path / "labels.jsonl", [self.label(source="live_human")])
        _, rows, windows = collect(self.sessions)
        self.assertEqual(rows[0]["board_motion"]["events"], [])
        self.assertIsNone(rows[0]["board_motion"]["count"])
        self.assertFalse(windows)

    def test_bad_event_data_blocks_build_and_event_edits_during_scan_are_detected(self):
        self.configure_events([motion_event(wake_up=True)])
        report, rows, windows = collect(self.sessions)
        self.assertTrue(report["sessions"][0]["error"])
        with self.assertRaisesRegex(ValueError, "unreadable"):
            build(self.root / "invalid-events", report, rows, windows)
        self.configure_events([motion_event()])
        from capture.dataset import load_samples
        def changed(content):
            (self.path / "events.jsonl").write_text("")
            return load_samples(content)
        with patch("capture.dataset.load_samples", side_effect=changed), self.assertRaisesRegex(ValueError, "changed during scan"):
            inspect_session(self.path)

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

    def test_background_subtypes_survive_export_without_becoming_trick_outcomes(self):
        self.write_jsonl(self.path / "labels.jsonl", [
            self.label(outcome="background", trick="", activity="rolling"),
            self.label(id="legacy", outcome="background", trick="", start_s=1, end_s=1.5,
                       video_start_s=11, video_end_s=11.5)])
        report, labels, windows = collect(self.sessions)
        self.assertEqual(report["background_activities"], {"rolling": 1, "unspecified": 1})
        self.assertEqual(report["eligible_attempts"], 0)
        output = self.root / "typed-background"
        build(output, report, labels, windows)
        exported = {row["label"]["id"]: row["label"] for row in
                    (json.loads(line) for line in (output / "manifest.jsonl").read_text().splitlines())}
        self.assertTrue(all(row["outcome"] == "background" for row in exported.values()))
        self.assertEqual(exported["same-id"]["activity"], "rolling")
        self.assertNotIn("activity", exported["legacy"])

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

    def test_labeled_only_build_skips_incomplete_unlabeled_recordings(self):
        for name, content in (("a7s-002", None), ("a7s-003", ""), ("a7s-004", " \n")):
            path = self.sessions / name
            path.mkdir()
            if content is not None:
                (path / "labels.jsonl").write_text(content)
        self.assertTrue(any(s.get("error") for s in collect(self.sessions)[0]["sessions"]))
        report, labels, windows = collect(self.sessions, labeled_only=True)
        self.assertEqual([s["session_id"] for s in report["sessions"]], ["a7s-001"])
        self.assertEqual(report["skipped_unlabeled_sessions"], ["a7s-002", "a7s-003", "a7s-004"])
        output = self.root / "labeled-snapshot"
        build(output, report, labels, windows)
        self.assertTrue(json.loads((output / "report.json").read_text())["labeled_only"])

    def test_labeled_only_still_blocks_unreadable_labeled_recordings(self):
        other = self.make_session("a7s-002")
        for filename in ("labels.jsonl", "samples.csv"):
            with self.subTest(filename=filename):
                source = other / filename
                original = source.read_bytes()
                source.write_text("invalid")
                report, labels, windows = collect(self.sessions, labeled_only=True)
                self.assertTrue(report["sessions"][1]["error"])
                self.assertEqual(report["skipped_unlabeled_sessions"], [])
                with self.assertRaisesRegex(ValueError, "unreadable"):
                    build(self.root / "bad-snapshot", report, labels, windows)
                source.write_bytes(original)

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

    def test_sample_gap_override_is_scoped_and_preserves_source_and_export_warnings(self):
        other = self.make_session("a7s-002")
        for path in (self.path, other):
            meta = json.loads((path / "metadata.json").read_text())
            meta["onboard_quality"] = dict(usable=False, complete=True, issues=["sample_gaps_over_10ms"])
            (path / "metadata.json").write_text(json.dumps(meta))
            self.write_samples(path, [i / 200 for i in range(401) if i != 150])
        # A 15 ms interval is above the importer limit but below the window limit.
        self.write_samples(self.path, [i / 200 for i in range(401) if i not in (150, 151)])
        before = {p.name: p.read_bytes() for p in self.path.iterdir()}
        self.assertFalse(collect(self.sessions)[2])
        report, labels, windows = collect(self.sessions, allow_sample_gaps=["a7s-001"])
        self.assertEqual(report["eligible_attempts"], 1)
        self.assertEqual(report["excluded"], 1)
        self.assertEqual(set(windows), {"a7s-001/same-id"})
        output = self.root / "gap-snapshot"
        build(output, report, labels, windows)
        exported_report = json.loads((output / "report.json").read_text())
        exported_labels = [json.loads(line) for line in (output / "manifest.jsonl").read_text().splitlines()]
        self.assertEqual(exported_report["allow_sample_gaps"], ["a7s-001"])
        self.assertIn("sample_gaps_over_10ms", exported_report["sessions"][0]["warnings"][0])
        self.assertIn("sample_gaps_over_10ms", exported_labels[0]["warnings"][0])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.iterdir()})
        with self.assertRaisesRegex(ValueError, "do not match selection"):
            collect(self.sessions, allow_sample_gaps=["a7s-typo"])

    def test_sample_gap_override_does_not_accept_incomplete_or_other_quality_failures(self):
        meta = json.loads((self.path / "metadata.json").read_text())
        for quality in (
                dict(usable=False, complete=False, issues=["sample_gaps_over_10ms"]),
                dict(usable=False, issues=["sample_gaps_over_10ms"]),
                dict(usable=False, complete=True, issues=["sample_gaps_over_10ms", "acquisition_errors"]),
                dict(usable=False, complete=True, issues=[])):
            with self.subTest(quality=quality):
                meta["onboard_quality"] = quality
                (self.path / "metadata.json").write_text(json.dumps(meta))
                self.assertFalse(collect(self.sessions, allow_sample_gaps=["a7s-001"])[2])

    def test_sample_gap_override_retains_window_gap_and_clipping_checks(self):
        meta = json.loads((self.path / "metadata.json").read_text())
        meta["onboard_quality"] = dict(usable=False, complete=True, issues=["sample_gaps_over_10ms"])
        (self.path / "metadata.json").write_text(json.dumps(meta))
        for lo, hi in ((.7, .8), (.48, .52), (.98, 1.02)):
            with self.subTest(gap=(lo, hi)):
                self.write_samples(self.path, [i / 100 for i in range(201) if not lo < i / 100 < hi])
                _, labels, windows = collect(self.sessions, allow_sample_gaps=["a7s-001"])
                self.assertFalse(windows)
                self.assertIn("sensor gap over 30 ms", labels[0]["exclusion_reasons"])
        self.write_samples(self.path, channel_changes={.75: [320, 0, 9.8, 0, 0, 0]})
        _, labels, windows = collect(self.sessions, allow_sample_gaps=["a7s-001"])
        self.assertFalse(windows)
        self.assertIn("sensor clipping near full scale", labels[0]["exclusion_reasons"])

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
