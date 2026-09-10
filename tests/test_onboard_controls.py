"""Onboard countdowns must use current board state at each handoff."""

import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from capture.onboard import OnboardRecording, record


IDENTITY = "a" * 32


def board_status(**changes):
    return dict(dict(protocol=1, phase="recording", id=IDENTITY, boot_id="1234abcd",
                     sensor_ready=True, storage_ready=True, led_enabled=True,
                     accel_hz=196.5, gyro_hz=196.5, accel_samples=1000,
                     gyro_samples=1000, zero_accel=0, io_errors=0, fifo_overruns=0), **changes)


class OnboardSyncTests(unittest.TestCase):
    def test_checks_live_health_and_identity_without_flashing(self):
        client = Mock()
        recording = OnboardRecording(client, ".")
        recording.identity, recording.boot = IDENTITY, "1234abcd"
        recording.remote = board_status(phase="starting", accel_hz=0, gyro_hz=0)
        client.request.return_value = board_status()
        self.assertEqual(recording.check_sync_ready(), board_status())
        client.request.assert_called_once_with("/status")

        for changes in (dict(phase="saved"), dict(accel_hz=0), dict(gyro_hz=0),
                        dict(io_errors=1), dict(id="b" * 32), dict(boot_id="changed")):
            with self.subTest(changes=changes):
                client.request.return_value = board_status(**changes)
                with self.assertRaises(ValueError):
                    recording.check_sync_ready()
        self.assertTrue(all(args == call("/status") for args in client.request.call_args_list))

    def test_saved_or_downloading_batch_cannot_sync(self):
        for attribute, value in (("result", dict(saved=True)), ("downloading", True)):
            with self.subTest(attribute=attribute):
                client = Mock()
                recording = OnboardRecording(client, ".")
                recording.identity = IDENTITY
                setattr(recording, attribute, value)
                with self.assertRaisesRegex(ValueError, "saving or already saved"):
                    recording.sync(1)
                client.request.assert_not_called()

    def test_command_rechecks_board_after_stale_main_loop_read(self):
        for current, expected_phase in ((board_status(), "countdown"),
                                        (board_status(phase="fault"), "error"),
                                        (OSError("Board disconnected"), "error")):
            with self.subTest(current=current), tempfile.TemporaryDirectory() as directory:
                client = Mock()
                client.request.side_effect = [board_status(phase="starting", accel_hz=0), current]
                control = Mock()
                control.videos.active = None

                def controls(commands, path, port, onboard):
                    onboard.identity, onboard.boot = IDENTITY, "1234abcd"
                    commands.put("countdown")
                    return control

                args = SimpleNamespace(output=Path(directory) / "session", port=0,
                                       rider="test", board_name="test", board="http://board")
                with contextlib.ExitStack() as stack:
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    stack.enter_context(patch("capture.onboard.board_client", return_value=client))
                    stack.enter_context(patch("capture.onboard.ControlServer", side_effect=controls))
                    stack.enter_context(patch("capture.onboard.threading.Thread"))
                    stack.enter_context(patch("capture.onboard.time.sleep", side_effect=KeyboardInterrupt))
                    record(args)

                status = control.publish_status.call_args.args[0]
                self.assertEqual(status["phase"], expected_phase)
                self.assertEqual(status["remaining"], 3 if expected_phase == "countdown" else None)
                self.assertTrue(control.publish_status.call_args.kwargs["countdown_handled"])
                self.assertEqual(client.request.call_args_list, [call("/status"), call("/status")])


if __name__ == "__main__":
    unittest.main()
