import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from capture.onboard_log import MOTION, decode_log, encode_packet
from test_onboard import fixture


class MotionTests(unittest.TestCase):
    def make_log(self, directory, payload, configured=True):
        packets = fixture()
        meta = json.loads(packets[0][24:])
        if configured:
            meta["motion_events_version"] = 1
        packets[0] = encode_packet(0, 0, 1000000, json.dumps(meta).encode())
        original = [(p[4], struct.unpack_from("<Q", p, 12)[0], p[24:]) for p in packets]
        original.insert(-2, (MOTION, 1600000, payload))
        path = Path(directory) / "motion.bin"
        path.write_bytes(b"".join(encode_packet(k, i, stamp, data) for i, (k, stamp, data) in enumerate(original)))
        return path

    def test_events_preserve_polling_bounds_and_do_not_become_outcome_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = struct.pack("<BQQBB", 1, 1540000, 1600300, 0x28, 0x41)
            decoded = decode_log(self.make_log(tmp, payload))
            event = next(e for e in decoded["events"] if e["kind"] == "board_motion")
            self.assertTrue(event["free_fall"] and event["wake_up"] and event["orientation_changed"])
            self.assertEqual(event["earliest_device_us"], 1540000)
            self.assertEqual(event["latest_device_us"], 1600300)
            self.assertNotIn("outcome", event)
            self.assertEqual(len(decoded["samples"]), 416)
            self.assertTrue(decoded["quality"]["usable"])

    def test_unconfigured_truncated_and_impossible_events_rejected(self):
        good = struct.pack("<BQQBB", 1, 1540000, 1600300, 0x28, 0x41)
        bad = [good[:-1], struct.pack("<BQQBB", 2, 1540000, 1600300, 0x28, 0x41),
               struct.pack("<BQQBB", 1, 1700000, 1600300, 0x28, 0x41),
               struct.pack("<BQQBB", 1, 1540000, 1600300, 0, 0)]
        with tempfile.TemporaryDirectory() as tmp:
            for payload in bad:
                with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "motion"):
                    decode_log(self.make_log(tmp, payload))
            with self.assertRaisesRegex(ValueError, "unconfigured"):
                decode_log(self.make_log(tmp, good, False))

    @unittest.skipUnless(shutil.which("c++"), "native compiler needed")
    def test_configuration_does_not_change_acquisition_and_failure_stops_configuration(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            binary = str(Path(tmp) / "motion-test")
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-I", str(root / "firmware/imu_logger"),
                            str(root / "tests/native/motion_events_test.cpp"), "-o", binary], check=True, capture_output=True)
            subprocess.run([binary], check=True, capture_output=True)
