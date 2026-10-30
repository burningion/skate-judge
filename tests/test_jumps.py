import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from capture.jumps import detect_jumps, G


def jumping_samples(flights=((1., 1.6), (2.1, 3.2)), duration=4.):
    """Known flights with noisy weightlessness and broad push/contact loads."""
    samples = []
    for i in range(round(duration * 200) + 1):
        time = i / 200
        accel = 1 + .015 * math.sin(i * .73)
        for takeoff, landing in flights:
            if takeoff <= time < landing:
                accel = .04 + .015 * math.sin(i * .73)
            elif takeoff - .12 <= time < takeoff or landing <= time < landing + .12:
                accel = 2 + .03 * math.sin(i)
        samples.append([time, accel * G, .03])
    return samples


def body_motion_samples(hz=200):
    """Two jumps with in-flight motion and a ground-contact rebound after each."""
    rows = []
    for i in range(4 * hz + 1):
        time, accel = i / hz, 1.
        for takeoff in (1., 2.5):
            landing = takeoff + .55
            if takeoff - .18 <= time < takeoff:
                accel = 2.5
            elif takeoff <= time < landing:
                accel = .55 if (time - takeoff) % .1 < .07 else 1.8
            elif landing <= time < landing + .10:
                accel = 4.
            elif landing + .20 <= time < landing + .36:
                accel = .3
            elif landing + .36 <= time < landing + .40:
                accel = 2.2
        rows.append([time, accel * G, .03])
    return rows


class JumpDetectorTests(unittest.TestCase):
    def test_flights_and_midpoints_are_separate_even_for_repeated_bounces(self):
        samples = jumping_samples()
        original = [row[:] for row in samples]
        result = detect_jumps(samples)
        self.assertEqual(result["availability"], "ready")
        self.assertEqual(len(result["events"]), 2)
        for event, (takeoff, landing) in zip(result["events"], ((1., 1.6), (2.1, 3.2))):
            self.assertAlmostEqual(event["takeoff_s"], takeoff, delta=.006)
            self.assertAlmostEqual(event["landing_s"], landing, delta=.006)
            self.assertAlmostEqual(event["peak_s"], (takeoff + landing) / 2, delta=.006)
            self.assertAlmostEqual(event["flight_s"], landing - takeoff, delta=.006)
            self.assertEqual(event["peak_method"], "flight_midpoint")
            self.assertEqual(event["detection_method"], "sustained_low_g")
        self.assertEqual(samples, original)

    def test_quiet_noise_walking_like_loads_and_short_dips_are_not_jumps(self):
        quiet = jumping_samples(flights=())
        walking = [[i / 200, G * (1 + .4 * math.sin(i / 200 * 4 * math.pi)), .1] for i in range(800)]
        for samples in (quiet, walking, jumping_samples(flights=((1, 1.05),))):
            self.assertEqual(detect_jumps(samples)["events"], [])

    def test_needs_unloading_and_load_on_both_sides(self):
        for change in ("no_push", "no_contact", "not_low_enough"):
            samples = jumping_samples(flights=((1, 1.6),))
            for row in samples:
                if change == "no_push" and row[0] < 1 or change == "no_contact" and row[0] >= 1.6:
                    row[1] = G
                if change == "not_low_enough" and 1 <= row[0] < 1.6:
                    row[1] = .85 * G
            with self.subTest(change=change):
                self.assertEqual(detect_jumps(samples)["events"], [])

    def test_single_sample_spike_does_not_split_one_flight(self):
        samples = jumping_samples(flights=((1, 1.6),))
        samples[250][1] = 2 * G
        self.assertEqual(len(detect_jumps(samples)["events"]), 1)

    def test_interrupted_low_g_uses_load_pairs_and_accepts_nominal_50hz_timestamps(self):
        samples = jumping_samples(flights=((1, 1.6),))
        self.assertEqual(len(detect_jumps(samples[::4])["events"]), 1)
        for i, row in enumerate(samples):
            if 1 <= row[0] < 1.6:
                row[1] = (.3 if i % 8 < 4 else .55) * G
        events = detect_jumps(samples)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["detection_method"], "load_pair")

    def test_load_pairs_recover_body_motion_without_duplicate_rebounds_at_multiple_rates(self):
        for hz in (50, 100, 200, 400):
            with self.subTest(hz=hz):
                samples = body_motion_samples(hz)
                original = [row[:] for row in samples]
                events = detect_jumps(samples)["events"]
                self.assertEqual(len(events), 2)
                for event, takeoff in zip(events, (1., 2.5)):
                    self.assertEqual(event["detection_method"], "load_pair")
                    self.assertEqual(event["peak_method"], "load_pair_midpoint")
                    self.assertAlmostEqual(event["peak_s"], takeoff + .275, delta=.03)
                self.assertEqual(samples, original)

    def test_relaxed_unloading_still_needs_broad_push_and_substantial_landing(self):
        for change in ("narrow_push", "weak_landing", "no_unloading"):
            rows = body_motion_samples()
            for row in rows:
                t = row[0]
                for takeoff in (1., 2.5):
                    if change == "narrow_push" and takeoff - .18 <= t < takeoff - .04:
                        row[1] = G
                    if change == "weak_landing" and takeoff + .55 <= t < takeoff + .65:
                        row[1] = 1.5 * G
                    if change == "no_unloading" and takeoff <= t < takeoff + .55:
                        row[1] = .9 * G
            with self.subTest(change=change):
                self.assertEqual(detect_jumps(rows)["events"], [])

    def test_load_pairs_never_bridge_missing_data_or_incomplete_context(self):
        rows = body_motion_samples()
        for change in ("gap", "zero", "missing_push_context", "missing_landing_context"):
            samples = [r[:] for r in rows if r[0] < 2]
            if change == "gap":
                samples = [r for r in samples if not 1.2 <= r[0] <= 1.3]
            elif change == "zero":
                samples[250] = [1.25, 0, 0]
            elif change == "missing_push_context":
                samples = [r for r in samples if r[0] >= .8]
            else:
                samples = [r for r in samples if r[0] <= 1.7]
            with self.subTest(change=change):
                self.assertEqual(detect_jumps(samples)["events"], [])

    def test_missing_data_and_zero_packets_do_not_become_flights(self):
        samples = jumping_samples()
        gaps = [row for row in samples if not 1.2 <= row[0] <= 1.3]
        zeros = [row[:] for row in samples]
        zeros[250] = [1.25, 0, 0]
        for rows in (gaps, zeros):
            result = detect_jumps(rows)
            self.assertEqual(len(result["events"]), 1)
            self.assertGreater(result["events"][0]["takeoff_s"], 2)
            self.assertTrue(result["gaps"])
        self.assertEqual(detect_jumps([[i / 200, 0, 0] for i in range(800)])["availability"], "unavailable")

    def test_partial_flights_and_overlong_unloading_are_ignored(self):
        rows = jumping_samples(flights=((1, 1.6),))
        for samples in ([r for r in rows if r[0] >= 1.1], [r for r in rows if r[0] <= 1.5],
                        [r for r in rows if r[0] <= 1.7], jumping_samples(((1, 4.5),), duration=5)):
            self.assertEqual(detect_jumps(samples)["events"], [])

    def test_invalid_or_sparse_inputs_are_unavailable_not_zero_jumps(self):
        for samples in ([], [[0, G, 0]] * 3, [[0, G, 0], [.01, float("nan"), 0], [.02, G, 0]],
                        [[i, G, 0] for i in range(4)], [[0, -1, 0], [.01, G, 0], [.02, G, 0]]):
            self.assertEqual(detect_jumps(samples)["availability"], "unavailable")


HAS_SCIENCE = all(importlib.util.find_spec(name) for name in ("numpy", "scipy"))


@unittest.skipUnless(HAS_SCIENCE, "review needs numpy/scipy")
class JumpReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name)
        self.video = self.session / "jumps.mp4"
        self.meta = {"closed_utc": "2026-10-29"}
        self.rows = jumping_samples()
        self.write_session()

    def write_session(self):
        (self.session / "metadata.json").write_text(json.dumps(self.meta))
        (self.session / "samples.csv").write_text("t_s,ax,ay,az,gx,gy,gz\n" + "".join(
            f"{time},0,0,{accel},{gyro},0,0\n" for time, accel, gyro in self.rows))

    def test_context_detects_from_raw_samples_and_requires_a_finished_recording(self):
        from viz.trick_review import sensor_context
        self.assertEqual(len(sensor_context(self.video)["jumps"]["events"]), 2)
        for meta in ({}, {**self.meta, "onboard_quality": {"complete": False}},
                     {**self.meta, "onboard_stop_error": "storage_flush_failed"}):
            self.meta = meta
            self.write_session()
            result = sensor_context(self.video)["jumps"]
            self.assertEqual(result["availability"], "unavailable")
            self.assertEqual(result["events"], [])

    def test_nonfinite_raw_rows_cannot_be_silently_removed_to_invent_a_flight(self):
        from viz.trick_review import sensor_context
        self.rows[250][1] = float("nan")
        self.write_session()
        result = sensor_context(self.video)["jumps"]
        self.assertEqual(result["availability"], "unavailable")
        self.assertIn("nonfinite", result["message"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_silent_video_prepares_in_jump_mode_without_a_skateboard_model(self):
        from viz.trick_review import prepare
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=60",
                        "-t", "4", "-c:v", "libx264", str(self.video)], check=True, capture_output=True)
        original = self.video.read_bytes()
        with patch("viz.trick_review.attempt_context", side_effect=AssertionError("No skateboard model in jump mode")):
            _, _, data = prepare(self.video, jump_peaks=True)
        self.assertEqual(data["review_mode"], "jumps")
        self.assertFalse(data["audio"]["available"])
        self.assertEqual(len(data["sensor"]["jumps"]["events"]), 2)
        self.assertEqual(len(data["video_timing"]["frame_times_s"]), 240)
        self.assertEqual(self.video.read_bytes(), original)
        self.assertFalse((self.session / "labels.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
