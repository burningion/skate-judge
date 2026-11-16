"""Decode onboard packets as they arrive, for live use.

The importer in onboard_log.py interpolates between clock anchors on both
sides of a sample, which needs the future. This decoder only looks back: a
sample's time is extrapolated from the newest anchor at the rate measured over
the retained anchors. The phone implements the same rules; keep them in step.
"""

from __future__ import annotations

from collections import deque
import math
import struct
import zlib

from .onboard_log import CLOCK, FIFO, HEADER

MAX_PAYLOAD = 896
ANCHORS = 6           # About five seconds of baseline for the tick rate.
MAX_ANCHOR_READ_US = 2000
REPLY = struct.Struct("<4sII")


def parse_reply(body):
    """Split a /live response into (start offset, end offset of the recording so far, bytes)."""
    if len(body) < REPLY.size:
        raise ValueError("short live reply")
    magic, offset, head = REPLY.unpack_from(body)
    data = body[REPLY.size:]
    if magic != b"LIV1" or offset + len(data) > head:
        raise ValueError("invalid live reply")
    return offset, head, data


class LiveDecoder:
    """feed(offset, bytes) -> [(device_us, ax, ay, az, gx, gy, gz)] in physical units."""

    def __init__(self):
        self.next_offset = None
        self.buffer = b""
        self.synced = False
        self.sequence = None
        self.anchors = deque(maxlen=ANCHORS)
        self.first_tick = None
        self.slot, self.counter = {}, None
        self.last_us = None
        self.resyncs = self.dropped_samples = 0

    def _break(self):
        self.buffer, self.synced, self.sequence = b"", False, None
        self.slot, self.counter = {}, None

    def feed(self, offset, data):
        if self.next_offset is not None and offset != self.next_offset:
            self._break()   # Bytes were lost; the next packet boundary is unknown.
            self.resyncs += 1
        elif self.next_offset is None and offset != 0:
            self.resyncs += 1
        self.next_offset = offset + len(data)
        self.buffer += data
        samples = []
        while True:
            if not self.synced and offset == 0 and self.sequence is None and self.buffer[:4] == b"SKL1":
                self.synced = True
            if not self.synced:
                start = self.buffer.find(b"SKL1")
                if start < 0:
                    self.buffer = self.buffer[-3:]
                    break
                self.buffer = self.buffer[start:]
            if len(self.buffer) < HEADER.size:
                break
            magic, kind, version, length, number, stamp, checksum = HEADER.unpack_from(self.buffer)
            valid = magic == b"SKL1" and version == 1 and kind <= 5 and length <= MAX_PAYLOAD
            if valid and len(self.buffer) < HEADER.size + length:
                break
            payload = self.buffer[HEADER.size:HEADER.size + length] if valid else b""
            if not valid or zlib.crc32(payload, zlib.crc32(self.buffer[:20])) != checksum:
                # Not a packet boundary after all; keep searching one byte on.
                self.buffer, self.synced = self.buffer[1:], False
                self.slot, self.counter = {}, None
                continue
            if self.sequence is not None and number != self.sequence + 1:
                self.slot, self.counter = {}, None
            self.synced, self.sequence = True, number
            self.buffer = self.buffer[HEADER.size + length:]
            if kind == CLOCK and length == 12:
                end_us, tick = struct.unpack("<QI", payload)
                if stamp <= end_us and end_us - stamp <= MAX_ANCHOR_READ_US:
                    if self.first_tick is None:
                        self.first_tick = tick
                    relative = self._relative(tick)
                    if not self.anchors or relative > self.anchors[-1][0]:
                        self.anchors.append((relative, (stamp + end_us) / 2))
            elif kind == FIFO and length and length % 7 == 0:
                for i in range(0, length, 7):
                    self._word(payload[i:i + 7], samples)
        return samples

    def _relative(self, tick):
        return ((tick - self.first_tick + 2**31) % 2**32) - 2**31

    def _word(self, word, samples):
        kind, count = word[0] >> 3, (word[0] >> 1) & 3
        if word[0].bit_count() % 2 or kind not in (1, 2, 3, 4):
            self.slot, self.counter = {}, None
            return
        if self.counter is not None and count != self.counter:
            self._finish(samples)
            self.slot = {}
        self.counter = count
        self.slot[kind] = word[1:]

    def _finish(self, samples):
        slot = self.slot
        if not all(kind in slot for kind in (1, 2, 4)) or slot[4][5] != 0x55 or len(self.anchors) < 2:
            return
        (x0, y0), (x1, y1) = self.anchors[0], self.anchors[-1]
        tick = self._relative(struct.unpack_from("<I", slot[4])[0])
        stamp = y1 + (tick - x1) * (y1 - y0) / (x1 - x0)
        if self.last_us is not None and stamp <= self.last_us:
            self.dropped_samples += 1
            return
        self.last_us = stamp
        accel = [raw * .000976 * 9.80665 for raw in struct.unpack("<hhh", slot[2])]
        gyro = [raw * .070 * math.pi / 180 for raw in struct.unpack("<hhh", slot[1])]
        samples.append((stamp, *accel, *gyro))


def decode_file(path, chunk=1000):
    """Replay a finished raw recording through the live decoder in poll-sized pieces."""
    data = open(path, "rb").read()
    decoder, samples = LiveDecoder(), []
    for offset in range(0, len(data), chunk):
        samples.extend(decoder.feed(offset, data[offset:offset + chunk]))
    return samples
