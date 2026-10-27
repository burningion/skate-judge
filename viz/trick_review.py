#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.4.6", "scipy==1.16.3", "scikit-learn==1.8.0"]
# ///
"""Review audio onset pairs alongside a recorded video and optional raw IMU.

uv run viz/trick_review.py sessions/<session>/webcam-<id>.webm
Requires ffmpeg and ffprobe on PATH. All processing and playback stay local.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

VERSION = 2
API_VERSION = 7
SAMPLE_RATE = 16000
HOP = 160
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ATTEMPT_MODEL = ROOT / ".build" / "attempts-v1" / "model.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from capture.session import append_json, build_label, file_lock, read_jsonl, video_mapping
from capture.motion_events import board_motion_context


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


def video_timing(media, duration):
    """Index decoded presentation timestamps, including variable-rate footage."""
    stream = next(row for row in probe(media)["streams"] if row["codec_type"] == "video")
    try:
        fps = float(Fraction(stream.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        fps = 0
    frames = json.loads(run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(media),
    ]))["frames"]
    times = (float(row["best_effort_timestamp_time"]) for row in frames
             if row.get("best_effort_timestamp_time") not in (None, "N/A"))
    times = sorted({t for t in times if math.isfinite(t) and 0 <= t < duration})
    if not times:
        raise ValueError("No video frame timestamps found for stepping.")
    return dict(fps=fps if math.isfinite(fps) and fps > 0 else None, frame_times_s=times)


def cached_video_timing(directory, media, duration):
    path = directory / "frames.json"
    stat = media.stat()
    identity = dict(name=media.name, bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
    with file_lock(directory / ".frames.lock"):
        cached = read_json(path, {})
        if cached.get("media") != identity or cached.get("schema") != 1:
            print("Indexing video frames for single-frame stepping (cached after this run)…", flush=True)
            cached = dict(schema=1, media=identity, **video_timing(media, duration))
            write_json(path, cached)
    return dict(fps=cached["fps"], frame_times_s=cached["frame_times_s"])


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


def motion_features(samples):
    """Inspection hints in sensor seconds, never activity or outcome labels.

    Find acceleration/rotation peaks above a one-second local median. Physical
    floors prevent quiet sensor noise from becoming strong normalized events.
    Process continuous runs separately so gaps cannot become invented impacts.
    """
    import numpy as np
    from scipy.ndimage import median_filter
    from scipy.signal import find_peaks

    result = dict(algorithm="imu-local-peaks-v1", events=[], unavailable_reason="", coverage=None)
    values = np.asarray(samples, dtype=float)
    if len(values) < 3:
        result["unavailable_reason"] = "Not enough IMU samples for motion suggestions."
        return result
    gaps = np.diff(values[:, 0])
    if not np.all(np.isfinite(values)) or np.any(gaps <= 0):
        result["unavailable_reason"] = "IMU timestamps or samples are invalid."
        return result
    spacing = float(np.median(gaps))
    if spacing > .02 or np.mean(values[:, 1] == 0) > .05:
        result["unavailable_reason"] = "IMU data is too sparse or contains too many zero readings for motion suggestions."
        return result
    result["coverage"] = [float(values[0, 0]), float(values[-1, 0])]
    size = max(3, round(1 / spacing) | 1)
    segments = np.split(np.arange(len(values)), np.flatnonzero(gaps > .03) + 1)
    for axis, kind, floor in ((1, "accel", 15.), (2, "gyro", 2.)):
        runs = []
        for indices in segments:
            if len(indices) < 3:
                continue
            signal = values[indices, axis]
            excess = np.maximum(signal - median_filter(signal, size=size, mode="nearest"), 0)
            runs.append((indices, excess))
        scale = max(floor, float(np.percentile(np.concatenate([v for _, v in runs]), 99.5))) if runs else floor
        for indices, excess in runs:
            peaks, _ = find_peaks(excess, height=floor, prominence=floor / 2,
                                 distance=max(1, round(.1 / spacing)))
            for peak in peaks:
                at = indices[peak]
                result["events"].append(dict(time_s=float(values[at, 0]), kind=kind,
                    strength=round(float(excess[peak] / scale), 4), magnitude=round(float(values[at, axis]), 4)))
    result["events"].sort(key=lambda row: row["time_s"])
    # Pair generation may not span a known acquisition gap.
    result["gaps"] = [[float(values[i, 0]), float(values[i + 1, 0])] for i in np.flatnonzero(gaps > .03)]
    # Missing acceleration is not evidence of a quiet board. Keep audio
    # candidates around zero readings even below the session-wide cutoff.
    result["gaps"].extend([[float(values[max(0, i - 1), 0]), float(values[min(len(values) - 1, i + 1), 0])]
                           for i in np.flatnonzero(values[:, 1] == 0)])
    return result


def sensor_context(source):
    import numpy as np

    session = source.parent
    meta = read_json(session / "metadata.json", {})
    sidecar = read_json(source.with_suffix(".json"), {})
    result = dict(samples=[], syncs=[], mapping=None, offset_hint_s=0, warnings=[])
    event_path = session / "events.jsonl"
    event_content = event_path.read_bytes() if event_path.exists() else None
    try:
        result["board_motion"] = board_motion_context(meta, event_content)
        result["warnings"].extend(result["board_motion"]["warnings"])
    except (ValueError, TypeError, KeyError) as error:
        result["board_motion"] = dict(schema=1, availability="invalid", events=[],
                                       message=f"Board events could not be read: {error}")
    if "created_utc" in sidecar and "created_utc" in meta:
        result["offset_hint_s"] = round((datetime.fromisoformat(sidecar["created_utc"])
            - datetime.fromisoformat(meta["created_utc"])).total_seconds(), 3)
    csv_path = session / "samples.csv"
    invalid_samples = False
    if csv_path.exists():
        with csv_path.open() as stream:
            for row in csv.DictReader(stream):
                values = [float(row[k]) for k in ("t_s", "ax", "ay", "az", "gx", "gy", "gz")]
                if all(math.isfinite(v) for v in values):
                    t, *axes = values
                    result["samples"].append([t, math.hypot(*axes[:3]), math.hypot(*axes[3:])])
                else:
                    invalid_samples = True
        samples = result["samples"]
        if len(samples) > 1:
            gaps = np.diff([row[0] for row in samples])
            elapsed = samples[-1][0] - samples[0][0]
            rate = (len(samples) - 1) / elapsed if elapsed > 0 else 0
            zero = sum(row[1] == 0 for row in samples) / len(samples)
            result.update(average_hz=round(rate, 2), zero_accel_fraction=round(zero, 3),
                          gaps_over_30ms=int(sum(gaps > .03)))
            if rate < 50 or zero > .05:
                result["warnings"].append(
                    f"IMU quality: {rate:.1f} samples/s; {zero:.0%} of rows have zero acceleration. "
                    "Raw points are for inspection; gaps over 30 ms are not connected.")
    if event_content:
        for line in event_content.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if (row.get("kind") == "sync" and row.get("edge") == 1
                    and row.get("led_enabled") and row.get("boot_id") == meta.get("boot_id")
                    and meta.get("device_origin_us") is not None):
                result["syncs"].append(dict(id=row["sync_id"],
                    session_s=(row["device_us"] - meta["device_origin_us"]) / 1e6))
    result["samples_message"] = ""
    if not result["samples"]:
        result["samples_message"] = ("No accelerometer or gyroscope data: samples.csv is missing from this session."
            if not csv_path.exists() else "samples.csv contains no usable sensor samples.")
        if meta.get("onboard_id"):
            result["samples_message"] += " Download or import this session's onboard recording, then restart the review server."
    result["sync_unavailable_reason"] = ""
    if not result["syncs"]:
        result["sync_unavailable_reason"] = ("Flash matching is unavailable: events.jsonl is missing from this session."
            if not (session / "events.jsonl").exists()
            else "Flash matching is unavailable: no enabled LED flashes were recorded for this session's device clock.")
    elif "closed_utc" not in meta:
        result["sync_unavailable_reason"] = "Stop recording and finish importing the sensor data before matching flashes."
    issues = list(meta.get("onboard_quality", {}).get("issues", []))
    if meta.get("onboard_stop_error") and meta["onboard_stop_error"] not in issues:
        issues.append(meta["onboard_stop_error"])
    if issues:
        result["warnings"].append(f"Board recording reported {', '.join(issues)}; sensor data may be incomplete.")
    if (session / "video_sync.jsonl").exists():
        sys.path.insert(0, str(ROOT)) if str(ROOT) not in sys.path else None
        from capture.session import video_mapping
        try:
            scale, offset, count = video_mapping(session, source.name)
            result["mapping"] = dict(scale=scale, offset_s=offset, points=count)
        except ValueError as error:
            result["warnings"].append(str(error))
    result["motion"] = motion_features(result["samples"])
    if invalid_samples:
        result["motion"]["unavailable_reason"] = "IMU contains nonfinite samples; using audio only."
    return result


def attempt_context(session, model_path, cache_path):
    """Score continuous IMU with the trained detector; raw interrupts are not a gate.

    Cache only derived results, keyed by the complete sensor and model contents.
    Missing/invalid inference stays distinct from a successful scan with no hits.
    """
    result = dict(availability="unavailable", events=[], message="")
    if not model_path.is_file():
        return dict(result, message="Attempt model is missing. Train learning/attempts.py, then restart with --attempt-model pointing to model.json.")
    samples_path = session / "samples.csv"
    if not samples_path.is_file():
        return dict(result, message="Attempt detection needs this session's samples.csv. Finish importing the recording and restart the review server.")
    if not read_json(session / "metadata.json", {}).get("closed_utc"):
        return dict(result, message="Stop recording and finish importing the sensor data before detecting attempts.")
    try:
        from learning import attempts

        identity = dict(schema=2, detector=attempts.VERSION,
                        model_sha256=attempts.landing.sha256(model_path),
                        samples_sha256=attempts.landing.sha256(samples_path))
        with file_lock(cache_path.with_suffix(".lock")):
            try:
                cached = read_json(cache_path, {})
            except (ValueError, OSError):
                cached = {}
            if isinstance(cached, dict) and cached.get("identity") == identity and isinstance(cached.get("scan"), dict):
                scan = cached["scan"]
            else:
                scan = attempts.scan_file(model_path, samples_path)
                if any(scan[key] != identity[key] for key in ("model_sha256", "samples_sha256")):
                    raise ValueError("Model or sensor data changed during detection; restart to try again.")
                write_json(cache_path, dict(identity=identity, scan=scan))
        result.update(scan, availability="ready" if scan["status"] == "scanned" else "unknown",
                      model_name=model_path.parent.name, model_path=str(model_path.resolve()))
        if scan["status"] != "scanned":
            result["message"] = "No usable two-second IMU windows. Attempt detection is unknown for this recording."
        return result
    except (ImportError, OSError, ValueError, KeyError, TypeError, IndexError) as error:
        return dict(result, message=f"Attempt detector unavailable: {error}")


def prepare(source, rebuild=False, attempt_model=DEFAULT_ATTEMPT_MODEL):
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
        has_audio = any(row["codec_type"] == "audio" for row in info["streams"])
        if not any(row["codec_type"] == "video" for row in info["streams"]):
            raise ValueError("This recording has no video track.")
        origin = float(info["format"].get("start_time", 0))
        print("Preparing a seekable review copy" + (" and audio onsets…" if has_audio else " (no audio track)…"), flush=True)
        temporary = directory / ("media.tmp" + extension)
        # Stream copy adds duration/cues to browser MediaRecorder WebM without
        # re-encoding. One shared timestamp shift preserves track offsets.
        command = ["ffmpeg", "-v", "error", "-y", "-copyts", "-start_at_zero",
                   "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                   "-avoid_negative_ts", "disabled"]
        if extension == ".mp4":
            command += ["-movflags", "+faststart"]
            if any(row.get("codec_name") == "hevc" for row in info["streams"] if row["codec_type"] == "video"):
                # hvc1 permits native HEVC playback in Safari without reducing FPS.
                command += ["-tag:v", "hvc1"]
        run(command + [str(temporary)])
        temporary.replace(media)
        review_info = probe(media)
        audio = extract_audio(media) if has_audio else None
        audio_duration = len(audio) / SAMPLE_RATE if audio is not None else 0
        duration = float(review_info["format"].get("duration", audio_duration))
        features = audio_features(audio) if audio is not None else dict(
            available=False, unavailable_reason="No audio track. Manual review and aligned IMU suggestions remain available.",
            step_s=HOP / SAMPLE_RATE, waveform_min=[], waveform_max=[], rms=[], strength=[], onsets=[])
        features.setdefault("available", True)
        data = dict(schema=VERSION, source=fingerprint, video=source.name,
                    source_pts_origin_s=origin, duration_s=duration,
                    audio_duration_s=audio_duration,
                    algorithm="log-spectral-flux-v1", sample_rate=SAMPLE_RATE,
                    media_url="/media" + extension, audio=features)
        write_json(analysis_path, data)
    # Refresh alignment/quality on every launch, even if audio is cached.
    data["api_version"] = API_VERSION
    data["video_timing"] = cached_video_timing(directory, media, data["duration_s"])
    data["sensor"] = sensor_context(source)
    data["sensor"]["attempts"] = attempt_context(source.parent, attempt_model, directory / "attempts.json")
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
    if settings.get("candidate_mode", "audio") not in ("audio", "audio_motion", "audio_filtered", "audio_attempts"):
        raise ValueError("Invalid candidate mode.")
    if not finite_number(settings.get("motion_threshold", .65)) or not .1 <= settings.get("motion_threshold", .65) <= 2:
        raise ValueError("Invalid motion threshold.")
    if not finite_number(settings.get("filter_threshold", .4)) or not .1 <= settings.get("filter_threshold", .4) <= 2:
        raise ValueError("Invalid filter threshold.")
    if not isinstance(settings.get("show_filtered", False), bool):
        raise ValueError("Invalid filtered-suggestion visibility.")
    if not finite_number(settings.get("attempt_threshold", .5)) or not .5 <= settings.get("attempt_threshold", .5) <= 1:
        raise ValueError("Invalid attempt score threshold.")
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
    data["api_version"] = API_VERSION
    review_path = directory / "review.json"
    session = directory.parent.parent
    thread_lock = threading.Lock()

    @contextmanager
    def lock():
        with thread_lock, file_lock(directory / ".review.lock"), file_lock(session / ".labels.lock"):
            yield

    def label_state():
        path = session / "labels.jsonl"
        content = path.read_bytes() if path.exists() else b""
        active = {row["id"]: row for row in (json.loads(line) for line in content.splitlines() if line.strip())}
        meta = read_json(session / "metadata.json", {})
        context = dict(available=False, reason=data["sensor"].get("samples_message")
                       or data["sensor"].get("sync_unavailable_reason")
                       or "Align this clip to a closed sensor recording to save labels.",
                       mapping=None, coverage=None)
        try:
            scale, offset, count = video_mapping(session, data["video"])
            context["mapping"] = dict(scale=scale, offset_s=offset, points=count)
            duration = meta.get("duration_s")
            if "closed_utc" in meta and finite_number(duration) and duration > 0:
                origin = data["source_pts_origin_s"]
                lo, hi = max(0, -offset / scale - origin), min(data["duration_s"], (duration - offset) / scale - origin)
                if lo < hi:
                    context.update(available=True, reason="", coverage=[lo, hi])
        except ValueError:
            pass
        return active, hashlib.sha256(content).hexdigest(), context

    def review():
        saved = read_json(review_path, dict(revision=0, intervals=[], settings=None))
        if saved.get("source", data["source"]) != data["source"]:
            raise ValueError("Source recording changed; saved edits refer to a different file.")
        active, token, context = label_state()
        intervals = {row["id"]: row for row in saved["intervals"]}
        for row in active.values():
            if row.get("video") != data["video"]:
                continue
            start, end = row.get("video_start_s"), row.get("video_end_s")
            if not finite_number(start) or not finite_number(end):
                continue
            origin = data["source_pts_origin_s"]
            pair = dict(row.get("review_interval") or dict(id=f"label-{row['id']}",
                        start_s=start - origin, end_s=end - origin))
            mapping = context["mapping"]
            stale = not mapping or any(not math.isclose(mapping["scale"] * t + mapping["offset_s"],
                        row[key], abs_tol=1e-6, rel_tol=0) for t, key in ((start, "start_s"), (end, "end_s")))
            stale = stale or row.get("review_source", data["source"]) != data["source"]
            pair.update(label_id=row["id"], label_start_s=start - origin, label_end_s=end - origin,
                        outcome=row["outcome"], trick=row.get("trick", ""), note=row.get("note", ""),
                        activity=row.get("activity", ""),
                        status="labeled", label_stale=stale)
            intervals[pair["id"]] = pair
        saved.update(intervals=sorted(intervals.values(), key=lambda row: row["start_s"]),
                     labels_revision=token, label_context=context)
        return saved

    def save_decision(value):
        if not isinstance(value, dict):
            raise ValueError("Expected a label decision.")
        previous = review()
        if (value.get("revision") != previous["revision"] or
                value.get("labels_revision") != previous["labels_revision"]):
            return 409, dict(error="Labels changed in another tab or command. Reload before saving.")
        # Validate pop/contact boundaries and settings with the original review validator.
        pair = value.get("interval")
        validate_review(dict(intervals=[pair], settings=value.get("settings")), data["duration_s"])
        identity = pair["id"]
        existing = next((row for row in previous["intervals"] if row["id"] == identity), None)
        decision = value.get("decision")
        row = None
        clean = {key: pair[key] for key in ("id", "start_s", "end_s", "status")}
        clean["note"] = pair.get("note", "")
        sources = pair.get("sources", [])
        if not isinstance(sources, list) or any(not isinstance(s, str) or s not in ("audio", "accel", "gyro") for s in sources):
            raise ValueError("Invalid suggestion sources.")
        if sources:
            clean.update(sources=list(dict.fromkeys(sources)), candidate_algorithm="audio-imu-pairs-v1")
        if decision == "skip":
            if existing and existing.get("label_id"):
                raise ValueError("This interval already has a label. Correct its outcome or boundaries and save it again.")
            clean["status"] = "rejected"
        elif decision == "label":
            context = previous["label_context"]
            if not context["available"]:
                raise ValueError(context["reason"])
            if value.get("mapping") != context["mapping"]:
                return 409, dict(error="Alignment changed. Reload and check the attempt window before saving.")
            start, end = pair.get("label_start_s"), pair.get("label_end_s")
            if not (finite_number(start) and finite_number(end) and 0 <= start < end <= data["duration_s"]):
                raise ValueError("Attempt window must have 0 <= start < finish <= video duration.")
            origin = data["source_pts_origin_s"]
            outcome = pair.get("outcome")
            row = build_label(argparse.Namespace(session=session, video=data["video"],
                start=start + origin, end=end + origin, outcome=outcome,
                trick="" if outcome == "background" else pair.get("trick", ""), note=clean["note"],
                activity=pair.get("activity"),
                replace=existing.get("label_id") if existing else None))
            row.update(review_interval=clean, review_source=data["source"],
                       review_algorithm=data["algorithm"], source_pts_origin_s=origin)
        else:
            raise ValueError("Choose label or skip.")
        # Labels are authoritative. The review sidecar stores settings and skips only;
        # reconstruct labeled rows from labels.jsonl after a crash or a CLI correction.
        intervals = [p for p in previous["intervals"] if p["id"] != identity and not p.get("label_id")]
        if decision == "skip":
            intervals.append(clean)
        saved = dict(schema=VERSION, revision=previous["revision"] + 1,
                     source=data["source"], video=data["video"], source_pts_origin_s=data["source_pts_origin_s"],
                     timeline="review_video_seconds", algorithm=data["algorithm"],
                     updated_utc=datetime.now(timezone.utc).isoformat(), intervals=intervals,
                     settings=value["settings"])
        write_json(review_path, saved)
        if row:
            append_json(session / "labels.jsonl", row)
        return 200, review()

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
                    with lock():
                        self.json_response(200, review())
                except ValueError as error:
                    self.json_response(409, dict(error=str(error)))
                return
            if self.path == "/api/labels":
                try:
                    with lock():
                        saved = review()
                        if any(row.get("label_stale") for row in saved["intervals"]):
                            raise ValueError("Alignment changed. Review and save the flagged labels before downloading.")
                        active, _, _ = label_state()
                        labels = [row for row in active.values() if row.get("video") == data["video"]]
                        self.json_response(200, dict(labels=sorted(labels, key=lambda row: row["start_s"])))
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
            if self.path not in ("/api/review", "/api/sync", "/api/label"):
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Expected JSON, at most 1 MiB.")
                value = json.loads(self.rfile.read(size))
                if self.path == "/api/label":
                    with lock():
                        code, result = save_decision(value)
                    self.json_response(code, result)
                    return
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
                    with lock():
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
                with lock():
                    previous = review()
                    if any(row.get("label_id") for row in previous["intervals"]):
                        self.json_response(409, dict(error="Use the label controls to revise saved labels."))
                        return
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
    parser.add_argument("--attempt-model", type=Path, default=DEFAULT_ATTEMPT_MODEL,
                        help="Trained attempt model JSON (default: .build/attempts-v1/model.json)")
    args = parser.parse_args()
    try:
        directory, media, data = prepare(args.video, args.rebuild, args.attempt_model)
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
