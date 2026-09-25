from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class LogQueueTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "C++ compiler needed for storage diagnostics test")
    def test_storage_failures_preserve_operation_errno_and_last_successful_sync(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / "diagnostics-test")
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra",
                            "-I", str(root / "firmware/imu_logger"),
                            str(root / "tests/native/log_diagnostics_test.cpp"), "-o", binary],
                           check=True, capture_output=True, timeout=30)
            subprocess.run([binary], check=True, capture_output=True, timeout=30)

    @unittest.skipUnless(shutil.which("c++"), "C++ compiler needed for firmware queue test")
    def test_full_wraparound_and_concurrent_packet_integrity(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / "queue-test")
            subprocess.run(["c++", "-std=c++17", "-O2", "-pthread", "-Wall", "-Wextra",
                            "-I", str(root / "firmware/imu_logger"),
                            str(root / "tests/native/log_queue_test.cpp"), "-o", binary],
                           check=True, capture_output=True, timeout=30)
            subprocess.run([binary], check=True, capture_output=True, timeout=30)
