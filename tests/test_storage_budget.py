import math
import unittest

from capture.onboard import storage_budget


class StorageBudgetTests(unittest.TestCase):
    def status(self, **changes):
        return dict(dict(phase="recording", storage_ready=True, storage="flash",
                         free_bytes=200000, storage_buffer_bytes=32768, bytes=45000,
                         started_us=1000000, device_us=11000000, ended_us=0,
                         accel_hz=196.5, gyro_hz=196.5, accel_samples=1965,
                         gyro_samples=1965, zero_accel=0, error=""), **changes)

    def test_current_rate_reserves_footer_and_uncommitted_buffer(self):
        budget = storage_budget(self.status())
        self.assertEqual(budget["bytes_per_second"], 4500)
        self.assertEqual(budget["rate_source"], "measured")
        self.assertAlmostEqual(budget["remaining_s"], (200000 - 32768 - 32768) / 4500)
        self.assertEqual(budget["state"], "recording")

    def test_recovery_hides_capacity_estimate_then_reports_success(self):
        status = self.status(storage="sd", storage_recovering=True, storage_recoveries=1)
        budget = storage_budget(status)
        self.assertEqual(budget["state"], "recovering")
        self.assertIsNone(budget["remaining_s"])
        self.assertEqual(storage_budget(status, connected=False)["state"], "offline")
        self.assertEqual(storage_budget(dict(status, phase="fault"))["state"], "fault")
        budget = storage_budget(dict(status, storage_recovering=False))
        self.assertEqual((budget["state"], budget["recoveries"]), ("recording", 1))

    def test_old_flash_firmware_needs_no_upgrade(self):
        status = self.status()
        del status["storage_buffer_bytes"]
        del status["storage"]
        budget = storage_budget(status)
        self.assertEqual(budget["storage"], "flash")
        self.assertAlmostEqual(budget["remaining_s"], (200000 - 32768) / 4500)

    def test_sd_fallback_warning_requires_current_confirmed_internal_storage(self):
        fallback = self.status(sd_ready=False)
        for phase in ("idle", "starting", "recording", "stopping", "saved", "fault"):
            with self.subTest(phase=phase):
                self.assertTrue(storage_budget(dict(fallback, phase=phase))["sd_fallback"])
        for status in (self.status(), dict(fallback, storage="sd", sd_ready=True),
                       dict(fallback, storage_ready=False)):
            self.assertFalse(storage_budget(status)["sd_fallback"])
        self.assertFalse(storage_budget(fallback, connected=False)["sd_fallback"])

    def test_new_recording_warms_up_without_dividing_by_zero_or_extrapolating_headers(self):
        for start, now, size in ((0, 0, 0), (1000000, 1000000, 500), (1000000, 4000000, 13500)):
            with self.subTest(now=now):
                budget = storage_budget(self.status(started_us=start, device_us=now, bytes=size))
                self.assertEqual(budget["rate_source"], "typical")
                self.assertTrue(math.isfinite(budget["remaining_s"]))

    def test_empty_feather_estimate_uses_the_data_partition_not_eight_megabytes(self):
        # With the actual 4.5 kB/s rate, an empty 1.5 MiB partition has at most
        # 342 seconds after firmware reserve, before filesystem overhead.
        budget = storage_budget(self.status(phase="saved", free_bytes=0x180000,
                                            ended_us=11000000, device_us=999000000))
        self.assertAlmostEqual(budget["remaining_s"], 342.24355555555553)
        self.assertEqual(budget["state"], "ready")

    def test_deleted_files_increase_next_recording_capacity_without_old_file_limit(self):
        before = storage_budget(self.status(phase="saved", ended_us=11000000))
        after = storage_budget(self.status(phase="saved", ended_us=11000000, free_bytes=600000))
        self.assertGreater(after["remaining_s"], before["remaining_s"])

    def test_large_sd_is_limited_by_one_file_and_free_bytes_do_not_overflow(self):
        budget = storage_budget(self.status(storage="sd", free_bytes=30 * 1024 ** 3))
        self.assertEqual(budget["free_bytes"], 30 * 1024 ** 3)
        self.assertEqual(budget["limit"], "file")
        self.assertAlmostEqual(budget["remaining_s"], (0xffffffff - 32768 - 45000) / 4500)

    def test_full_or_nearly_full_space_never_produces_negative_time(self):
        for free in (0, 100, 32768, 60000):
            with self.subTest(free=free):
                self.assertEqual(storage_budget(self.status(free_bytes=free))["remaining_s"], 0)
                budget = storage_budget(self.status(phase="idle", free_bytes=free))
                self.assertEqual((budget["state"], budget["remaining_s"]), ("full", 0))

    def test_unavailable_or_stale_storage_does_not_invent_remaining_time(self):
        for changes in (dict(storage_ready=False), dict(free_bytes=None), dict(free_bytes=-1),
                        dict(free_bytes=float("nan")), dict(free_bytes=float("inf"))):
            with self.subTest(changes=changes):
                self.assertIsNone(storage_budget(self.status(**changes))["remaining_s"])
        stale = storage_budget(self.status(), connected=False)
        self.assertEqual(stale["state"], "offline")
        self.assertIsNone(stale["remaining_s"])
        self.assertIsNone(stale["free_bytes"])

    def test_stopped_fault_and_transitions_cannot_look_like_live_countdowns(self):
        for phase in ("starting", "stopping", "testing_led", "fault"):
            with self.subTest(phase=phase):
                budget = storage_budget(self.status(phase=phase))
                self.assertEqual(budget["state"], phase)
                self.assertIsNone(budget["remaining_s"])

    def test_slow_unhealthy_sampling_does_not_promise_extra_recording_time(self):
        budget = storage_budget(self.status(accel_hz=8, gyro_hz=8, bytes=1000))
        self.assertEqual(budget["rate_source"], "typical")
