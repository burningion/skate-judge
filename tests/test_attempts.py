"""Continuous detection: unknown time, session leakage, event counts, and inference."""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from learning import attempts as detector
from learning import landing


def sensor_data():
    t = np.arange(0, 30.001, 0.01)
    data = np.column_stack([t, np.zeros((len(t), 2)), np.full(len(t), landing.G), np.zeros((len(t), 3))])
    for center in (6, 17):
        motion = np.abs(t - center) < 0.3
        airborne = np.abs(t - center) < 0.15
        data[motion, 4] = 8 * np.cos(3 * (t[motion] - center))
        data[airborne, 3] = 0.2 * landing.G
        for impact in (center - 0.2, center + 0.2):
            data[np.abs(t - impact) < 0.015, 3] = 5 * landing.G
    return data


def label_rows(session="test"):
    rows = []
    for name, start, end, outcome in [("make", 4.5, 8.5, "make"), ("miss", 15.5, 19.5, "fall"),
                                      ("bg1", 10, 14, "background"), ("bg2", 22, 27, "background")]:
        label = dict(start_s=start, end_s=end, outcome=outcome, trick="kickflip", video="test.mp4",
                     video_start_s=start + 10, video_end_s=end + 10,
                     video_mapping=dict(scale=1, offset_s=-10))
        if outcome != "background":
            center = 6 if outcome == "make" else 17
            label["review_interval"] = dict(start_s=center - 0.2 + 10, end_s=center + 0.2 + 10)
        rows.append(dict(example_id=f"{session}/{name}", session_id=session, eligible=True,
                         outcome_training=True, rider="test-rider", board="test-board", label=label))
    return rows


def clip_fixture(session="test"):
    scan = detector.scan_windows(sensor_data())
    return dict(session_id=session, **scan, **detector.label_windows(scan, label_rows(session)),
                source_sha256={}, riders=["test-rider"], boards=["test-board"])


class AttemptTests(unittest.TestCase):
    def test_scanner_needs_no_labels_and_covers_known_motion(self):
        scan = detector.scan_windows(sensor_data())
        self.assertEqual(scan["quality"]["valid_windows"], 281)
        self.assertEqual(scan["x"].shape, (281, len(detector.FEATURE_NAMES)))
        near_motion = np.abs(scan["centers"] - 6) < 0.01
        self.assertGreater(scan["x"][near_motion, 0][0], 4.9)
        self.assertGreater(scan["x"][near_motion, 2][0], 7)
        rotated = sensor_data()
        rotation = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]])
        rotated[:, 1:4] = rotated[:, 1:4] @ rotation
        rotated[:, 4:7] = rotated[:, 4:7] @ rotation
        np.testing.assert_allclose(detector.scan_windows(rotated)["x"], scan["x"], atol=1e-12)

    def test_sensor_faults_are_unscored_and_never_bridged(self):
        samples = sensor_data()
        samples = samples[(samples[:, 0] < 12) | (samples[:, 0] > 12.2)]
        samples[np.isclose(samples[:, 0], 20), 1] = np.nan
        samples[np.isclose(samples[:, 0], 24), 4] = 100
        samples[np.isclose(samples[:, 0], 27), 1:4] = 0
        scan = detector.scan_windows(samples)
        for center in scan["centers"]:
            self.assertFalse(center - 1 < 12.2 and center + 1 > 12)
            for point in (20, 24, 27):
                self.assertFalse(center - 1 <= point < center + 1 - 1e-7)
        self.assertGreater(scan["quality"]["skipped"]["sensor gap over 30 ms"], 0)
        self.assertGreater(scan["quality"]["skipped"]["nonfinite, zero, or clipped channels"], 0)

    def test_short_recording_and_nonmonotonic_timestamps(self):
        self.assertEqual(detector.scan_windows(sensor_data()[:100])["quality"]["valid_windows"], 0)
        samples = sensor_data()
        samples[100, 0] = samples[99, 0]
        with self.assertRaisesRegex(ValueError, "increasing"):
            detector.scan_windows(samples)

    def test_unknown_time_is_not_a_negative_and_coverage_denominator_is_eroded(self):
        clip = clip_fixture()
        self.assertTrue(np.all(clip["target"][clip["centers"] < 4] == -1))
        self.assertTrue(np.all(clip["target"][(clip["centers"] >= 6 - .2) & (clip["centers"] <= 6 + .2)] == 1))
        self.assertTrue(np.all(clip["target"][(clip["centers"] >= 16.8) & (clip["centers"] <= 17.2)] == 1))
        self.assertAlmostEqual(clip["background_s"], 9)
        self.assertAlmostEqual(clip["exposure"].sum(), 5)
        self.assertEqual(set(clip["sources"][clip["target"] == 0]), {"test/bg1", "test/bg2"})
        for value in ("unknown", "background"):
            rows = label_rows()
            rows[0]["label"]["outcome"] = value
            labels = detector.label_windows(clip, rows)
            self.assertEqual(len(labels["attempts"]), 1)
        rows = label_rows()
        rows[0]["eligible"] = False
        labels = detector.label_windows(clip, rows)
        self.assertTrue(np.all(labels["target"][np.abs(clip["centers"] - 6) < 0.1] == -1))

    def test_short_attempt_still_counts_in_event_recall_without_training_windows(self):
        scan = detector.scan_windows(sensor_data())
        rows = label_rows()
        rows[0]["label"].update(start_s=5.5, end_s=6.5, video_start_s=15.5, video_end_s=16.5)
        labels = detector.label_windows(scan, rows)
        self.assertEqual(len(labels["attempts"]), 2)
        self.assertIn("test/make", [r["example_id"] for r in labels["training_omissions"]])
        self.assertNotIn("test/make", labels["sources"])
        summary, _, _ = detector.evaluate_events(dict(**scan, **labels), [])
        self.assertEqual(summary["attempts"], 2)
        self.assertEqual(summary["recall"], 0)

    def test_invalid_markers_are_reported_without_manufacturing_background(self):
        scan = detector.scan_windows(sensor_data())
        rows = label_rows()
        rows[0]["label"]["review_interval"]["start_s"] = 11
        rows[1]["label"]["review_interval"].update(start_s=26, end_s=28)
        labels = detector.label_windows(scan, rows)
        self.assertEqual(len(labels["exclusions"]), 2)
        self.assertEqual(len(labels["attempts"]), 0)
        self.assertTrue(np.all(labels["target"][np.abs(scan["centers"] - 6) < 1] == -1))

    def test_clock_scale_and_pts_origin_are_applied(self):
        row = label_rows()[0]
        row["label"].update(source_pts_origin_s=2, video_mapping=dict(scale=1.001, offset_s=-10),
                            start_s=1.001 * 14.5 - 10, end_s=1.001 * 18.5 - 10)
        row["label"]["review_interval"] = dict(start_s=13.8, end_s=14.2)
        action = detector.action_interval(row)
        self.assertAlmostEqual(action["pop_s"], 1.001 * 15.8 - 10)
        self.assertAlmostEqual(action["contact_s"], 1.001 * 16.2 - 10)

    def test_annotation_weights_do_not_treat_overlapping_windows_as_new_attempts(self):
        clip = clip_fixture()
        _, y, weights = detector.training_arrays([clip])
        sources = clip["sources"][clip["target"] >= 0]
        self.assertAlmostEqual(weights[y == 0].sum(), weights[y == 1].sum())
        self.assertAlmostEqual(weights[sources == "test/bg1"].sum(), weights[sources == "test/bg2"].sum())
        clip["target"][clip["target"] == 0] = -1
        with self.assertRaisesRegex(ValueError, "attempts and background"):
            detector.training_arrays([clip])

    def test_suppression_and_event_matching_count_duplicates_and_unknowns(self):
        events = detector.detect_events([5, 5.1, 5.2, 6.2], [.6, .9, .8, .7])
        self.assertEqual([r["time_s"] for r in events], [5.1, 6.2])
        clip = clip_fixture()
        selected = []
        for time in (5.5, 6.5, 11.5, 9.5):
            i = int(np.argmin(np.abs(clip["centers"] - time)))
            selected.append(dict(index=i, time_s=float(clip["centers"][i]), score=0.8))
        summary, evaluated, attempts = detector.evaluate_events(clip, selected)
        self.assertEqual(summary["detected_attempts"], 1)
        self.assertEqual(summary["duplicate_events"], 1)
        self.assertEqual(summary["background_false_events"], 1)
        self.assertEqual(summary["unclassified_events"], 1)
        self.assertAlmostEqual(summary["false_events_per_background_minute"], 12)
        self.assertEqual([a["detected"] for a in attempts], [True, False])
        self.assertEqual(sum(e["assessment"] == "matched_attempt" for e in evaluated), 1)

    def test_one_event_cannot_match_two_attempts_at_a_shared_boundary(self):
        clip = clip_fixture()
        clip["attempts"][0]["match_end_s"] = 10
        clip["attempts"][1]["match_start_s"] = 10
        i = int(np.argmin(np.abs(clip["centers"] - 10)))
        result, _, _ = detector.evaluate_events(clip, [dict(index=i, time_s=10, score=0.8)])
        self.assertEqual(result["detected_attempts"], 1)

    def test_portable_forest_matches_sklearn_and_rejects_cyclic_artifacts(self):
        clip = clip_fixture()
        with patch.dict(detector.FOREST_CONFIG, n_estimators=7):
            fitted = detector.fit_detector([clip])
        model = json.loads(json.dumps(detector.export_model(fitted, {})))
        np.testing.assert_allclose(detector.model_scores(model, clip["x"]), fitted.predict_proba(clip["x"])[:, 1], atol=1e-12)
        model["trees"][0]["left"][0] = 0
        with self.assertRaisesRegex(ValueError, "structure"):
            detector.model_scores(model, clip["x"])


class AttemptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        self.sessions = self.root / "sessions"
        entries, rows = [], []
        for index in range(3):
            name = f"session-{index}"
            path = self.sessions / name
            path.mkdir(parents=True)
            samples = sensor_data()
            samples[:, 4:] *= 1 + index * 0.05
            with (path / "samples.csv").open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(landing.CHANNELS)
                writer.writerows(samples)
            (path / "metadata.json").write_text('{}\n')
            (path / "video_sync.jsonl").write_text('')
            entries.append(dict(session_id=name, source_sha256={p.name: landing.sha256(p) for p in path.iterdir()}))
            rows.extend(label_rows(name))
        (self.dataset / "manifest.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        (self.dataset / "samples.csv").write_text("example_id,t_s,ax,ay,az,gx,gy,gz\n")
        (self.dataset / "report.json").write_text(json.dumps(dict(sessions_root=str(self.sessions), sessions=entries,
                                                                 eligible_attempts=6, background=6)))

    def tearDown(self):
        self.temp.cleanup()

    def test_train_holds_out_all_session_windows_and_scan_needs_no_labels(self):
        output = self.root / "run"
        original = detector.fit_detector
        seen = []

        def observed_fit(clips):
            seen.append([c["session_id"] for c in clips])
            return original(clips)

        with patch.dict(detector.FOREST_CONFIG, n_estimators=7), patch.object(detector, "fit_detector", side_effect=observed_fit):
            report = detector.train(self.dataset, output)
        self.assertEqual(len(seen), 4)
        for i, fold in enumerate(report["folds"]):
            self.assertEqual(seen[i], fold["train_sessions"])
            self.assertNotIn(fold["held_out_session"], seen[i])
        self.assertEqual(report["metrics"]["attempts"], 6)
        self.assertGreater(report["metrics"]["detected_attempts"], 0)
        self.assertAlmostEqual(report["metrics"]["background_exposure_s"], 15)
        with (output / "windows.csv").open() as stream:
            windows = list(csv.DictReader(stream))
        self.assertEqual(len(windows), 843)
        self.assertEqual(len({(r["session_id"], r["center_s"]) for r in windows}), 843)
        result = detector.scan_file(output / "model.json", self.sessions / "session-0" / "samples.csv")
        self.assertEqual(result["status"], "scanned")
        self.assertEqual(result["scored_center_ranges_s"], [[1, 29]])
        self.assertTrue(result["training_session_overlap"])
        self.assertGreater(len(result["events"]), 0)
        self.assertTrue(all("index" not in e and "time_s" in e for e in result["events"]))
        before = {p.name: landing.sha256(p) for p in output.iterdir()}
        with self.assertRaisesRegex(ValueError, "already exists"):
            detector.train(self.dataset, output)
        self.assertEqual(before, {p.name: landing.sha256(p) for p in output.iterdir()})

    def test_changed_continuous_source_blocks_evaluation(self):
        path = self.sessions / "session-0" / "metadata.json"
        path.write_text('{"changed": true}\n')
        with self.assertRaisesRegex(ValueError, "changed since snapshot"):
            detector.load_collection(self.dataset)

    def test_no_background_exposure_is_undefined_not_zero_false_alarm_rate(self):
        clip = clip_fixture()
        clip["exposure"][:] = 0
        summary, _, _ = detector.evaluate_events(clip, [])
        self.assertIsNone(summary["false_events_per_background_minute"])


if __name__ == "__main__":
    unittest.main()
