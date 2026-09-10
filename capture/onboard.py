#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5"]
# ///
"""Record motion on the ESP32 flash; download only after recording stops.

python3 capture/onboard.py record
python3 capture/onboard.py list
python3 capture/onboard.py download ID --output sessions/recovered
"""

from __future__ import annotations

import argparse
import io
from datetime import datetime, timezone
from http.client import IncompleteRead
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid
import zlib

if __package__:
    from .onboard_log import import_log, read_packets
    from .session import ControlServer
else:
    from onboard_log import import_log, read_packets
    from session import ControlServer


def valid_id(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise ValueError("Expected a 32-character recording ID from the board's file list.")
    return identity


def checksum(path):
    crc = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            crc = zlib.crc32(chunk, crc)
    return f"{crc:08x}"


class BoardClient:
    def __init__(self, address="http://192.168.4.1", opener=urlopen):
        if "://" not in address:
            address = "http://" + address
        parsed = urlsplit(address)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("Use the board's HTTP address, for example http://192.168.4.1.")
        self.address = address.rstrip("/")
        self.opener = opener
        self.lock = threading.Lock()

    def close(self):
        pass

    def request(self, route, params=None, post=False, timeout=2):
        url = self.address + route + ("?" + urlencode(params) if params else "")
        request = Request(url, data=b"" if post else None, method="POST" if post else "GET")
        try:
            with self.lock, self.opener(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                message = json.load(error).get("error", str(error))
            except (ValueError, AttributeError):
                message = str(error)
            raise ValueError(message) from error
        except (URLError, TimeoutError, OSError) as error:
            raise OSError(f"Board connection unavailable: {error}") from error

    def download(self, identity, directory):
        identity = valid_id(identity)
        info = self.request("/file-info", {"id": identity})
        if info.get("id") != identity or not isinstance(info.get("bytes"), int) or info["bytes"] <= 0:
            raise ValueError("Invalid file metadata from board.")
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"onboard-{identity}.bin"
        partial = target.with_suffix(".bin.part")
        if target.exists():
            if target.stat().st_size != info["bytes"] or checksum(target) != info["crc32"]:
                raise ValueError("An existing download differs from the board; it will not be overwritten.")
            return target
        last_error = None
        for _ in range(3):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > info["bytes"]:
                raise ValueError("Partial file is larger than the board recording; keep it and use a new directory.")
            if offset == info["bytes"]:
                break
            url = self.address + "/file?" + urlencode(dict(id=identity, offset=offset))
            try:
                with self.lock, self.opener(url, timeout=5) as response:
                    if response.headers.get("X-Log-CRC32") != info["crc32"]:
                        raise ValueError("Recording changed during download.")
                    with partial.open("ab") as stream:
                        remaining = info["bytes"] - offset
                        while remaining:
                            chunk = response.read(min(65536, remaining))
                            if not chunk:
                                raise OSError("Download ended early.")
                            stream.write(chunk)
                            remaining -= len(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())
                break
            except (OSError, URLError, IncompleteRead) as error:
                last_error = error
        if not partial.exists() or partial.stat().st_size != info["bytes"]:
            raise OSError(f"Download incomplete; partial data is saved at {partial}. Retry after reconnecting. {last_error}")
        if checksum(partial) != info["crc32"]:
            raise ValueError("Downloaded file checksum mismatch; retain it and download to a new directory.")
        packets, _, _ = read_packets(partial, allow_incomplete=True)
        if json.loads(packets[0][2]).get("id") != identity:
            raise ValueError("Downloaded recording ID does not match.")
        partial.rename(target)
        return target


class SerialResponse:
    def __init__(self, port, size, crc):
        self.port, self.remaining = port, size
        self.headers = {"X-Log-CRC32": crc}

    def read(self, size):
        data = self.port.read(min(size, self.remaining))
        if not data and self.remaining:
            raise OSError("USB download timed out; reconnect and retry.")
        self.remaining -= len(data)
        return data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class SerialBoardClient(BoardClient):
    def __init__(self, address):
        import serial
        import serial.tools.list_ports

        port = address.removeprefix("serial:")
        if port == "auto":
            ports = [p.device for p in serial.tools.list_ports.comports() if p.vid in (0x303A, 0x239A)]
            if len(ports) != 1:
                raise ValueError(f"Expected one USB board, found {ports}. Pass --board serial:/dev/cu.usbmodem...")
            port = ports[0]
        self.port = serial.Serial(port, 115200, timeout=2, write_timeout=2)
        self.address = address
        self.lock = threading.Lock()
        self.opener = self._open

    def _open(self, request, timeout):
        url = request if isinstance(request, str) else request.full_url
        route = url[len(self.address):]
        method = "GET" if isinstance(request, str) else request.get_method()
        self.port.timeout = timeout
        self.port.write(f"{method} {route}\n".encode())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.port.read_until(b"\n", size=4096)
            if not line.startswith(b'{"status":'):
                continue  # Skip boot diagnostics before the protocol reply.
            reply = json.loads(line)
            if reply["status"] >= 400:
                raise ValueError(reply["body"].get("error", "Board request failed."))
            if "binary" in reply:
                return SerialResponse(self.port, reply["binary"], reply["crc32"])
            return io.BytesIO(json.dumps(reply["body"]).encode())
        raise OSError("USB board did not answer. Install the onboard logger firmware.")

    def close(self):
        self.port.close()


def board_client(address):
    return SerialBoardClient(address) if address.startswith("serial:") else BoardClient(address)


def test_led(client, timeout=12):
    before = client.request("/status")
    if before.get("phase") not in ("idle", "saved", "fault"):
        raise ValueError("Stop recording and wait for the board to be idle before testing LEDs.")
    if "led_tests" not in before:
        raise ValueError("Install the updated logger with ./flash-feather.sh to use test-led.")
    print(f"Watch the stick on GPIO{before['led_pin']}: three one-second white pulses "
          f"({before['led_count']} pixels, {before['led_format']}, brightness {before['led_brightness']}/255).",
          flush=True)
    client.request("/test-led", post=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.request("/status")
        if status.get("boot_id") != before.get("boot_id"):
            raise ValueError("Board restarted during the LED test; completion is unconfirmed.")
        if status.get("led_tests", 0) > before["led_tests"]:
            print("LED commands completed; LEDs commanded off. Confirm visible light by eye.", flush=True)
            for key, label in (("led_test_battery_before_mv", "Battery before test"),
                               ("led_test_battery_on_mv", "Battery with LEDs commanded on")):
                value = status.get(key, -1)
                print(f"{label}: {value / 1000:.3f} V" if value >= 0 else f"{label}: unavailable")
            rmt_ready = status.get("led_test_rmt_ready", -1)
            print("GPIO LED transmitter attached and idle: " + {1: "yes", 0: "no"}.get(rmt_ready, "unavailable"))
            return status
        time.sleep(.25)
    raise ValueError("LED test completion was not acknowledged. Check board status.")


def healthy(status):
    return (status.get("phase") == "recording" and 180 <= status.get("accel_hz", 0) <= 230
            and 180 <= status.get("gyro_hz", 0) <= 230 and not status.get("error")
            and not status.get("io_errors") and not status.get("fifo_overruns")
            and status.get("zero_accel", 0) <= status.get("accel_samples", 0) * .25)


class OnboardRecording:
    def __init__(self, client, path):
        self.client, self.path = client, Path(path)
        self.identity = None
        self.boot = None
        self.result = None
        self.operation = threading.Lock()
        self.remote = None
        self.message = "Connect to the board's Wi-Fi to prepare recording."
        self.downloading = False

    def status(self):
        status = self.client.request("/status")
        if status.get("protocol") != 1:
            raise ValueError("Install the onboard logger firmware first.")
        if self.boot and status.get("boot_id") != self.boot:
            raise ValueError("Board restarted. The previous flash log is retained; use the download command to recover it.")
        self.remote = status
        return status

    def prepare(self):
        with self.operation:
            if self.result:
                raise ValueError("This batch is saved. Start a new recorder command for the next batch.")
            status = self.status()
            if status["phase"] in ("recording", "starting", "stopping") and status.get("id") != self.identity:
                raise ValueError("The board already has an active recording. It will not be replaced.")
            if not status.get("storage_ready"):
                raise ValueError("Initialize the unused data partition first: python3 capture/onboard.py initialize")
            if not status.get("sensor_ready") or not status.get("led_enabled"):
                raise ValueError(status.get("error") or "Sensor and sync LED must be ready.")
            if self.identity is None:
                self.identity, self.boot = uuid.uuid4().hex, status["boot_id"]
                manifest = self.path / "metadata.json"
                meta = json.loads(manifest.read_text())
                meta.update(onboard_id=self.identity, onboard_address=self.client.address, boot_id=self.boot)
                temporary = manifest.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(meta, indent=2) + "\n")
                temporary.replace(manifest)
            self.client.request("/start", {"id": self.identity}, post=True)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                status = self.status()
                if status.get("id") == self.identity:
                    if status["phase"] in ("fault", "saved"):
                        raise ValueError(status.get("error") or "This recording has already stopped; download it.")
                    if healthy(status):
                        self.message = "Recording to onboard flash. Wi-Fi carries controls and status only."
                        return dict(ready=True, id=self.identity)
                time.sleep(.1)
            raise ValueError("Sensor has not reached 180 Hz on both axes groups. Countdown cancelled; inspect board status.")

    def check_sync_ready(self):
        if self.result or self.downloading:
            raise ValueError("This batch is saving or already saved. Start a new recorder command for the next batch.")
        status = self.status()
        if not self.identity or status.get("id") != self.identity or not healthy(status):
            raise ValueError("Onboard recording is not healthy; sync cancelled.")
        return status

    def sync(self, marker):
        self.check_sync_ready()
        self.client.request("/sync", dict(id=self.identity, marker=marker), post=True)

    def finish(self):
        with self.operation:
            if self.result:
                return self.result
            if not self.identity:
                raise ValueError("No onboard recording was started. The video is saved separately.")
            self.downloading = True
            try:
                status = self.status()
                if status.get("id") != self.identity:
                    raise ValueError("Board recording changed. Recover the original recording with the download command.")
                self.message = "Stopping acquisition and closing the onboard file…"
                self.client.request("/stop", {"id": self.identity}, post=True)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    status = self.status()
                    if status["phase"] in ("saved", "fault"):
                        break
                    time.sleep(.1)
                else:
                    raise OSError("Board has not acknowledged stopping. Reconnect and retry; its file remains onboard.")
                self.message = "Downloading and verifying the raw onboard file…"
                raw = self.client.download(self.identity, self.path)
                meta = import_log(raw, self.path, allow_incomplete=True)
                quality = meta["onboard_quality"]
                self.result = dict(saved=True, raw_file=raw.name, samples=meta["samples"], quality=quality)
                self.message = f"Saved {meta['samples']:,} samples at {quality['measured_hz']:.1f} Hz. Original retained onboard."
                if not quality["usable"]:
                    self.message += " Quality issues: " + ", ".join(quality["issues"])
                return self.result
            except (ValueError, OSError) as error:
                self.message = f"Board data needs recovery: {error} Use Retry board download or the download command."
                raise
            finally:
                self.downloading = False


def record(args):
    path = args.output or Path("sessions") / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    path.mkdir(parents=True, exist_ok=False)
    (path / "metadata.json").write_text(json.dumps(dict(schema=1, transport="onboard_flash", synthetic=False,
        created_utc=datetime.now(timezone.utc).isoformat(), rider=args.rider, board=args.board_name,
        samples=0, duration_s=0, onboard_address=args.board), indent=2) + "\n")
    recording = OnboardRecording(board_client(args.board), path)
    commands = queue.Queue()
    control = ControlServer(commands, path, args.port, onboard=recording)
    phase, message, remaining = "ready", "Record video + sync starts video before the countdown.", None
    deadline, marker = 0, 0

    def terminal():
        for line in sys.stdin:
            commands.put(line.strip())
    threading.Thread(target=terminal, daemon=True).start()
    print(f"Onboard capture controls: {control.url}\nSession: {path}\nCommands: status, finish, quit.\nQuitting the laptop does not stop the board or erase its log.", flush=True)
    try:
        while True:
            countdown_handled = False
            try:
                remote = recording.status()
                connected = True
            except (ValueError, OSError) as error:
                remote, connected = recording.remote or {}, False
                recording.message = f"{error} Onboard capture does not depend on Wi-Fi. Reconnect to stop/download."
            now = time.monotonic()
            while not commands.empty():
                command = commands.get_nowait()
                if command == "quit":
                    if control.videos.active:
                        print("Stop & save video first. Board data remains onboard.", flush=True)
                    else:
                        return
                elif command == "status":
                    print(json.dumps(remote, indent=2), flush=True)
                elif command == "finish":
                    try:
                        recording.finish()
                    except (ValueError, OSError) as error:
                        print(error, file=sys.stderr, flush=True)
                elif command == "countdown":
                    countdown_handled = True
                    try:
                        # The status at the top of this iteration may predate
                        # prepare() reaching a healthy acquisition rate.
                        remote = recording.check_sync_ready()
                        connected = True
                    except (ValueError, OSError) as error:
                        remote, connected = recording.remote or {}, False
                        phase, remaining, message = "error", None, str(error)
                        continue
                    now = time.monotonic()
                    phase, remaining, deadline = "countdown", 3, now + 3
                    message = "Video and onboard data are recording. Sync in 3…"
            if phase == "countdown":
                if not connected or not healthy(remote):
                    phase, remaining, message = "error", None, "Countdown cancelled: board status unavailable or unhealthy."
                else:
                    remaining = max(0, math.ceil(deadline - now))
                    message = f"Sync in {remaining}…"
                    if not remaining:
                        marker += 1
                        try:
                            recording.sync(marker)
                            phase, remaining, deadline = "waiting", None, time.monotonic() + 3
                            message = "Waiting for the recorded LED timestamp…"
                        except (OSError, ValueError) as error:
                            phase, message = "error", str(error)
            elif phase == "waiting":
                if connected and remote.get("id") == recording.identity and remote.get("sync_id") == marker:
                    phase, message = "done", f"Flash recorded onboard — sync {marker}."
                elif now > deadline:
                    phase, message = "error", "No flash acknowledgement. The onboard log is retained; reconnect and retry sync."
            active = remote.get("phase") == "recording"
            if connected and not recording.downloading and not recording.result:
                if active:
                    recording.message = (f"ONBOARD FLASH · Accel {remote.get('accel_hz', 0):.1f} Hz · "
                        f"Gyro {remote.get('gyro_hz', 0):.1f} Hz · {remote.get('free_bytes', 0) / 1024:.0f} KiB free · "
                        f"Read retries {remote.get('bus_retries', 0)} · Read errors {remote.get('io_errors', 0)} · FIFO overruns {remote.get('fifo_overruns', 0)}")
                elif remote.get("error"):
                    recording.message = remote["error"]
                elif not remote.get("storage_ready"):
                    recording.message = "Initialize unused storage with: python3 capture/onboard.py initialize"
                else:
                    recording.message = "Board ready. Motion will be recorded to flash when you start video + sync."
            ready = (connected and not recording.result and not recording.downloading
                     and remote.get("sensor_ready") and remote.get("storage_ready")
                     and remote.get("led_enabled") and remote.get("phase") in ("idle", "saved", "recording")
                     and (not active or (remote.get("id") == recording.identity and healthy(remote))))
            own_recording = bool(recording.identity and remote.get("id") == recording.identity)
            elapsed = max(0, ((remote.get("ended_us") or remote.get("device_us", 0)) - remote.get("started_us", 0)) / 1e6) if own_recording and remote.get("started_us") else 0
            control.publish_status(dict(onboard=True, session=path.name, session_path=str(path.resolve()),
                    synthetic=False, samples=remote.get("accel_samples", 0) if own_recording else 0, duration_s=elapsed,
                    fresh=bool(ready), can_sync=bool(ready and phase not in ("countdown", "waiting")),
                    phase=phase, remaining=remaining, message=message, board_message=recording.message,
                    can_download=bool(recording.identity and not recording.result and not recording.downloading)),
                    countdown_handled=countdown_handled)
            time.sleep(.2)
    except KeyboardInterrupt:
        pass
    finally:
        control.close()
        recording.client.close()
        if recording.identity and not recording.result:
            print(f"Board recording retained: {recording.identity}\nRecover: python3 capture/onboard.py download {recording.identity} --output {path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", default="http://192.168.4.1")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--output", type=Path)
    rec.add_argument("--port", type=int, default=0)
    rec.add_argument("--rider", default="rider-01")
    rec.add_argument("--board-name", default="deck-01")
    sub.add_parser("status")
    sub.add_parser("check-sensor", help="Reinitialize the IMU while idle, after checking its cable")
    sub.add_parser("test-led", help="Three one-second LED pulses while idle; does not create a recording")
    sub.add_parser("initialize", help="Initialize a blank data partition; refuses to erase existing data")
    sub.add_parser("list")
    down = sub.add_parser("download")
    down.add_argument("id")
    down.add_argument("--output", type=Path, required=True)
    down.add_argument("--allow-incomplete", action="store_true")
    delete = sub.add_parser("delete", help="Delete one verified downloaded recording from the board")
    delete.add_argument("id")
    delete.add_argument("--downloaded", type=Path, required=True)
    stop = sub.add_parser("stop")
    stop.add_argument("id")
    convert = sub.add_parser("import")
    convert.add_argument("file", type=Path)
    convert.add_argument("--output", type=Path, required=True)
    convert.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    client = None
    try:
        client = board_client(args.board) if args.command not in ("record", "import") else None
        if args.command == "record":
            record(args)
        elif args.command == "status":
            print(json.dumps(client.request("/status"), indent=2))
        elif args.command == "check-sensor":
            client.request("/check-sensor", post=True)
            time.sleep(.5)
            print(json.dumps(client.request("/status"), indent=2))
        elif args.command == "test-led":
            test_led(client)
        elif args.command == "initialize":
            print(json.dumps(client.request("/initialize", post=True, timeout=15), indent=2))
        elif args.command == "list":
            print(json.dumps(client.request("/files"), indent=2))
        elif args.command == "stop":
            print(json.dumps(client.request("/stop", {"id": valid_id(args.id)}, post=True), indent=2))
        elif args.command in ("download", "import"):
            if args.command == "download":
                raw = client.download(valid_id(args.id), args.output)
                print(f"Verified raw file: {raw}. Original retained onboard.")
            else:
                raw = args.file
            meta = import_log(raw, args.output, args.allow_incomplete)
            print(json.dumps(meta["onboard_quality"], indent=2))
        elif args.command == "delete":
            identity = valid_id(args.id)
            packets, _, _ = read_packets(args.downloaded, allow_incomplete=True)
            if json.loads(packets[0][2]).get("id") != identity:
                raise ValueError("The downloaded file has a different recording ID.")
            print(json.dumps(client.request("/delete", dict(id=identity, crc32=checksum(args.downloaded)), post=True)))
    except (ValueError, OSError, ImportError) as error:
        parser.exit(1, f"Error: {error}\n")
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    main()
