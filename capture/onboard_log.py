"""Validate onboard packets and reconstruct sensor time without using Wi-Fi arrival time."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct
import zlib

HEADER = struct.Struct("<4sBBHIQI")
META, FIFO, CLOCK, SYNC, END = range(5)
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
            if magic != b"SKL1" or version != 1 or kind not in range(5) or length > 896:
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
                anchors.append((tick, (stamp + end_us) / 2))
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

    anchors = [(relative_tick(tick), stamp) for tick, stamp in anchors]
    fine = meta.get("frequency_fine")
    if fine is not None and (type(fine) is not int or not -128 <= fine <= 127):
        raise ValueError("Invalid factory timestamp calibration.")
    # ST AN5473 §6.4: this signed factory trim describes the actual sensor
    # clock. Validate the independently fitted clock against that calibration.
    calibrated_tick = 25 / (1 + .0015 * fine) if fine is not None else 25.
    if any(b[0] <= a[0] for a, b in zip(anchors, anchors[1:])):
        raise ValueError("Sensor clock did not advance between anchors.")
    if len(anchors) >= 2:
        mean_x = sum(x for x, _ in anchors) / len(anchors)
        mean_y = sum(y for _, y in anchors) / len(anchors)
        denominator = sum((x - mean_x) ** 2 for x, _ in anchors)
        scale = sum((x - mean_x) * (y - mean_y) for x, y in anchors) / denominator
        offset = mean_y - scale * mean_x
    else:
        scale, offset = calibrated_tick, anchors[0][1]
        issues.append("single_clock_anchor_nominal_tick_assumed")
    residual = max(abs(scale * x + offset - y) for x, y in anchors)
    clock_agrees = abs(scale / calibrated_tick - 1) <= .01 if fine is not None else 24 <= scale <= 26
    if not clock_agrees or residual > 5000:
        raise ValueError("Sensor/ESP32 clock anchors disagree; retain raw data for investigation.")

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
        stamp = round(scale * relative_tick(tick) + offset)
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
    quality = dict(complete=complete, usable=complete and not issues, issues=issues,
                   measured_hz=rate, raw_accel_samples=raw_accel, raw_gyro_samples=raw_gyro,
                   zero_accel=zero_accel, incomplete_slots=incomplete_slots,
                   clock_anchors=len(anchors), clock_tick_us=scale,
                   calibrated_tick_us=calibrated_tick,
                   clock_fit_max_error_us=residual, max_gap_s=max(gaps, default=0), footer=footer)
    return dict(metadata=meta, samples=samples, events=syncs, quality=quality)


def import_log(path, directory, allow_incomplete=False):
    path, directory = Path(path), Path(directory)
    data = decode_log(path, allow_incomplete)
    directory.mkdir(parents=True, exist_ok=True)
    if any((directory / name).exists() for name in ("samples.csv", "events.jsonl")):
        raise ValueError("Samples/events already exist; choose a new output directory.")
    previous_path = directory / "metadata.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    samples, quality = data["samples"], data["quality"]
    meta = dict(previous, schema=1, transport="onboard_flash", synthetic=False,
                device_origin_us=samples[0]["device_us"], boot_id=data["metadata"]["boot_id"],
                samples=len(samples), duration_s=samples[-1]["t_s"], malformed=0, stale=0,
                missing_sequences=0, gaps_over_30ms=sum(b["t_s"] - a["t_s"] > .03 for a, b in zip(samples, samples[1:])),
                closed_utc=datetime.now(timezone.utc).isoformat(),
                onboard_id=data["metadata"]["id"], onboard_quality=quality,
                onboard_raw_file=path.name, onboard_sensor=data["metadata"],
                sequence_note="Imported sequence numbers index complete FIFO slots; inspect onboard_quality for loss.")
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
