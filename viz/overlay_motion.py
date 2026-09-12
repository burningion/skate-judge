"""Clock alignment and attitude estimation for offline overlay rendering.

Quaternions are w,x,y,z, rotating sensor coordinates into a Z-up world.
No position or jump height is inferred from the IMU.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

G = 9.80665
MAX_GAP = 0.03


def read_jsonl(path):
    return [
        json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()
    ]


def latest_labels(session, video):
    latest = {row["id"]: row for row in read_jsonl(Path(session) / "labels.jsonl")}
    return sorted(
        (r for r in latest.values() if r.get("video") == video),
        key=lambda r: r["start_s"],
    )


def latest_syncs(session, video):
    latest = {
        row["sync_id"]: row
        for row in read_jsonl(Path(session) / "video_sync.jsonl")
        if row.get("video") == video
    }
    return sorted(latest.values(), key=lambda row: row["t_s"])


def load_samples(path):
    with Path(path).open() as stream:
        data = np.array(
            [
                [float(row[k]) for k in ("t_s", "ax", "ay", "az", "gx", "gy", "gz")]
                for row in csv.DictReader(stream)
            ],
            dtype=float,
        )
    if (
        data.ndim != 2
        or len(data) < 2
        or not np.isfinite(data).all()
        or np.any(np.diff(data[:, 0]) <= 0)
    ):
        raise ValueError(
            "Samples must contain finite values and strictly increasing timestamps."
        )
    return data


def check_alignment(labels, scale, offset, coverage):
    for row in labels:
        for key, video_key in (("start_s", "video_start_s"), ("end_s", "video_end_s")):
            if not math.isclose(
                scale * row[video_key] + offset, row[key], rel_tol=0, abs_tol=1e-6
            ):
                raise ValueError(
                    f"Label {row['id']} has stale alignment. Review and save it again before rendering."
                )
        if not coverage[0] <= row["start_s"] < row["end_s"] <= coverage[1]:
            raise ValueError(
                f"Label {row['id']} extends outside recorded sensor samples."
            )


def qmul(a, b):
    w, x, y, z = a
    v, i, j, k = b
    return np.array(
        [
            w * v - x * i - y * j - z * k,
            w * i + x * v + y * k - z * j,
            w * j - x * k + y * v + z * i,
            w * k + x * j - y * i + z * v,
        ]
    )


def matrix(q):
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def from_gravity(accel):
    norm = np.linalg.norm(accel)
    if norm < 1e-8:
        return np.array([1.0, 0.0, 0.0, 0.0])
    up = accel / norm
    if up[2] < -0.999999:
        return np.array([0.0, 1.0, 0.0, 0.0])
    q = np.array([1 + up[2], up[1], -up[0], 0.0])
    return q / np.linalg.norm(q)


def estimate_attitude(samples, gyro_bias=None):
    """Gyro integration with gravity correction only during low-dynamic motion.

    A Mahony-style proportional correction follows the existing live viewer.
    Bias defaults to quiet samples in the first two seconds, or accepts an
    externally calibrated per-sample XYZ bias. Missing samples are not integrated
    across; the exported manifest reports those gaps.
    """
    times, accel, gyro = samples[:, 0], samples[:, 1:4], samples[:, 4:7]
    norms = np.linalg.norm(accel, axis=1)
    quiet = (
        (times <= times[0] + 2)
        & (np.linalg.norm(gyro, axis=1) < 0.15)
        & (abs(norms - G) < 0.15 * G)
    )
    calibrated = int(quiet.sum()) >= 20
    bias = np.median(gyro[quiet], axis=0) if calibrated else np.zeros(3)
    gravity = np.median(accel[quiet], axis=0) if calibrated else accel[0]
    if gyro_bias is not None:
        bias_curve = np.asarray(gyro_bias, dtype=float)
        if bias_curve.shape != gyro.shape or not np.isfinite(bias_curve).all():
            raise ValueError("Gyro bias must have one finite XYZ vector per sample.")
        bias = np.mean(bias_curve, axis=0)
        calibrated = True
    else:
        bias_curve = np.tile(bias, (len(times), 1))
    q = from_gravity(gravity)
    result = np.empty((len(times), 4))
    result[0] = q
    for i in range(1, len(times)):
        dt = times[i] - times[i - 1]
        if dt > MAX_GAP:
            # Hold heading/attitude through missing data; never invent a rotation.
            result[i] = q
            continue
        omega = (gyro[i - 1] + gyro[i] - bias_curve[i - 1] - bias_curve[i]) * 0.5
        if abs(norms[i] - G) < 0.15 * G and np.linalg.norm(omega) < 1:
            expected_up = matrix(q)[2]
            omega += 0.8 * np.cross(accel[i] / norms[i], expected_up)
        angle = np.linalg.norm(omega) * dt
        delta = (
            np.array(
                [math.cos(angle / 2), *(omega * (math.sin(angle / 2) * dt / angle))]
            )
            if angle > 1e-12
            else np.array([1.0, 0.0, 0.0, 0.0])
        )
        q = qmul(q, delta)
        q /= np.linalg.norm(q)
        result[i] = q
    return result, {
        "gyro_bias_rad_s": bias.tolist(),
        "quiet_bias_calibrated": calibrated,
        "initial_gravity_sensor": gravity.tolist(),
        "gaps_over_30ms": int(np.count_nonzero(np.diff(times) > MAX_GAP)),
    }


def flat_sync_attitude(samples, syncs, nominal_mount, window=0.5):
    """Offline orientation estimate using explicitly declared flat, quiet syncs.

    Gravity defines an effective deck-up reference; it does not independently
    identify accelerometer bias, scale, or physical mounting errors. Stationary
    gyro means are interpolated between the known-rest windows. A small,
    interpolated world-frame tilt correction then satisfies the flat constraints.
    This is an anchored estimate, not a probabilistic smoother or height solver.
    """
    if not syncs:
        raise ValueError("Flat-sync calibration needs saved flash correspondences.")
    if not math.isfinite(window) or window <= 0:
        raise ValueError("Flat-sync window must be positive and finite.")
    times = samples[:, 0]
    anchors = []
    for sync in sorted(syncs, key=lambda row: row["t_s"]):
        time_s = float(sync["t_s"])
        local = samples[abs(times - time_s) <= window]
        if not times[0] <= time_s <= times[-1] or len(local) < 20:
            raise ValueError(
                f"Flash {sync['sync_id']} has insufficient sensor coverage."
            )
        accel, gyro = local[:, 1:4], local[:, 4:7]
        gravity = np.median(accel, axis=0)
        if (
            np.max(np.diff(local[:, 0])) > MAX_GAP
            or np.linalg.norm(np.std(accel, axis=0)) > 0.3
            or np.linalg.norm(np.std(gyro, axis=0)) > 0.02
            or np.max(np.linalg.norm(gyro, axis=1)) > 0.15
            or not 0.75 * G < np.linalg.norm(gravity) < 1.25 * G
        ):
            raise ValueError(
                f"Flash {sync['sync_id']} is not quiet enough for flat-sync calibration."
            )
        up = gravity / np.linalg.norm(gravity)
        if np.dot(up, nominal_mount[:, 2]) < math.cos(math.radians(15)):
            raise ValueError(
                f"Flash {sync['sync_id']} disagrees with deck-up; check mounting or the flat assumption."
            )
        anchors.append(
            {
                "sync_id": sync["sync_id"],
                "sensor_s": time_s,
                "window_start_s": float(local[0, 0]),
                "window_end_s": float(local[-1, 0]),
                "samples": len(local),
                "gravity_sensor": gravity.tolist(),
                "gyro_bias_rad_s": np.mean(gyro, axis=0).tolist(),
                "accel_std_ms2": np.std(accel, axis=0).tolist(),
                "gyro_std_rad_s": np.std(gyro, axis=0).tolist(),
            }
        )
    anchor_times = np.array([a["sensor_s"] for a in anchors])
    if len(anchor_times) > 1 and np.any(np.diff(anchor_times) <= 0):
        raise ValueError("Flat-sync anchors need distinct timestamps.")
    up = np.mean(
        [
            np.array(a["gravity_sensor"]) / np.linalg.norm(a["gravity_sensor"])
            for a in anchors
        ],
        axis=0,
    )
    up /= np.linalg.norm(up)
    nose = nominal_mount[:, 0] - np.dot(nominal_mount[:, 0], up) * up
    nose /= np.linalg.norm(nose)
    mount = np.column_stack([nose, np.cross(up, nose), up])
    bias_points = np.array([a["gyro_bias_rad_s"] for a in anchors])
    bias_curve = np.column_stack(
        [np.interp(times, anchor_times, bias_points[:, axis]) for axis in range(3)]
    )
    baseline, _ = estimate_attitude(samples)
    attitudes, metadata = estimate_attitude(samples, bias_curve)
    corrections = []
    for anchor in anchors:
        t = anchor["sensor_s"]
        before = interpolate(samples, baseline, t)[1] @ nominal_mount[:, 2]
        up_estimate = interpolate(samples, attitudes, t)[1] @ mount[:, 2]
        residual = math.degrees(math.acos(np.clip(up_estimate[2], -1, 1)))
        if residual > 10:
            raise ValueError(
                "Flat-sync residual exceeds 10 degrees; inspect the recording before correcting it."
            )
        correction = from_gravity(up_estimate)
        if corrections and np.dot(corrections[-1], correction) < 0:
            correction = -correction
        corrections.append(correction)
        anchor["baseline_tilt_deg"] = math.degrees(math.acos(np.clip(before[2], -1, 1)))
        anchor["tilt_before_anchor_correction_deg"] = residual
    # The small correction is interpolated smoothly, retaining complete flips.
    # Yaw is not observed by these constraints and is never forced to zero.
    corrections = np.asarray(corrections)
    correction_curve = np.column_stack(
        [np.interp(times, anchor_times, corrections[:, axis]) for axis in range(4)]
    )
    correction_curve /= np.linalg.norm(correction_curve, axis=1, keepdims=True)
    corrected = np.array([qmul(c, q) for c, q in zip(correction_curve, attitudes)])
    for anchor in anchors:
        normal = interpolate(samples, corrected, anchor["sensor_s"])[1] @ mount[:, 2]
        anchor["anchored_tilt_deg"] = math.degrees(math.acos(np.clip(normal[2], -1, 1)))
    metadata.update(
        {
            "gyro_bias_source": "quiet windows around declared flat syncs",
            "flat_sync_anchors": anchors,
            "effective_board_to_sensor_matrix": mount.tolist(),
            "flat_sync_window_half_width_s": window,
            "flat_sync_method": "empirical level reference plus interpolated tilt correction",
            "flat_sync_assumptions": [
                "Deck is level and quiet at each selected flash.",
                "No independent yaw, position, or jump-height observation.",
                "Near-zero anchor residual is enforced, not independent accuracy validation.",
                "One repeated flat pose cannot separate accelerometer bias, gain, and mount tilt.",
            ],
        }
    )
    return corrected, mount, metadata


def mount_matrix(nose_axis="x", up_axis="-z"):
    def axis(name):
        if name not in ("x", "y", "z", "-x", "-y", "-z"):
            raise ValueError(f"Invalid sensor axis: {name}")
        v = np.zeros(3)
        v["xyz".index(name[-1])] = -1 if name.startswith("-") else 1
        return v

    nose, up = axis(nose_axis), axis(up_axis)
    if abs(np.dot(nose, up)) > 0.1:
        raise ValueError("Nose and deck-up axes must be perpendicular.")
    return np.column_stack([nose, np.cross(up, nose), up])


def infer_mount(samples, labels):
    """Infer unsigned deck axes; positive nose direction remains a convention."""
    initial = samples[samples[:, 0] <= samples[0, 0] + 1]
    gravity = np.median(initial[:, 1:4], axis=0)
    up_index = int(np.argmax(abs(gravity)))
    up = ("-" if gravity[up_index] < 0 else "") + "xyz"[up_index]
    mask = np.zeros(len(samples), dtype=bool)
    # A kickflip spins primarily about the deck's long axis.
    for row in labels:
        if "kickflip" in row.get("trick", "").lower().replace(" ", ""):
            mask |= (samples[:, 0] >= row["start_s"]) & (samples[:, 0] <= row["end_s"])
    horizontal = [i for i in range(3) if i != up_index]
    if mask.sum() >= 20:
        energy = np.mean(samples[mask, 4:7] ** 2, axis=0)
        nose = "xyz"[max(horizontal, key=lambda i: energy[i])]
        method = "dominant kickflip rotation axis; nose sign assumed positive"
    else:
        nose = "xyz"[horizontal[0]]
        method = "default horizontal sensor axis; mounting needs confirmation"
    return nose, up, method


def interpolate(samples, quaternions, time):
    times = samples[:, 0]
    if time < times[0] or time > times[-1]:
        return None
    i = min(len(times) - 2, max(0, int(np.searchsorted(times, time, side="right") - 1)))
    dt = times[i + 1] - times[i]
    if dt > MAX_GAP and times[i] < time < times[i + 1]:
        return None
    f = (time - times[i]) / dt
    b = quaternions[i + 1]
    if np.dot(quaternions[i], b) < 0:
        b = -b
    q = quaternions[i] * (1 - f) + b * f
    q /= np.linalg.norm(q)
    return samples[i, 1:] * (1 - f) + samples[i + 1, 1:] * f, matrix(q)
