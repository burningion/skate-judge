#!/usr/bin/env python3
"""Verify an iPhone session export and decode it into a new desktop session.

python3 capture/import_mobile.py ~/Downloads/session-ID.zip --output sessions/iphone-001
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import statistics
import subprocess
import tempfile
import zipfile

if __package__:
    from .onboard_log import import_log, read_packets
else:
    from onboard_log import import_log, read_packets


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def safe_name(name):
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError("Export contains an unsafe filename.")
    return name


def unpack(source, destination):
    """Copy flat session files; reject traversal, symlinks and ambiguous archive roots."""
    if source.is_dir():
        for file in source.iterdir():
            if file.is_symlink() or not file.is_file():
                raise ValueError("Session export must contain only regular files.")
            shutil.copyfile(file, destination / safe_name(file.name))
        return
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Export archive contains an unsafe path or symlink.")
        roots = [PurePosixPath(m.filename).parent for m in members if PurePosixPath(m.filename).name == "mobile_session.json"]
        if len(roots) != 1:
            raise ValueError("Expected exactly one mobile session in the export.")
        root = roots[0]
        seen = set()
        for member in members:
            path = PurePosixPath(member.filename)
            if member.is_dir() or path.parts[0] == "__MACOSX":
                continue
            if path.parent != root:
                raise ValueError("Unexpected nested or unrelated file in session export.")
            name = safe_name(path.name)
            if name in seen:
                raise ValueError("Duplicate export filename.")
            seen.add(name)
            if member.file_size > shutil.disk_usage(destination).free:
                raise ValueError("Not enough disk space for this export.")
            with archive.open(member) as reader, (destination / name).open("xb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)


def verify_export(directory):
    manifest = json.loads((directory / "export_manifest.json").read_text())
    mobile = json.loads((directory / "mobile_session.json").read_text())
    if manifest.get("schema") != 1 or mobile.get("schema") != 1:
        raise ValueError("Unsupported mobile export version.")
    identity = mobile.get("id", "")
    if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity) or manifest.get("session_id") != identity:
        raise ValueError("Invalid or inconsistent session identity.")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ValueError("Missing exported file inventory.")
    seen = set()
    for entry in entries:
        name = safe_name(entry.get("path"))
        if name in seen or name == "export_manifest.json":
            raise ValueError("Duplicate or recursive export inventory.")
        seen.add(name)
        file = directory / name
        if not file.is_file() or file.stat().st_size != entry.get("bytes") or digest(file) != entry.get("sha256"):
            raise ValueError(f"Export checksum or size mismatch: {name}")
    actual = {file.name for file in directory.iterdir()} - {"export_manifest.json"}
    if actual != seen or not {"metadata.json", "mobile_session.json"}.issubset(seen):
        raise ValueError("Export inventory does not match session files.")
    metadata = json.loads((directory / "metadata.json").read_text())
    if metadata.get("onboard_id") != identity:
        raise ValueError("Desktop and mobile recording identities differ.")
    raw = directory / f"onboard-{identity}.bin"
    if raw.name not in seen or mobile.get("downloadState") != "verified":
        raise ValueError("Motion download is not verified. Recover the session in the app before importing; the export is retained.")
    packets, _, _ = read_packets(raw, allow_incomplete=True)
    raw_meta = json.loads(packets[0][2])
    if (raw_meta.get("id") != identity or raw_meta.get("boot_id") != mobile.get("bootID")
            or raw_meta.get("boot_id") != metadata.get("boot_id")
            or raw_meta.get("storage", "flash") != mobile.get("storage")
            or raw_meta.get("storage", "flash") != metadata.get("onboard_storage")):
        raise ValueError("Raw log identity, boot, or storage differs from its manifest.")
    info = mobile.get("fileInfo") or {}
    import zlib
    crc = 0
    with raw.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            crc = zlib.crc32(chunk, crc)
    if (info.get("id") != identity or info.get("storage") != mobile.get("storage")
            or info.get("bytes") != raw.stat().st_size or info.get("crc32") != f"{crc:08x}"):
        raise ValueError("Raw file differs from the board's verified transfer metadata.")
    video = mobile.get("videoFile")
    if video and safe_name(video) not in seen:
        raise ValueError("The session's original video is missing.")
    return mobile, raw


def inspect_video(path, requested_fps=None):
    """Inspect original PTS without generating a retimed or frame-reduced copy."""
    if not shutil.which("ffprobe"):
        return {"available": False, "reason": "Install ffmpeg/ffprobe to measure recorded frame cadence."}
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True)
    if probe.returncode:
        return {"available": False, "reason": "Video is incomplete or unreadable; original retained.", "detail": probe.stderr[-1000:]}
    metadata = json.loads(probe.stdout)
    video = next((s for s in metadata["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        return {"available": False, "reason": "No video track; original retained."}
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                             "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)], capture_output=True, text=True)
    if result.returncode:
        return {"available": False, "reason": "Could not index all video frames; original retained."}
    times = [float(frame["best_effort_timestamp_time"]) for frame in json.loads(result.stdout)["frames"] if "best_effort_timestamp_time" in frame]
    times = [value for value in times if math.isfinite(value)]
    intervals = [b - a for a, b in zip(times, times[1:])]
    positive = [dt for dt in intervals if dt > 0]
    measured = 1 / statistics.median(positive) if positive else None
    expected = 1 / requested_fps if isinstance(requested_fps, (int, float)) and requested_fps > 0 else None
    return dict(available=True, frames=len(times), codec=video.get("codec_name"),
                audio_present=any(s["codec_type"] == "audio" for s in metadata["streams"]),
                duration_s=float(metadata["format"].get("duration", 0)), measured_median_fps=measured,
                configured_fps=requested_fps, nonincreasing_timestamps=sum(dt <= 0 for dt in intervals),
                gaps_over_1_5_frame_intervals=sum(dt > expected * 1.5 for dt in intervals) if expected else None,
                note="Cadence measures recorded frames; it does not establish sensor alignment.")


def import_bundle(source, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise ValueError("Choose a new output directory; existing sessions are never overwritten.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mobile-import-", dir=output.parent) as temporary:
        staging = Path(temporary) / "session"
        staging.mkdir()
        unpack(source, staging)
        mobile, raw = verify_export(staging)
        metadata = import_log(raw, staging, allow_incomplete=True)
        if mobile.get("videoFile"):
            report = inspect_video(staging / mobile["videoFile"], mobile.get("capture", {}).get("configured_fps"))
            (staging / "video_probe.json").write_text(json.dumps(report, indent=2) + "\n")
        # The incoming manifest describes pre-import metadata; preserve it as provenance.
        (staging / "export_manifest.json").rename(staging / "source_export_manifest.json")
        if output.exists():
            raise ValueError("Output appeared during import; choose a new directory.")
        os.rename(staging, output)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Exported ZIP or session folder")
    parser.add_argument("--output", required=True, type=Path, help="New desktop session directory")
    args = parser.parse_args()
    try:
        meta = import_bundle(args.source, args.output)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Import failed: {error}\n")
    print(f"Imported {meta['samples']:,} samples into {args.output}")
    print("Quality: " + (", ".join(meta["onboard_quality"]["issues"]) or "passed"))
    print("Open the original video in viz/trick_review.py and match the beginning/ending LED flashes.")


if __name__ == "__main__":
    main()
