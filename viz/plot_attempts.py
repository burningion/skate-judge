# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26,<3", "matplotlib>=3.8,<4"]
# ///
"""Export comparable raw IMU images around saved pop/contact markers.

Example: uv run viz/plot_attempts.py sessions/a7s-001 --video C0642.MP4
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".build/matplotlib"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
import numpy as np  # noqa: E402

from capture.session import video_mapping  # noqa: E402
from viz.overlay_motion import G, MAX_GAP, check_alignment, latest_labels, load_samples  # noqa: E402

INK = "#20313c"
MUTED = "#596c79"
ACCEL = "#087f8c"
GYRO = "#b27013"
AXES = ["#2166ac", "#c27d12", "#8b55a2"]
POP = "#31704f"
CONTACT = "#b4492c"


def stamp(seconds):
    return f"{int(seconds // 60)}:{seconds % 60:06.3f}"


def dataset_note(attempts):
    counts = Counter((a["label"]["trick"], a["label"]["outcome"]) for a in attempts)
    groups = ", ".join(
        f"{n} {trick} {outcome}{'s' if n != 1 else ''}"
        for (trick, outcome), n in counts.items()
    )
    by_trick = {}
    for trick, outcome in counts:
        by_trick.setdefault(trick, set()).add(outcome)
    confounded = len({outcome for _, outcome in counts}) > 1 and all(
        len(outcomes) == 1 for outcomes in by_trick.values()
    )
    return f"{groups}. " + (
        "These groups differ in both trick type and outcome."
        if confounded
        else "Exploratory comparison of saved attempts."
    )


def load_attempts(session, video, before, after):
    samples = load_samples(session / "samples.csv")
    scale, offset, points = video_mapping(session, video)
    labels = latest_labels(session, video)
    check_alignment(labels, scale, offset, (samples[0, 0], samples[-1, 0]))
    video_times = (samples[:, 0] - offset) / scale
    accel = np.linalg.norm(samples[:, 1:4], axis=1)
    gyro = np.linalg.norm(samples[:, 4:7], axis=1)
    attempts = []
    for row in labels:
        if row["outcome"] == "background":
            continue
        marker = row.get("review_interval", {})
        origin = row.get("source_pts_origin_s", 0)
        if not all(k in marker for k in ("start_s", "end_s")):
            raise ValueError(f"Missing pop/contact markers for {row['id']}")
        pop, contact = marker["start_s"] + origin, marker["end_s"] + origin
        if not row["video_start_s"] <= pop < contact <= row["video_end_s"]:
            raise ValueError(f"Markers outside saved attempt {row['id']}")
        if pop - before < video_times[0] or contact + after > video_times[-1]:
            raise ValueError(f"Incomplete plot coverage for {row['id']}")
        attempts.append(
            dict(number=len(attempts) + 1, label=row, pop=pop, contact=contact)
        )
    if not attempts:
        raise ValueError("No saved attempts for this video.")
    return (
        samples,
        video_times,
        accel,
        gyro,
        attempts,
        dict(scale=scale, offset=offset, points=points),
    )


def series(ax, times, values, color, label=None, width=1.2):
    # Insert an actual break rather than drawing through missing samples.
    indexes = np.flatnonzero(np.diff(times) > MAX_GAP)
    if len(indexes):
        times = np.insert(times, indexes + 1, np.nan)
        values = np.insert(values, indexes + 1, np.nan)
    ax.plot(
        times, values, color=color, linewidth=width, label=label, solid_capstyle="round"
    )


def style(ax, before, after, limits):
    ax.set_xlim(-before, after)
    ax.set_ylim(*limits)
    ax.set_facecolor("white")
    ax.grid(color="#dfe6eb", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    for name in ("bottom", "left"):
        ax.spines[name].set_color("#acbbc5")
    ax.tick_params(colors=MUTED, labelsize=9)


def markers(ax, attempt, event, before, after):
    center = attempt[event]
    ax.axvspan(
        attempt["pop"] - center,
        attempt["contact"] - center,
        color="#2f7961",
        alpha=0.055,
        linewidth=0,
    )
    for key, color in (("pop", POP), ("contact", CONTACT)):
        position = attempt[key] - center
        if -before <= position <= after:
            ax.axvline(
                position,
                color=color,
                linewidth=1.1,
                linestyle="-" if key == event else "--",
                alpha=0.85,
            )


def save(fig, path):
    fig.savefig(path, dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)


def individual(folder, samples, times, accel, gyro, attempt, args, limits):
    label = attempt["label"]
    fig, axes = plt.subplots(4, 2, figsize=(12, 11), sharex="col")
    fig.subplots_adjust(
        left=0.085, right=0.975, top=0.84, bottom=0.13, hspace=0.31, wspace=0.22
    )
    fig.suptitle(
        f"{attempt['number']:02d}  /  {label['trick'].upper()}  /  {label['outcome'].upper()}",
        x=0.085,
        y=0.975,
        ha="left",
        fontsize=23,
        fontweight="bold",
        color=INK,
    )
    fig.text(0.085, 0.925, "POP AND CONTACT IN ISOLATION", fontsize=12, color=MUTED)
    fig.text(
        0.085,
        0.895,
        f"{args.video}  ·  label {label['id'][:8]}  ·  raw samples, no smoothing",
        fontsize=10,
        color=MUTED,
    )
    fig.legend(
        [Line2D([0], [0], color=c, linewidth=2) for c in AXES],
        ["Sensor X", "Sensor Y", "Sensor Z"],
        loc="upper right",
        bbox_to_anchor=(0.977, 0.943),
        ncol=3,
        frameon=False,
        fontsize=10,
    )
    for column, event in enumerate(("pop", "contact")):
        center = attempt[event]
        mask = (times >= center - args.before) & (times <= center + args.after)
        relative = times[mask] - center
        values = [accel[mask], samples[mask, 1:4], gyro[mask], samples[mask, 4:7]]
        ranges = [
            (0, limits[0]),
            (-limits[1], limits[1]),
            (0, limits[2]),
            (-limits[3], limits[3]),
        ]
        names = [
            "Acceleration magnitude\nm/s²",
            "Acceleration XYZ\nm/s²",
            "Gyro magnitude\nrad/s",
            "Gyro XYZ\nrad/s",
        ]
        axes[0, column].set_title(
            f"{event.upper()}  ·  video {stamp(center)}",
            loc="left",
            fontsize=12,
            color=INK,
            pad=12,
        )
        for row, (value, bounds, name) in enumerate(zip(values, ranges, names)):
            ax = axes[row, column]
            style(ax, args.before, args.after, bounds)
            markers(ax, attempt, event, args.before, args.after)
            if value.ndim == 1:
                series(ax, relative, value, ACCEL if row == 0 else GYRO)
                if row == 0:
                    ax.axhline(G, color="#899aa5", linewidth=0.8, linestyle=":")
            else:
                ax.axhline(0, color="#a1afb8", linewidth=0.6)
                for axis in range(3):
                    series(ax, relative, value[:, axis], AXES[axis], width=0.95)
            if column == 0:
                ax.set_ylabel(name, fontsize=10, color=INK)
            if row == 3:
                ax.set_xlabel(
                    f"Seconds relative to saved {event}", fontsize=10, color=INK
                )
    fig.text(
        0.085,
        0.075,
        "Solid vertical line = saved event at 0 s. Dashed line = the other event. Shading = saved pop-to-contact interval.",
        fontsize=9,
        color=MUTED,
    )
    fig.text(
        0.085,
        0.05,
        "X runs nose–tail; Y runs across the deck. XYZ stay in the sensor frame. Acceleration includes gravity.",
        fontsize=9,
        color=MUTED,
    )
    fig.text(
        0.085,
        0.025,
        "All attempts use the same axis scales. Pop-to-contact time is a label interval, not a validated flight-time measurement.",
        fontsize=9,
        color=MUTED,
    )
    filename = f"{attempt['number']:02d}-{label['trick']}-{label['outcome']}-{label['id'][:8]}.png"
    save(fig, folder / filename)
    return filename


def overview(folder, times, accel, gyro, attempts, event, args, limits):
    fig, axes = plt.subplots(
        len(attempts),
        2,
        figsize=(14, 2 + 1.6 * len(attempts)),
        squeeze=False,
        sharex=True,
    )
    fig.subplots_adjust(
        left=0.17, right=0.975, top=0.90, bottom=0.065, hspace=0.33, wspace=0.10
    )
    fig.suptitle(
        f"{event.upper()} COMPARISON",
        x=0.045,
        y=0.975,
        ha="left",
        fontsize=25,
        fontweight="bold",
        color=INK,
    )
    fig.text(
        0.045,
        0.932,
        "Same time window and scales on every row  ·  raw magnitude  ·  saved marker at 0 s",
        fontsize=12,
        color=MUTED,
    )
    axes[0, 0].set_title(
        "ACCELEROMETER  /  m/s²", fontsize=12, color=ACCEL, loc="left", pad=12
    )
    axes[0, 1].set_title(
        "GYROSCOPE  /  rad/s", fontsize=12, color=GYRO, loc="left", pad=12
    )
    for row, attempt in enumerate(attempts):
        label = attempt["label"]
        center = attempt[event]
        mask = (times >= center - args.before) & (times <= center + args.after)
        for column, (value, limit, color) in enumerate(
            ((accel, limits[0], ACCEL), (gyro, limits[2], GYRO))
        ):
            ax = axes[row, column]
            style(ax, args.before, args.after, (0, limit))
            markers(ax, attempt, event, args.before, args.after)
            series(ax, times[mask] - center, value[mask], color)
            if column == 0:
                ax.axhline(G, color="#899aa5", linewidth=0.7, linestyle=":")
                ax.text(
                    -0.30,
                    0.65,
                    f"{attempt['number']:02d}  {label['trick'].upper()}\n{label['outcome'].upper()}",
                    transform=ax.transAxes,
                    fontsize=11,
                    fontweight="bold",
                    color=POP if label["outcome"] == "make" else CONTACT,
                    va="center",
                )
                ax.text(
                    -0.30,
                    0.12,
                    f"video {stamp(center)}",
                    transform=ax.transAxes,
                    fontsize=9,
                    color=MUTED,
                )
            if row == len(attempts) - 1:
                ax.set_xlabel(
                    f"Seconds relative to saved {event}", fontsize=11, color=INK
                )
    fig.text(
        0.045,
        0.022,
        dataset_note(attempts),
        fontsize=11,
        color=MUTED,
    )
    name = f"{event}-overview.png"
    save(fig, folder / name)
    return name


def outcomes(folder, times, accel, gyro, attempts, args, limits):
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True, sharey="row")
    fig.subplots_adjust(
        left=0.085, right=0.975, top=0.82, bottom=0.18, hspace=0.19, wspace=0.09
    )
    fig.suptitle(
        "MAKES VS BAILS",
        x=0.085,
        y=0.97,
        ha="left",
        fontsize=27,
        fontweight="bold",
        color=INK,
    )
    fig.text(
        0.085,
        0.912,
        "Aligned to the saved contact marker  ·  common scales  ·  one trace per attempt",
        fontsize=12,
        color=MUTED,
    )
    colors = {
        "make": ["#084c61", "#167d9a", "#3e968b", "#638440", "#89aa4d"],
        "bail": ["#952f38", "#d45c37", "#da922f"],
    }
    for column, outcome in enumerate(("make", "bail")):
        selected = [a for a in attempts if a["label"]["outcome"] == outcome]
        tricks = Counter(a["label"]["trick"] for a in selected)
        title = " / ".join(f"{count} {name.upper()}" for name, count in tricks.items())
        axes[0, column].set_title(
            f"{outcome.upper()}S  ·  {title}",
            loc="left",
            fontsize=13,
            color=INK,
            pad=16,
        )
        for row, (values, bounds) in enumerate(
            ((accel, (0, limits[0])), (gyro, (0, limits[2])))
        ):
            ax = axes[row, column]
            style(ax, args.before, args.after, bounds)
            ax.axvline(0, color=INK, linewidth=1.1, linestyle="--")
            if row == 0:
                ax.axhline(G, color="#a0adb5", linewidth=0.8, linestyle=":")
            for i, attempt in enumerate(selected):
                center = attempt["contact"]
                mask = (times >= center - args.before) & (times <= center + args.after)
                series(
                    ax,
                    times[mask] - center,
                    values[mask],
                    colors[outcome][i % len(colors[outcome])],
                    label=f"{attempt['number']:02d}  ·  video {stamp(center)}",
                    width=1.25,
                )
            if row == 0:
                ax.legend(
                    loc="upper right",
                    fontsize=9,
                    facecolor="white",
                    edgecolor="#dfe6eb",
                    framealpha=0.95,
                )
            else:
                ax.set_xlabel(
                    "Seconds relative to saved contact", fontsize=11, color=INK
                )
        if column == 0:
            axes[0, column].set_ylabel(
                "Acceleration magnitude (m/s²)", fontsize=11, color=INK
            )
            axes[1, column].set_ylabel(
                "Gyroscope magnitude (rad/s)", fontsize=11, color=INK
            )
    fig.text(0.085, 0.075, dataset_note(attempts), fontsize=11, color=MUTED)
    fig.text(
        0.085,
        0.039,
        "Raw signals, no smoothing or peak alignment. Contact during a bail does not imply a successful rider landing.",
        fontsize=10,
        color=MUTED,
    )
    name = "makes-vs-bails.png"
    save(fig, folder / name)
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--video", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--before", type=float, default=0.5)
    parser.add_argument("--after", type=float, default=1.0)
    args = parser.parse_args(argv)
    if any(not math.isfinite(v) or v <= 0 for v in (args.before, args.after)):
        parser.error("Use positive, finite window lengths.")
    session = args.session.resolve()
    folder = args.output or session / "plots" / f"{Path(args.video).stem}-pop-contact"
    folder.mkdir(parents=True, exist_ok=True)
    samples, times, accel, gyro, attempts, mapping = load_attempts(
        session, args.video, args.before, args.after
    )
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "figure.facecolor": "#f7fafc",
            "savefig.facecolor": "#f7fafc",
        }
    )
    mask = np.zeros(len(samples), dtype=bool)
    for attempt in attempts:
        for event in ("pop", "contact"):
            mask |= (times >= attempt[event] - args.before) & (
                times <= attempt[event] + args.after
            )
    limits = [
        max(25, math.ceil(np.max(accel[mask]) / 25) * 25),
        max(25, math.ceil(np.max(abs(samples[mask, 1:4])) / 25) * 25),
        max(5, math.ceil(np.max(gyro[mask]) / 5) * 5),
        max(5, math.ceil(np.max(abs(samples[mask, 4:7])) / 5) * 5),
    ]
    files = [
        overview(folder, times, accel, gyro, attempts, event, args, limits)
        for event in ("contact", "pop")
    ]
    outcome_file = outcomes(folder, times, accel, gyro, attempts, args, limits)
    metrics = []
    for attempt in attempts:
        files.append(
            individual(folder, samples, times, accel, gyro, attempt, args, limits)
        )
        contact = attempt["contact"]
        nearby = np.flatnonzero((times >= contact - 0.15) & (times <= contact + 0.30))
        peak = nearby[np.argmax(accel[nearby])]
        post = (times >= contact + 0.2) & (times <= contact + 1)
        metrics.append(
            dict(
                attempt=attempt["number"],
                label_id=attempt["label"]["id"],
                trick=attempt["label"]["trick"],
                outcome=attempt["label"]["outcome"],
                pop_video_s=attempt["pop"],
                contact_video_s=contact,
                peak_near_contact_ms2=float(accel[peak]),
                peak_offset_ms=float((times[peak] - contact) * 1000),
                post_contact_gyro_median_rad_s=float(np.median(gyro[post])),
            )
        )
    with (folder / "metrics.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(metrics[0]))
        writer.writeheader()
        writer.writerows(metrics)
    manifest = dict(
        video=args.video,
        mapping=mapping,
        before_s=args.before,
        after_s=args.after,
        shared_limits=limits,
        files=[outcome_file, *files],
        attempts=attempts,
        metrics=metrics,
        signal_processing="Raw samples; no smoothing, normalization, gravity subtraction, or automatic event realignment.",
        source_sha256={
            name: hashlib.sha256((session / name).read_bytes()).hexdigest()
            for name in ("samples.csv", "labels.jsonl", "video_sync.jsonl")
        },
    )
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n"
    )
    lines = [
        "# Pop and contact signal images",
        "",
        "Start with the outcome comparison:",
        "",
        "- [Makes versus bails](makes-vs-bails.png)",
        "- [Contact / landing comparison](contact-overview.png)",
        "- [Pop / takeoff comparison](pop-overview.png)",
        "",
        "Each row uses the same time and amplitude scales. Zero is the saved event marker; dashed lines show the other event. The shaded interval is the labeled pop-to-contact interval, not independently measured flight time.",
        "",
        "The individual images include magnitudes and raw X/Y/Z for both events:",
        "",
    ]
    for attempt, file in zip(attempts, files[2:]):
        label = attempt["label"]
        lines.append(
            f"- [{attempt['number']:02d} · {label['trick']} · {label['outcome']}]({file})"
        )
    lines += [
        "",
        dataset_note(attempts),
        "",
        "Contact means the manually saved contact marker, including board contact during a bail. It is not necessarily a successful rider landing. Large nearby acceleration peaks can fall before or after that marker; plots retain the saved timing.",
        "",
        "Signals are unsmoothed, in the original sensor frame. X is along the deck, Y is across it, and Z is perpendicular. Acceleration includes gravity and impacts. Recorded sample spacing is about 5.1 ms; maxima are sampled values, not guaranteed physical impact peaks.",
        "",
        "`metrics.csv` includes the largest sampled acceleration magnitude within −0.15 to +0.30 s of contact and median gyro magnitude from +0.20 to +1.00 s. These fixed exploratory windows are not new labels or a trained classifier.",
        "",
        "```bash",
        f"uv run viz/plot_attempts.py {args.session} --video {args.video}",
        "```",
        "",
    ]
    (folder / "README.md").write_text("\n".join(lines))
    print(
        json.dumps(
            dict(
                output=str(folder),
                images=len(files) + 1,
                shared_limits=limits,
                metrics=metrics,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
