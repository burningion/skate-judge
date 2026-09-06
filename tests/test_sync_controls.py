"""A sync needs continuous acquisition and an actual board acknowledgement."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from capture.session import Session, SyncTrigger, read_jsonl


class CountdownTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.path = (
            Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / "session"
        )
        self.now = 10.0
        self.stack.enter_context(
            patch("capture.session.time.monotonic", lambda: self.now)
        )
        self.stack.enter_context(
            patch("capture.session.time.monotonic_ns", lambda: int(self.now * 1e9))
        )
        self.session = Session(self.path, {"synthetic": False})
        self.stack.callback(self.session.close)
        self.source = Mock()
        self.trigger = SyncTrigger()
        self.sequence = 0
        self.sample()

    def sample(self):
        self.session.ingest(
            f"D,{int(self.now * 1e6)},0,0,9.8,0,0,0,22,{self.sequence},abcdef01"
        )
        self.sequence += 1

    def advance(self, seconds, fresh=True):
        self.now += seconds
        if fresh:
            self.sample()
        self.trigger.tick(self.session, self.source)

    def test_samples_continue_during_countdown_and_only_one_flash_is_sent(self):
        self.trigger.start(self.session)
        with self.assertRaisesRegex(ValueError, "already"):
            self.trigger.start(self.session)
        for i in range(1, 301):
            self.now = 10 + i / 100
            self.sample()
            self.trigger.tick(self.session, self.source)
            if i < 300:
                self.source.send.assert_not_called()
        self.assertEqual(self.session.samples, 301)
        self.source.send.assert_called_once_with(b"s")
        self.assertEqual(self.trigger.phase, "waiting")
        self.advance(0.01)
        self.session.ingest("S,13005000,1,1,1,abcdef01")
        self.trigger.tick(self.session, self.source)
        self.assertEqual(self.trigger.phase, "done")
        self.assertEqual(self.session.last_sync["device_us"], 13005000)
        self.source.send.assert_called_once()

    def test_stale_data_cancels_countdown_without_flashing(self):
        self.trigger.start(self.session)
        self.advance(0.6, fresh=False)
        self.assertEqual(self.trigger.phase, "error")
        self.advance(3)
        self.source.send.assert_not_called()
        events = read_jsonl(self.path / "events.jsonl")
        self.assertTrue(any(event["kind"] == "countdown_cancelled" for event in events))

    def test_cannot_start_without_fresh_samples(self):
        self.now += 1
        with self.assertRaisesRegex(ValueError, "fresh"):
            self.trigger.start(self.session)
        self.source.send.assert_not_called()

    def test_missing_acknowledgement_is_not_reported_as_a_flash(self):
        self.trigger.request(self.session, self.source)
        self.advance(2.1)
        self.assertEqual(self.trigger.phase, "error")
        self.assertIsNone(self.session.last_sync)
        self.assertTrue(
            any(
                row["kind"] == "sync_unacknowledged"
                for row in read_jsonl(self.path / "events.jsonl")
            )
        )

    def test_delayed_old_marker_does_not_acknowledge_new_request(self):
        self.trigger.request(self.session, self.source)
        self.advance(0.01)
        self.session.ingest("S,9900000,1,1,1,abcdef01")
        self.trigger.tick(self.session, self.source)
        self.assertEqual(self.trigger.phase, "waiting")

    def test_disabled_led_marker_is_not_reported_as_a_physical_flash(self):
        self.trigger.request(self.session, self.source)
        self.advance(0.01)
        self.session.ingest("S,10005000,1,1,0,abcdef01")
        self.trigger.tick(self.session, self.source)
        self.assertEqual(self.trigger.phase, "error")
        self.assertIn("disabled", self.trigger.message)


if __name__ == "__main__":
    unittest.main()
