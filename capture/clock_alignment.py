"""Optional iPhone movie/ESP32 clock evidence. No wall time or synthetic flashes.

Each device_us was sampled during its phone send/receive interval. Midpoints
estimate the clock offset; half the round trip describes network uncertainty,
not camera exposure, rolling shutter, or sensor timing accuracy.
"""
import json
import math
from pathlib import Path


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def clock_mapping(session, video):
    session = Path(session)
    sidecar = session / Path(video).with_suffix(".json")
    if not sidecar.exists():
        return None
    try:
        evidence = json.loads(sidecar.read_text()).get("clock_alignment")
        if evidence is None:
            return None
        meta = json.loads((session / "metadata.json").read_text())
        return fit_clock_evidence(evidence, meta, video)
    except (KeyError, TypeError, AttributeError, OSError, ValueError) as error:
        raise ValueError(f"Phone clock estimate unavailable: {error}") from error


def fit_clock_evidence(evidence, meta, video):
    if (evidence["schema"] != 1 or evidence["method"] != "avcapture_start_pts"
            or evidence["filename"] != video or not meta.get("onboard_id")
            or evidence["sessionID"] != meta["onboard_id"]
            or not meta.get("boot_id") or evidence["bootID"] != meta["boot_id"]):
        raise ValueError("timing identity does not match this movie and board recording")
    start = evidence["anchor"]["hostTimeS"]
    origin = meta.get("device_origin_us")
    if (not meta.get("closed_utc") or not number(origin) or origin < 0
            or not number(start) or start < 0
            or not number(evidence["anchor"]["sessionTimeS"])):
        raise ValueError("a camera start timestamp and closed, imported sensor recording are required")
    probes = evidence["probes"]
    if not isinstance(probes, list):
        raise ValueError("invalid clock observations")
    best, network_rtts = {}, []
    previous_end, previous_device, previous_video = start, -1, 0
    for probe in probes:
        sent, received, device = (probe[key] for key in ("sendHostS", "receiveHostS", "deviceUS"))
        video_sent, video_received = probe["sendVideoS"], probe["receiveVideoS"]
        if (not all(number(v) for v in (sent, received, device, video_sent, video_received))
                or sent < previous_end or received <= sent or device <= previous_device or device < 0
                or video_sent < previous_video or video_received <= video_sent):
            raise ValueError("clock observations are invalid or moved backwards")
        previous_end, previous_device, previous_video = received, device, video_received
        rtt = received - sent
        video_rtt = video_received - video_sent
        if not .98 <= video_rtt / rtt <= 1.02:
            raise ValueError("camera clock conversion is inconsistent")
        if rtt > .5:
            continue  # Long stalls provide little useful timing information.
        network_rtts.append(rtt)
        x, y = (video_sent + video_received) / 2, (device - origin) / 1e6
        bucket = math.floor(x / 5)
        if bucket not in best or video_rtt < best[bucket][2]:
            best[bucket] = (x, y, video_rtt)
    anchors = sorted(best.values())
    if not anchors:
        raise ValueError("no clock exchanges completed within 500 ms; use LED matches")
    weights = [1 / max(rtt, .001) ** 2 for _, _, rtt in anchors]
    total = sum(weights)
    x_mean = sum(w * x for w, (x, _, _) in zip(weights, anchors)) / total
    y_mean = sum(w * y for w, (_, y, _) in zip(weights, anchors)) / total
    span = anchors[-1][0] - anchors[0][0]
    drift = len(anchors) >= 3 and span >= 30
    scale = 1.0
    if drift:
        scale = sum(w * (x - x_mean) * (y - y_mean) for w, (x, y, _) in zip(weights, anchors)) / sum(
            w * (x - x_mean) ** 2 for w, (x, _, _) in zip(weights, anchors))
    if not .98 <= scale <= 1.02:
        raise ValueError("clock rate differs by over 2%; use LED matches")
    offset = y_mean - scale * x_mean
    residual = max(abs(y - (scale * x + offset)) for x, y, _ in anchors)
    if residual > .25:
        raise ValueError("clock observations disagree by over 250 ms; use LED matches")
    uncertainty = max(abs(y - (scale * x + offset)) + scale * rtt / 2 for x, y, rtt in anchors)
    return dict(scale=scale, offset_s=offset, points=0, source="phone_clock",
                drift_estimated=drift, network_uncertainty_s=uncertainty,
                best_rtt_s=min(network_rtts), probe_count=len(probes),
                anchor_count=len(anchors), measured_video_range_s=[anchors[0][0], anchors[-1][0]])
