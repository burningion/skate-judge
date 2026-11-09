"""Automatic timing, nested recording holdouts, unknowns, and saved inference."""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from learning import automatic_landing as auto
from learning import attempts, landing


def samples():
    t = np.arange(0, 14.001, .01)
    result = np.column_stack([t, np.zeros((len(t), 2)), np.full(len(t), landing.G), np.zeros((len(t), 3))])
    for center in (4, 8):
        result[np.abs(t - center) < .2, 4] = 8
        result[np.abs(t - (center - .2)) < .011, 3] = 5 * landing.G
        result[np.abs(t - (center + .2)) < .011, 3] = 8 * landing.G
    result[(t > 8.3) & (t < 9), 4] = 10
    return result


def labels(session):
    result = []
    for name, start, end, outcome, center in (("make", 2.5, 5.5, "make", 4),
                                              ("miss", 6.5, 9.5, "bail", 8),
                                              ("background1", 0, 2.3, "background", None),
                                              ("background2", 10, 14, "background", None)):
        label = dict(start_s=start, end_s=end, video_start_s=start, video_end_s=end,
                     video_mapping=dict(scale=1, offset_s=0), trick="kickflip", outcome=outcome)
        if center:
            label["review_interval"] = dict(start_s=center - .2, end_s=center + .2)
        if session == "s0" and name == "make":
            label["trick"] = "flatground hand plant"
        result.append(dict(example_id=f"{session}/{name}", session_id=session, eligible=True,
                           outcome_training=outcome != "background", label=label))
    return result


class AutomaticTests(unittest.TestCase):
    def test_impact_is_sensor_only_and_missing_or_bad_support_stays_unknown(self):
        sensor = samples()
        event = dict(index=0, time_s=4., score=.9)
        impact = auto.estimate_contact(sensor, 4)
        self.assertAlmostEqual(impact["contact_peak_g"], 8)
        self.assertLess(abs(impact["estimated_contact_s"] - 4.2), .02)
        rows, raw = auto.event_inputs(sensor, [event])
        self.assertEqual(raw.shape, (1, 6, auto.cnn.N_SAMPLES))
        np.testing.assert_array_equal(raw[0], auto.cnn.resample_window(sensor, rows[0]["estimated_contact_s"]))
        missing, empty = auto.event_inputs(sensor, [dict(event, time_s=11.5)])
        self.assertIsNone(missing[0]["input_index"])
        self.assertEqual(len(empty), 0)
        self.assertIn("no impact", missing[0]["unknown_reason"])
        for bad in (sensor[(sensor[:, 0] < 4) | (sensor[:, 0] > 4.1)], sensor.copy()):
            if len(bad) == len(sensor):
                bad[np.argmin(abs(bad[:, 0] - 4.2)), 3] = 400
            with self.assertRaises(landing.FeatureError):
                auto.estimate_contact(bad, 4)
        edge = sensor.copy()
        edge[-25, 3] = 8 * landing.G
        rows, raw = auto.event_inputs(edge, [dict(event, time_s=13.)])
        self.assertEqual(len(raw), 0)
        self.assertIsNotNone(rows[0]["estimated_contact_s"])
        self.assertTrue(rows[0]["unknown_reason"])

    def test_unknown_and_missed_attempts_are_not_credited_as_rejected_misses(self):
        rows = [dict(landed=1, detected=True, landed_score=.8, prediction="landed", contact_error_s=.05),
                dict(landed=0, detected=True, landed_score=None, prediction="unknown", contact_error_s=None),
                dict(landed=0, detected=False, landed_score=None, prediction="undetected", contact_error_s=None)]
        m = auto.metrics(rows)
        self.assertEqual(m["scored"], 1)
        self.assertEqual(m["detected_but_unknown"], 1)
        self.assertEqual(m["misses"]["correct"], 0)
        self.assertEqual(m["end_to_end_balanced_recall"], .5)
        self.assertEqual(m["timing"]["n"], 1)
        self.assertIsNone(auto.metrics([])["conditional_landing"])

    def test_nested_training_and_label_free_saved_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            entries, manifest = [], []
            for i in range(4):
                directory = root / "sessions" / f"s{i}"
                directory.mkdir(parents=True)
                with (directory / "samples.csv").open("w", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(landing.CHANNELS)
                    writer.writerows(samples())
                (directory / "metadata.json").write_text('{}')
                (directory / "video_sync.jsonl").write_text('')
                entries.append(dict(session_id=f"s{i}", source_sha256={
                    p.name: landing.sha256(p) for p in directory.iterdir()}))
                manifest.extend(labels(f"s{i}"))
            dataset = root / "dataset"
            dataset.mkdir()
            (dataset / "report.json").write_text(json.dumps(dict(
                sessions_root=str(root / "sessions"), sessions=entries, eligible_attempts=8, background=8)))
            (dataset / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in manifest))
            (dataset / "samples.csv").write_text("example_id,t_s,ax,ay,az,gx,gy,gz\n")
            original = {p.name: landing.sha256(p) for p in dataset.iterdir()}
            output = root / "run"
            with patch.dict(attempts.FOREST_CONFIG, n_estimators=7, min_samples_leaf=1):
                report = auto.train(dataset, output, seeds=(0,), epochs=1)
            for fold in report["folds"]:
                outer = fold["held_out_session"]
                self.assertTrue(fold["cnn_training_examples"])
                for example in fold["cnn_training_examples"]:
                    self.assertNotEqual(outer, example["session_id"])
                    self.assertEqual(set(example["detector_excluded_sessions"]), {outer, example["session_id"]})
                    self.assertNotEqual(example["example_id"], "s0/make")
            for fitted in report["detector_fits"]:
                self.assertTrue(set(fitted["excluded_sessions"]).isdisjoint(fitted["train_sessions"]))
                self.assertNotIn("s0/make", fitted["training_example_ids"])
            self.assertNotIn("s0/make", report["provenance"]["training_example_ids"])
            self.assertEqual(report["subsets"]["heldout_tricks"]["0"]["attempts"], 1)
            self.assertEqual(original, {p.name: landing.sha256(p) for p in dataset.iterdir()})
            for path in (dataset / "manifest.jsonl", dataset / "report.json"):
                path.unlink()  # Inference cannot consult labels/alignment.
            result = auto.scan_file(output / "model.json", root / "sessions" / "s0" / "samples.csv")
            self.assertEqual(result["status"], "scanned")
            self.assertTrue(result["training_session_overlap"])
            self.assertTrue(result["events"])
            self.assertTrue(all("estimated_contact_s" in e and "prediction" in e and "index" not in e
                                for e in result["events"]))
            with self.assertRaisesRegex(ValueError, "already exists"):
                auto.train(dataset, output)
            metadata = output / "landing" / "model.json"
            metadata.write_text(metadata.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                auto.load_model(output / "model.json")


if __name__ == "__main__":
    unittest.main()
