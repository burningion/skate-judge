import io
import json
import unittest
from unittest.mock import Mock, patch

from capture.onboard import main, wipe_recordings


class WipeTests(unittest.TestCase):
    def setUp(self):
        # Identical IDs on different media must remain distinct deletion targets.
        self.files = {
            (storage, "a" * 32): dict(id="a" * 32, storage=storage, bytes=size, crc32=crc)
            for storage, size, crc in (("sd", 100, "12345678"), ("flash", 0, "00000000"))
        }
        self.phase = "idle"
        self.info_changes = {}
        self.fail_storage = None
        self.acknowledge = True
        self.deleted = []
        self.client = Mock()
        self.client.request.side_effect = self.respond
        self.output = io.StringIO()
        self.stdout_patch = patch("sys.stdout", self.output)
        self.stdout_patch.start()
        self.addCleanup(self.stdout_patch.stop)

    def respond(self, route, params=None, post=False, timeout=2):
        if route == "/status":
            return dict(phase=self.phase)
        if route == "/files":
            return [{k: item[k] for k in ("id", "storage", "bytes")}
                    for item in self.files.values()
                    if not params or item["storage"] == params["storage"]]
        key = (params["storage"], params["id"])
        item = self.files[key]
        if route == "/file-info":
            return dict(item, **self.info_changes)
        self.assertEqual(route, "/delete")
        self.assertTrue(post)
        self.assertEqual(params["crc32"], item["crc32"])
        if params["storage"] == self.fail_storage:
            raise OSError("Connection lost")
        if self.acknowledge:
            self.deleted.append(key)
            del self.files[key]
        return dict(deleted=self.acknowledge)

    def test_wipes_both_media_including_empty_log_without_downloading(self):
        result = wipe_recordings(self.client)
        self.assertEqual(result, dict(deleted=2, bytes=100, storage="all mounted"))
        self.assertEqual(self.files, {})
        self.assertEqual(self.deleted, [("sd", "a" * 32), ("flash", "a" * 32)])
        self.client.download.assert_not_called()

    def test_storage_filter_keeps_other_medium(self):
        result = wipe_recordings(self.client, "sd")
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(list(self.files), [("flash", "a" * 32)])
        self.client.request.assert_any_call("/files", {"storage": "sd"}, timeout=120)

    def test_empty_board_succeeds(self):
        self.files.clear()
        self.assertEqual(wipe_recordings(self.client)["deleted"], 0)
        self.assertEqual(self.deleted, [])

    def test_busy_board_is_never_deleted_or_stopped(self):
        for phase in ("recording", "starting", "stopping", "testing_led", "unknown"):
            with self.subTest(phase=phase):
                self.phase = phase
                self.client.reset_mock()
                with self.assertRaisesRegex(ValueError, "Stop recording"):
                    wipe_recordings(self.client)
                self.client.request.assert_called_once_with("/status")

    def test_entire_file_list_is_validated_before_any_deletion(self):
        good = dict(id="a" * 32, storage="sd", bytes=100)
        bad_entries = [dict(id="bad", storage="sd", bytes=1),
                       dict(id="b" * 32, bytes=1),
                       dict(id="b" * 32, storage="sd", bytes=-1), good]
        for bad in bad_entries:
            with self.subTest(entry=bad):
                client = Mock()
                client.request.side_effect = [dict(phase="idle"), [good, bad]]
                with self.assertRaises(ValueError):
                    wipe_recordings(client)
                self.assertEqual(client.request.call_count, 2)

    def test_changed_metadata_cannot_delete_recording(self):
        for changes in (dict(id="b" * 32), dict(storage="flash"),
                        dict(bytes=101), dict(crc32="invalid")):
            with self.subTest(changes=changes):
                self.info_changes = changes
                with self.assertRaisesRegex(ValueError, "0 confirmed deletions"):
                    wipe_recordings(self.client)
                self.assertEqual(self.deleted, [])

    def test_interruption_reports_progress_and_retry_clears_remaining(self):
        self.fail_storage = "flash"
        with self.assertRaisesRegex(ValueError, "1 confirmed deletions.*Connection lost"):
            wipe_recordings(self.client)
        self.assertEqual(list(self.files), [("flash", "a" * 32)])
        self.fail_storage = None
        self.assertEqual(wipe_recordings(self.client)["deleted"], 1)
        self.assertEqual(self.files, {})

    def test_missing_delete_acknowledgement_does_not_claim_success(self):
        self.acknowledge = False
        with self.assertRaisesRegex(ValueError, "0 confirmed deletions.*acknowledge"):
            wipe_recordings(self.client)
        self.assertEqual(len(self.files), 2)

    def test_new_recording_is_not_silently_included_in_wipe(self):
        respond = self.respond

        def with_new_file(route, *args, **kwargs):
            result = respond(route, *args, **kwargs)
            if route == "/delete" and len(self.deleted) == 2:
                self.files[("sd", "b" * 32)] = dict(id="b" * 32, storage="sd", bytes=1, crc32="12345678")
            return result

        self.client.request.side_effect = with_new_file
        with self.assertRaisesRegex(ValueError, "2 confirmed deletions.*Recordings remain"):
            wipe_recordings(self.client)
        self.assertEqual(list(self.files), [("sd", "b" * 32)])

    @patch("capture.onboard.board_client")
    def test_yes_required_before_opening_board(self, factory):
        with patch("sys.argv", ["onboard.py", "wipe"]), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)
        factory.assert_not_called()

    @patch("capture.onboard.board_client")
    def test_single_command_dispatches_and_closes_connection(self, factory):
        factory.return_value = self.client
        with patch("sys.argv", ["onboard.py", "--board", "serial:auto", "wipe", "--yes", "--storage", "sd"]):
            main()
        factory.assert_called_once_with("serial:auto")
        self.client.close.assert_called_once()
        self.assertEqual(json.loads(self.output.getvalue().splitlines()[-1]),
                         dict(deleted=1, bytes=100, storage="sd"))
        self.assertEqual(list(self.files), [("flash", "a" * 32)])


if __name__ == "__main__":
    unittest.main()
