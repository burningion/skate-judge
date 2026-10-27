"""Optional localhost board for the native UI integration test (no hardware writes).

PYTHONPATH=.:tests python3 tests/mobile_board_fixture.py
Then run the SkateJudge UI tests in an iPhone simulator.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import socket
import time
from urllib.parse import parse_qs, urlsplit
import zlib

from capture.onboard_log import META, encode_packet
from test_onboard import fixture


class Board:
    identity = ""
    phase = "idle"
    marker = 0
    start = 0
    raw = b""
    faults = {}

    def fail(self, name):
        remaining = self.faults.get(name, 0)
        if remaining:
            self.faults[name] = remaining - 1
            return True
        return False

    def status(self):
        now = int(time.monotonic() * 1e6)
        return dict(protocol=1, phase=self.phase, id=self.identity, boot_id="1234abcd",
                    storage="sd", sensor_ready=True, storage_ready=True, led_enabled=True,
                    accel_hz=196.5, gyro_hz=196.5, accel_samples=416, gyro_samples=416,
                    zero_accel=0, io_errors=0, fifo_overruns=0, error="", free_bytes=1_000_000_000,
                    bytes=len(self.raw), started_us=self.start, ended_us=0, device_us=now,
                    sync_id=self.marker, sync_us=now if self.marker else 0,
                    battery_available=True, battery_percent=85, battery_age_ms=0)


board = Board()


class Handler(BaseHTTPRequestHandler):
    def drop_connection(self):
        self.close_connection = True
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()

    def reply(self, value, code=200, headers=None):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream" if isinstance(value, bytes) else "application/json")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlsplit(self.path)
        params = parse_qs(route.query)
        if route.path == "/health":
            return self.reply(dict(fixture="skatejudge-ios-v1"))
        if route.path == "/status":
            if board.fail("status_failures"):
                return self.drop_connection()
            return self.reply(board.status())
        info = dict(id=board.identity, storage="sd", bytes=len(board.raw), crc32=f"{zlib.crc32(board.raw):08x}")
        if route.path == "/files":
            return self.reply([info] if board.raw else [])
        if params.get("id", [""])[0] != board.identity or not board.raw:
            return self.reply(dict(error="No such fixture recording"), 404)
        if route.path == "/file-info":
            if board.fail("file_info_failures"):
                return self.reply(dict(error="Simulated file-info failure. Reconnect and retry."), 503)
            return self.reply(info)
        if route.path == "/file":
            offset = int(params.get("offset", ["0"])[0])
            return self.reply(board.raw[offset:], 206 if offset else 200, {"X-Log-CRC32": info["crc32"]})
        self.reply(dict(error="Unknown fixture route"), 404)

    def do_POST(self):
        route = urlsplit(self.path)
        params = parse_qs(route.query)
        if route.path == "/fixture":
            board.faults = {key: int(value[0]) for key, value in params.items()
                            if key in ("status_failures", "sync_ack_failures", "stop_failures", "file_info_failures")}
            return self.reply(dict(configured=True))
        if route.path == "/start":
            identity = params.get("id", [""])[0]
            if not re.fullmatch("[0-9a-f]{32}", identity):
                return self.reply(dict(error="Invalid ID"), 400)
            packets = fixture(storage="sd")
            meta = json.loads(packets[0][24:])
            meta["id"] = identity
            packets[0] = encode_packet(META, 0, 1_000_000, json.dumps(meta).encode())
            board.raw = b"".join(packets)
            board.identity, board.phase, board.marker = identity, "recording", 0
            board.start = int(time.monotonic() * 1e6)
        elif route.path in ("/sync", "/stop"):
            if params.get("id", [""])[0] != board.identity:
                return self.reply(dict(error="Recording ID mismatch"), 409)
            if route.path == "/sync":
                board.marker = int(params["marker"][0])
                if board.fail("sync_ack_failures"):
                    return self.drop_connection()
            else:
                if board.fail("stop_failures"):
                    return self.reply(dict(error="Simulated stop failure. Retry this session."), 503)
                board.phase = "saved"
        elif route.path != "/test-led":
            return self.reply(dict(error="Unsupported fixture operation"), 404)
        self.reply(dict(accepted=True), 202)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print("Native UI board fixture: http://127.0.0.1:18765", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 18765), Handler).serve_forever()
