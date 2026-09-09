#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26,<3", "scipy>=1.12,<2"]
# ///
"""Review audio onset pairs alongside a recorded video and optional raw IMU.

uv run viz/trick_review.py sessions/<session>/webcam-<id>.webm
Requires ffmpeg and ffprobe on PATH. All processing and playback stay local.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

VERSION = 1
SAMPLE_RATE = 16000
HOP = 160
ROOT = Path(__file__).resolve().parents[1]


def read_json(path, fallback=None):
    return json.loads(path.read_text()) if path.exists() else fallback


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False, separators=(",", ":")))
    temporary.replace(path)


def run(command):
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace")[-3000:])
    return result.stdout


def probe(path):
    return json.loads(run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ]))


def extract_audio(path):
    import numpy as np

    # Keep the review media's PTS. Pad delayed audio and timestamp gaps with
    # silence; trim negative encoder preroll. Never concatenate away silence.
    raw = run([
        "ffmpeg", "-v", "error", "-copyts", "-i", str(path), "-map", "0:a:0",
        "-vn", "-ac", "1", "-af", f"aresample={SAMPLE_RATE}:async=1:first_pts=0",
        "-f", "f32le", "pipe:1",
    ])
    return np.frombuffer(raw, dtype="<f4")


def audio_features(audio, sample_rate=SAMPLE_RATE, hop=HOP):
    """Centered 32 ms log-spectral flux, with 10 ms timestamp resolution.

    Spectral increases above a one-second local median emphasize transients.
    Strength is relative to this clip's 99.5th percentile, not a probability.
    """
    import numpy as np
    from scipy.ndimage import median_filter
    from scipy.signal import find_peaks

    if len(audio) == 0 or not np.all(np.isfinite(audio)):
        raise ValueError("Audio is empty or contains nonfinite samples.")
    size = round(sample_rate * .032)
    frames = np.lib.stride_tricks.sliding_window_view(
        np.pad(audio, (size // 2, size // 2)), size
    )[::hop]
    bands = np.fft.rfftfreq(size, 1 / sample_rate)
    # Chunk FFTs to avoid holding an entire recording's complex spectrum.
    flux, previous = [], None
    window = np.hanning(size)
    for start in range(0, len(frames), 4096):
        spectrum = np.abs(np.fft.rfft(frames[start:start + 4096] * window, axis=1))
        spectrum = np.log1p(10 * spectrum[:, (bands >= 180) & (bands <= 7500)])
        before = spectrum[:1] if previous is None else previous
        flux.extend(np.maximum(np.diff(spectrum, axis=0, prepend=before), 0).mean(axis=1))
        previous = spectrum[-1:]
    flux = np.asarray(flux)
    median_size = max(3, round(sample_rate / hop) | 1)
    novelty = np.maximum(flux - median_filter(flux, size=median_size), 0)
    strength = novelty / max(float(np.percentile(novelty, 99.5)), 1e-5)
    peaks, _ = find_peaks(strength, height=.05, prominence=.04,
                         distance=max(1, round(.10 * sample_rate / hop)))
    # Min/max bins retain impacts even when the overview is wider than the data.
    padded = np.pad(audio, (0, (-len(audio)) % hop)).reshape(-1, hop)
    rms = np.sqrt(np.mean(padded ** 2, axis=1))
    return dict(
        step_s=hop / sample_rate,
        waveform_min=np.round(padded.min(axis=1), 5).tolist(),
        waveform_max=np.round(padded.max(axis=1), 5).tolist(),
        rms=np.round(rms, 5).tolist(),
        strength=np.round(strength, 4).tolist(),
        onsets=[dict(time_s=round(float(p * hop / sample_rate), 4),
                     strength=round(float(strength[p]), 4)) for p in peaks
                if p * hop / sample_rate < len(audio) / sample_rate],
    )


def sensor_context(source):
    import numpy as np

    session = source.parent
    meta = read_json(session / "metadata.json", {})
    sidecar = read_json(source.with_suffix(".json"), {})
    result = dict(samples=[], syncs=[], mapping=None, offset_hint_s=0, warnings=[])
    if "created_utc" in sidecar and "created_utc" in meta:
        result["offset_hint_s"] = round((datetime.fromisoformat(sidecar["created_utc"])
            - datetime.fromisoformat(meta["created_utc"])).total_seconds(), 3)
    csv_path = session / "samples.csv"
    if csv_path.exists():
        with csv_path.open() as stream:
            for row in csv.DictReader(stream):
                values = [float(row[k]) for k in ("t_s", "ax", "ay", "az", "gx", "gy", "gz")]
                if all(math.isfinite(v) for v in values):
                    t, *axes = values
                    result["samples"].append([t, math.hypot(*axes[:3]), math.hypot(*axes[3:])])
        samples = result["samples"]
        if len(samples) > 1:
            gaps = np.diff([row[0] for row in samples])
            rate = (len(samples) - 1) / (samples[-1][0] - samples[0][0])
            zero = sum(row[1] == 0 for row in samples) / len(samples)
            result.update(average_hz=round(rate, 2), zero_accel_fraction=round(zero, 3),
                          gaps_over_30ms=int(sum(gaps > .03)))
            if rate < 50 or zero > .05:
                result["warnings"].append(
                    f"IMU quality: {rate:.1f} samples/s; {zero:.0%} of rows have zero acceleration. "
                    "Raw points are for inspection; gaps over 30 ms are not connected.")
    if (session / "events.jsonl").exists():
        for line in (session / "events.jsonl").read_text().splitlines():
            row = json.loads(line)
            if (row.get("kind") == "sync" and row.get("edge") == 1
                    and row.get("led_enabled") and row.get("boot_id") == meta.get("boot_id")
                    and meta.get("device_origin_us") is not None):
                result["syncs"].append(dict(id=row["sync_id"],
                    session_s=(row["device_us"] - meta["device_origin_us"]) / 1e6))
    if (session / "video_sync.jsonl").exists():
        sys.path.insert(0, str(ROOT)) if str(ROOT) not in sys.path else None
        from capture.session import video_mapping
        try:
            scale, offset, count = video_mapping(session, source.name)
            result["mapping"] = dict(scale=scale, offset_s=offset, points=count)
        except ValueError as error:
            result["warnings"].append(str(error))
    return result


def prepare(source, rebuild=False):
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            raise ValueError(f"Install {binary} first (macOS: brew install ffmpeg).")
    source = source.resolve(strict=True)
    if source.suffix.lower() not in (".webm", ".mp4", ".mov", ".mkv"):
        raise ValueError("Choose a WebM, MP4, MOV, or MKV recording.")
    directory = source.parent / "review" / source.stem
    directory.mkdir(parents=True, exist_ok=True)
    extension = ".webm" if source.suffix.lower() == ".webm" else ".mp4"
    media = directory / ("media" + extension)
    fingerprint = dict(name=source.name, bytes=source.stat().st_size,
                       mtime_ns=source.stat().st_mtime_ns)
    analysis_path = directory / "analysis.json"
    data = read_json(analysis_path, {})
    if rebuild or data.get("source") != fingerprint or data.get("schema") != VERSION or not media.exists():
        info = probe(source)
        if not any(row["codec_type"] == "audio" for row in info["streams"]):
            raise ValueError("This recording has no audio track. Choose a clip recorded with microphone audio.")
        if not any(row["codec_type"] == "video" for row in info["streams"]):
            raise ValueError("This recording has no video track.")
        origin = float(info["format"].get("start_time", 0))
        print("Preparing a seekable review copy and audio onsets…", flush=True)
        temporary = directory / ("media.tmp" + extension)
        # Stream copy adds duration/cues to browser MediaRecorder WebM without
        # re-encoding. One shared timestamp shift preserves track offsets.
        command = ["ffmpeg", "-v", "error", "-y", "-copyts", "-start_at_zero",
                   "-i", str(source), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                   "-avoid_negative_ts", "disabled"]
        if extension == ".mp4":
            command += ["-movflags", "+faststart"]
        run(command + [str(temporary)])
        temporary.replace(media)
        review_info = probe(media)
        audio = extract_audio(media)
        duration = float(review_info["format"].get("duration", len(audio) / SAMPLE_RATE))
        data = dict(schema=VERSION, source=fingerprint, video=source.name,
                    source_pts_origin_s=origin, duration_s=duration,
                    audio_duration_s=len(audio) / SAMPLE_RATE,
                    algorithm="log-spectral-flux-v1", sample_rate=SAMPLE_RATE,
                    media_url="/media" + extension, audio=audio_features(audio))
        write_json(analysis_path, data)
    # Refresh alignment/quality on every launch, even if audio is cached.
    data["sensor"] = sensor_context(source)
    return directory, media, data


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_review(value, duration):
    if not isinstance(value, dict) or not isinstance(value.get("intervals"), list):
        raise ValueError("Expected a review with intervals.")
    if len(value["intervals"]) > 5000:
        raise ValueError("Too many intervals.")
    ids = set()
    for row in value["intervals"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid interval.")
        identity = row.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", identity) or identity in ids:
            raise ValueError("Interval IDs must be unique.")
        ids.add(identity)
        start, end = row.get("start_s"), row.get("end_s")
        if not (finite_number(start) and finite_number(end) and 0 <= start < end <= duration):
            raise ValueError("Intervals need 0 <= start < finish <= video duration.")
        if row.get("status") not in ("reviewed", "rejected"):
            raise ValueError("Invalid review status.")
        if not isinstance(row.get("note", ""), str) or len(row.get("note", "")) > 2000:
            raise ValueError("Notes must be at most 2000 characters.")
    settings = value.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("Invalid settings.")
    for key, low, high in (("threshold", .05, 5), ("min_gap", .1, 5),
                           ("max_gap", .1, 5), ("offset", -1e6, 1e6), ("scale", .98, 1.02)):
        if not finite_number(settings.get(key)) or not low <= settings[key] <= high:
            raise ValueError(f"Invalid {key}.")
    if settings["min_gap"] >= settings["max_gap"]:
        raise ValueError("Minimum gap must be less than maximum gap.")
    return dict(intervals=value["intervals"], settings=settings)


def byte_range(header, size):
    if not header:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()):
        raise ValueError("Invalid byte range")
    first, last = match.groups()
    if first:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    else:
        if int(last) <= 0:
            raise ValueError("Invalid suffix range")
        start, end = max(0, size - int(last)), size - 1
    if start > end or start >= size:
        raise ValueError("Range outside media")
    return start, end, True


def make_server(directory, media, data, port):
    review_path = directory / "review.json"
    lock = threading.Lock()

    def review():
        saved = read_json(review_path, dict(revision=0, intervals=[], settings=None))
        if saved.get("source", data["source"]) != data["source"]:
            raise ValueError("Source recording changed; saved edits refer to a different file.")
        return saved

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self):
            allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host")
            origin = self.headers.get("Origin")
            if host not in allowed_hosts or (origin and origin != f"http://{host}"):
                self.send_error(403, "Local review origin required")
                return False
            return True

        def json_response(self, status, value):
            body = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.allowed():
                return
            if self.path == "/api/data":
                self.json_response(200, data)
                return
            if self.path == "/api/review":
                try:
                    with lock:
                        self.json_response(200, review())
                except ValueError as error:
                    self.json_response(409, dict(error=str(error)))
                return
            assets = {"/": (Path(__file__).with_name("trick_review.html"), "text/html"),
                      "/trick_review.mjs": (Path(__file__).with_name("trick_review.mjs"), "text/javascript"),
                      data["media_url"]: (media, "video/webm" if media.suffix == ".webm" else "video/mp4")}
            if self.path not in assets:
                self.send_error(404)
                return
            path, content_type = assets[self.path]
            size = path.stat().st_size
            try:
                start, end, partial = byte_range(self.headers.get("Range"), size)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                with path.open("rb") as stream:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = stream.read(min(remaining, 256 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Scrubbing cancels previous range requests.

        def do_POST(self):
            if not self.allowed():
                return
            if self.path not in ("/api/review", "/api/sync"):
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Expected JSON, at most 1 MiB.")
                value = json.loads(self.rfile.read(size))
                if self.path == "/api/sync":
                    if not isinstance(value, dict):
                        raise ValueError("Expected a flash correspondence.")
                    time_s, identity = value.get("review_s"), value.get("sync_id")
                    if not finite_number(time_s) or not 0 <= time_s <= data["duration_s"]:
                        raise ValueError("Flash must lie within this video.")
                    if not isinstance(identity, int) or isinstance(identity, bool):
                        raise ValueError("Choose a recorded sync ID.")
                    session = directory.parent.parent
                    sys.path.insert(0, str(ROOT)) if str(ROOT) not in sys.path else None
                    from capture.session import fit_video_points, read_jsonl, sync_point
                    meta = read_json(session / "metadata.json", {})
                    if "closed_utc" not in meta:
                        raise ValueError("Stop recording before saving review alignment.")
                    matches = [s for s in data["sensor"]["syncs"] if s["id"] == identity]
                    if len(matches) != 1:
                        raise ValueError("Choose an available recorded LED flash.")
                    original_s = time_s + data["source_pts_origin_s"]
                    with lock:
                        points = {row["sync_id"]: row for row in read_jsonl(session / "video_sync.jsonl")
                                  if row["video"] == data["video"]}
                        points[identity] = dict(video=data["video"], video_s=original_s,
                                                t_s=matches[0]["session_s"], sync_id=identity)
                        # Reject inconsistent correspondences before appending.
                        scale, offset, count = fit_video_points(list(points.values()))
                        sync_point(argparse.Namespace(session=session, video=data["video"],
                                                      sync_id=identity, video_seconds=original_s))
                        data["sensor"]["mapping"] = dict(scale=scale, offset_s=offset, points=count)
                    self.json_response(200, data["sensor"]["mapping"])
                    return
                validated = validate_review(value, data["duration_s"])
                with lock:
                    previous = review()
                    if value.get("revision") != previous["revision"]:
                        self.json_response(409, dict(error="Review changed in another tab. Reload before saving."))
                        return
                    saved = dict(schema=VERSION, revision=previous["revision"] + 1,
                                 source=data["source"], video=data["video"],
                                 source_pts_origin_s=data["source_pts_origin_s"],
                                 timeline="review_video_seconds", algorithm=data["algorithm"],
                                 updated_utc=datetime.now(timezone.utc).isoformat(), **validated)
                    write_json(review_path, saved)
                self.json_response(200, saved)
            except (ValueError, TypeError, OSError) as error:
                self.json_response(400, dict(error=str(error)))

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--rebuild", action="store_true", help="Rebuild derived audio/media; preserve reviewed intervals")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    try:
        directory, media, data = prepare(args.video, args.rebuild)
        print(f"Ready: {data['duration_s']:.2f}s, {len(data['audio']['onsets'])} raw onsets.\nDerived files: {directory}", flush=True)
        if args.prepare_only:
            return
        server = make_server(directory, media, data, args.port)
        print(f"Review: http://127.0.0.1:{server.server_port}\nCtrl+C stops the review server.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    except (ValueError, OSError, ImportError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
