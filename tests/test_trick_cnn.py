import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from learning import landing_cnn as base, trick_cnn as cnn


class TrickCNNTests(unittest.TestCase):
    def raw(self):
        x = np.random.default_rng(4).normal(size=(6, 6, 230)).astype(np.float32)
        x[:, 2] += 9.8
        return x

    def test_magnitudes_and_shape_validation(self):
        raw = self.raw()
        result = cnn.input_channels(raw, True)
        np.testing.assert_array_equal(result[:, :6], raw)
        np.testing.assert_allclose(result[:, 6], np.sqrt((raw[:, :3] ** 2).sum(axis=1)))
        np.testing.assert_allclose(result[:, 7], np.sqrt((raw[:, 3:] ** 2).sum(axis=1)))
        with self.assertRaises(ValueError):
            cnn.input_channels(raw[:, :5], True)

    def test_two_outputs_need_no_trick_input_and_survive_save(self):
        x = self.raw()
        y = [1, 0, 1, 0, 1, 0]
        tricks = ["ollie", "kickflip"] * 3
        for variant in ("raw8_multi", "raw8_multi_separable"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                model, meta, _ = cnn.fit(x, y, tricks, cnn.VARIANTS[variant], epochs=2)
                scores, probabilities = cnn.scores_for(model, meta, x)
                self.assertEqual(probabilities.shape, (6, 2))
                np.testing.assert_allclose(probabilities.sum(axis=1), 1, atol=1e-6)
                root = Path(tmp) / "model"
                cnn.save_model(root, model, meta)
                restored, saved = cnn.load_model(root / "model.json")
                actual, actual_tricks = cnn.scores_for(restored, saved, x)
                np.testing.assert_array_equal(scores, actual)
                np.testing.assert_array_equal(probabilities, actual_tricks)
                with (root / "weights.npz").open("ab") as stream:
                    stream.write(b"corrupt")
                with self.assertRaisesRegex(ValueError, "hash"):
                    cnn.load_model(root / "model.json")

    def test_vocabulary_weights_and_normalization_use_only_given_training_data(self):
        raw = self.raw()
        model, meta, _ = cnn.fit(raw[:4], [1, 0, 0, 0], ["a", "a", "a", "b"],
                                 cnn.VARIANTS["raw8_multi_separable"], epochs=1)
        self.assertEqual(meta["tricks"], ["a", "b"])
        self.assertEqual(meta["training"]["positive_weight"], 3)
        np.testing.assert_allclose(meta["training"]["trick_weights"], [2 / 3, 2])
        expected = cnn.fit_normalizer(cnn.input_channels(raw[:4], True))
        self.assertEqual(meta["normalizer"], expected)
        raw[4:] += 100
        cnn.scores_for(model, meta, raw[4:])
        self.assertEqual(meta["normalizer"], expected)

    def test_all_variants_fit_the_same_folds_and_unknown_tricks_count_as_errors(self):
        raw = self.raw()
        raw[4:] += 100
        examples = [dict(example_id=str(i), session_id=f"s{i // 2}", trick="kickflip" if i % 2 else f"rare{i // 2}",
                         landed=1 - i % 2, outcome="make" if i % 2 == 0 else "bail") for i in range(6)]
        audit = dict(dataset="synthetic", sha256={}, feature_exclusions=[], resampling_exclusions=[])
        with tempfile.TemporaryDirectory() as tmp, patch.object(base, "load_dataset", return_value=(
                examples, raw, np.arange(48).reshape(6, 8), audit)), patch.dict(base.FOREST, n_estimators=3):
            root = Path(tmp) / "run"
            report = cnn.train(Path(tmp), root, epochs=1, seeds=(0,))
            self.assertEqual(len(report["folds"]), 3)
            for fold in report["folds"]:
                train, test = fold["train_indices"], fold["test_indices"]
                self.assertFalse(set(train) & set(test))
                for variant, entry in fold["models"].items():
                    model, meta = cnn.load_model(root / entry["path"])
                    self.assertEqual(meta["training_example_ids"], [str(i) for i in train])
                    self.assertNotIn(f"rare{test[0] // 2}", meta["tricks"])
                    expected = cnn.fit_normalizer(cnn.input_channels(raw[train], cnn.VARIANTS[variant]["magnitudes"]))
                    self.assertEqual(meta["normalizer"], expected)
            self.assertEqual(report["results"]["raw8_multi"][0]["trick"]["unseen_in_training"], 3)
            before = (root / "report.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "already exists"):
                cnn.train(Path(tmp), root)
            self.assertEqual(before, (root / "report.json").read_bytes())
            self.assertEqual(json.loads(before)["primary_seed"], 0)


if __name__ == "__main__":
    unittest.main()
