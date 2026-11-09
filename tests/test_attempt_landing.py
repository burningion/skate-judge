"""Session/trick isolation and honest end-to-end accounting."""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from learning import attempt_landing as classifier
from learning import attempts, landing


def clip_fixture(name):
    centers = np.arange(3., 9.)
    x = np.ones((6, len(attempts.FEATURE_NAMES)))
    x[:3] *= 5
    x[0, 5:] *= .1
    rows = []
    for i, (trick, outcome) in enumerate((("kickflip", "make"), ("kickflip", "bail"),
                                         ("flatground hand plant", "make"))):
        rows.append(dict(example_id=f"{name}/{i}", trick=trick, outcome=outcome,
                         start_s=centers[i] - 1, end_s=centers[i] + 1,
                         pop_s=centers[i] - .2, contact_s=centers[i] + .2,
                         match_start_s=centers[i] - .3, match_end_s=centers[i] + .3))
    return dict(session_id=name, centers=centers, x=x, grid_indices=np.arange(6),
                target=np.array([1, 1, 1, 0, 0, -1]),
                sources=np.array([f"{name}/0", f"{name}/1", f"{name}/2", f"{name}/bg", f"{name}/bg", ""]),
                exposure=np.array([0, 0, 0, .1, .1, 0]), attempts=rows, quality={},
                source_sha256={"samples.csv": "fixture"}, riders=["one"], boards=["one"],
                exclusions=[], training_omissions=[])


class ClassifierTests(unittest.TestCase):
    def test_holdout_removes_positive_windows_without_manufacturing_negatives(self):
        clip = clip_fixture("s0")
        training = classifier.training_clips([clip], [" Flatground Hand Plant "])[0]
        self.assertEqual(training["target"].tolist(), [1, 1, -1, 0, 0, -1])
        self.assertEqual(clip["target"].tolist(), [1, 1, 1, 0, 0, -1])
        self.assertEqual(len(training["attempts"]), 3)
        _, y, weights = classifier.outcome_arrays([training])
        self.assertAlmostEqual(weights[y == 0].sum(), weights[y == 1].sum())

    def test_missing_detection_is_not_a_correctly_classified_miss(self):
        rows = [dict(detected=True, landed=1, landed_score=.8, prediction="landed"),
                dict(detected=False, landed=0, landed_score=None, prediction="undetected")]
        result = classifier.outcome_metrics(rows)
        self.assertEqual(result["conditional_landing"]["n"], 1)
        self.assertEqual(result["detected_and_correct"], 1)
        self.assertEqual(result["misses"]["detected_and_correct"], 0)
        self.assertEqual(result["end_to_end_balanced_recall"], .5)
        self.assertIsNone(classifier.outcome_metrics([])["conditional_landing"])

    def test_training_artifacts_hold_out_whole_sessions_and_trick_in_every_fit(self):
        clips = [clip_fixture(f"s{i}") for i in range(3)]
        audit = dict(dataset="fixture", dataset_sha256={}, eligible_attempts=9, background_ranges=3)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "run"
            with patch.object(attempts, "load_collection", return_value=(clips, audit)), \
                    patch.dict(attempts.FOREST_CONFIG, n_estimators=5), \
                    patch.dict(classifier.OUTCOME_CONFIG, n_estimators=5):
                report = classifier.train(Path(tmp), output)
            for fold in report["folds"]:
                self.assertNotIn(fold["held_out_session"], fold["train_sessions"])
                self.assertTrue(all(not s.startswith(fold["held_out_session"] + "/")
                                    for s in fold["training_example_ids"]))
                self.assertTrue(all(not s.endswith("/2") for s in fold["training_example_ids"]))
            self.assertEqual(report["subsets"]["heldout_tricks"]["attempts"], 3)
            self.assertEqual(report["subsets"]["in_scope"]["attempts"], 6)
            model = json.loads((output / "model.json").read_text())
            self.assertTrue(all(not s.endswith("/2") for s in model["provenance"]["training_example_ids"]))
            with patch.dict(attempts.FOREST_CONFIG, n_estimators=5), \
                    patch.dict(classifier.OUTCOME_CONFIG, n_estimators=5):
                original_detector, original_outcome, _ = classifier.fit(clips, classifier.DEFAULT_HOLDOUT)
            # Independently check both serialized trees, beyond the train-time parity check.
            for expected, actual in zip((original_detector, original_outcome), classifier.model_scores(model, clips[0]["x"])):
                np.testing.assert_allclose(expected.predict_proba(clips[0]["x"])[:, 1], actual, atol=1e-12)
            samples = Path(tmp) / "samples.csv"
            t = np.arange(0, 4, .01)
            with samples.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(landing.CHANNELS)
                writer.writerows(np.column_stack([t, np.zeros((len(t), 2)),
                                                    np.full(len(t), landing.G), np.zeros((len(t), 3))]))
            result = classifier.scan_file(output / "model.json", samples)
            self.assertEqual(result["status"], "scanned")
            np.testing.assert_allclose(result["scored_center_ranges_s"], [[1., 2.9]])
            self.assertFalse(result["training_session_overlap"])
            with self.assertRaisesRegex(ValueError, "already exists"):
                classifier.train(Path(tmp), output)


if __name__ == "__main__":
    unittest.main()
