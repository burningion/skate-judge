"""Validate recorded board observations and retain their uncertain timing.

Shared by video review and dataset export; no inference or outcome labels here.
"""

import json

SOURCE = "lsm6dso32_basic_interrupts"
FLAGS = ("free_fall", "wake_up", "orientation_changed")


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def board_motion_context(meta, content):
    """content=None means missing; empty bytes mean a present, empty event file."""
    config = meta.get("onboard_sensor", {})
    enabled = config.get("motion_events_version") == 1
    origin, boot = meta.get("device_origin_us"), meta.get("boot_id")
    clock_known = integer(origin) and origin >= 0 and isinstance(boot, str) and bool(boot)
    availability = "recorded" if enabled else "not_recorded"
    message = "" if enabled else "Board-event recording was not enabled for this session."
    if enabled and content is None:
        availability, message = "missing", "Board-event file is missing. Finish downloading or importing this recording."
    elif enabled and not clock_known:
        availability, message = "unverified", "Board events need this session's device clock before they can be aligned."
    elif enabled and (not meta.get("closed_utc") or meta.get("onboard_quality", {}).get("complete") is not True):
        availability, message = "incomplete", "Board-event recording is incomplete; observed events may be missing."
    events, ignored = [], 0
    previous = None
    for line_number, line in enumerate((content or b"").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Invalid event record at line {line_number}")
        if row.get("kind") != "board_motion":
            continue
        if not clock_known:
            continue
        if row.get("boot_id") != boot:
            ignored += 1
            continue
        if not enabled or row.get("source") != SOURCE:
            raise ValueError("Unconfigured or unsupported board-motion event")
        earliest, observed, latest = (row.get(key) for key in
                                     ("earliest_device_us", "device_us", "latest_device_us"))
        if (any(not integer(value) for value in (earliest, observed, latest))
                or not 0 < earliest <= observed <= latest or (previous is not None and observed <= previous)):
            raise ValueError("Invalid or reordered board-motion timestamps")
        wake, orientation = row.get("wake_up_src"), row.get("d6d_src")
        if any(not integer(value) or not 0 <= value <= 255 for value in (wake, orientation)):
            raise ValueError("Invalid board-motion source registers")
        expected = dict(zip(FLAGS, (bool(wake & 0x20), bool(wake & 0x08), bool(orientation & 0x40))))
        if not any(expected.values()) or any(not isinstance(row.get(key), bool) or row[key] != value for key, value in expected.items()):
            raise ValueError("Board-motion flags disagree with recorded registers")
        previous = observed
        events.append(dict(event_id=f"{boot}:{observed}", source_line=line_number, source=SOURCE,
                           boot_id=boot, device_us=observed, earliest_device_us=earliest, latest_device_us=latest,
                           earliest_s=(earliest - origin) / 1e6, observed_s=(observed - origin) / 1e6,
                           latest_s=(latest - origin) / 1e6, **expected,
                           wake_up_src=wake, d6d_src=orientation))
    warnings = [f"Ignored {ignored} board event(s) from another device boot."] if ignored else []
    return dict(schema=1, availability=availability, message=message, events=events, warnings=warnings,
                profile=config.get("motion_profile"), poll_interval_us=config.get("motion_poll_us"),
                observed_packets=len(events), ignored_other_boot=ignored)


def overlapping_events(events, start, end):
    """Closed uncertainty bounds intersect a half-open labeled window.

    A straddling interval can belong to two adjacent windows. Its event_id stays
    the same; neither join establishes the event's exact time or its outcome.
    """
    return [dict(event, relative_earliest_s=event["earliest_s"] - start,
                 relative_observed_s=event["observed_s"] - start,
                 relative_latest_s=event["latest_s"] - start,
                 fully_contained=start <= event["earliest_s"] and event["latest_s"] < end,
                 available_by_window_end=event["latest_s"] < end)
            for event in events if event["earliest_s"] < end and event["latest_s"] >= start]
