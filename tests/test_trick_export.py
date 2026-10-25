import importlib.util
import unittest

import numpy as np

from learning import trick_cnn as cnn


@unittest.skipUnless(importlib.util.find_spec("tensorflow"), "Run in the export environment for TensorFlow tests")
class ExportTests(unittest.TestCase):
    def test_both_convolution_types_match_float_graph_and_produce_integer_only_graphs(self):
        from learning.export_trick_cnn import convert, run_graph
        raw = np.random.default_rng(2).normal(size=(6, 6, 230)).astype(np.float32)
        for variant in ("raw8_multi", "raw8_multi_separable"):
            with self.subTest(variant=variant):
                model, metadata, _ = cnn.fit(raw[:4], [1, 0, 1, 0], ["kickflip", "ollie"] * 2,
                                            cnn.VARIANTS[variant], epochs=1)
                graph, reference = convert(model, metadata, raw[:4], raw[4:])
                scores, tricks, info, quantized = run_graph(graph, raw[4:], metadata)
                self.assertEqual(quantized.dtype, np.int8)
                self.assertEqual(info["input"]["shape"], [1, 1, 230, 8])
                self.assertTrue(set(info["operators"]) <= {"CONV_2D", "DEPTHWISE_CONV_2D", "FULLY_CONNECTED",
                                                          "LOGISTIC", "MEAN", "PAD", "SOFTMAX"})
                np.testing.assert_allclose(scores, reference[0], atol=.03)
                np.testing.assert_allclose(tricks, reference[1], atol=.03)
                self.assertLess(len(graph), 30000)


if __name__ == "__main__":
    unittest.main()
