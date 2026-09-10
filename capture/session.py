#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5"]
# ///
"""Record board motion and human labels; this does not classify tricks.

Run with uv run capture/session.py --help. Demo, UDP, labeling, and video
alignment also work with plain Python (only USB recording needs pyserial).
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import json
import math
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__:
    from .video import CHUNK_LIMIT, VideoConflict, VideoStore
else:
    from video import CHUNK_LIMIT, VideoConflict, VideoStore

OUTCOMES = ("make", "bail", "fall", "background", "unknown")
FIELDS = (
    "t_s",
    "device_us",
    "host_monotonic_ns",
    "sequence",
    "boot_id",
    "ax",
    "ay",
    "az",
    "gx",
    "gy",
    "gz",
    "temp_C",
)


def append_json(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, allow_nan=False) + "\n")


def read_jsonl(path):
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


@contextmanager
def file_lock(path):
    """Serialize local writers, including separate review servers and the CLI."""
    import fcntl

    with Path(path).open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class BoardRestart(RuntimeError):
    pass


class Timeline:
    """Reject stale packets; unwrap legacy micros() and detect new-firmware boots."""

    def __init__(self):
        self.boot = None
        self.previous_us = None
        self.previous_sequence = None
        self.wrap_offset = 0
        self.origin = None

    def sample(self, line):
        parts = line.split(",")
        if len(parts) not in (9, 11) or parts[0] != "D":
            raise ValueError("expected a legacy or v2 D sample")
        device_us = int(parts[1])
        values = [float(value) for value in parts[2:9]]
        if device_us < 0 or not all(math.isfinite(value) for value in values):
            raise ValueError("invalid sample numbers")
        sequence = int(parts[9]) if len(parts) == 11 else None
        boot = parts[10] if len(parts) == 11 else "legacy"
        if sequence is not None and not 0 <= sequence < 2**32:
            raise ValueError("invalid sequence")
        if boot != "legacy" and (
            len(boot) != 8 or any(c not in "0123456789abcdef" for c in boot)
        ):
            raise ValueError("invalid boot ID")
        if self.boot is not None and boot != self.boot:
            raise BoardRestart(
                "Board restarted. Start a new session to keep labels on one clock."
            )
        missing = 0
        if sequence is not None and self.previous_sequence is not None:
            distance = (sequence - self.previous_sequence) % 2**32
            if distance == 0 or distance >= 2**31:
                return None  # duplicate / out of order UDP packet
            missing = distance - 1
        unwrapped = device_us + self.wrap_offset
        if self.previous_us is not None and unwrapped <= self.previous_us:
            previous_raw = self.previous_us - self.wrap_offset
            if (
                boot == "legacy"
                and previous_raw > 0xF0000000
                and device_us < 0x10000000
            ):
                self.wrap_offset += 2**32
                unwrapped = device_us + self.wrap_offset
            elif boot == "legacy":
                raise BoardRestart(
                    "Legacy board clock moved backwards; start a new session."
                )
            else:
                raise ValueError("timestamp did not advance with sequence")
        gap = 0 if self.previous_us is None else (unwrapped - self.previous_us) / 1e6
        if self.origin is None:
            self.origin = unwrapped
        self.boot = boot
        self.previous_us = unwrapped
        self.previous_sequence = sequence
        return (
            dict(
                t_s=(unwrapped - self.origin) / 1e6,
                device_us=unwrapped,
                sequence=sequence,
                boot_id=boot,
                **dict(zip(FIELDS[5:], values)),
            ),
            missing,
            gap,
        )


class Session:
    def __init__(self, path, metadata):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=False)
        self.metadata = dict(
            schema=1, created_utc=datetime.now(timezone.utc).isoformat(), **metadata
        )
        self.timeline = Timeline()
        self.csv_file = (self.path / "samples.csv").open(
            "w", newline="", encoding="utf-8"
        )
        self.writer = csv.DictWriter(self.csv_file, FIELDS)
        self.writer.writeheader()
        self.raw_file = (self.path / "raw.jsonl").open("w", encoding="utf-8")
        self.samples = self.malformed = self.stale = self.missing = self.gaps = 0
        self.last_host_ns = None
        self.latest = None
        self.pending = None
        self.syncs = set()
        self.last_sync = None
        self.save_metadata()

    def save_metadata(self):
        self.metadata.update(
            device_origin_us=self.timeline.origin,
            boot_id=self.timeline.boot,
            samples=self.samples,
            malformed=self.malformed,
            stale=self.stale,
            missing_sequences=self.missing,
            gaps_over_30ms=self.gaps,
            duration_s=self.latest,
        )
        # Atomic replacement leaves a readable manifest if recording is interrupted.
        temporary = self.path / "metadata.tmp"
        temporary.write_text(
            json.dumps(self.metadata, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path / "metadata.json")

    def event(self, kind, **fields):
        append_json(self.path / "events.jsonl", dict(kind=kind, **fields))

    def ingest(self, raw, host_ns=None):
        host_ns = time.monotonic_ns() if host_ns is None else host_ns
        line = (
            raw.decode("ascii", errors="replace").strip()
            if isinstance(raw, bytes)
            else raw.strip()
        )
        self.raw_file.write(
            json.dumps(dict(host_monotonic_ns=host_ns, line=line)) + "\n"
        )
        try:
            if line.startswith("D,"):
                result = self.timeline.sample(line)
                if result is None:
                    self.stale += 1
                    return
                row, missing, gap = result
                row["host_monotonic_ns"] = host_ns
                self.writer.writerow(row)
                self.samples += 1
                self.latest = row["t_s"]
                self.last_host_ns = host_ns
                self.missing += missing
                if gap > 0.03 or missing:
                    self.gaps += 1
                    self.event(
                        "gap", t_s=self.latest, gap_s=gap, missing_sequences=missing
                    )
                if self.samples == 1:
                    self.save_metadata()
            elif line.startswith("S,"):
                _, stamp, sync_id, edge, led, boot = line.split(",")
                stamp, sync_id, edge, led = map(int, (stamp, sync_id, edge, led))
                if stamp < 0 or sync_id < 1 or edge not in (0, 1) or led not in (0, 1):
                    raise ValueError("invalid sync marker")
                if self.timeline.boot is not None and boot != self.timeline.boot:
                    raise BoardRestart(
                        "Board restarted during sync. Start a new session."
                    )
                key = (boot, sync_id, edge)
                if key not in self.syncs:
                    self.syncs.add(key)
                    self.event(
                        "sync",
                        device_us=stamp,
                        sync_id=sync_id,
                        edge=edge,
                        led_enabled=bool(led),
                        boot_id=boot,
                        host_monotonic_ns=host_ns,
                    )
                    if edge:
                        self.last_sync = dict(
                            sync_id=sync_id,
                            device_us=stamp,
                            host_monotonic_ns=host_ns,
                            led_enabled=bool(led),
                        )
                        print(
                            f"Sync {sync_id}: {'LED flash' if led else 'marker only; LED is disabled'}",
                            flush=True,
                        )
            elif line:
                self.event("device", line=line, host_monotonic_ns=host_ns)
                if line.startswith("E,"):
                    print(line, file=sys.stderr, flush=True)
        except (ValueError, OverflowError) as error:
            self.malformed += 1
            self.event(
                "malformed", line=line, error=str(error), host_monotonic_ns=host_ns
            )

    def current_time(self):
        if (
            self.last_host_ns is None
            or time.monotonic_ns() - self.last_host_ns > 500_000_000
        ):
            raise ValueError(
                "No fresh samples. Wait for the stream before marking a trick."
            )
        return self.latest

    def mark(self, command):
        name, _, text = command.partition(" ")
        if name == "start":
            if self.pending is not None:
                raise ValueError("A trick is already open; label it or use cancel.")
            self.pending = dict(start_s=self.current_time(), trick=text.strip())
            self.event("attempt_start", **self.pending)
            print(f"Attempt started at {self.pending['start_s']:.3f}s", flush=True)
        elif name in OUTCOMES:
            if self.pending is None:
                raise ValueError("Use start before assigning an outcome.")
            end = self.current_time()
            if end <= self.pending["start_s"]:
                raise ValueError("Wait for samples after the start of the attempt.")
            label = dict(
                id=uuid.uuid4().hex,
                **self.pending,
                end_s=end,
                outcome=name,
                source="live_human",
                note=text.strip(),
                created_utc=datetime.now(timezone.utc).isoformat(),
            )
            append_json(self.path / "labels.jsonl", label)
            self.pending = None
            print(f"Labeled {name}: {label['start_s']:.3f}–{end:.3f}s", flush=True)
        elif name == "cancel":
            self.event("attempt_cancelled", attempt=self.pending)
            self.pending = None
        elif name == "note" and text.strip():
            self.event("note", t_s=self.latest, text=text.strip())
        else:
            raise ValueError(
                "Commands: start [trick], make, bail, fall, background, unknown, cancel, countdown, sync, note TEXT, quit"
            )

    def flush(self):
        self.csv_file.flush()
        self.raw_file.flush()

    def close(self):
        if self.pending:
            self.event("unfinished_attempt", **self.pending)
        self.metadata["closed_utc"] = datetime.now(timezone.utc).isoformat()
        self.save_metadata()
        self.csv_file.close()
        self.raw_file.close()


class DemoSource:
    def __init__(self):
        self.start = time.monotonic()
        self.next_sample = self.start
        self.sequence = 0
        self.sync_id = 0
        self.pending = []

    def read(self):
        if self.pending and self.pending[0][0] <= time.monotonic():
            return self.pending.pop(0)[1]
        time.sleep(max(0, self.next_sample - time.monotonic()))
        now = time.monotonic()
        self.next_sample = now + 0.01
        stamp = int((now - self.start) * 1e6)
        value = f"D,{stamp},0,0,9.80665,0,0,{math.sin(stamp / 1e6):.5f},22,{self.sequence},de000001"
        self.sequence += 1
        return value

    def send(self, command):
        if command == b"s":
            self.sync_id += 1
            now = time.monotonic()
            stamp = int((now - self.start) * 1e6)
            self.pending.extend(
                [
                    (now, f"S,{stamp},{self.sync_id},1,0,de000001"),
                    (now + 0.15, f"S,{stamp + 150000},{self.sync_id},0,0,de000001"),
                ]
            )

    def close(self):
        pass


class UDPSource:
    def __init__(self, host, port):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(0.1)
        self.socket.connect((host, port))

    def read(self):
        try:
            return self.socket.recv(2048)
        except socket.timeout:
            return b""

    def send(self, command):
        self.socket.send(command)

    def close(self):
        self.socket.close()


class SerialSource:
    def __init__(self, port):
        import serial
        import serial.tools.list_ports

        if port == "auto":
            ports = [
                p.device
                for p in serial.tools.list_ports.comports()
                if p.vid in {0x303A, 0x239A, 0x10C4, 0x1A86, 0x0403}
                or any(
                    s in p.device for s in ("usbmodem", "usbserial", "ttyACM", "ttyUSB")
                )
            ]
            if len(ports) != 1:
                raise ValueError(
                    f"Expected one serial board, found {ports}. Pass --serial PORT."
                )
            port = ports[0]
        self.serial = serial.Serial(port, 115200, timeout=0.1, write_timeout=1)
        self.buffer = b""

    def read(self):
        # A timeout can split a line; preserve it for the next read.
        self.buffer += self.serial.read_until(b"\n", size=1024)
        if b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            return line
        if len(self.buffer) > 4096:
            line, self.buffer = self.buffer, b""
            return line
        return b""

    def send(self, command):
        self.serial.write(command)

    def close(self):
        self.serial.close()


class SyncTrigger:
    """Computer-side countdown. Samples keep flowing while we wait for each deadline."""

    def __init__(self):
        self.phase = "ready"
        self.remaining = None
        self.deadline = 0.0
        self.request_ns = 0
        self.request_device_us = 0
        self.message = "Start video, then start the countdown."

    @property
    def busy(self):
        return self.phase in ("countdown", "waiting")

    def start(self, session):
        if self.busy:
            raise ValueError("A sync is already in progress.")
        session.current_time()
        self.phase = "countdown"
        self.remaining = 3
        self.deadline = time.monotonic() + 3
        self.message = "Sync in 3…"
        session.event(
            "countdown_start", t_s=session.latest, host_monotonic_ns=time.monotonic_ns()
        )
        print(self.message, flush=True)

    def request(self, session, source):
        session.current_time()
        self.request_ns = time.monotonic_ns()
        self.request_device_us = session.timeline.previous_us
        source.send(b"s")
        self.phase = "waiting"
        self.remaining = None
        self.deadline = time.monotonic() + 2
        self.message = "Flash requested; waiting for the board's timestamp."
        session.event(
            "sync_requested", t_s=session.latest, host_monotonic_ns=self.request_ns
        )
        print(self.message, flush=True)

    def tick(self, session, source):
        if self.phase == "countdown":
            try:
                session.current_time()
            except ValueError:
                self.phase = "error"
                self.remaining = None
                self.message = "Countdown cancelled: motion data stopped. Reconnect, then try again."
                session.event(
                    "countdown_cancelled", t_s=session.latest, reason="stale_stream"
                )
                print(self.message, flush=True)
                return
            remaining = max(0, math.ceil(self.deadline - time.monotonic()))
            if remaining == 0:
                self.request(session, source)
            elif remaining != self.remaining:
                self.remaining = remaining
                self.message = f"Sync in {remaining}…"
                print(self.message, flush=True)
        elif self.phase == "waiting":
            marker = session.last_sync
            if (
                marker
                and marker["host_monotonic_ns"] >= self.request_ns
                and marker["device_us"] > self.request_device_us
            ):
                if marker["led_enabled"]:
                    self.phase = "done"
                    self.message = f"Flash acknowledged — sync {marker['sync_id']}."
                else:
                    self.phase = "error"
                    self.message = f"Sync {marker['sync_id']} received, but the physical LED is disabled."
                print(self.message, flush=True)
            elif time.monotonic() >= self.deadline:
                self.phase = "error"
                self.message = (
                    "No flash acknowledgement. Check the connection and try again."
                )
                session.event(
                    "sync_unacknowledged",
                    t_s=session.latest,
                    request_host_ns=self.request_ns,
                )
                print(self.message, flush=True)


class ControlServer:
    """Optional local browser button; recording and timing remain in the main loop."""

    def __init__(self, commands, session_path, port=0, onboard=None):
        self.commands = commands
        self.lock = threading.Lock()
        self.status = dict(can_sync=False, message="Waiting for motion data.")
        self.countdown_pending = False
        self.videos = VideoStore(session_path)
        assets = {
            "/": ("controls.html", "text/html; charset=utf-8"),
            "/controls.mjs": ("controls.mjs", "text/javascript; charset=utf-8"),
            "/webcam.mjs": ("webcam.mjs", "text/javascript; charset=utf-8"),
        }
        pages = {
            url: (Path(__file__).with_name(name).read_bytes(), mime)
            for url, (name, mime) in assets.items()
        }
        control = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body, content_type="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.headers.get("Host") != control.address:
                    self.reply(403, b"{}")
                elif self.path in pages:
                    self.reply(200, *pages[self.path])
                elif self.path == "/api/status":
                    with control.lock:
                        body = json.dumps(control.status).encode()
                    self.reply(200, body)
                else:
                    self.reply(404, b"{}")

            def read_body(self, limit):
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Chunked HTTP transfers are not supported.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= limit:
                    raise ValueError("Request body is too large.")
                self.connection.settimeout(3)
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("Incomplete request body.")
                return body

            def do_POST(self):
                if (
                    self.headers.get("Host") != control.address
                    or self.headers.get("Origin") != control.url
                ):
                    self.reply(403, b"{}")
                    return
                try:
                    if self.path.startswith("/api/video/chunk/"):
                        if (
                            self.headers.get("Content-Type")
                            != "application/octet-stream"
                        ):
                            raise ValueError("Expected binary video data.")
                        _, _, _, _, clip_id, sequence = self.path.split("/")
                        result = control.videos.chunk(
                            clip_id, int(sequence), self.read_body(CHUNK_LIMIT)
                        )
                    else:
                        if self.headers.get("Content-Type") != "application/json":
                            raise ValueError("Expected JSON.")
                        data = json.loads(self.read_body(8192) or b"{}")
                        if not isinstance(data, dict):
                            raise ValueError("Expected a JSON object.")
                        if self.path in ("/api/prepare", "/api/countdown"):
                            if data.get("external") is not True and not control.videos.ready_for_sync(data.get("video_id")):
                                raise VideoConflict("Start video and wait for the first saved data before syncing.")
                        if self.path == "/api/prepare":
                            if onboard:
                                result = onboard.prepare()
                            else:
                                with control.lock:
                                    if not control.status.get("fresh"):
                                        raise VideoConflict("Wait for fresh motion data before syncing.")
                                result = dict(ready=True)
                        elif self.path == "/api/board/finish" and onboard:
                            result = onboard.finish()
                        elif self.path == "/api/countdown":
                            control.queue_countdown(onboard)
                            self.reply(202, b"{}")
                            return
                        elif self.path == "/api/video/start":
                            result = control.videos.start(data)
                        elif self.path == "/api/video/finish":
                            result = control.videos.finish(data)
                        elif self.path == "/api/video/abandon":
                            result = control.videos.abandon(data.get("id", ""))
                        else:
                            self.reply(404, b"{}")
                            return
                    self.reply(200, json.dumps(result).encode())
                except VideoConflict as error:
                    self.reply(409, json.dumps(dict(error=str(error))).encode())
                except (ValueError, TypeError) as error:
                    self.reply(400, json.dumps(dict(error=str(error))).encode())
                except OSError as error:
                    self.reply(
                        503 if onboard and self.path in ("/api/prepare", "/api/countdown", "/api/board/finish") else 507,
                        json.dumps(dict(error=f"Recording operation failed: {error}")).encode(),
                    )

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.address = f"127.0.0.1:{self.server.server_port}"
        self.url = f"http://{self.address}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def queue_countdown(self, onboard=None):
        with self.lock:
            if self.countdown_pending or self.status.get("phase") in ("countdown", "waiting"):
                raise VideoConflict("A sync is already in progress. Wait for it to finish before retrying.")
            if not onboard and not self.status.get("can_sync"):
                raise VideoConflict("Wait for fresh motion data before syncing.")
            # Reserve the request across board I/O and main-loop publications.
            self.countdown_pending = True
            self.status["can_sync"] = False
        try:
            if onboard:
                # prepare() can finish before the polling loop publishes its
                # healthy reading. Check the board, not that older UI snapshot.
                onboard.check_sync_ready()
            self.commands.put("countdown")
        except (ValueError, OSError) as error:
            with self.lock:
                self.countdown_pending = False
            if isinstance(error, ValueError):
                raise VideoConflict(str(error)) from error
            raise

    def publish_status(self, status, *, countdown_handled=False):
        with self.lock:
            if countdown_handled:
                self.countdown_pending = False
            self.status = dict(status, can_sync=bool(status.get("can_sync") and not self.countdown_pending))

    def publish(self, session, trigger, *, countdown_handled=False):
        fresh = (
            session.last_host_ns is not None
            and time.monotonic_ns() - session.last_host_ns <= 500_000_000
        )
        self.publish_status(
            dict(
                session=session.path.name,
                session_path=str(session.path.resolve()),
                synthetic=session.metadata.get("synthetic", False),
                samples=session.samples,
                duration_s=session.latest or 0,
                can_sync=fresh and not trigger.busy,
                fresh=fresh,
                phase=trigger.phase,
                remaining=trigger.remaining,
                message=trigger.message
                if fresh or trigger.busy
                else "Waiting for fresh motion data. Check the recorder connection.",
            ),
            countdown_handled=countdown_handled,
        )

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.videos.close()


def record(args):
    if args.duration is not None and (
        not math.isfinite(args.duration) or args.duration <= 0
    ):
        raise ValueError("--duration must be positive and finite")
    if not math.isfinite(args.sync_every) or args.sync_every < 0:
        raise ValueError("--sync-every must be nonnegative and finite")
    if not 0 <= args.controls_port <= 65535:
        raise ValueError("--controls-port must be between 0 and 65535")
    path = args.output or Path("sessions") / (
        datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    )
    if Path(path).exists():
        raise ValueError(f"Session already exists: {path}. Use a new directory.")
    source = (
        DemoSource()
        if args.demo
        else UDPSource(args.udp, args.udp_port)
        if args.udp
        else SerialSource(args.serial)
    )
    try:
        session = Session(
            path,
            dict(
                rider=args.rider,
                board=args.board,
                mounting=args.mounting,
                surface=args.surface,
                video=args.video,
                transport="demo" if args.demo else "udp" if args.udp else "serial",
                synthetic=args.demo,
            ),
        )
    except Exception:
        source.close()
        raise
    commands = queue.Queue()
    trigger = SyncTrigger()

    def stdin_reader():
        for line in sys.stdin:
            commands.put(line.strip())
        # EOF on a pipe should not discard samples while --duration is running.

    threading.Thread(target=stdin_reader, daemon=True).start()
    print(
        f"Recording {session.path}\nCommands: start [trick], make / bail / fall / background / unknown, cancel, countdown, sync, note TEXT, quit",
        flush=True,
    )
    started = last_flush = last_warning = time.monotonic()
    last_heartbeat = last_sync = 0.0
    controls = None
    try:
        if args.controls:
            controls = ControlServer(commands, session.path, args.controls_port)
            print(
                f"Open countdown controls on this computer: {controls.url}", flush=True
            )
        source.send(b"i")
        while args.duration is None or time.monotonic() - started < args.duration:
            countdown_handled = False
            now = time.monotonic()
            if args.udp and now - last_heartbeat >= 2:
                source.send(b"i" if session.samples == 0 else b"k")
                last_heartbeat = now
            raw = source.read()
            if raw:
                session.ingest(raw)
            while not commands.empty():
                command = commands.get_nowait()
                if command in ("quit", "q"):
                    if controls and controls.videos.active:
                        print(
                            "Stop & save the webcam video in its browser tab first. Ctrl-C forces exit with a partial video.",
                            flush=True,
                        )
                        continue
                    return
                if command:
                    try:
                        if command == "countdown":
                            countdown_handled = True
                            trigger.start(session)
                            last_sync = now
                        elif command == "sync":
                            if trigger.busy:
                                raise ValueError("A sync is already in progress.")
                            trigger.request(session, source)
                            last_sync = now
                        else:
                            session.mark(command)
                    except ValueError as error:
                        print(error, file=sys.stderr, flush=True)
            trigger.tick(session, source)
            if (
                args.sync_every
                and session.samples
                and not trigger.busy
                and now - last_sync >= args.sync_every
            ):
                try:
                    trigger.request(session, source)
                except ValueError as error:
                    print(error, file=sys.stderr, flush=True)
                last_sync = now
            if now - last_flush >= 1:
                session.flush()
                last_flush = now
            age = (
                now - started
                if session.last_host_ns is None
                else (time.monotonic_ns() - session.last_host_ns) / 1e9
            )
            if age > 5 and now - last_warning > 5:
                print(
                    "No fresh motion data for 5s. Check power / connection; labels are paused.",
                    file=sys.stderr,
                    flush=True,
                )
                session.event("stream_timeout", t_s=session.latest)
                last_warning = now
            if controls:
                controls.publish(session, trigger, countdown_handled=countdown_handled)
            if not session.samples and now - started > 15:
                raise ValueError(
                    "No samples received. Check board firmware, sensor, and selected transport."
                )
    except KeyboardInterrupt:
        pass
    except Exception as error:
        session.event("recording_error", error=str(error))
        raise
    finally:
        try:
            if controls:
                controls.close()
        finally:
            try:
                session.close()
            finally:
                source.close()
        print(
            f"Saved {session.samples} samples; {session.missing} missing sequences, {session.gaps} gaps, "
            f"{session.malformed} malformed, {session.stale} stale packets. Session: {session.path}",
            flush=True,
        )


def metadata(path):
    return json.loads((Path(path) / "metadata.json").read_text(encoding="utf-8"))


def sync_point(args):
    meta = metadata(args.session)
    if meta.get("synthetic"):
        raise ValueError(
            "Demo markers do not flash a physical LED and cannot align video."
        )
    if not math.isfinite(args.video_seconds) or args.video_seconds < 0:
        raise ValueError("Video time must be finite and nonnegative")
    matches = [
        event
        for event in read_jsonl(args.session / "events.jsonl")
        if event.get("kind") == "sync"
        and event["sync_id"] == args.sync_id
        and event["edge"] == 1
        and event["boot_id"] == meta["boot_id"]
    ]
    if len(matches) != 1 or not matches[0]["led_enabled"]:
        raise ValueError(
            "Expected one recorded rising edge with a physical LED enabled."
        )
    value = dict(
        video=args.video,
        sync_id=args.sync_id,
        video_s=args.video_seconds,
        t_s=(matches[0]["device_us"] - meta["device_origin_us"]) / 1e6,
    )
    points = {row["sync_id"]: row for row in read_jsonl(args.session / "video_sync.jsonl")
              if row["video"] == args.video}
    points[args.sync_id] = value
    scale, offset, count = fit_video_points(list(points.values()))
    append_json(args.session / "video_sync.jsonl", value)
    summary = ("1 flash matched. Add a second flash near the end to estimate clock drift."
               if count == 1 else
               f"Using {count} flash matches; drift correction is active "
               f"(clock scale {scale:.9f}, offset {offset:+.6f}s).")
    print(f"Linked video {args.video_seconds:.3f}s to session {value['t_s']:.3f}s. {summary}")


def video_mapping(path, video):
    # Re-entering a flash corrects its existing correspondence.
    points = {
        row["sync_id"]: row
        for row in read_jsonl(path / "video_sync.jsonl")
        if row["video"] == video
    }
    if not points:
        raise ValueError(
            "No video sync points. Use align with the visible LED flash time first."
        )
    return fit_video_points(list(points.values()))


def fit_video_points(points):
    """Fit and validate already matched video/session flash correspondences."""
    if not points:
        raise ValueError("At least one video sync point is required")
    xs = [row["video_s"] for row in points]
    ys = [row["t_s"] for row in points]
    x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if len(points) > 1 and denominator <= 0:
        raise ValueError("Sync points need distinct video times")
    scale = (
        sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
        if denominator
        else 1.0
    )
    if not 0.98 <= scale <= 1.02:
        raise ValueError(
            "Video clock differs by over 2%; check flash IDs and use original-speed video."
        )
    offset = y_mean - scale * x_mean
    if any(abs(scale * x + offset - y) > 0.1 for x, y in zip(xs, ys)):
        raise ValueError(
            "Sync points disagree by over 100ms; check flash correspondence."
        )
    return scale, offset, len(points)


def build_label(args):
    """Validate and map a human label without writing it."""
    if args.outcome not in OUTCOMES:
        raise ValueError("Choose make, bail, fall, background, or unknown.")
    for name, limit in (("trick", 200), ("note", 2000)):
        value = getattr(args, name)
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"{name.title()} must be at most {limit} characters.")
    meta = metadata(args.session)
    if "closed_utc" not in meta:
        raise ValueError("Stop recording before adding or correcting offline labels.")
    if args.video:
        scale, offset, count = video_mapping(args.session, args.video)
        start, end = scale * args.start + offset, scale * args.end + offset
        if count == 1:
            print("Only one sync point: assuming no clock drift.")
    else:
        start, end = args.start, args.end
    if not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end:
        raise ValueError("Label must have finite times with 0 <= start < end")
    if meta["duration_s"] is None or end > meta["duration_s"]:
        raise ValueError("Label extends past the recorded samples")
    existing = {row["id"]: row for row in read_jsonl(args.session / "labels.jsonl")}
    if args.replace and args.replace not in existing:
        raise ValueError("--replace must name an existing label ID")
    for row in existing.values():
        if row["id"] != args.replace and start < row["end_s"] and end > row["start_s"]:
            raise ValueError(
                f"Label overlaps {row['id']}. Use --replace ID to correct that label."
            )
    row = dict(
        id=args.replace or uuid.uuid4().hex,
        start_s=start,
        end_s=end,
        outcome=args.outcome,
        trick=args.trick,
        note=args.note,
        source="video_human" if args.video else "offline_human",
        video=args.video,
        video_start_s=args.start if args.video else None,
        video_end_s=args.end if args.video else None,
        created_utc=datetime.now(timezone.utc).isoformat(),
    )
    if args.video:
        row["video_mapping"] = dict(scale=scale, offset_s=offset, points=count)
    if args.replace and existing[args.replace].get("video") == args.video:
        for key in ("review_interval", "review_source", "review_algorithm", "source_pts_origin_s"):
            if key in existing[args.replace]:
                row[key] = existing[args.replace][key]
    return row


def label(args):
    with file_lock(args.session / ".labels.lock"):
        row = build_label(args)
        append_json(args.session / "labels.jsonl", row)
    print(f"Saved {row['id']}: {args.outcome} {row['start_s']:.3f}–{row['end_s']:.3f}s")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    rec = subparsers.add_parser(
        "record", help="Record continuous raw motion with optional human labels"
    )
    source = rec.add_mutually_exclusive_group()
    source.add_argument("--serial", default="auto", metavar="PORT")
    source.add_argument("--udp", metavar="BOARD_IP")
    source.add_argument("--demo", action="store_true")
    rec.add_argument("--udp-port", type=int, default=5050)
    rec.add_argument("--output", type=Path)
    rec.add_argument(
        "--controls",
        action="store_true",
        help="Serve webcam recording and sync controls in a browser on this computer",
    )
    rec.add_argument(
        "--controls-port",
        type=int,
        default=0,
        help="Local controls port (default: choose a free port)",
    )
    rec.add_argument(
        "--duration", type=float, help="Stop after this many host-clock seconds"
    )
    rec.add_argument(
        "--sync-every",
        type=float,
        default=0,
        help="Optional automatic LED flashes every N seconds (default: 0, manual only)",
    )
    for key in ("rider", "board", "mounting", "surface", "video"):
        rec.add_argument(f"--{key}", default="")
    rec.set_defaults(func=record)
    align = subparsers.add_parser(
        "align", help="Match a recorded LED flash to an original-speed video frame"
    )
    align.add_argument("session", type=Path)
    align.add_argument("--sync-id", type=int, required=True)
    align.add_argument("--video", required=True, help="Video filename / identifier")
    align.add_argument("--video-seconds", type=float, required=True)
    align.set_defaults(func=sync_point)
    lab = subparsers.add_parser(
        "label", help="Add or correct a human outcome interval after recording"
    )
    lab.add_argument("session", type=Path)
    lab.add_argument("--start", type=float, required=True)
    lab.add_argument("--end", type=float, required=True)
    lab.add_argument("--outcome", choices=OUTCOMES, required=True)
    lab.add_argument("--trick", default="")
    lab.add_argument("--note", default="")
    lab.add_argument(
        "--video", help="Interpret start/end in this video's seconds using align points"
    )
    lab.add_argument(
        "--replace", help="Existing label ID to revise (append-only audit history)"
    )
    lab.set_defaults(func=label)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, OSError, BoardRestart, ImportError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
