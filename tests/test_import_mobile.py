import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

from capture.import_mobile import import_bundle, import_in_place, inspect_video
from test_onboard import fixture, IDENTITY
from viz.trick_review import prepare


class MobileImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "imported"

    def bundle(self, *, error="", footer=True):
        raw = b"".join(fixture(storage="sd", error=error))
        if not footer:
            # Keep the META and intact data prefix while dropping a tail packet.
            raw = raw[:-10]
        raw_name = f"onboard-{IDENTITY}.bin"
        (self.source / raw_name).write_bytes(raw)
        mobile = dict(schema=1, id=IDENTITY, bootID="1234abcd", storage="sd", downloadState="verified",
                      fileInfo=dict(id=IDENTITY, storage="sd", bytes=len(raw), crc32=f"{zlib.crc32(raw):08x}"))
        (self.source / "mobile_session.json").write_text(json.dumps(mobile))
        (self.source / "metadata.json").write_text(json.dumps(dict(
            schema=1, onboard_id=IDENTITY, boot_id="1234abcd", onboard_storage="sd", rider="rider", board="deck")))
        self.inventory()

    def inventory(self):
        entries = [dict(path=p.name, bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                   for p in self.source.iterdir() if p.name != "export_manifest.json"]
        (self.source / "export_manifest.json").write_text(json.dumps(dict(schema=1, session_id=IDENTITY, files=entries)))

    def test_folder_import_preserves_raw_and_decodes_existing_schema(self):
        self.bundle()
        meta = import_bundle(self.source, self.output)
        self.assertEqual(meta["onboard_id"], IDENTITY)
        self.assertTrue(meta["onboard_quality"]["usable"])
        self.assertTrue((self.output / "samples.csv").exists())
        self.assertTrue((self.output / "source_export_manifest.json").exists())
        self.assertEqual((self.source / f"onboard-{IDENTITY}.bin").read_bytes(), (self.output / f"onboard-{IDENTITY}.bin").read_bytes())

    def test_in_place_import_preserves_originals_and_review_edits_and_runs_once(self):
        self.bundle()
        originals = {p.name: p.read_bytes() for p in self.source.iterdir()}
        review = self.source / "review" / "iphone"
        review.mkdir(parents=True)
        edits = {review / "review.json": b'{"revision":1}',
                 self.source / "labels.jsonl": b'{"id":"saved-label"}\n',
                 self.source / "video_sync.jsonl": b'{"sync_id":1}\n',
                 self.source / ".labels.lock": b'', self.source / ".DS_Store": b'finder'}
        for path, content in edits.items():
            path.write_bytes(content)
        with patch("capture.import_mobile.inspect_video", side_effect=AssertionError("Review indexes the video")):
            meta = import_in_place(self.source)
        self.assertEqual(meta["samples"], 416)
        self.assertTrue(meta["onboard_quality"]["usable"])
        self.assertTrue((self.source / "events.jsonl").read_text())
        for name, content in originals.items():
            saved_name = "source_" + name if name in ("metadata.json", "export_manifest.json") else name
            self.assertEqual((self.source / saved_name).read_bytes(), content)
        for path, content in edits.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.source / "export_manifest.json").exists())
        samples = self.source / "samples.csv"
        stamp = samples.stat().st_mtime_ns
        with patch("capture.import_mobile.verify_export", side_effect=AssertionError("Already imported")):
            self.assertIsNone(import_in_place(self.source))
        self.assertEqual(samples.stat().st_mtime_ns, stamp)

    def test_in_place_import_leaves_desktop_sessions_alone(self):
        self.assertIsNone(import_in_place(self.source))
        self.assertEqual(list(self.source.iterdir()), [])

    def test_in_place_import_rejects_missing_inventory_and_partial_existing_output(self):
        self.bundle()
        (self.source / "export_manifest.json").unlink()
        with self.assertRaisesRegex(ValueError, "Export complete session"):
            import_in_place(self.source)
        samples = self.source / "samples.csv"
        samples.write_text("existing samples")
        with self.assertRaisesRegex(ValueError, "existing import files"):
            import_in_place(self.source)
        self.assertEqual(samples.read_text(), "existing samples")

    def test_in_place_import_retains_incomplete_log_and_stop_fault_warnings(self):
        self.bundle(footer=False)
        path = self.source / "metadata.json"
        meta = json.loads(path.read_text())
        meta["onboard_stop_error"] = "storage_flush_failed"
        path.write_text(json.dumps(meta))
        self.inventory()
        imported = import_in_place(self.source)
        self.assertFalse(imported["onboard_quality"]["usable"])
        self.assertIn("missing_footer", imported["onboard_quality"]["issues"])
        self.assertIn("storage_flush_failed", imported["onboard_quality"]["issues"])

    def test_in_place_import_rolls_back_failed_publication_and_can_retry(self):
        self.bundle()
        originals = {p.name: p.read_bytes() for p in self.source.iterdir()}
        original_replace = Path.replace
        original_unlink = Path.unlink

        def fail_events(path, target):
            if Path(target) == self.source / "events.jsonl":
                raise OSError("disk failure")
            return original_replace(path, target)

        def fail_manifest(path, *args, **kwargs):
            if path == self.source / "export_manifest.json":
                raise OSError("disk failure")
            return original_unlink(path, *args, **kwargs)

        for method, replacement in (("replace", fail_events), ("unlink", fail_manifest)):
            with self.subTest(method=method), patch.object(Path, method, replacement):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    import_in_place(self.source)
            self.assertEqual({p.name: p.read_bytes() for p in self.source.iterdir()
                              if p.name != ".mobile-import.lock"}, originals)
        self.assertEqual(import_in_place(self.source)["samples"], 416)

    def test_in_place_import_rejects_unlisted_files_and_symlinked_raw(self):
        self.bundle()
        extra = self.source / "unlisted.bin"
        extra.write_bytes(b"unknown")
        with self.assertRaisesRegex(ValueError, "inventory"):
            import_in_place(self.source)
        extra.unlink()
        raw = self.source / f"onboard-{IDENTITY}.bin"
        moved = self.root / "raw.bin"
        raw.rename(moved)
        raw.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, "checksum or size"):
            import_in_place(self.source)
        self.assertFalse((self.source / "samples.csv").exists())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_review_auto_imports_from_video_or_mobile_directory_and_reopens(self):
        for use_directory in (False, True):
            with self.subTest(use_directory=use_directory):
                self.source = self.root / f"export-{use_directory}"
                self.source.mkdir()
                self.bundle()
                video = self.source / "iphone.mov"
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=32x32:rate=240",
                                "-frames:v", "120", "-c:v", "libx264", str(video)], check=True, capture_output=True)
                path = self.source / "mobile_session.json"
                mobile = json.loads(path.read_text())
                mobile["videoFile"] = video.name
                path.write_text(json.dumps(mobile))
                self.inventory()
                _, _, data = prepare(self.source if use_directory else video, jump_peaks=True)
                self.assertEqual(len(data["sensor"]["samples"]), 416)
                self.assertEqual(len(data["sensor"]["syncs"]), 1)
                self.assertFalse(data["sensor"]["samples_message"])
                self.assertFalse(data["sensor"]["sync_unavailable_reason"])
                self.assertEqual(len(data["video_timing"]["frame_times_s"]), 120)
                self.assertEqual(data["review_mode"], "jumps")
                with patch("capture.import_mobile.import_log", side_effect=AssertionError("Already imported")):
                    _, _, reopened = prepare(self.source, jump_peaks=True)
                self.assertEqual(reopened["sensor"], data["sensor"])

    def test_wrapped_zip_import(self):
        self.bundle()
        archive = self.root / "session.zip"
        with zipfile.ZipFile(archive, "w") as writer:
            for file in self.source.iterdir():
                writer.write(file, f"session-{IDENTITY}/{file.name}")
        self.assertEqual(import_bundle(archive, self.output)["onboard_id"], IDENTITY)

    def test_corruption_does_not_publish_session(self):
        self.bundle()
        with (self.source / f"onboard-{IDENTITY}.bin").open("ab") as stream:
            stream.write(b"bad")
        with self.assertRaisesRegex(ValueError, "checksum or size"):
            import_bundle(self.source, self.output)
        self.assertFalse(self.output.exists())
        original_metadata = (self.source / "metadata.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "checksum or size"):
            import_in_place(self.source)
        self.assertEqual((self.source / "metadata.json").read_bytes(), original_metadata)
        self.assertFalse((self.source / "samples.csv").exists())

    def test_identity_mismatch_even_with_valid_inventory(self):
        self.bundle()
        path = self.source / "mobile_session.json"
        mobile = json.loads(path.read_text())
        mobile["bootID"] = "different"
        path.write_text(json.dumps(mobile))
        self.inventory()
        with self.assertRaisesRegex(ValueError, "identity, boot, or storage"):
            import_bundle(self.source, self.output)
        with self.assertRaisesRegex(ValueError, "identity, boot, or storage"):
            import_in_place(self.source)
        self.assertFalse((self.source / "samples.csv").exists())

    def test_partial_log_stays_flagged(self):
        self.bundle(footer=False)
        meta = import_bundle(self.source, self.output)
        self.assertFalse(meta["onboard_quality"]["usable"])
        self.assertIn("missing_footer", meta["onboard_quality"]["issues"])

    def test_stop_fault_is_preserved(self):
        self.bundle()
        path = self.source / "metadata.json"
        meta = json.loads(path.read_text())
        meta["onboard_stop_error"] = "storage_flush_failed"
        path.write_text(json.dumps(meta))
        self.inventory()
        imported = import_bundle(self.source, self.output)
        self.assertIn("storage_flush_failed", imported["onboard_quality"]["issues"])

    def test_existing_output_and_traversal_are_rejected(self):
        self.bundle()
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "new output"):
            import_bundle(self.source, self.output)
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as writer:
            writer.writestr("../escape", "unsafe")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            import_bundle(archive, self.root / "new")
        self.assertFalse((self.root / "escape").exists())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class MobileVideoTests(unittest.TestCase):
    def test_silent_240fps_movie_preserves_frames_through_review(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "iphone.mov"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=32x32:rate=240",
                            "-frames:v", "120", "-c:v", "libx264", str(source)], check=True, capture_output=True)
            report = inspect_video(source, 240)
            self.assertEqual(report["frames"], 120)
            self.assertAlmostEqual(report["duration_s"], .5, delta=.005)
            self.assertAlmostEqual(report["measured_median_fps"], 240, delta=.1)
            self.assertEqual(report["gaps_over_1_5_frame_intervals"], 0)
            with patch("viz.trick_review.attempt_context", return_value={}):
                _, _, data = prepare(source)
            self.assertFalse(data["audio"]["available"])
            self.assertEqual(data["audio"]["onsets"], [])
            self.assertEqual(len(data["video_timing"]["frame_times_s"]), 120)
            self.assertAlmostEqual(data["duration_s"], .5, delta=.005)


if __name__ == "__main__":
    unittest.main()
