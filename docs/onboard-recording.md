# Onboard motion recording

`flash-feather.sh` installs `firmware/imu_logger`, and `capture/onboard.py`
controls it. The older `firmware/imu_stream` / `capture/session.py record`
workflow remains available for live viewing and synthetic demos.

## Acquisition and sync

The LSM6DSO32 is configured for nominal 208 Hz acceleration and angular velocity, with ranges
±32 g and ±2000 degrees/second. Both streams and sensor timestamps enter its
hardware FIFO. An acquisition task drains tagged, uncompressed FIFO words into
a 32 KiB RAM queue; a separate task writes the binary log to SD or internal flash.
Every I²C read must return its requested bytes;
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

## SD card and internal flash

The logger now prefers an **Adafruit 254 microSD breakout board+** with a mounted
card. The [editable Fritzing design](../hardware/fritzing/skate-judge-direct-lipo.fzz)
includes these connections:

| Feather ESP32-S3 | Adafruit 254 |
| --- | --- |
| `3.3V` | `3V` |
| `GND` | `GND` |
| `SCK` / GPIO36 | `CLK` |
| `MO` / GPIO35 | `DI` |
| `MI` / GPIO37 | `DO` |
| `10` / GPIO10 | `CS` |

Leave `5V` and `CD` unconnected. The `3V` pin accepts a regulated 3.3 V supply
([Adafruit confirmation](https://forums.adafruit.com/viewtopic.php?p=755866));
do not connect it to BAT. The main Feather 3.3 V rail powers the card, while the
IMU remains on switched STEMMA QT power. Use short SPI wires.

Use a **FAT32** card, such as the previously selected 32 GB SanDisk High Endurance.
Insert it with power off, then boot. The firmware mounts without formatting and
creates `/skate-judge/`; recordings are `/skate-judge/<32-character-id>.bin`.
No card, a mount failure, or an unusable recording directory selects internal
flash instead. Status and the recorder show `storage: "sd"` or `"flash"`,
`sd_ready`, `flash_ready`, free bytes, and a fallback warning. Selection remains
fixed until reboot. A write failure during capture stops that recording and
reports a fault; it does not start a second file on another medium. There is no
hot-swap support. A read-only/full card that mounts can still fail at recording
start; correct that issue or power down and remove it to use flash.

The Feather build enables SD by default. Upload with `./flash-feather.sh` when
ready. `SKATE_SD=0 ./flash-feather.sh` disables it. `SD_CS_PIN`, `SD_SCK_PIN`,
`SD_MOSI_PIN`, `SD_MISO_PIN`, and `SD_SPI_HZ` can override the defaults
10/36/35/37/4000000. Keep the wiring and settings consistent.

SPI runs at **4 MHz**, a raw ceiling of 500 kB/s. The log requires approximately
4.5 KiB/s (roughly 16 MiB/hour). Protocol/filesystem overhead and card write
pauses reduce actual throughput; the clock rate is not a card benchmark.
The 32 KiB queue provides roughly seven seconds of buffering at that log rate.
Disk writes, flushes and free-space queries run on the writer task while the
acquisition task continues draining the sensor FIFO. Buffer exhaustion is an
explicit `storage_buffer_full` fault. Files stop before FAT32's 4 GiB file limit;
they are never automatically rotated or overwritten. Use separate manageable
batches: laptop import currently holds decoded samples in memory.

**Physical SD validation is still required.** After wiring and uploading:

1. Run `uv run --offline capture/onboard.py --board serial:auto status` and
   confirm `storage` is `sd` and free space matches the card, including above 4 GiB.
2. Record a five-minute bench batch, exercise several sync flashes, then stop,
   download, and import. Check `onboard_quality.usable`, normal measured rates,
   and zero read errors/FIFO overruns. Repeat on battery power.
3. Inspect `storage_queue_peak_bytes` against `storage_buffer_bytes` (32768),
   and `storage_max_write_us` / `storage_max_flush_us` in status. The footer
   includes timing through the pre-footer flush; status includes the final flush.
   These measured pauses and queue headroom establish whether the actual card
   and wiring keep up.
4. Power off, remove the card, reboot and confirm the explicit flash fallback.
   Existing flash recordings remain listed/downloadable with the card installed.

## Internal flash capacity

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
overwrites an old recording. SD removes this small internal-storage limit without
changing the partition layout or moving existing flash logs.

The saved `a7s-001` through `a7s-004` raw logs use approximately **4.4–4.5 kB/s**.
An empty 1.5 MiB partition has roughly **5½ minutes** of recording capacity after
reserved space and filesystem overhead; this is storage capacity, not battery
runtime. `a7s-004` itself recorded 93.66 seconds before `flash_capacity_reached`.
Previous recordings occupy space until explicitly deleted, even after downloading.

FIFO packets are queued at least every 50 ms while healthy, and the writer schedules
flush/fsync every 250 ms. These are scheduled intervals, not a hard power-loss
guarantee: abrupt loss can discard pending FIFO/RAM/filesystem data. Recovery
retains valid whole packets and marks a missing footer or truncated tail
incomplete. CRC and sequence failures are rejected; no missing samples are
invented. Prefer **Stop & save** before removing power.

## Record and download

To check the physical LEDs without starting a recording, install the current
logger with `./flash-feather.sh`, then run while the board is idle:

```bash
uv run --offline capture/onboard.py --board serial:auto test-led
```

This sends three one-second white pulses and leaves the stick off. Omit
`--board serial:auto` to use Wi-Fi. Status reports the installed `led_pin`,
`led_count`, `led_format`, and `led_brightness`; the Feather preset uses GPIO5,
eight pixels, GRB, and 255/255. Completion confirms LED commands, not visible
light. With the stick powered from `BAT`, leave the LiPo connected. GPIO5 must
connect to the stick's `DIN` input. The test does not write recording files or
sync markers, and it is blocked during recording.
It also samples battery voltage before and during a pulse using the Feather's
MAX17048 (unavailable on older gauge revisions), and checks that the GPIO's RMT
transmitter is attached and idle after sending. These diagnostics cannot verify
voltage at the stick's solder pads or detect visible light; `-1` means unavailable
or not yet tested. Battery reads happen only during this idle test.

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

The **Recording time left** panel shows an approximate storage budget alongside
SD/internal flash and free space. Before recording, it shows capacity for the next
log using a conservative 5,000 bytes/second estimate (or the last completed log's
measured rate). After five seconds of healthy recording, it uses logged bytes
divided by elapsed device time. It subtracts the 32 KiB firmware reserve and,
while recording, the full async buffer size to allow for pending writes. SD
estimates also respect the 4 GiB per-file stopping limit. Filesystem allocation
and variations in write rate mean this is an estimate, not an exact countdown.

The panel warns at one minute and becomes more urgent at 30 seconds. It clears
the estimate on a board/recorder disconnect, fault, or start/stop transition.
An idle board below the firmware's minimum starting free space shows that it
cannot start a new log. Battery runtime and space for laptop video are separate.
This UI also works with older onboard flash firmware; no reflash is needed just
to display the estimate. Restart the laptop recorder to load the updated UI.

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

Use `list` while idle to find IDs on both mounted media, including their `storage`
field. Add `--storage sd` or `--storage flash` to `list`, `download`, or `delete`
to select a medium explicitly (required if an ID exists on both).
If a capture is still running, `status` shows
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

You can also copy a stopped recording from the SD card into a session directory
and run `uv run --offline capture/onboard.py import <copied-file.bin> --output <session>`.
Imports record `onboard_storage` and use transport `onboard_sd`; older logs retain
`onboard_flash`. Both receive the same dataset quality checks and labeling flow.
Final close errors reported by the board are also preserved by **Stop & save**;
a raw-card import cannot recover an error reported only after the last packet.

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
