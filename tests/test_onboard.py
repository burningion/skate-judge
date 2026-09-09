import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import zlib

from capture.onboard import BoardClient, SerialBoardClient, OnboardRecording, healthy
from capture.onboard_log import CLOCK, END, FIFO, META, SYNC, decode_log, encode_packet, import_log, read_packets

IDENTITY = "a" * 32


def fixture(count=416, *, error="", start_tick=1000, fine=None):
    meta = dict(schema=1, id=IDENTITY, boot_id="1234abcd", sensor="LSM6DSO32", odr_hz=208,
                accel_g_per_lsb=.000976, gyro_dps_per_lsb=.070, timestamp_tick_us=25, initial_temp_C=25.)
    packets = []
    if fine is not None:
        meta["frequency_fine"] = fine

    def add(kind, time, payload):
        if fine is not None:
            time = round(1000000 + (time - 1000000) / (1 + .0015 * fine))
            if kind == CLOCK:
                end, tick = struct.unpack("<QI", payload)
                payload = struct.pack("<QI", round(1000000 + (end - 1000000) / (1 + .0015 * fine)), tick)
        packets.append(encode_packet(kind, len(packets), time, payload))

    def tag(kind, counter):
        value = (kind << 3) | counter
        return bytes([value | (value.bit_count() % 2)])

    add(META, 1000000, json.dumps(meta).encode())
    add(CLOCK, 999950, struct.pack("<QI", 1000050, start_tick))
    words = []
    for i in range(count):
        tick = (start_tick + round((i + 1) * 40000 / 208)) % 2**32
        counter = (i % 4) << 1
        words.extend([tag(4, counter) + struct.pack("<I", tick) + b"\x00\x55",
                      tag(1, counter) + struct.pack("<hhh", 10, -20, 30),
                      tag(2, counter) + struct.pack("<hhh", 0, 0, 1025)])
        if len(words) >= 60:
            add(FIFO, 1000000 + round((i + 1) * 1e6 / 208), b"".join(words)); words = []
    if words:
        add(FIFO, 1000000 + round(count * 1e6 / 208), b"".join(words))
    add(SYNC, 1500000, struct.pack("<IBB", 1, 1, 1))
    add(SYNC, 1650000, struct.pack("<IBB", 1, 0, 1))
    end = 1000000 + round((count + 1) * 40000 / 208) * 25
    end_tick = (start_tick + round((count + 1) * 40000 / 208)) % 2**32
    add(CLOCK, end - 50, struct.pack("<QI", end + 50, end_tick))
    add(END, end + 100, json.dumps(dict(accel_samples=count, gyro_samples=count, zero_accel=0,
                                      io_errors=int(bool(error)), fifo_overruns=0, error=error)).encode())
    return packets


class LogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "log.bin"

    def write(self, packets=None):
        self.log.write_bytes(b"".join(packets if packets is not None else fixture()))
        return self.log

    def test_208hz_reconstruction_uses_sensor_clock_and_preserves_axes_and_led(self):
        decoded = decode_log(self.write())
        self.assertEqual(len(decoded["samples"]), 416)
        self.assertAlmostEqual(decoded["quality"]["measured_hz"], 208, delta=.01)
        self.assertTrue(decoded["quality"]["usable"], decoded["quality"])
        row = decoded["samples"][0]
        self.assertAlmostEqual(row["az"], 1025 * .000976 * 9.80665)
        self.assertLess(row["gy"], 0)
        self.assertEqual(row["host_monotonic_ns"], "")
        self.assertEqual(decoded["events"][0]["device_us"], 1500000)
        self.assertEqual(decoded["events"][1]["edge"], 0)

    def test_timestamp_rollover_does_not_change_sample_order(self):
        data = decode_log(self.write(fixture(start_tick=2**32 - 4000)))
        self.assertTrue(data["quality"]["usable"], data["quality"])
        self.assertAlmostEqual(data["quality"]["measured_hz"], 208, delta=.01)

    def test_factory_trim_validates_the_actual_clock_instead_of_assuming_25us(self):
        packets = fixture(fine=-38)
        data = decode_log(self.write(packets))
        self.assertTrue(data["quality"]["usable"], data["quality"])
        self.assertAlmostEqual(data["quality"]["clock_tick_us"], 25 / .943, delta=.001)
        self.assertAlmostEqual(data["quality"]["measured_hz"], 208 * .943, delta=.01)
        metadata = json.loads(packets[0][24:])
        metadata["frequency_fine"] = 0
        packets[0] = encode_packet(META, 0, 1000000, json.dumps(metadata).encode())
        with self.assertRaisesRegex(ValueError, "clock anchors disagree"):
            decode_log(self.write(packets))

    def test_bus_corruption_is_rejected_even_with_a_valid_flash_packet_checksum(self):
        packets = fixture()
        kind, stamp, payload = read_packets(self.write(packets))[0][2]
        broken = bytearray(payload); broken[0] ^= 1
        packets[2] = encode_packet(kind, 2, stamp, broken)
        with self.assertRaisesRegex(ValueError, "parity"):
            decode_log(self.write(packets))
        packets = fixture()
        packets[2] = encode_packet(kind, 2, stamp, payload[:14] + payload[7:14] + payload[14:])
        with self.assertRaisesRegex(ValueError, "Duplicate sensor word"):
            decode_log(self.write(packets))

    def test_power_cut_recovers_only_whole_packets_and_marks_incomplete(self):
        packets = fixture()
        self.write(packets[:-1] + [packets[-1][:13]])
        with self.assertRaisesRegex(ValueError, "Truncated"):
            decode_log(self.log)
        data = decode_log(self.log, allow_incomplete=True)
        self.assertEqual(len(data["samples"]), 416)
        self.assertFalse(data["quality"]["usable"])
        self.assertIn("missing_footer", data["quality"]["issues"])

    def test_corruption_missing_packets_and_count_mismatch_are_rejected(self):
        packets = fixture()
        broken = bytearray(packets[2]); broken[-1] ^= 0x10
        self.write(packets[:2] + [broken] + packets[3:])
        with self.assertRaisesRegex(ValueError, "checksum"):
            decode_log(self.log, allow_incomplete=True)
        self.write(packets[:2] + packets[3:])
        with self.assertRaisesRegex(ValueError, "Missing"):
            decode_log(self.log)
        footer = json.dumps(dict(accel_samples=415, gyro_samples=416)).encode()
        self.write(packets[:-1] + [encode_packet(END, len(packets) - 1, 4000000, footer)])
        with self.assertRaisesRegex(ValueError, "counts"):
            decode_log(self.log)

    def test_acquisition_fault_is_preserved_and_not_usable(self):
        data = decode_log(self.write(fixture(error="fifo_data_read_failed")))
        self.assertTrue(data["quality"]["complete"])
        self.assertFalse(data["quality"]["usable"])
        self.assertIn("fifo_data_read_failed", data["quality"]["issues"])

    def test_import_preserves_video_and_refuses_to_overwrite_existing_samples(self):
        self.write()
        out = self.root / "session"; out.mkdir()
        (out / "webcam.webm").write_bytes(b"video")
        (out / "metadata.json").write_text(json.dumps(dict(rider="test", created_utc="2026-09-09")))
        meta = import_log(self.log, out)
        self.assertEqual(meta["rider"], "test")
        self.assertEqual(meta["transport"], "onboard_flash")
        self.assertEqual((out / "webcam.webm").read_bytes(), b"video")
        self.assertEqual(len((out / "samples.csv").read_text().splitlines()), 417)
        self.assertEqual(len((out / "events.jsonl").read_text().splitlines()), 2)
        original = (out / "samples.csv").read_bytes()
        with self.assertRaisesRegex(ValueError, "already exist"):
            import_log(self.log, out)
        self.assertEqual((out / "samples.csv").read_bytes(), original)


class Response(io.BytesIO):
    def __init__(self, data, headers=None, fail_after=None):
        super().__init__(data)
        self.headers = headers or {}
        self.fail_after = fail_after

    def read(self, size=-1):
        if self.fail_after is not None:
            if self.tell() >= self.fail_after:
                raise OSError("Wi-Fi disconnected")
            size = min(size, self.fail_after - self.tell())
        return super().read(size)


class DownloadTests(unittest.TestCase):
    def test_resume_after_wifi_failure_and_keep_verified_board_original(self):
        data = b"".join(fixture())
        crc = f"{zlib.crc32(data):08x}"
        offsets, routes = [], []

        def open_url(request, timeout):
            parsed = urlsplit(request if isinstance(request, str) else request.full_url)
            routes.append(parsed.path)
            if parsed.path == "/file-info":
                return Response(json.dumps(dict(id=IDENTITY, bytes=len(data), crc32=crc)).encode())
            offset = int(parse_qs(parsed.query)["offset"][0]); offsets.append(offset)
            return Response(data[offset:], {"X-Log-CRC32": crc}, fail_after=1500 if len(offsets) == 1 else None)

        client = BoardClient(opener=open_url)
        with tempfile.TemporaryDirectory() as directory:
            path = client.download(IDENTITY, directory)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(offsets, [0, 1500])
            self.assertNotIn("/delete", routes)
            self.assertFalse(path.with_suffix(".bin.part").exists())
            self.assertEqual(client.download(IDENTITY, directory), path)
            self.assertEqual(len(offsets), 2)

    def test_different_existing_file_is_not_overwritten(self):
        def open_url(request, timeout):
            return Response(json.dumps(dict(id=IDENTITY, bytes=100, crc32="12345678")).encode())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"onboard-{IDENTITY}.bin"; path.write_bytes(b"keep me")
            with self.assertRaisesRegex(ValueError, "not be overwritten"):
                BoardClient(opener=open_url).download(IDENTITY, directory)
            self.assertEqual(path.read_bytes(), b"keep me")


class ReadinessTests(unittest.TestCase):
    def test_rate_errors_and_zero_data_block_sync(self):
        status = dict(phase="recording", accel_hz=208, gyro_hz=208, accel_samples=1000,
                      gyro_samples=1000, zero_accel=0, io_errors=0, fifo_overruns=0, error="")
        self.assertTrue(healthy(status))
        for changes in [dict(accel_hz=8), dict(gyro_hz=8), dict(io_errors=1),
                        dict(fifo_overruns=1), dict(zero_accel=550), dict(phase="fault")]:
            self.assertFalse(healthy(dict(status, **changes)))

    def test_active_recording_cannot_be_replaced(self):
        class Board:
            def request(self, route):
                return dict(protocol=1, phase="recording", id="b" * 32)
        with tempfile.TemporaryDirectory() as directory:
            recording = OnboardRecording(Board(), directory)
            with self.assertRaisesRegex(ValueError, "already has an active"):
                recording.prepare()

    def test_board_restart_is_not_silently_combined_with_old_recording(self):
        class Board:
            def request(self, route):
                return dict(protocol=1, phase="idle", boot_id="new")
        with tempfile.TemporaryDirectory() as directory:
            recording = OnboardRecording(Board(), directory); recording.boot = "old"
            with self.assertRaisesRegex(ValueError, "restarted"):
                recording.status()


class USBTests(unittest.TestCase):
    def test_usb_transport_decodes_status_and_downloads_exact_binary(self):
        raw = b"".join(fixture())
        crc = f"{zlib.crc32(raw):08x}"

        class Port:
            def __init__(self):
                self.replies = io.BytesIO()
                self.requests = []

            def write(self, data):
                command = data.decode().strip()
                self.requests.append(command)
                if command == "GET /status":
                    response = dict(status=200, body=dict(protocol=1, phase="idle"))
                    data = b"I,boot diagnostic\n" + json.dumps(response, separators=(',', ':')).encode() + b"\n"
                elif command.startswith("GET /file-info?"):
                    response = dict(status=200, body=dict(id=IDENTITY, bytes=len(raw), crc32=crc))
                    data = json.dumps(response, separators=(',', ':')).encode() + b"\n"
                else:
                    response = dict(status=200, binary=len(raw), crc32=crc)
                    data = json.dumps(response, separators=(',', ':')).encode() + b"\n" + raw
                self.replies = io.BytesIO(data)

            def read_until(self, separator, size):
                return self.replies.readline(size)

            def read(self, size):
                # Serial reads may return fewer bytes than requested.
                return self.replies.read(min(size, 256))

        client = SerialBoardClient.__new__(SerialBoardClient)
        client.address = "serial:test"
        client.port, client.lock, client.opener = Port(), threading.Lock(), client._open
        self.assertEqual(client.request("/status"), dict(protocol=1, phase="idle"))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(client.download(IDENTITY, directory).read_bytes(), raw)
        self.assertEqual(len(client.port.requests), 3)


if __name__ == "__main__":
    unittest.main()
