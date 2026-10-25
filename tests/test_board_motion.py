import json
import unittest

from capture.motion_events import board_motion_context, overlapping_events


def motion_event(**changes):
    return dict(dict(kind="board_motion", boot_id="boot", source="lsm6dso32_basic_interrupts",
                     earliest_device_us=1490000, device_us=1540000, latest_device_us=1550000,
                     free_fall=False, wake_up=False, orientation_changed=True, wake_up_src=16, d6d_src=96), **changes)


def motion_metadata(**changes):
    return dict(dict(boot_id="boot", device_origin_us=1000000, closed_utc="2026-10-23",
                     onboard_quality=dict(complete=True), onboard_sensor=dict(
                         motion_events_version=1, motion_profile=2, motion_poll_us=250000)), **changes)


class BoardMotionTests(unittest.TestCase):
    def parse(self, rows, **changes):
        return board_motion_context(motion_metadata(**changes), "\n".join(json.dumps(r) for r in rows).encode())

    def test_device_bounds_convert_without_clamping_or_assigning_outcomes(self):
        context = self.parse([dict(kind="sync"), motion_event()])
        event = context["events"][0]
        self.assertEqual((event["earliest_s"], event["observed_s"], event["latest_s"]), (.49, .54, .55))
        self.assertEqual(event["source_line"], 2)
        self.assertEqual(context["profile"], 2)
        self.assertEqual(context["poll_interval_us"], 250000)
        self.assertNotIn("outcome", event)
        self.assertEqual(event["wake_up_src"], 16)

    def test_missing_disabled_empty_and_incomplete_are_distinct(self):
        self.assertEqual(board_motion_context({}, None)["availability"], "not_recorded")
        self.assertEqual(board_motion_context(motion_metadata(), None)["availability"], "missing")
        empty = self.parse([])
        self.assertEqual(empty["availability"], "recorded")
        self.assertEqual(empty["observed_packets"], 0)
        self.assertEqual(self.parse([motion_event()], onboard_quality=dict(complete=False))["availability"], "incomplete")
        self.assertEqual(self.parse([motion_event()], device_origin_us=None)["availability"], "unverified")

    def test_foreign_boot_is_ignored_and_impossible_or_corrupt_flags_fail(self):
        context = self.parse([motion_event(boot_id="other"), motion_event()])
        self.assertEqual(context["observed_packets"], 1)
        self.assertEqual(context["ignored_other_boot"], 1)
        self.assertTrue(context["warnings"])
        for updates in (dict(device_us=1400000), dict(latest_device_us=float("nan")),
                        dict(device_us=True), dict(wake_up_src=256), dict(wake_up=True),
                        dict(free_fall=0), dict(source="unrecognized")):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.parse([motion_event(**updates)])
        with self.assertRaisesRegex(ValueError, "reordered"):
            self.parse([motion_event(), motion_event()])

    def test_overlap_keeps_uncertainty_and_availability_at_window_boundary(self):
        events = self.parse([motion_event()])["events"]
        left = overlapping_events(events, .4, .5)[0]
        right = overlapping_events(events, .5, .6)[0]
        self.assertEqual(left["event_id"], right["event_id"])
        self.assertEqual(right["earliest_s"], .49)
        self.assertAlmostEqual(right["relative_earliest_s"], -.01)
        self.assertFalse(left["available_by_window_end"])
        self.assertTrue(right["available_by_window_end"])
        self.assertFalse(right["fully_contained"])
        self.assertEqual(overlapping_events(events, .4, .49), [])
        self.assertEqual(len(overlapping_events(events, .55, .6)), 1)
        point = dict(events[0], earliest_s=.5, observed_s=.5, latest_s=.5)
        self.assertEqual(overlapping_events([point], .4, .5), [])
        self.assertEqual(len(overlapping_events([point], .5, .6)), 1)
