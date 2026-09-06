import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capture.video import CHUNK_LIMIT, VideoConflict, VideoStore


class VideoStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = VideoStore(self.path)
        self.data = dict(
            id="a" * 32, mime_type="video/webm;codecs=vp8", capture={"audio": False}
        )
        self.clip = self.store.start(self.data)

    def finish(self, **kwargs):
        return self.store.finish(
            dict(id=self.data["id"], chunks=2, bytes=6, duration_s=1.5, **kwargs)
        )

    def test_save_exact_ordered_bytes_with_metadata_and_idempotent_retries(self):
        self.assertEqual(self.store.start(self.data)["id"], self.clip["id"])
        first = self.store.chunk(self.clip["id"], 0, b"abc")
        self.assertEqual(self.store.chunk(self.clip["id"], 0, b"abc"), first)
        self.store.chunk(self.clip["id"], 1, b"def")
        result = self.finish()
        self.assertEqual(self.finish(), result)
        self.assertIsNone(self.store.active)
        self.assertEqual((self.path / result["filename"]).read_bytes(), b"abcdef")
        self.assertFalse((self.path / (result["filename"] + ".part")).exists())
        manifest = json.loads((self.path / f"webcam-{result['id']}.json").read_text())
        self.assertEqual(manifest["status"], "saved")
        self.assertEqual(manifest["browser_duration_s"], 1.5)
        self.assertFalse(manifest["capture"]["audio"])

    def test_missing_reordered_and_conflicting_retries_rejected(self):
        with self.assertRaises(VideoConflict):
            self.store.chunk(self.clip["id"], 1, b"later")
        self.store.chunk(self.clip["id"], 0, b"abc")
        with self.assertRaises(VideoConflict):
            self.store.chunk(self.clip["id"], 0, b"bad")
        with self.assertRaises(VideoConflict):
            self.finish()
        self.assertEqual(
            (self.path / (self.clip["filename"] + ".part")).read_bytes(), b"abc"
        )

    def test_second_tab_cannot_replace_an_active_video(self):
        with self.assertRaises(VideoConflict):
            self.store.start(dict(self.data, id="b" * 32))
        self.assertEqual(self.store.active, self.clip["id"])

    def test_format_and_path_validation(self):
        for invalid in ("../samples.csv", "", "/tmp/video", "A" * 32, None):
            with self.assertRaises(ValueError):
                self.store.start(dict(self.data, id=invalid))
        for mime in ("text/html", "video/unknown", None):
            with self.assertRaises(ValueError):
                self.store.start(dict(self.data, mime_type=mime))

    def test_chunk_limits_and_unknown_ids(self):
        for body in (b"", b"x" * (CHUNK_LIMIT + 1)):
            with self.assertRaises(ValueError):
                self.store.chunk(self.clip["id"], 0, body)
        with self.assertRaises(VideoConflict):
            self.store.chunk("unknown", 0, b"x")

    def test_storage_error_does_not_acknowledge_or_duplicate_bytes(self):
        with patch("capture.video.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.chunk(self.clip["id"], 0, b"abc")
        self.assertEqual(
            (self.path / (self.clip["filename"] + ".part")).stat().st_size, 0
        )
        result = self.store.chunk(self.clip["id"], 0, b"abc")
        self.assertEqual((result["chunks"], result["bytes"]), (1, 3))

    def test_close_preserves_incomplete_video_and_rejects_late_upload(self):
        self.store.chunk(self.clip["id"], 0, b"abc")
        with contextlib.redirect_stdout(io.StringIO()):
            self.store.close()
        self.assertEqual(
            (self.path / (self.clip["filename"] + ".part")).read_bytes(), b"abc"
        )
        self.assertFalse((self.path / self.clip["filename"]).exists())
        manifest = json.loads(
            (self.path / f"webcam-{self.clip['id']}.json").read_text()
        )
        self.assertEqual(manifest["status"], "incomplete")
        with self.assertRaises(VideoConflict):
            self.store.chunk(self.clip["id"], 1, b"def")

    def test_failed_camera_start_can_leave_partial_and_start_new_clip(self):
        self.store.abandon(self.clip["id"])
        self.assertTrue((self.path / (self.clip["filename"] + ".part")).exists())
        clip = self.store.start(dict(self.data, id="b" * 32, mime_type="video/mp4"))
        self.assertTrue(clip["filename"].endswith(".mp4"))

    def test_empty_clip_and_invalid_duration_cannot_claim_complete(self):
        with self.assertRaises(VideoConflict):
            self.store.finish(dict(id=self.clip["id"], bytes=0, chunks=0, duration_s=1))
        self.store.chunk(self.clip["id"], 0, b"x")
        for duration in (None, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.store.finish(
                    dict(id=self.clip["id"], bytes=1, chunks=1, duration_s=duration)
                )

    def test_new_store_cannot_overwrite_previous_files(self):
        with self.assertRaises(VideoConflict):
            VideoStore(self.path).start(self.data)


if __name__ == "__main__":
    unittest.main()
