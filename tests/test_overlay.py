"""Numerical and saved-data checks for transparent motion overlays."""

import json
import math
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

from viz.overlay_motion import (
    check_alignment,
    estimate_attitude,
    from_gravity,
    infer_mount,
    interpolate,
    latest_labels,
    load_samples,
    matrix,
    mount_matrix,
)


class OverlayMotionTests(unittest.TestCase):
    def test_resting_inverted_sensor_produces_level_skateboard(self):
        times = np.linspace(0, 2, 401)
        samples = np.column_stack(
            [times, np.tile([0, 0, -9.80665, 0.01, -0.02, 0.015], (len(times), 1))]
        )
        attitudes, calibration = estimate_attitude(samples)
        self.assertTrue(calibration["quiet_bias_calibrated"])
        np.testing.assert_allclose(calibration["gyro_bias_rad_s"], [0.01, -0.02, 0.015])
        np.testing.assert_allclose(
            matrix(attitudes[-1]) @ mount_matrix("x", "-z"), np.eye(3), atol=1e-8
        )

    def test_gyro_replays_a_full_flip_without_euler_wraparound(self):
        times = np.linspace(0, 1, 201)
        # Free fall: gyro is authoritative and gravity correction is disabled.
        samples = np.column_stack(
            [times, np.tile([0, 0, 0, 2 * math.pi, 0, 0], (len(times), 1))]
        )
        attitudes, _ = estimate_attitude(samples)
        np.testing.assert_allclose(
            matrix(attitudes[50]) @ [0, 0, 1], [0, -1, 0], atol=1e-8
        )
        np.testing.assert_allclose(
            matrix(attitudes[100]) @ [0, 0, 1], [0, 0, -1], atol=1e-8
        )
        np.testing.assert_allclose(matrix(attitudes[-1]), np.eye(3), atol=1e-8)

    def test_nonuniform_sample_times_integrate_elapsed_time(self):
        times = np.cumsum(np.tile([0.004, 0.006, 0.005], 100))
        times -= times[0]
        samples = np.column_stack([times, np.tile([0, 0, 0, 0, 0, 1], (len(times), 1))])
        attitudes, _ = estimate_attitude(samples)
        np.testing.assert_allclose(
            matrix(attitudes[-1]) @ [1, 0, 0],
            [math.cos(times[-1]), math.sin(times[-1]), 0],
            atol=1e-8,
        )

    def test_missing_sensor_time_does_not_become_interpolated_motion(self):
        samples = np.array(
            [[0, 0, 0, 0, 1, 0, 0], [0.01, 0, 0, 0, 1, 0, 0], [1, 0, 0, 0, 1, 0, 0]]
        )
        attitudes, meta = estimate_attitude(samples)
        self.assertEqual(meta["gaps_over_30ms"], 1)
        self.assertIsNone(interpolate(samples, attitudes, 0.5))
        self.assertIsNone(interpolate(samples, attitudes, -0.01))
        self.assertIsNotNone(interpolate(samples, attitudes, 1))
        np.testing.assert_allclose(attitudes[-1], attitudes[-2])

    def test_sensor_frame_interpolation_and_mount_are_right_handed(self):
        samples = np.array([[0, 1, 2, 3, 0, 0, 0], [0.01, 3, 4, 5, 0, 0, 0]])
        quats = np.array([[1.0, 0, 0, 0], [-1.0, 0, 0, 0]])
        values, attitude = interpolate(samples, quats, 0.005)
        np.testing.assert_allclose(values[:3], [2, 3, 4])
        np.testing.assert_allclose(attitude, np.eye(3))
        for nose, up in [("x", "-z"), ("y", "z"), ("-x", "-z")]:
            mount = mount_matrix(nose, up)
            self.assertAlmostEqual(np.linalg.det(mount), 1)
        with self.assertRaises(ValueError):
            mount_matrix("z", "-z")

    def test_gravity_initialization_handles_tilt(self):
        gravity = np.array([2.0, -3.0, 8.0])
        np.testing.assert_allclose(
            matrix(from_gravity(gravity)) @ gravity,
            [0, 0, np.linalg.norm(gravity)],
            atol=1e-8,
        )

    def test_latest_saved_revision_is_used_and_other_videos_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                dict(id="a", video="clip.mp4", start_s=2, outcome="make"),
                dict(id="b", video="other.mp4", start_s=1, outcome="make"),
                dict(id="a", video="clip.mp4", start_s=3, outcome="bail"),
            ]
            (Path(directory) / "labels.jsonl").write_text(
                "\n".join(map(json.dumps, rows))
            )
            self.assertEqual(latest_labels(directory, "clip.mp4"), [rows[-1]])

    def test_stale_alignment_and_outside_coverage_are_rejected(self):
        label = dict(
            id="a", video_start_s=20, video_end_s=21, start_s=4.502, end_s=5.5021
        )
        check_alignment([label], 1.0001, -15.5, [0, 10])
        with self.assertRaisesRegex(ValueError, "stale"):
            check_alignment([label], 1, -15.5, [0, 10])
        with self.assertRaisesRegex(ValueError, "outside"):
            check_alignment([label], 1.0001, -15.5, [0, 5])

    def test_mount_inference_uses_flip_axis_and_records_up(self):
        samples = np.array(
            [[t, 0, 0, -9.8, 10, 0.1, 0.1] for t in np.linspace(0, 2, 401)]
        )
        nose, up, method = infer_mount(
            samples, [dict(start_s=0, end_s=2, trick="kickflip")]
        )
        self.assertEqual((nose, up), ("x", "-z"))
        self.assertIn("sign assumed", method)

    def test_invalid_sample_order_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.csv"
            path.write_text("t_s,ax,ay,az,gx,gy,gz\n1,0,0,9.8,0,0,0\n0,0,0,9.8,0,0,0\n")
            with self.assertRaisesRegex(ValueError, "increasing"):
                load_samples(path)


class OverlayRenderTests(unittest.TestCase):
    def test_silent_stationary_data_renders_in_both_layouts_with_alpha(self):
        from viz.render_overlay import Renderer

        times = np.linspace(0, 1, 201)
        samples = np.column_stack(
            [times, np.tile([0, 0, -9.80665, 0, 0, 0], (len(times), 1))]
        )
        quats, _ = estimate_attitude(samples)
        audio = {"step_s": 0.01, "strength": [0] * 101, "onsets": []}
        segment = {
            "start": 0.0,
            "end": 1.0,
            "label": {"trick": "ollie", "outcome": "make"},
        }
        for width, height, clear_rows in [(360, 640, 280), (640, 360, 180)]:
            with self.subTest(size=(width, height)):
                args = SimpleNamespace(
                    width=width,
                    height=height,
                    speed=1,
                    antialias=1,
                    font=None,
                    onset_threshold=0.65,
                )
                renderer = Renderer(
                    samples, quats, audio, (1, 0, 0), mount_matrix("x", "-z"), args
                )
                frame = renderer.render(0.5, segment, np.eye(3))
                self.assertEqual(frame.size, (width, height))
                alpha = np.asarray(frame)[:, :, 3]
                self.assertEqual(int(alpha[:clear_rows].max()), 0)
                self.assertEqual(int(alpha.max()), 255)
                self.assertTrue(
                    np.any((alpha > 0) & (alpha < 255)), "translucent panels retained"
                )


if __name__ == "__main__":
    unittest.main()
