"""Raw-waveform resampling, session isolation, deterministic training, and model IO."""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from learning import landing
from learning import landing_cnn as cnn


def sensor_window(motion=1.0):
    times = np.arange(0, 2.5, 0.005)
    return np.column_stack([times, motion * np.sin(8 * times), motion * np.cos(9 * times),
                            landing.G + motion * np.sin(20 * times),
                            motion * np.sin(13 * times), motion * np.cos(7 * times), motion * np.sin(4 * times)])


def tensors():
    return np.asarray([cnn.resample_window(sensor_window(motion), 1) for motion in [.1, 2, .2, 3, .3, 4]])


class CNNTests(unittest.TestCase):
    def test_resampling_uses_actual_timestamps_and_preserves_six_channels(self):
        times = np.arange(0, 2.5, .0051)
        channels = np.column_stack([times, 2 * times, landing.G + times, .1 * times, .2 * times, .3 * times])
        actual = cnn.resample_window(np.column_stack([times, channels]), 1)
        query = 1 + cnn.RELATIVE_S
        expected = np.array([query, 2 * query, landing.G + query, .1 * query, .2 * query, .3 * query])
        self.assertEqual(actual.shape, (6, 230))
        self.assertEqual(actual.dtype, np.float32)
        np.testing.assert_allclose(actual, expected, rtol=1e-7)

    def test_resampling_never_extrapolates_or_fills_acquisition_gaps(self):
        times = np.arange(.853, 2.2, .005)
        samples = np.column_stack([times, np.tile([0, 0, landing.G, .1, .2, .3], (len(times), 1))])
        with self.assertRaisesRegex(landing.FeatureError, "no extrapolation"):
            cnn.resample_window(samples, 1)
        samples = sensor_window()
        samples = samples[(samples[:, 0] < 1.2) | (samples[:, 0] > 1.25)]
        with self.assertRaisesRegex(landing.FeatureError, "gap"):
            cnn.resample_window(samples, 1)

    def test_normalization_retains_between_attempt_offsets_and_handles_constant_channels(self):
        x = np.zeros((2, 6, 230), dtype=np.float32)
        x[1, 0] = 4
        normalization = cnn.fit_normalizer(x)
        self.assertEqual(normalization["mean"][0], 2)
        self.assertEqual(normalization["scale"][0], 2)
        self.assertEqual(normalization["scale"][1], 1)
        transformed = cnn.normalize(x, normalization)
        np.testing.assert_array_equal(transformed[0, 0], -1)
        np.testing.assert_array_equal(transformed[1, 0], 1)
        self.assertTrue(np.isfinite(transformed).all())
        normalization["scale"][0] = 0
        with self.assertRaisesRegex(ValueError, "normalization"):
            cnn.normalize(x, normalization)

    def test_architecture_size_and_sigmoid_predictions(self):
        model = cnn.LandingCNN()
        size = cnn.model_size(model)
        self.assertEqual(size["parameters"], 6345)
        self.assertEqual(size["estimated_int8_weight_bytes"], 6272)
        self.assertEqual(size["float32_parameter_bytes"], 25380)
        x = tensors()
        norm = cnn.fit_normalizer(x)
        scores = cnn.scores_for(model, norm, x)
        self.assertEqual(scores.shape, (6,))
        self.assertTrue(((scores >= 0) & (scores <= 1)).all())
        np.testing.assert_array_equal(scores, cnn.scores_for(model, norm, x))

    def test_fixed_seed_reproduces_training_and_class_weights_use_training_counts(self):
        x = tensors()
        y = [1, 0, 1, 0, 0, 0]
        first = cnn.fit_network(x, y, seed=7, epochs=2)
        second = cnn.fit_network(x, y, seed=7, epochs=2)
        self.assertEqual(first[2], 2)
        self.assertEqual(first[3], second[3])
        np.testing.assert_array_equal(cnn.scores_for(first[0], first[1], x), cnn.scores_for(second[0], second[1], x))
        with self.assertRaisesRegex(ValueError, "both landed"):
            cnn.fit_network(x, np.zeros(6), epochs=2)
        with self.assertRaisesRegex(ValueError, "positive"):
            cnn.fit_network(x, y, epochs=0)

    def test_held_out_session_never_enters_fit_or_normalization(self):
        x = tensors()
        x[4:] += 100  # A held-out outlier must not change that fold's channel statistics.
        y = np.array([1, 0, 1, 0, 1, 0])
        groups = np.array(["a", "a", "b", "b", "c", "c"])
        features = np.arange(48, dtype=float).reshape(6, 8)
        original = cnn.fit_network
        seen = []

        def observed_fit(train_x, train_y, seed, epochs):
            seen.append((train_x.copy(), train_y.copy()))
            return original(train_x, train_y, seed, epochs)

        with patch.dict(cnn.FOREST, n_estimators=7), patch.object(cnn, "fit_network", side_effect=observed_fit):
            scores, _, folds, history = cnn.evaluate(x, y, groups, features, seeds=(3,), epochs=2)
        indices = []
        for fold, (train_x, train_y) in zip(folds, seen):
            train = fold["train_indices"]
            self.assertNotIn(fold["held_out_session"], fold["train_sessions"])
            np.testing.assert_array_equal(train_x, x[train])
            np.testing.assert_array_equal(train_y, y[train])
            np.testing.assert_allclose(fold["normalization"]["mean"], x[train].mean(axis=(0, 2), dtype=np.float64))
            indices.extend(fold["test_indices"])
        self.assertEqual(sorted(indices), list(range(6)))
        self.assertTrue(np.isfinite(scores[3]).all())
        self.assertEqual(len(history), 6)

    def test_save_load_parity_unknown_inputs_and_integrity_checks(self):
        x = tensors()
        model, norm, _, _ = cnn.fit_network(x, [1, 0, 1, 0, 1, 0], epochs=2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cnn.save_model(root, model, norm, {}, "kickflip", 0)
            restored, metadata = cnn.load_model(root / "model.json")
            np.testing.assert_array_equal(cnn.scores_for(restored, metadata["normalizer"], x), cnn.scores_for(model, norm, x))
            self.assertEqual(cnn.predict(restored, metadata, sensor_window(), 1, "kickflip")["status"], "scored")
            self.assertEqual(cnn.predict(restored, metadata, sensor_window(), 5, "kickflip")["status"], "unknown")
            self.assertEqual(cnn.predict(restored, metadata, sensor_window(), 1, "ollie")["status"], "unknown")
            with (root / "weights.npz").open("ab") as stream:
                stream.write(b"changed")
            with self.assertRaisesRegex(ValueError, "model hash"):
                cnn.load_model(root / "model.json")


class CNNIntegrationTests(unittest.TestCase):
    def test_dataset_training_artifacts_and_existing_output_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            manifest, sample_rows = [], []
            for i, motion in enumerate([.1, 2, .2, 3, .3, 4]):
                identity = f"s{i // 2}/example-{i}"
                data = sensor_window(motion)
                manifest.append(dict(example_id=identity, session_id=f"s{i // 2}", rider="one", board="deck",
                                     eligible=True, outcome_training=True, sample_count=len(data),
                                     label=dict(trick="kickflip", outcome="make" if i % 2 == 0 else "bail",
                                                start_s=0, end_s=2.5, video_start_s=10, video_end_s=12.5,
                                                video_mapping=dict(scale=1, offset_s=-10),
                                                review_interval=dict(start_s=10.8, end_s=11))))
                sample_rows.extend([[identity, *row] for row in data])
            (dataset / "manifest.jsonl").write_text("".join(json.dumps(row) + "\n" for row in manifest))
            (dataset / "report.json").write_text('{"allow_sample_gaps": []}\n')
            with (dataset / "samples.csv").open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["example_id", *landing.CHANNELS])
                writer.writerows(sample_rows)
            rows, x, features, audit = cnn.load_dataset(dataset)
            self.assertEqual(x.shape, (6, 6, 230))
            self.assertEqual(features.shape, (6, 8))
            self.assertEqual(audit["resampling_exclusions"], [])
            before = {p.name: landing.sha256(p) for p in dataset.iterdir()}
            output = root / "run"
            with patch.dict(cnn.FOREST, n_estimators=7):
                report = cnn.train(dataset, output, seeds=(0, 1), epochs=2)
            self.assertEqual(report["metrics"]["n"], 6)
            self.assertEqual(report["primary_seed"], 0)
            self.assertEqual(len(report["seed_metrics"]), 2)
            with (output / "predictions.csv").open() as stream:
                predictions = list(csv.DictReader(stream))
            self.assertEqual([r["example_id"] for r in predictions], [r["example_id"] for r in rows])
            self.assertTrue(all("cnn_seed_0_score" in r and "cnn_seed_1_score" in r for r in predictions))
            self.assertEqual(report["provenance"]["code_sha256"], landing.sha256(Path(cnn.__file__)))
            self.assertEqual(report["weights_sha256"], landing.sha256(output / "weights.npz"))
            model, metadata = cnn.load_model(output / "model.json")
            self.assertEqual(cnn.predict(model, metadata, sensor_window(), 1, "kickflip")["status"], "scored")
            artifacts = {p.name: landing.sha256(p) for p in output.iterdir()}
            with self.assertRaisesRegex(ValueError, "already exists"):
                cnn.train(dataset, output, epochs=2)
            self.assertEqual(artifacts, {p.name: landing.sha256(p) for p in output.iterdir()})
            self.assertEqual(before, {p.name: landing.sha256(p) for p in dataset.iterdir()})


if __name__ == "__main__":
    unittest.main()
