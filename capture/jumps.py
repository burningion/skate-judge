"""Experimental waist/back jump hints in sensor seconds, not measured apexes.

Use raw acceleration magnitude (including gravity), in m/s². The midpoint of
a low-g flight is an apex estimate only for similar takeoff/landing COM heights.
"""

from bisect import bisect_left, bisect_right
import math
from statistics import median

G = 9.80665
CONFIG = dict(low_g=.45, boundary_g=.65, min_flight_s=.18, max_flight_s=3.,
              min_low_s=.05, support_g=1.2, context_s=.25, max_gap_s=.03)
PAIR_CONFIG = dict(smoothing_s=.05, peak_separation_s=.18,
                   push_g=1.5, push_width_g=1.4, min_push_s=.10,
                   landing_g=2., landing_push_ratio=.8,
                   min_pair_s=.30, max_pair_s=.95, boundary_g=1.2,
                   low_g=.75, min_low_s=.05, min_low_fraction=.12)


def jump_event(takeoff, landing, method, **evidence):
    return dict(event_id=f"jump-{takeoff:.6f}-{landing:.6f}",
                takeoff_s=takeoff, peak_s=(takeoff + landing) / 2,
                landing_s=landing, flight_s=landing - takeoff,
                peak_method="flight_midpoint" if method == "sustained_low_g" else "load_pair_midpoint",
                detection_method=method, **evidence)


def load_pair_jumps(times, raw):
    """Recover interrupted unloading using broad push-off and landing loads.

    Time-based smoothing keeps this pass comparable at different sample rates.
    Strong, sustained push-off and a substantial landing are required because
    relaxed unloading alone also picks up crouches and post-landing rebounds.
    These are review hints: tucks can corrupt both the flight and its midpoint.
    """
    cfg = PAIR_CONFIG
    half = cfg["smoothing_s"] / 2
    signal = [median(raw[bisect_left(times, t - half):bisect_right(times, t + half)])
              for t in times]
    local = [i for i in range(1, len(signal) - 1)
             if signal[i] > signal[i - 1] and signal[i] >= signal[i + 1]
             and signal[i] >= cfg["push_g"]]
    # Suppress nearby maxima in descending strength, including flat-top loads.
    peaks, peak_times = [], []
    for i in sorted(local, key=lambda i: signal[i], reverse=True):
        slot = bisect_left(peak_times, times[i])
        if any(abs(times[i] - t) < cfg["peak_separation_s"]
               for t in peak_times[max(0, slot - 1):slot + 1]):
            continue
        peaks.insert(slot, i)
        peak_times.insert(slot, times[i])

    candidates = []
    for contact in peaks:
        if signal[contact] < cfg["landing_g"]:
            continue
        begin = bisect_left(peak_times, times[contact] - cfg["max_pair_s"])
        end = bisect_right(peak_times, times[contact] - cfg["min_pair_s"])
        if begin == end:
            continue
        push = max(peaks[begin:end], key=lambda i: signal[i])
        if signal[contact] < cfg["landing_push_ratio"] * signal[push]:
            continue
        left = right = push
        while left > 0 and signal[left - 1] >= cfg["push_width_g"]:
            left -= 1
        while right + 1 < len(signal) and signal[right + 1] >= cfg["push_width_g"]:
            right += 1
        if times[right] - times[left] + 1e-9 < cfg["min_push_s"]:
            continue

        unloaded = [i for i in range(push + 1, contact) if signal[i] < cfg["boundary_g"]]
        if not unloaded:
            continue
        first, last = unloaded[0], unloaded[-1]

        def crossing(a, b):
            fraction = (cfg["boundary_g"] - signal[a]) / (signal[b] - signal[a])
            return times[a] + fraction * (times[b] - times[a])

        takeoff, landing = crossing(first - 1, first), crossing(last, last + 1)
        flight = landing - takeoff
        if not CONFIG["min_flight_s"] <= flight <= CONFIG["max_flight_s"]:
            continue
        if takeoff - CONFIG["context_s"] < times[0] or landing + CONFIG["context_s"] > times[-1]:
            continue
        low = sum(times[i + 1] - times[i] for i in range(first, last)
                  if max(signal[i], signal[i + 1]) <= cfg["low_g"])
        if low + 1e-9 < cfg["min_low_s"] or low / flight < cfg["min_low_fraction"]:
            continue
        candidates.append(jump_event(takeoff, landing, "load_pair",
                                     low_s=low, push_g=signal[push], landing_g=signal[contact]))
    return candidates


def detect_jumps(samples):
    """Accept [time, acceleration magnitude, gyro magnitude] at >=50 Hz.

    Preserve conservative low-g flights; add load-pair hints for interrupted
    unloading. Each event names its detection method so review can filter them.
    Never span timestamp gaps, all-six-axis zero packets, or recording edges.
    Thresholds are experimental defaults; validate with video before tuning.
    """
    result = dict(algorithm="waist-jump-hybrid-v2", availability="unavailable",
                  message="", config={**CONFIG, "load_pair": dict(PAIR_CONFIG)}, events=[], gaps=[])
    if len(samples) < 3:
        return dict(result, message="Jump detection needs more IMU samples. Finish importing samples.csv.")
    if any(len(row) != 3 or not all(math.isfinite(v) for v in row)
           or row[1] < 0 or row[2] < 0 for row in samples):
        return dict(result, message="Jump detection needs finite, nonnegative sensor magnitudes.")
    spacing = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
    if min(spacing) <= 0 or median(spacing) > .02 + 1e-9:
        return dict(result, message="Jump detection needs ordered IMU timestamps at 50 Hz or faster.")

    # Both magnitudes exactly zero means all six raw axes were zero. Unlike a
    # near-zero accelerometer reading with live gyro/noise, that packet is suspect.
    runs, current = [], []
    for row in samples:
        broken = current and row[0] - current[-1][0] > CONFIG["max_gap_s"]
        empty = row[1] == row[2] == 0
        if broken or empty:
            if current:
                runs.append(current)
                result["gaps"].append([current[-1][0], row[0]])
            current = []
        if not empty:
            current.append(row)
    if current:
        runs.append(current)
    usable = False
    for run in runs:
        if len(run) < 3 or run[-1][0] - run[0][0] < 2 * CONFIG["context_s"] + CONFIG["min_flight_s"]:
            continue
        usable = True
        times = [row[0] for row in run]
        raw = [row[1] / G for row in run]
        signal = [median(raw[max(0, i - 1):i + 2]) for i in range(len(raw))]
        start = None
        boundary = CONFIG["boundary_g"]

        def crossing(left, right):
            fraction = (boundary - signal[left]) / (signal[right] - signal[left])
            return times[left] + fraction * (times[right] - times[left])

        for i, value in enumerate(signal):
            if value <= boundary:
                if start is None:
                    start = i
                continue
            if start is None:
                continue
            first, start = start, None
            if first == 0:
                continue
            takeoff, landing = crossing(first - 1, first), crossing(i - 1, i)
            flight = landing - takeoff
            if not CONFIG["min_flight_s"] <= flight <= CONFIG["max_flight_s"]:
                continue
            low_duration = longest_low = 0.
            for j in range(first, i - 1):
                low_duration = (low_duration + times[j + 1] - times[j]
                                if max(signal[j], signal[j + 1]) <= CONFIG["low_g"] else 0.)
                longest_low = max(longest_low, low_duration)
            if longest_low < CONFIG["min_low_s"]:
                continue
            before, after = takeoff - CONFIG["context_s"], landing + CONFIG["context_s"]
            if before < times[0] or after > times[-1]:
                continue
            push = signal[bisect_left(times, before):first]
            contact = signal[i:bisect_right(times, after)]
            if not push or not contact or min(max(push), max(contact)) < CONFIG["support_g"]:
                continue
            result["events"].append(jump_event(takeoff, landing, "sustained_low_g"))
        # Keep the original estimate when both passes find the same flight.
        # Among overlapping fallback hints, prefer the strongest landing.
        for event in sorted(load_pair_jumps(times, raw), key=lambda e: e["landing_g"], reverse=True):
            if not any(event["takeoff_s"] < other["landing_s"] and event["landing_s"] > other["takeoff_s"]
                       for other in result["events"]):
                result["events"].append(event)
    result["events"].sort(key=lambda e: e["peak_s"])
    result["availability"] = "ready" if usable else "unavailable"
    if not usable:
        result["message"] = "No continuous usable IMU segment long enough to detect a complete jump."
    return result
