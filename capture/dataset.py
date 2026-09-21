#!/usr/bin/env python3
"""Inventory reviewed sessions and export immutable, dependency-free IMU datasets."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile

if __package__:
    from .session import OUTCOMES, fit_video_points
else:
    from session import OUTCOMES, fit_video_points

CHANNELS = ("t_s", "ax", "ay", "az", "gx", "gy", "gz")
MAX_GAP_S = 0.03


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def jsonl(content):
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def load_samples(content):
    rows = [tuple(float(row[k]) for k in CHANNELS)
            for row in csv.DictReader(io.StringIO(content.decode("utf-8")))]
    if (len(rows) < 2 or any(not all(map(math.isfinite, row)) for row in rows)
            or any(b[0] <= a[0] for a, b in zip(rows, rows[1:]))):
        raise ValueError("Samples need finite channels and strictly increasing timestamps")
    return rows


def inspect_session(path):
    """Read one snapshot; latest revision per label ID is authoritative."""
    content = {name: (path / name).read_bytes() if (path / name).exists() else b""
               for name in ("metadata.json", "labels.jsonl", "video_sync.jsonl", "samples.csv")}
    meta = json.loads(content["metadata.json"])
    history = jsonl(content["labels.jsonl"])
    latest = {row["id"]: row for row in history}
    if any(not isinstance(identity, str) or not identity for identity in latest):
        raise ValueError("Labels need nonempty string IDs")
    syncs = {(row["video"], row["sync_id"]): row for row in jsonl(content["video_sync.jsonl"])}
    samples = load_samples(content["samples.csv"])
    times = [row[0] for row in samples]
    issues = []
    if meta.get("synthetic") is not False:
        issues.append("synthetic or unverified recording")
    if not meta.get("closed_utc"):
        issues.append("recording not closed")
    quality = meta.get("onboard_quality", {})
    if meta.get("transport") in ("onboard_flash", "onboard_sd") and quality.get("usable") is not True:
        issues.append("onboard quality: " + ", ".join(quality.get("issues") or ["not verified usable"]))
    rows, windows = [], {}
    for identity, label in sorted(latest.items()):
        reasons, warnings = list(issues), []
        start, end = label.get("start_s"), label.get("end_s")
        valid_range = finite(start) and finite(end) and 0 <= start < end
        if not valid_range:
            reasons.append("invalid label times")
        if label.get("outcome") not in OUTCOMES:
            reasons.append("invalid outcome")
        if label.get("source") != "video_human" or not label.get("video"):
            reasons.append("needs video review")
        else:
            video = label["video"]
            try:
                scale, offset, points = fit_video_points(
                    [row for (name, _), row in syncs.items() if name == video])
                if points == 1:
                    warnings.append("only one flash match; drift is unmeasured")
                if not valid_range or any(
                    not finite(label.get(key)) or not math.isclose(
                        scale * label[key] + offset, sensor, rel_tol=0, abs_tol=1e-6)
                    for key, sensor in (("video_start_s", start), ("video_end_s", end))
                ):
                    reasons.append("stale alignment; review and save label again")
            except (ValueError, KeyError, TypeError, ZeroDivisionError):
                reasons.append("missing or invalid video alignment")
            source = path / video
            if not source.is_file():
                reasons.append("source video missing")
            elif label.get("review_source"):
                stat = source.stat()
                if label["review_source"] != dict(name=source.name, bytes=stat.st_size, mtime_ns=stat.st_mtime_ns):
                    reasons.append("source video changed; review label again")
        window = []
        if valid_range:
            if not times[0] <= start < end <= times[-1]:
                reasons.append("label outside sensor coverage")
            lo, hi = bisect_left(times, start), bisect_left(times, end)
            window = samples[lo:hi]  # Half-open ranges never duplicate touching boundaries.
            if len(window) < 2:
                reasons.append("fewer than two samples")
            # Include gaps crossing either boundary, even if no sample lies inside the gap.
            context = times[max(0, lo - 1):hi + 1]
            gaps = [b - a for a, b in zip(context, context[1:]) if a < end and b > start]
            if gaps and max(gaps) > MAX_GAP_S + 1e-9:
                reasons.append("sensor gap over 30 ms")
            if any(all(v == 0 for v in row[1:4]) for row in window):
                reasons.append("all-zero acceleration sample")
            # Raw full-scale thresholds for this project's LSM6DSO32 configuration.
            accel_limit = 32767 * 0.000976 * 9.80665 * 0.99
            gyro_limit = math.radians(32767 * 0.07) * 0.99
            if any(any(abs(v) >= accel_limit for v in row[1:4])
                   or any(abs(v) >= gyro_limit for v in row[4:]) for row in window):
                reasons.append("sensor clipping near full scale")
            if any(other["id"] != identity and finite(other.get("start_s"))
                   and finite(other.get("end_s")) and start < other["end_s"]
                   and end > other["start_s"] for other in latest.values()):
                reasons.append("overlapping labels")
        if label.get("outcome") not in ("background", "unknown") and not label.get("trick", "").strip():
            warnings.append("trick name missing")
        row = dict(example_id=f"{path.name}/{identity}", session_id=path.name,
                   rider=meta.get("rider"), board=meta.get("board"), label=label,
                   eligible=not reasons, exclusion_reasons=reasons, warnings=warnings,
                   sample_count=len(window),
                   outcome_training=not reasons and label.get("outcome") != "unknown")
        rows.append(row)
        if row["eligible"]:
            windows[row["example_id"]] = window
    for name, data in content.items():
        current = (path / name).read_bytes() if (path / name).exists() else b""
        if current != data:
            raise ValueError("Session changed during scan; rerun after saving review")
    info = dict(session_id=path.name, source=str(path.resolve()), issues=issues,
                label_revisions=len(history), unique_labels=len(latest),
                eligible_attempts=sum(r["outcome_training"] and r["label"]["outcome"] != "background" for r in rows),
                background=sum(r["outcome_training"] and r["label"]["outcome"] == "background" for r in rows),
                unknown=sum(r["eligible"] and r["label"]["outcome"] == "unknown" for r in rows),
                excluded=sum(not r["eligible"] for r in rows),
                source_sha256={name: hashlib.sha256(data).hexdigest() for name, data in content.items()})
    return info, rows, windows


def collect(root, pattern="a7s-*"):
    paths = sorted(p for p in root.glob(pattern) if p.is_dir())
    if not paths:
        raise ValueError(f"No sessions match {root / pattern}")
    sessions, labels, windows = [], [], {}
    for path in paths:
        try:
            info, rows, data = inspect_session(path)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
            info = dict(session_id=path.name, issues=[f"cannot read session: {error}"],
                        error=True, unique_labels=None, eligible_attempts=0, background=0, unknown=0, excluded=0)
            rows, data = [], {}
        sessions.append(info)
        labels.extend(rows)
        windows.update(data)
    counts = Counter((r["label"].get("trick", ""), r["label"]["outcome"])
                     for r in labels if r["outcome_training"])
    report = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
                  sessions_root=str(root.resolve()), pattern=pattern, sessions=sessions,
                  unique_labels=sum(s["unique_labels"] or 0 for s in sessions),
                  eligible_attempts=sum(s["eligible_attempts"] for s in sessions),
                  background=sum(s["background"] for s in sessions),
                  unknown=sum(s["unknown"] for s in sessions),
                  excluded=sum(s["excluded"] for s in sessions),
                  counts=[dict(trick=trick, outcome=outcome, count=count)
                          for (trick, outcome), count in sorted(counts.items())])
    return report, labels, windows


def print_report(report):
    print("Session               Saved  Attempts  Background  Unknown  Excluded")
    for row in report["sessions"]:
        saved = "?" if row["unique_labels"] is None else str(row["unique_labels"])
        print(f"{row['session_id']:<21} {saved:>5} {row['eligible_attempts']:>9}"
              f" {row['background']:>11} {row['unknown']:>8} {row['excluded']:>9}")
        for issue in row["issues"]:
            print(f"  {issue}")
    print(f"\n{report['eligible_attempts']} eligible labeled attempts; {report['background']} background ranges; "
          f"{report['unknown']} unknown outcomes; {report['excluded']} excluded labels.")
    for row in report["counts"]:
        print(f"  {row['trick'] or '(background / unnamed)'} / {row['outcome']}: {row['count']}")


def build(output, report, labels, windows):
    if output.exists():
        raise ValueError(f"Snapshot already exists: {output}. Choose a new version directory.")
    if any(s.get("error") for s in report["sessions"]):
        raise ValueError("Cannot build with unreadable sessions; fix the reported errors first")
    if not windows:
        raise ValueError("No eligible reviewed windows to export")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dataset-", dir=output.parent) as temp:
        staging = Path(temp) / "snapshot"
        staging.mkdir()
        (staging / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        (staging / "manifest.jsonl").write_text(
            "".join(json.dumps(row, allow_nan=False) + "\n" for row in labels))
        with (staging / "samples.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("example_id", "relative_s", *CHANNELS))
            for row in labels:
                for sample in windows.get(row["example_id"], []):
                    writer.writerow((row["example_id"], sample[0] - row["label"]["start_s"], *sample))
        (staging / "README.txt").write_text(
            "Immutable labeled IMU snapshot. Original video/raw recordings remain in sessions/.\n"
            "manifest.jsonl: latest labels, session/rider groups, eligibility and exclusions.\n"
            "samples.csv: eligible windows joined by example_id; [start_s, end_s), original sampling.\n"
            "Channels: t_s in sensor seconds, acceleration in m/s^2, gyro in rad/s.\n"
            "Unknown outcomes are preserved; filter outcome_training=true for outcome supervision.\n"
            "report.json: counts and SHA-256 hashes of the source annotation/sensor files.\n"
            "Split by whole session (or rider), never random samples from an attempt.\n"
            "Eligibility checks data integrity, not whether a label is semantically correct.\n")
        # Reserve the destination exclusively so concurrent builds cannot replace a snapshot.
        output.mkdir()
        staging.replace(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "build"), nargs="?", default="status")
    parser.add_argument("--sessions", type=Path, default=Path("sessions"))
    parser.add_argument("--pattern", default="a7s-*", help="Session directory glob (default: a7s-*)")
    parser.add_argument("--output", type=Path, help="New snapshot directory, required for build")
    args = parser.parse_args(argv)
    if args.command == "build" and args.output is None:
        parser.error("build requires --output")
    if args.command == "status" and args.output is not None:
        parser.error("--output is only used by build")
    try:
        report, labels, windows = collect(args.sessions, args.pattern)
        print_report(report)
        for row in labels:
            for issue in row["exclusion_reasons"] + row["warnings"]:
                print(f"  {row['example_id']}: {issue}")
        if args.command == "build":
            build(args.output, report, labels, windows)
            print(f"\nSaved snapshot: {args.output}")
        return int(any(s.get("error") for s in report["sessions"]))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Dataset error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
