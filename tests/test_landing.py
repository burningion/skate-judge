"""Checks for outcome leakage, contact timing, unusable data, and saved inference."""

import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from learning import landing


def signal(contact=1.0, motion=1.0):
    t = np.arange(0, 2.505, 0.005)
    phase = np.maximum(t - contact, 0)
    accel = np.column_stack([motion * np.sin(15 * phase),
                             motion * np.cos(12 * phase),
                             landing.G + motion * np.sin(30 * phase)])
    gyro = motion * np.column_stack([np.sin(8 * phase), np.cos(9 * phase), np.sin(11 * phase)])
    return np.column_stack([t, accel, gyro])


class LandingTests(unittest.TestCase):
    def test_contact_uses_scale_offset_and_pts_origin_and_rejects_partial_window(self):
        label = dict(video_mapping=dict(scale=1.001, offset_s=-10), source_pts_origin_s=2,
                     review_interval=dict(start_s=10, end_s=10.5),
                     video_start_s=11, video_end_s=14,
                     start_s=1.001 * 11 - 10, end_s=1.001 * 14 - 10)
        self.assertAlmostEqual(landing.contact_time(label), 1.001 * 12.5 - 10)
        label["end_s"] = 1.001 * 13 - 10
        label["video_end_s"] = 13
        with self.assertRaisesRegex(landing.FeatureError, "fixed"):
            landing.contact_time(label)
        label["end_s"] += 0.1
        with self.assertRaisesRegex(landing.FeatureError, "stale"):
            landing.contact_time(label)

    def test_features_are_invariant_to_mount_rotation_and_ignore_outside_motion(self):
        samples = signal()
        expected = landing.extract_features(samples, 1.0)
        rotated = samples.copy()
        rotation = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]])
        rotated[:, 1:4] = rotated[:, 1:4] @ rotation
        rotated[:, 4:7] = rotated[:, 4:7] @ rotation
        np.testing.assert_allclose(landing.extract_features(rotated, 1), expected, atol=1e-12)
        samples[samples[:, 0] > 2.1, 1:] = 10000
        np.testing.assert_allclose(landing.extract_features(samples, 1), expected)
        self.assertEqual(expected.shape, (8,))

    def test_invalid_sensor_windows_are_rejected(self):
        samples = signal()
        cases = []
        cases.append((samples[samples[:, 0] < 1.9], "coverage"))
        cases.append((samples[(samples[:, 0] < 1.3) | (samples[:, 0] > 1.4)], "gap"))
        for channel, value, message in [(1, np.nan, "nonfinite"), (1, 400, "clipping"),
                                         (4, 50, "clipping")]:
            changed = samples.copy()
            changed[250, channel] = value
            cases.append((changed, message))
        changed = samples.copy()
        changed[250, 1:4] = 0
        cases.append((changed, "all-zero"))
        changed = samples.copy()
        changed[250, 0] = changed[249, 0]
        cases.append((changed, "increasing"))
        for data, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(landing.FeatureError, reason):
                landing.extract_features(data, 1)

    def test_evaluation_fits_scaler_only_on_training_sessions(self):
        x = np.arange(72, dtype=float).reshape(9, 8)
        y = np.array([0, 1, 0, 0, 1, 1, 0, 1, 0])
        groups = np.repeat(["one", "two", "three"], 3)
        estimators = []
        original = landing.new_estimator

        def tracked_estimator():
            estimator = original()
            estimators.append(estimator)
            return estimator

        with patch.object(landing, "new_estimator", side_effect=tracked_estimator):
            scores, majority, folds = landing.evaluate(x, y, groups)
        covered = []
        for estimator, fold in zip(estimators, folds):
            test = np.array(fold["test_example_indices"])
            train = np.flatnonzero(groups != fold["held_out_session"])
            self.assertNotIn(fold["held_out_session"], fold["train_sessions"])
            np.testing.assert_allclose(estimator.steps[0][1].mean_, x[train].mean(axis=0))
            np.testing.assert_allclose(scores[test], estimator.predict_proba(x[test])[:, 1])
            np.testing.assert_equal(majority[test], float(y[train].mean() > 0.5))
            covered.extend(test.tolist())
        self.assertEqual(sorted(covered), list(range(len(x))))

    def test_single_class_training_fold_fails_instead_of_reporting_partial_results(self):
        with self.assertRaisesRegex(ValueError, "both landed"):
            landing.evaluate(np.ones((6, 8)), [1, 1, 0, 0, 0, 0], ["a", "a", "b", "b", "c", "c"])

    def test_metrics_expose_class_imbalance_and_undefined_precision(self):
        result = landing.metrics([1, 0, 0, 0], [0, 0, 0, 0])
        self.assertEqual(result["accuracy"], 0.75)
        self.assertEqual(result["balanced_accuracy"], 0.5)
        self.assertEqual(result["landed_recall"], 0)
        self.assertIsNone(result["landed_precision"])
        self.assertEqual(result["confusion_matrix"], [[3, 0], [1, 0]])
        self.assertIsNone(landing.metrics([0, 0], [0, 0])["balanced_accuracy"])

    def test_portable_model_matches_sklearn_and_abstains_on_unusable_input(self):
        x = np.array([landing.extract_features(signal(motion=m), 1) for m in [0.1, 0.2, 1, 2, 3, 4]])
        estimator = landing.fit_model(x, [1, 1, 1, 0, 0, 0])
        model = json.loads(json.dumps(landing.export_model(estimator, {}, "kickflip")))
        np.testing.assert_allclose(landing.model_scores(model, x), estimator.predict_proba(x)[:, 1], atol=1e-12)
        result = landing.predict(model, signal(), 1, "Kickflip")
        self.assertAlmostEqual(result["landed_score"], estimator.predict_proba(x[2:3])[0, 1])
        self.assertEqual(landing.predict(model, signal(), 1, "ollie")["status"], "unknown")
        self.assertEqual(landing.predict(model, signal(), 3, "kickflip")["status"], "unknown")
        model["feature_version"] = "future-version"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            landing.predict(model, signal(), 1, "kickflip")


class DatasetIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dataset = self.root / "snapshot"
        self.dataset.mkdir()
        self.rows = []
        self.samples = []
        for group in range(3):
            for index, outcome in enumerate(["make", "bail", "fall"]):
                identity = f"session-{group}/label-{index}"
                data = signal(motion=(0.2 if outcome == "make" else 2) + group * 0.1)
                row = dict(example_id=identity, session_id=f"session-{group}", rider="one", board="deck",
                           eligible=True, outcome_training=True, sample_count=len(data),
                           label=dict(trick="kickflip", outcome=outcome,
                                      start_s=0, end_s=2.505, video_start_s=10, video_end_s=12.505,
                                      video_mapping=dict(scale=1, offset_s=-10),
                                      review_interval=dict(start_s=10.7, end_s=11)))
                self.rows.append(row)
                self.samples.extend([[identity, *sample] for sample in data])
        for kind in ("background", "unknown", "ineligible", "other_trick", "partial"):
            row = copy.deepcopy(self.rows[0])
            row["example_id"] = "extra/" + kind
            if kind in ("background", "unknown"):
                row["label"]["outcome"] = kind
                row["outcome_training"] = kind != "unknown"
            elif kind == "ineligible":
                row["eligible"] = row["outcome_training"] = False
            elif kind == "other_trick":
                row["label"]["trick"] = "ollie"
            else:
                row["label"].update(end_s=1.8, video_end_s=11.8)
                data = signal()
                data = data[data[:, 0] < 1.8]
                row["sample_count"] = len(data)
                self.samples.extend([[row["example_id"], *sample] for sample in data])
            self.rows.append(row)
        self.save_manifest()
        with (self.dataset / "samples.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["example_id", *landing.CHANNELS])
            writer.writerows(self.samples)
        (self.dataset / "report.json").write_text('{"allow_sample_gaps": []}\n')

    def tearDown(self):
        self.temp.cleanup()

    def save_manifest(self):
        (self.dataset / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.rows))

    def test_filters_background_unknown_and_ineligible_with_explicit_feature_exclusions(self):
        rows, x, audit = landing.load_dataset(self.dataset)
        self.assertEqual(x.shape, (9, 8))
        self.assertEqual(sum(r["landed"] for r in rows), 3)
        self.assertEqual(audit["ignored"], dict(background=1, unknown=1, ineligible=1, other_trick=1))
        self.assertEqual(len(audit["feature_exclusions"]), 1)
        self.assertEqual(audit["feature_exclusions"][0]["example_id"], "extra/partial")

    def test_corrupt_export_fails_instead_of_silently_dropping_samples(self):
        self.rows[0]["sample_count"] += 1
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "sample count mismatch"):
            landing.load_dataset(self.dataset)

    def test_end_to_end_artifacts_and_immutable_output(self):
        output = self.root / "run"
        source_hashes = {p.name: landing.sha256(p) for p in self.dataset.iterdir()}
        report = landing.train(self.dataset, output)
        self.assertEqual(report["metrics"]["n"], 9)
        self.assertEqual(len(report["folds"]), 3)
        self.assertEqual(report["metrics"]["landed"], 3)
        with (output / "predictions.csv").open(newline="") as stream:
            predictions = list(csv.DictReader(stream))
        self.assertEqual(len(predictions), 9)
        for fold in report["folds"]:
            for i in fold["test_example_indices"]:
                self.assertEqual(predictions[i]["session_id"], fold["held_out_session"])
        model = json.loads((output / "model.json").read_text())
        self.assertEqual(landing.predict(model, signal(), 1, "kickflip")["status"], "scored")
        self.assertIn("held-out", (output / "report.md").read_text())
        before = {p.name: landing.sha256(p) for p in output.iterdir()}
        with self.assertRaisesRegex(ValueError, "already exists"):
            landing.train(self.dataset, output)
        self.assertEqual(before, {p.name: landing.sha256(p) for p in output.iterdir()})
        self.assertEqual(source_hashes, {p.name: landing.sha256(p) for p in self.dataset.iterdir()})


if __name__ == "__main__":
    unittest.main()
