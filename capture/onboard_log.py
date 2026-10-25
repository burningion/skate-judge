"""Validate onboard packets and reconstruct sensor time without using Wi-Fi arrival time."""

from __future__ import annotations

from bisect import bisect_right
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct
import zlib

HEADER = struct.Struct("<4sBBHIQI")
META, FIFO, CLOCK, SYNC, END = range(5)
MOTION = 5
FIELDS = ("t_s", "device_us", "host_monotonic_ns", "sequence", "boot_id",
          "ax", "ay", "az", "gx", "gy", "gz", "temp_C")


def encode_packet(kind, sequence, device_us, payload):
    prefix = struct.pack("<4sBBHIQ", b"SKL1", kind, 1, len(payload), sequence, device_us)
    return prefix + struct.pack("<I", zlib.crc32(payload, zlib.crc32(prefix))) + payload


def read_packets(path, allow_incomplete=False):
    packets, issues = [], []
    with Path(path).open("rb") as stream:
        sequence = 0
        while True:
            header = stream.read(HEADER.size)
            if not header:
                break
            if len(header) != HEADER.size:
                if not allow_incomplete:
                    raise ValueError("Truncated packet header; keep the file and use --allow-incomplete for recovery.")
                issues.append("truncated_header")
                break
            magic, kind, version, length, number, device_us, checksum = HEADER.unpack(header)
            if magic != b"SKL1" or version != 1 or kind not in range(6) or length > 896:
                raise ValueError("Invalid onboard packet header.")
            if number != sequence:
                raise ValueError("Missing or reordered onboard packet.")
            payload = stream.read(length)
            if len(payload) != length:
                if not allow_incomplete:
                    raise ValueError("Truncated onboard payload.")
                issues.append("truncated_payload")
                break
            if zlib.crc32(payload, zlib.crc32(header[:20])) != checksum:
                raise ValueError("Onboard checksum mismatch; retain the file and retry downloading.")
            if packets and packets[-1][0] == END:
                raise ValueError("Data after the closing record.")
            packets.append((kind, device_us, payload))
            sequence += 1
    if not packets or packets[0][0] != META:
        raise ValueError("Missing onboard recording metadata.")
    complete = packets[-1][0] == END
    if not complete:
        if not allow_incomplete:
            raise ValueError("Recording did not close cleanly; use --allow-incomplete to recover its intact prefix.")
        issues.append("missing_footer")
    return packets, complete, issues


def _clock_model(anchors, fine):
    """Map sensor ticks using measured local rates, with independent checks.

    A single affine fit accumulates error as the oscillator warms up. Keep it
    as a diagnostic, but interpolate between validated one-second anchors.
    Test each interior anchor against its *neighbors*, not against a mapping
    through itself, so interpolation cannot conceal a bad timestamp.
    """
    if fine is not None and (type(fine) is not int or not -128 <= fine <= 127):
        raise ValueError("Invalid factory timestamp calibration.")
    # ST AN5473 §6.4: signed factory trim describes the actual sensor clock.
    calibrated = 25 / (1 + .0015 * fine) if fine is not None else 25.
    low, high = (calibrated * .99, calibrated * 1.01) if fine is not None else (24., 26.)
    ticks = [x for x, _, _ in anchors]
    intervals = list(zip(anchors, anchors[1:]))
    if any(b[0] <= a[0] for a, b in intervals):
        raise ValueError("Sensor clock did not advance between anchors.")
    if any(b[1] <= a[1] for a, b in intervals):
        raise ValueError("ESP32 clock did not advance between anchors.")
    rates = []
    for index, (a, b) in enumerate(intervals):
        dx, dy = b[0] - a[0], b[1] - a[1]
        # Bracketed reads and timestamp quantization bound rate uncertainty,
        # including a final anchor only milliseconds after the periodic one.
        uncertainty = (a[2] + b[2]) / 2 + 2 * high
        if not low * dx - uncertainty <= dy <= high * dx + uncertainty:
            raise ValueError(f"Sensor/ESP32 clock anchors disagree at interval {index}: "
                             f"{dy / dx:.6f} us/tick, expected {low:.6f}–{high:.6f}; raw log preserved.")
        rates.append(dy / dx)
    local_error = 0.
    for index, (a, b, c) in enumerate(zip(anchors, anchors[1:], anchors[2:]), 1):
        weight = (b[0] - a[0]) / (c[0] - a[0])
        error = abs(b[1] - (a[1] + weight * (c[1] - a[1])))
        local_error = max(local_error, error)
        uncertainty = (b[2] + (1 - weight) * a[2] + weight * c[2]) / 2 + 2 * high
        # Allow 1 ms of local curvature beyond read uncertainty, while keeping
        # the original 5 ms ceiling. Abrupt jumps/spikes still fail import.
        if error > min(5000, 1000 + uncertainty):
            raise ValueError(f"Sensor/ESP32 clock anchors disagree at anchor {index}: "
                             f"local error {error:.0f} us; raw log preserved.")
    if intervals:
        mean_x = sum(ticks) / len(anchors)
        mean_y = sum(y for _, y, _ in anchors) / len(anchors)
        scale = (sum((x - mean_x) * (y - mean_y) for x, y, _ in anchors)
                 / sum((x - mean_x) ** 2 for x in ticks))
        offset = mean_y - scale * mean_x
    else:
        scale, offset = calibrated, anchors[0][1] - calibrated * ticks[0]
    residual = max(abs(scale * x + offset - y) for x, y, _ in anchors)

    def device_time(tick):
        if not intervals:
            return scale * tick + offset
        index = max(0, min(bisect_right(ticks, tick) - 1, len(rates) - 1))
        x, y, _ = anchors[index]
        return y + (tick - x) * rates[index]

    gap_s = max(((b[1] - a[1]) / 1e6 for a, b in intervals), default=0)
    issues = []
    if not intervals:
        issues.append("single_clock_anchor_nominal_tick_assumed")
    if gap_s > 5:
        issues.append("clock_anchor_gaps_over_5s")
    quality = dict(clock_model="piecewise_linear" if intervals else "single_anchor",
                   clock_anchors=len(anchors), clock_tick_us=scale, calibrated_tick_us=calibrated,
                   clock_fit_max_error_us=residual, clock_local_max_error_us=local_error,
                   clock_interval_tick_us_min=min(rates, default=scale),
                   clock_interval_tick_us_max=max(rates, default=scale),
                   clock_anchor_max_gap_s=gap_s,
                   clock_anchor_max_read_us=max(duration for _, _, duration in anchors))
    return device_time, quality, issues


def decode_log(path, allow_incomplete=False):
    packets, complete, issues = read_packets(path, allow_incomplete)
    meta = json.loads(packets[0][2])
    if (meta.get("schema") != 1 or meta.get("sensor") != "LSM6DSO32" or meta.get("odr_hz") != 208
            or meta.get("accel_g_per_lsb") != .000976 or meta.get("gyro_dps_per_lsb") != .070
            or meta.get("timestamp_tick_us") != 25):
        raise ValueError("Unsupported onboard sensor configuration.")
    anchors, syncs, words = [], [], []
    footer = {}
    for kind, stamp, payload in packets[1:]:
        if kind == META:
            raise ValueError("Unexpected metadata in the middle of a log.")
        if kind == CLOCK:
            if len(payload) != 12:
                raise ValueError("Invalid clock record.")
            end_us, tick = struct.unpack("<QI", payload)
            if not stamp <= end_us:
                raise ValueError("Clock read ended before it began.")
            if end_us - stamp <= 2000:
                anchors.append((tick, (stamp + end_us) / 2, end_us - stamp))
            else:
                issues.append("slow_clock_anchor")
        elif kind == SYNC:
            if len(payload) != 6:
                raise ValueError("Invalid LED record.")
            identity, edge, enabled = struct.unpack("<IBB", payload)
            if identity < 1 or edge not in (0, 1) or enabled not in (0, 1):
                raise ValueError("Invalid LED event.")
            syncs.append(dict(kind="sync", device_us=stamp, sync_id=identity, edge=edge,
                              led_enabled=bool(enabled), boot_id=meta["boot_id"], source="onboard"))
        elif kind == MOTION:
            if len(payload) != 19 or meta.get("motion_events_version") != 1:
                raise ValueError("Invalid or unconfigured motion record.")
            version, since_us, end_us, wake, orientation = struct.unpack("<BQQBB", payload)
            if version != 1 or not 0 < since_us <= stamp <= end_us or not (wake & 0x28 or orientation & 0x40):
                raise ValueError("Invalid motion event bounds or flags.")
            syncs.append(dict(kind="board_motion", device_us=stamp, boot_id=meta["boot_id"],
                              source="lsm6dso32_basic_interrupts", earliest_device_us=since_us,
                              latest_device_us=end_us, free_fall=bool(wake & 0x20),
                              wake_up=bool(wake & 0x08), orientation_changed=bool(orientation & 0x40),
                              wake_up_src=wake, d6d_src=orientation,
                              timing_note="Latched status observed during polling; events may coalesce."))
        elif kind == FIFO:
            if not payload or len(payload) % 7:
                raise ValueError("Invalid FIFO payload length.")
            words.extend(payload[i:i + 7] for i in range(0, len(payload), 7))
        elif kind == END:
            footer = json.loads(payload)
    if not anchors:
        raise ValueError("No usable sensor/ESP32 clock anchors; raw recording preserved.")
    first_tick = anchors[0][0]

    def relative_tick(tick):
        # Sessions are capacity-limited to minutes. Signed modular differences
        # handle a sensor's 32-bit timestamp rollover during the recording.
        return ((tick - first_tick + 2**31) % 2**32) - 2**31

    anchors = [(relative_tick(tick), stamp, duration) for tick, stamp, duration in anchors]
    device_time, clock_quality, clock_issues = _clock_model(anchors, meta.get("frequency_fine"))
    issues.extend(clock_issues)

    samples, slot = [], {}
    counter = None
    incomplete_slots = 0
    temperature = float(meta["initial_temp_C"])
    raw_accel = raw_gyro = zero_accel = 0

    def finish_slot():
        nonlocal temperature, incomplete_slots
        if not slot:
            return
        if 3 in slot:
            temperature = 25 + struct.unpack_from("<h", slot[3])[0] / 256
        if not all(kind in slot for kind in (1, 2, 4)):
            if 1 in slot or 2 in slot:
                incomplete_slots += 1
            return
        tick = struct.unpack_from("<I", slot[4])[0]
        # FIFO timestamp records include the active accel/gyro batching rates.
        if slot[4][5] != 0x55:
            raise ValueError("FIFO batching rate changed during recording.")
        stamp = round(device_time(relative_tick(tick)))
        if samples and stamp <= samples[-1]["device_us"]:
            raise ValueError("Non-monotonic FIFO timestamps; raw log preserved.")
        accel = [raw * .000976 * 9.80665 for raw in struct.unpack("<hhh", slot[2])]
        gyro = [raw * .070 * math.pi / 180 for raw in struct.unpack("<hhh", slot[1])]
        samples.append(dict(device_us=stamp, sequence=len(samples), boot_id=meta["boot_id"],
                            host_monotonic_ns="", **dict(zip(FIELDS[5:], accel + gyro + [temperature]))))

    for word in words:
        if word[0].bit_count() % 2:
            raise ValueError("FIFO tag parity mismatch; raw log preserved.")
        kind, count = word[0] >> 3, (word[0] >> 1) & 3
        if kind not in (1, 2, 3, 4):
            raise ValueError("Unexpected FIFO tag.")
        if counter is not None and count != counter:
            finish_slot()
            slot = {}
        if kind in slot:
            raise ValueError("Duplicate sensor word in a FIFO time slot.")
        counter = count
        slot[kind] = word[1:]
        if kind == 1:
            raw_gyro += 1
        elif kind == 2:
            raw_accel += 1
            zero_accel += int(not any(word[1:]))
    finish_slot()
    if not samples:
        raise ValueError("No complete accel/gyro/timestamp slots. The raw recording is preserved.")
    origin = samples[0]["device_us"]
    for row in samples:
        row["t_s"] = (row["device_us"] - origin) / 1e6
    gaps = [b["t_s"] - a["t_s"] for a, b in zip(samples, samples[1:])]
    rate = (len(samples) - 1) / samples[-1]["t_s"] if len(samples) > 1 else 0
    if footer and (footer.get("accel_samples") != raw_accel or footer.get("gyro_samples") != raw_gyro):
        raise ValueError("Footer sample counts disagree with stored raw words.")
    if incomplete_slots:
        issues.append(f"{incomplete_slots}_incomplete_fifo_slots")
    if any(gap > .01 for gap in gaps):
        issues.append("sample_gaps_over_10ms")
    if not 180 <= rate <= 230:
        issues.append("unexpected_sample_rate")
    if zero_accel > max(1, raw_accel) * .25:
        issues.append("excessive_zero_acceleration")
    if footer.get("error"):
        issues.append(footer["error"])
    if footer.get("io_errors") or footer.get("fifo_overruns"):
        issues.append("acquisition_errors")
    extrapolation = max(0, anchors[0][1] - samples[0]["device_us"],
                        samples[-1]["device_us"] - anchors[-1][1]) / 1e6
    if extrapolation > 1:
        issues.append("clock_extrapolation_over_1s")
    quality = dict(complete=complete, usable=complete and not issues, issues=issues,
                   measured_hz=rate, raw_accel_samples=raw_accel, raw_gyro_samples=raw_gyro,
                   zero_accel=zero_accel, incomplete_slots=incomplete_slots,
                   **clock_quality, clock_extrapolation_s=extrapolation,
                   max_gap_s=max(gaps, default=0), footer=footer)
    return dict(metadata=meta, samples=samples, events=syncs, quality=quality)


def import_log(path, directory, allow_incomplete=False, stop_error=""):
    path, directory = Path(path), Path(directory)
    data = decode_log(path, allow_incomplete)
    directory.mkdir(parents=True, exist_ok=True)
    if any((directory / name).exists() for name in ("samples.csv", "events.jsonl")):
        raise ValueError("Samples/events already exist; choose a new output directory.")
    previous_path = directory / "metadata.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    stop_status = previous.get("onboard_stop_status", {})
    fault_path = directory / "board_fault.json"
    if not stop_status and fault_path.exists():
        stop_status = json.loads(fault_path.read_text())
    if any(stop_status.get(key) != data["metadata"].get(key) for key in ("id", "boot_id")):
        stop_status = {}
    samples, quality = data["samples"], data["quality"]
    storage = data["metadata"].get("storage", "flash")
    if storage not in ("sd", "flash"):
        raise ValueError(f"Unknown recording storage: {storage}")
    # Closing/fsync can fail after END was written. Preserve the board's final
    # error as well as errors embedded in the raw file.
    stop_error = stop_error or previous.get("onboard_stop_error", "") or stop_status.get("error", "")
    if stop_error:
        quality["usable"] = False
        if stop_error not in quality["issues"]:
            quality["issues"].append(stop_error)
    # Successful recovery is diagnostic information, not evidence of lost
    # samples. The usual CRC/sequence/count/timing checks still determine use.
    quality["storage_recoveries"] = max(quality["footer"].get("storage_recoveries", 0),
                                        stop_status.get("storage_recoveries", 0))
    meta = dict(previous, schema=1, transport="onboard_" + storage, synthetic=False,
                onboard_storage=storage, onboard_stop_error=stop_error,
                device_origin_us=samples[0]["device_us"], boot_id=data["metadata"]["boot_id"],
                samples=len(samples), duration_s=samples[-1]["t_s"], malformed=0, stale=0,
                missing_sequences=0, gaps_over_30ms=sum(b["t_s"] - a["t_s"] > .03 for a, b in zip(samples, samples[1:])),
                closed_utc=datetime.now(timezone.utc).isoformat(),
                onboard_id=data["metadata"]["id"], onboard_quality=quality,
                onboard_raw_file=path.name, onboard_sensor=data["metadata"],
                sequence_note="Imported sequence numbers index complete FIFO slots; inspect onboard_quality for loss.")
    if stop_status:
        meta["onboard_stop_status"] = stop_status
    meta.setdefault("created_utc", datetime.now(timezone.utc).isoformat())
    with (directory / "samples.csv.tmp").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, FIELDS)
        writer.writeheader()
        writer.writerows(samples)
    (directory / "events.jsonl.tmp").write_text("".join(json.dumps(row) + "\n" for row in data["events"]))
    (directory / "metadata.json.tmp").write_text(json.dumps(meta, indent=2, allow_nan=False) + "\n")
    for name in ("samples.csv", "events.jsonl", "metadata.json"):
        (directory / (name + ".tmp")).replace(directory / name)
    return meta
