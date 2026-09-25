from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from capture.onboard import battery_status


class BatteryTests(unittest.TestCase):
    def status(self, **changes):
        return dict(dict(battery_available=True, battery_percent=75.5,
                         battery_mv=4000, battery_age_ms=250), **changes)

    def test_fresh_reading_and_empty_battery_are_available(self):
        for percent in (0, 75.5, 100):
            self.assertEqual(battery_status(self.status(battery_percent=percent)),
                             dict(state="ready", percent=percent, voltage_mv=4000, age_ms=250))

    def test_disconnected_stale_missing_and_older_gauges_clear_values(self):
        cases = [(self.status(), False, "offline"), ({}, True, "unsupported"),
                 (self.status(battery_age_ms=15001), True, "stale"),
                 (self.status(battery_available=False), True, "unavailable")]
        for status, connected, state in cases:
            with self.subTest(state=state):
                self.assertEqual(battery_status(status, connected),
                                 dict(state=state, percent=None, voltage_mv=None))

    def test_invalid_readings_never_become_zero_or_full_batteries(self):
        for key, invalid in (("battery_percent", [None, True, "85", -1, 101, float("nan")]),
                             ("battery_mv", [None, False, "4000", 0, 5000, float("inf")]),
                             ("battery_age_ms", [None, True, -1, float("nan")])):
            for value in invalid:
                with self.subTest(key=key, value=value):
                    result = battery_status(self.status(**{key: value}))
                    self.assertEqual(result, dict(state="unavailable", percent=None, voltage_mv=None))

    @unittest.skipUnless(shutil.which("c++"), "C++ compiler needed for battery register tests")
    def test_gauge_registers_and_partial_read_failures(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / "battery-test")
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra",
                            "-I", str(root / "firmware/imu_logger"),
                            str(root / "tests/native/battery_monitor_test.cpp"), "-o", binary],
                           check=True, capture_output=True, timeout=30)
            subprocess.run([binary], check=True, capture_output=True, timeout=30)
