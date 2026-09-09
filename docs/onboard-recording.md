# Onboard motion recording

`flash-feather.sh` installs `firmware/imu_logger`, and `capture/onboard.py`
controls it. The older `firmware/imu_stream` / `capture/session.py record`
workflow remains available for live viewing and synthetic demos.

## Acquisition and sync

The LSM6DSO32 is configured for nominal 208 Hz acceleration and angular velocity, with ranges
±32 g and ±2000 degrees/second. Both streams and sensor timestamps enter its
hardware FIFO. The ESP32 drains tagged, uncompressed FIFO words into a binary
flash log on a dedicated task. Every I²C read must return its requested bytes;
configuration registers are read back. The tag and six data bytes are read in
separate transactions, and tag parity/duplicate time-slot entries are checked.
Non-consuming tag reads have bounded retries; a failed payload read is retried
only if the FIFO head still matches, proving that word was not consumed.
`bus_retries` is displayed and retained separately from unrecovered read errors.
FIFO overflow, read failures, persistently
low rate, and excessive all-zero acceleration stop the log and report a fault.
The sensor configuration and FIFO registers follow the
[ST datasheet](https://www.st.com/resource/en/datasheet/lsm6dso32.pdf).

**Record video + sync** waits for MediaRecorder to start and for the first video
chunk to be saved on the laptop, then starts onboard logging and checks actual
accel/gyro rates (180–230 Hz). Only then does the three-second countdown begin.
Another countdown uses the same running video and sensor log. External cameras
require the explicit “already recording” checkbox.

Both LED edges are timestamped on the ESP32 and written into that same log.
Periodic bracketed clock reads map sensor FIFO timestamps onto the ESP32 clock.
Import fits those anchors, compares them with the chip's signed factory
`INTERNAL_FREQ_FINE` calibration, and rejects inconsistent clocks. The actual
rate can differ from nominal: the tested IMU has a factory trim of −38 and
measures about 196.5 Hz, with approximately 5.09 ms sample spacing. This agrees
with the calibration formula in [ST AN5473, section 6.4](https://www.st.com/resource/en/application_note/an5473-lsm6dso32-alwayson-3axis-accelerometer-and-3axis-gyroscope-stmicroelectronics.pdf).
Wi-Fi arrival times are never used as sample timestamps. The LED write and actual light output have
a small hardware delay; matching a video frame also has exposure/frame-rate
uncertainty. Match beginning and ending flashes in the review UI to estimate
video offset and drift. Automatic flash recognition is not implemented.

208 Hz gives roughly 4.8 ms sample spacing. It is a useful initial rate for
takeoff/landing boundaries, not a guarantee of capturing an impact's true peak.
Inspect clipping and timing in real skating data before selecting final detector
thresholds or increasing the rate.

The replacement Feather passed a 61.99-second bench capture on 2026-09-09:
12,184 matched accel/gyro samples at 196.53 Hz, no retries/read errors/overruns,
no missing or duplicate slots, and three recorded sync pulses. A 20-second
host-client disconnect preserved 3,982 additional samples. A second capture on
the same boot also passed. The raw downloads and CSV imports were validated;
physical LED visibility and real skating impacts still require video review.

## Flash and capacity

The Feather preset keeps `default_8MB` and its existing **1.5 MiB data partition**.
The rest of the 8 MB is not available to this logger. No PSRAM is required.
LittleFS is never automatically formatted after a mount error. Initialize an
unused partition once:

```bash
uv run capture/onboard.py --board serial:auto initialize
```

This refuses to erase nonblank, unrecognized storage. Preserve it before changing
partition layouts or attempting recovery. Normal firmware uploads with this
unchanged partition layout leave recorded files in place.

Tagged accel/gyro/timestamp words consume about 4.4 KiB/s before packet and
filesystem overhead. Capacity is a few minutes when empty. **Start with one-minute
batches**, watch free space, download, verify, and explicitly reclaim space.
The logger stops before exhausting the partition. It never rotates files or
overwrites an old recording. Larger persistent storage, such as microSD, is the
next step for recording a whole outing.

FIFO packets are written at least every 50 ms while healthy, and the file is
flushed/fsynced every 250 ms. These are scheduled intervals, not a hard power-loss
guarantee: abrupt loss can discard pending FIFO/RAM/filesystem data. Recovery
retains valid whole packets and marks a missing footer or truncated tail
incomplete. CRC and sequence failures are rejected; no missing samples are
invented. Prefer **Stop & save** before removing power.

## Record and download

Join the Feather's `SkateJudge-XXXX` network, password `skate-judge`, then:

```bash
uv run --offline capture/onboard.py record --output sessions/pilot-001
```

Open the printed localhost URL, optionally enable microphone audio, and use
**Record video + sync**. Wi-Fi carries low-rate controls/status only during
capture. The page displays measured accel/gyro rates, errors, and free space.
The board continues logging if Wi-Fi disconnects, the browser closes, or the
laptop process exits. Reconnect to request the ending sync and download.
Keep the webcam recording running through a board connection outage.

After the final flash, **Stop & save video + board data** saves video, closes
the board log, downloads and verifies its byte count/CRC32 and packet CRCs, then
imports `samples.csv`, `events.jsonl`, and `metadata.json`. Retain the downloaded
`onboard-<id>.bin` as the original. `onboard_quality` records measured rate,
clock-fit error, gaps, zeros, incomplete FIFO slots, and firmware faults.
Host-arrival timestamps are blank because samples were recorded onboard.

If the sensor fails its ID/configuration check, recording is blocked. Check the
STEMMA QT cable and power, then run `check-sensor` while idle (with
`--board serial:auto` before it when using USB). This cycles the sensor's power
and rechecks its registers without deleting logs. A physical sampling-rate
check is still required after it reports ready.

Wait for both saved messages before `quit`. Start a new recorder command for
each batch. The stop/download button becomes available again if recovery needs
a retry. Downloading never automatically deletes the board's copy.

## Recovery and explicit deletion

Use `list` while idle to find IDs. If a capture is still running, `status` shows
its ID; stop it first. These example IDs and paths must be replaced with yours:

```bash
uv run --offline capture/onboard.py status
uv run --offline capture/onboard.py stop <id>
uv run --offline capture/onboard.py list
uv run --offline capture/onboard.py download <id> --output sessions/recovered-001
```

Use the original session folder to add sensor data beside an already saved
video, provided it has no existing `samples.csv`/`events.jsonl`. Existing imports
are not overwritten. An interrupted transfer keeps `.bin.part`; rerun the same
download to resume. A corrupt partial is preserved for investigation; use a new
output directory for a fresh download. After power loss, add `--allow-incomplete`
to import intact packets with explicit quality warnings.

USB supports the same protocol without joining Wi-Fi. Put the board argument
before the command, and close other serial clients first:

```bash
uv run --offline capture/onboard.py --board serial:auto status
uv run --offline capture/onboard.py --board serial:auto download <id> --output sessions/recovered-usb
```

After checking the local original and video, reclaim one recording's space:

```bash
uv run --offline capture/onboard.py delete <id> --downloaded sessions/pilot-001/onboard-<id>.bin
```

Deletion requires a local raw file with the matching ID and CRC32. There is no
bulk-delete or format-on-error command. A board reset starts a new clock/boot ID;
the laptop refuses to combine that boot with an existing capture.
