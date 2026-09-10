"""Local HTTP integration tests; these require permission to bind a loopback port."""

import contextlib
import http.client
import io
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

from capture.session import ControlServer


class ControlHTTPTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.commands = queue.Queue()
        self.control = ControlServer(self.commands, self.path)
        self.stack.callback(self.control.close)
        self.clip_id = "c" * 32

    def call(self, path, data=None, method="POST", headers=None):
        body = data if isinstance(data, bytes) else json.dumps(data or {}).encode()
        request_headers = {
            "Origin": self.control.url,
            "Content-Type": "application/octet-stream"
            if isinstance(data, bytes)
            else "application/json",
        }
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.control.server.server_port, timeout=3
        )
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.read(), response.getheader("Content-Type")
        finally:
            connection.close()

    def start(self):
        code, body, _ = self.call(
            "/api/video/start", dict(id=self.clip_id, mime_type="video/webm")
        )
        self.assertEqual(code, 200, body)
        return json.loads(body)

    def test_serves_page_and_modules_but_not_arbitrary_local_files(self):
        for path, mime in (
            ("/", "text/html"),
            ("/controls.mjs", "text/javascript"),
            ("/webcam.mjs", "text/javascript"),
        ):
            code, body, content_type = self.call(path, method="GET")
            self.assertEqual(code, 200)
            self.assertIn(mime, content_type)
            self.assertTrue(body)
        self.assertEqual(self.call("/../session.py", method="GET")[0], 404)

    def test_cross_origin_and_wrong_host_cannot_start_video(self):
        self.assertEqual(
            self.call("/api/video/start", headers={"Origin": "https://example.com"})[0],
            403,
        )
        self.assertEqual(
            self.call("/api/video/start", headers={"Host": "example.com"})[0], 403
        )
        self.assertIsNone(self.control.videos.active)

    def test_video_upload_and_countdown_coexist(self):
        clip = self.start()
        self.control.status = dict(can_sync=True, samples=100, fresh=True)
        path = f"/api/video/chunk/{self.clip_id}/0"
        self.assertEqual(self.call(path, b"video")[0], 200)
        self.assertEqual(self.call("/api/prepare", dict(video_id=self.clip_id))[0], 200)
        self.assertEqual(self.call("/api/countdown", dict(video_id=self.clip_id))[0], 202)
        self.assertEqual(self.commands.get_nowait(), "countdown")
        self.assertEqual(self.call("/api/countdown", dict(video_id=self.clip_id))[0], 409)
        self.assertEqual(self.call(path, b"video")[0], 200)
        self.assertEqual(self.call("/api/status", method="GET")[0], 200)
        code, body, _ = self.call(
            "/api/video/finish", dict(id=self.clip_id, chunks=1, bytes=5, duration_s=1)
        )
        self.assertEqual(code, 200, body)
        self.assertEqual((self.path / clip["filename"]).read_bytes(), b"video")

    def test_countdown_needs_saved_video_data_or_explicit_external_camera(self):
        self.control.status = dict(can_sync=True, fresh=True)
        self.assertEqual(self.call('/api/countdown')[0], 409)
        self.start()
        self.assertEqual(self.call('/api/countdown', dict(video_id=self.clip_id))[0], 409)
        self.assertEqual(self.call('/api/prepare', dict(video_id=self.clip_id))[0], 409)
        self.assertTrue(self.commands.empty())
        self.assertEqual(self.call('/api/countdown', dict(external=True))[0], 202)
        self.assertEqual(self.commands.get_nowait(), 'countdown')

    def test_onboard_start_is_gated_by_video_and_finish_reports_controller_errors(self):
        self.control.close()
        onboard = Mock()
        onboard.prepare.return_value = dict(ready=True, id="a" * 32)
        onboard.finish.side_effect = OSError("Wi-Fi disconnected; onboard original retained")
        self.control = ControlServer(self.commands, self.path, onboard=onboard)
        self.stack.callback(self.control.close)
        self.start()
        self.assertEqual(self.call('/api/prepare', dict(video_id=self.clip_id))[0], 409)
        onboard.prepare.assert_not_called()
        self.call(f'/api/video/chunk/{self.clip_id}/0', b'video')
        self.assertEqual(self.call('/api/prepare', dict(video_id=self.clip_id))[0], 200)
        onboard.prepare.assert_called_once()
        code, body, _ = self.call('/api/board/finish')
        self.assertEqual(code, 503)
        self.assertIn(b'onboard original retained', body)
        onboard.finish.side_effect = None
        onboard.finish.return_value = dict(saved=True, samples=2080)
        self.assertEqual(json.loads(self.call('/api/board/finish')[1])['samples'], 2080)

    def onboard_control(self):
        self.control.close()
        onboard = Mock()
        onboard.prepare.return_value = dict(ready=True, id="a" * 32)
        self.control = ControlServer(self.commands, self.path, onboard=onboard)
        self.stack.callback(self.control.close)
        self.start()
        self.call(f'/api/video/chunk/{self.clip_id}/0', b'video')
        return onboard

    def test_onboard_countdown_uses_current_readiness_after_prepare(self):
        onboard = self.onboard_control()
        # The main loop last saw the sensor warming up. prepare() has now
        # confirmed its rate, but the next status publication has not happened.
        self.control.status = dict(can_sync=False, fresh=False, phase="ready")
        data = dict(video_id=self.clip_id)
        self.assertEqual(self.call('/api/prepare', data)[0], 200)
        code, body, _ = self.call('/api/countdown', data)
        self.assertEqual(code, 202, body)
        onboard.check_sync_ready.assert_called_once()
        self.assertEqual(self.commands.get_nowait(), 'countdown')

        # A publication from before the command was consumed must not allow
        # a second countdown, even after the queue has been drained.
        self.control.publish_status(dict(can_sync=True, phase="ready"))
        self.assertFalse(self.control.status['can_sync'])
        code, body, _ = self.call('/api/countdown', data)
        self.assertEqual(code, 409)
        self.assertIn('already', json.loads(body)['error'])
        self.assertTrue(self.commands.empty())

        self.control.publish_status(dict(can_sync=False, phase="countdown"), countdown_handled=True)
        self.assertEqual(self.call('/api/countdown', data)[0], 409)
        self.control.publish_status(dict(can_sync=True, phase="done"))
        self.assertEqual(self.call('/api/countdown', data)[0], 202)
        self.assertEqual(self.commands.get_nowait(), 'countdown')

    def test_onboard_health_failure_or_disconnect_cannot_queue_countdown(self):
        onboard = self.onboard_control()
        data = dict(video_id=self.clip_id)
        self.assertEqual(self.call('/api/prepare', data)[0], 200)
        for error, expected_code in ((ValueError('Onboard recording is not healthy'), 409),
                                     (OSError('Board disconnected'), 503)):
            with self.subTest(error=error):
                onboard.check_sync_ready.side_effect = error
                self.control.status = dict(can_sync=True, phase="ready")
                code, body, _ = self.call('/api/countdown', data)
                self.assertEqual(code, expected_code)
                self.assertIn(str(error), json.loads(body)['error'])
                self.assertTrue(self.commands.empty())
        onboard.check_sync_ready.side_effect = None
        self.assertEqual(self.call('/api/countdown', data)[0], 202)

    def test_streaming_countdown_explains_missing_motion_data(self):
        self.control.status = dict(can_sync=False, fresh=False, phase="ready")
        code, body, _ = self.call('/api/countdown', dict(external=True))
        self.assertEqual(code, 409)
        self.assertIn('fresh motion data', json.loads(body)['error'])
        self.assertTrue(self.commands.empty())

    def test_malformed_oversized_and_reordered_uploads_rejected(self):
        self.start()
        path = f"/api/video/chunk/{self.clip_id}/1"
        self.assertEqual(self.call(path, b"video")[0], 409)
        self.assertEqual(
            self.call(path, b"", headers={"Content-Length": "1048577"})[0], 400
        )
        self.assertEqual(
            self.call(
                "/api/video/start",
                b"not json",
                headers={"Content-Type": "application/json"},
            )[0],
            400,
        )
        self.assertEqual(
            self.call("/api/video/start", headers={"Transfer-Encoding": "chunked"})[0],
            400,
        )

    def test_disk_failure_is_an_error_not_a_successful_save(self):
        self.start()
        with patch.object(
            self.control.videos, "chunk", side_effect=OSError("disk full")
        ):
            code, body, _ = self.call(f"/api/video/chunk/{self.clip_id}/0", b"video")
        self.assertEqual(code, 507)
        self.assertIn("disk full", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
