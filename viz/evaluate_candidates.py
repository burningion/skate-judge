#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26,<3", "scipy>=1.12,<2"]
# ///
"""Compare audio candidate filters using a dataset snapshot and cached review audio.

uv run viz/evaluate_candidates.py datasets/a7s-v3 --output /tmp/candidate-audit.json
Requires Node.js. Reads source sessions but never changes labels or review state.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from viz.trick_review import sensor_context


def load_clips(dataset, sessions_root=None):
    report = json.loads((dataset / "report.json").read_text())
    root = sessions_root or Path(report["sessions_root"])
    rows = [json.loads(line) for line in (dataset / "manifest.jsonl").read_text().splitlines() if line.strip()]
    clips = []
    for session in report["sessions"]:
        labels = [row["label"] for row in rows if row["session_id"] == session["session_id"] and row["eligible"]]
        if not labels:
            continue
        directory = root / session["session_id"]
        # Use snapshot labels, and require the same sensor stream and alignment.
        # New human label revisions do not invalidate this historical comparison.
        for name in ("samples.csv", "metadata.json", "video_sync.jsonl"):
            actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
            if actual != session["source_sha256"][name]:
                raise ValueError(f"{directory / name} changed since this snapshot; use the matching source files or a new dataset.")
        for video in sorted({label["video"] for label in labels}):
            source = directory / video
            analysis_path = directory / "review" / source.stem / "analysis.json"
            if not analysis_path.exists():
                raise ValueError(f"Missing {analysis_path}; open the clip in the review tool to prepare audio first.")
            data = json.loads(analysis_path.read_text())
            stat = source.stat()
            identity = dict(name=source.name, bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
            if data["source"] != identity:
                raise ValueError(f"Cached review audio is stale for {source}; rebuild it first.")
            selected = [label for label in labels if label["video"] == video]
            if any(label.get("review_source", identity) != identity for label in selected):
                raise ValueError(f"Source video no longer matches snapshot label provenance: {source}")
            sensor = sensor_context(source)
            clips.append(dict(session=session["session_id"], video=video, audio=data["audio"],
                motion=sensor["motion"], mapping=sensor["mapping"], origin=data["source_pts_origin_s"],
                duration=data["duration_s"], labels=selected))
    if not clips:
        raise ValueError("No eligible reviewed clips in this dataset.")
    return clips


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--sessions-root", type=Path)
    parser.add_argument("--output", type=Path, help="Write the full JSON audit; otherwise print it")
    args = parser.parse_args()
    try:
        clips = load_clips(args.dataset, args.sessions_root)
        base = dict(threshold=.65, min_gap=.18, max_gap=.9, show_filtered=False)
        settings = [dict(base, candidate_mode="audio")]
        settings.extend(dict(base, candidate_mode="audio_filtered", filter_threshold=threshold) for threshold in (.25, .4, .65))
        result = subprocess.run(["node", str(Path(__file__).with_suffix(".mjs"))],
            input=json.dumps(dict(clips=clips, settings=settings), allow_nan=False),
            capture_output=True, text=True, check=True)
        audit = json.loads(result.stdout)
        audit["dataset"] = args.dataset.name
        audit["snapshot_manifest_sha256"] = hashlib.sha256((args.dataset / "manifest.jsonl").read_bytes()).hexdigest()
        content = json.dumps(audit, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(content)
            print(f"Wrote {args.output}")
            for row in audit["results"]:
                options, totals = row["settings"], row["totals"]
                name = options["candidate_mode"] + (f" {options['filter_threshold']}" if "filter_threshold" in options else "")
                print(f"{name}: {totals['candidates']} candidates; {totals['matched_attempts']}/{totals['attempts_with_pop']} marked pops; "
                      f"{totals['background_candidates']} in reviewed background; {totals['unchecked_candidates']} kept without IMU coverage")
        else:
            print(content, end="")
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
