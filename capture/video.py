"""Ordered, retry-safe webcam uploads, independent of the sensor acquisition loop."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
from datetime import datetime, timezone

CHUNK_LIMIT = 1024 * 1024


class VideoConflict(ValueError):
    pass


class VideoStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()
        self.clips = {}
        self.active = None
        self.closed = False

    def _manifest(self, clip):
        target = self.directory / f"webcam-{clip['id']}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(clip, indent=2, allow_nan=False) + "\n")
        temporary.replace(target)

    def _get(self, clip_id):
        if self.closed:
            raise VideoConflict("Sensor recorder has stopped.")
        if clip_id not in self.clips:
            raise VideoConflict("Unknown video recording.")
        return self.clips[clip_id]

    def start(self, data):
        clip_id, mime = data.get("id", ""), data.get("mime_type", "")
        if not isinstance(clip_id, str) or not re.fullmatch(r"[a-f0-9]{32}", clip_id):
            raise ValueError("Invalid video ID.")
        if not isinstance(mime, str) or len(mime) > 200:
            raise ValueError("Invalid video format.")
        extension = {"video/webm": "webm", "video/mp4": "mp4"}.get(mime.split(";")[0])
        if not extension:
            raise ValueError("This browser must record WebM or MP4.")
        capture = data.get("capture", {})
        if (
            not isinstance(capture, dict)
            or len(json.dumps(capture, allow_nan=False)) > 4096
        ):
            raise ValueError("Invalid camera metadata.")
        with self.lock:
            if self.closed:
                raise VideoConflict("Sensor recorder has stopped.")
            if clip_id in self.clips:
                clip = self.clips[clip_id]
                if clip["mime_type"] != mime:
                    raise VideoConflict("Video format changed during a retry.")
                self._manifest(clip)
                return dict(clip)
            if self.active:
                raise VideoConflict(
                    "A video is already active. Stop it in its original tab first."
                )
            filename = f"webcam-{clip_id}.{extension}"
            if (self.directory / filename).exists() or (
                self.directory / f"webcam-{clip_id}.json"
            ).exists():
                raise VideoConflict("Video already exists; it will not be overwritten.")
            with (self.directory / (filename + ".part")).open("xb"):
                pass
            clip = dict(
                schema=1,
                id=clip_id,
                filename=filename,
                mime_type=mime,
                status="recording",
                bytes=0,
                chunks=0,
                capture=capture,
                created_utc=datetime.now(timezone.utc).isoformat(),
                alignment="Match visible LED flashes; browser times are not sensor sync.",
            )
            self.clips[clip_id] = clip
            self.active = clip_id
            self._manifest(clip)
            return dict(clip)

    def chunk(self, clip_id, sequence, body):
        if not 0 < len(body) <= CHUNK_LIMIT:
            raise ValueError("Video chunk must be between 1 byte and 1 MiB.")
        digest = hashlib.sha256(body).hexdigest()
        with self.lock:
            clip = self._get(clip_id)
            if clip["status"] != "recording":
                raise VideoConflict("Video is no longer accepting chunks.")
            if sequence == clip["chunks"] - 1 and digest == clip.get(
                "last_chunk_sha256"
            ):
                self._manifest(clip)
                return dict(clip)  # The previous acknowledgement was lost.
            if sequence != clip["chunks"]:
                raise VideoConflict("Missing, duplicate, or reordered video chunk.")
            partial = self.directory / (clip["filename"] + ".part")
            with partial.open("r+b") as stream:
                stream.seek(clip["bytes"])
                try:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                except OSError:
                    stream.seek(clip["bytes"])
                    stream.truncate()
                    raise
            clip.update(
                bytes=clip["bytes"] + len(body),
                chunks=sequence + 1,
                last_chunk_sha256=digest,
            )
            self._manifest(clip)
            return dict(clip)

    def finish(self, data):
        with self.lock:
            clip = self._get(data.get("id", ""))
            if (
                data.get("chunks") != clip["chunks"]
                or data.get("bytes") != clip["bytes"]
            ):
                raise VideoConflict(
                    "Video is missing uploaded data; retry saving first."
                )
            if clip["status"] == "saved":
                self._manifest(clip)
                return dict(clip)
            if clip["status"] != "recording" or not clip["bytes"]:
                raise VideoConflict("There is no complete video to save.")
            duration = data.get("duration_s")
            if (
                not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration < 0
            ):
                raise ValueError("Invalid video duration.")
            final = self.directory / clip["filename"]
            if final.exists():
                raise VideoConflict("Video already exists; it will not be overwritten.")
            (self.directory / (clip["filename"] + ".part")).rename(final)
            clip.update(
                status="saved",
                browser_duration_s=duration,
                warning=str(data.get("warning", ""))[:1000],
                closed_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.active = None
            self._manifest(clip)
            return dict(clip)

    def abandon(self, clip_id):
        with self.lock:
            clip = self._get(clip_id)
            if clip["status"] == "recording":
                clip.update(
                    status="incomplete", warning="Browser could not start recording."
                )
                self.active = None
                self._manifest(clip)
            return dict(clip)

    def close(self):
        with self.lock:
            self.closed = True
            if self.active:
                clip = self.clips[self.active]
                clip.update(
                    status="incomplete",
                    warning="Recorder stopped before video was saved.",
                )
                self._manifest(clip)
                print(
                    f"Webcam video is INCOMPLETE: {clip['filename']}.part", flush=True
                )
                self.active = None
