# Judgy Skateboard

Record skateboard motion from an Adafruit LSM6DSO32 (accelerometer + gyro)
wired to an ESP32-S3 over STEMMA QT / Qwiic. Record webcam video in the web UI,
label makes, bails, and falls, and align webcam or phone footage using timestamped LED flashes.
The original live 3D orientation viewer is also included.

![hardware layout](./hardware/fritzing/skate-judge-direct-lipo.png)

This is the **data collection stage**: the review tool can suggest audio-based
pop/contact pairs for human review; it does not yet classify tricks, train
a model, or play automatic judgments. A board-mounted IMU measures the board's
motion, which does not always reveal whether the rider stayed on or fell.
Start with [your first dataset recording](docs/first-recording.md). The
[hardware and recording reference](docs/recording.md) covers wiring, file formats,
and the model plan.

```
firmware/imu_logger/   208 Hz sensor FIFO to onboard flash, timestamped LED sync
firmware/imu_stream/   legacy live stream for the orientation viewer
capture/onboard.py    onboard recording controls, verified download, recovery, CSV import
capture/session.py    recorder, human outcome labels, phone/video clock alignment
capture/controls.html local webcam recording and countdown UI (--controls)
viz/imu_viz.py         pygame + OpenGL viewer: Mahony sensor fusion, calibration, 3D board with axes
viz/trick_review.py    local video/audio review, onset pairs, editable boundaries, LED alignment
flash.sh               compile + upload with arduino-cli
flash-feather.sh        preset: Feather S3 8MB / no PSRAM, eight-pixel RGB stick on GPIO5
tests/                 sensor/video integrity, local HTTP, and webcam controller checks
```

## Your hardware and pinout

An [editable Fritzing wiring project](hardware/fritzing/README.md) is included,
with bundled parts and a preview of the direct-LiPo prototype.

A [parametric OpenSCAD enclosure](hardware/enclosure/README.md) includes PLA print
files, a screw-mounted deck base, a Velcro alternative, and a recessed LED mount
integrated into the enclosure's side.
Check the battery dimensions and mounting clearance before printing.

This setup uses the Adafruit Feather ESP32-S3 **8 MB flash / no PSRAM**, an
LSM6DSO32 IMU, a **3.7 V 500 mAh LiPo**, and an **eight-pixel SKC6812 RGB stick**.
For the first direct-LiPo bench test, wire with power disconnected:

| Connection | Destination |
| --- | --- |
| LiPo plug | Feather battery JST socket, with matching polarity |
| Feather `BAT` | Stick `5V` / `+` input |
| Feather `GND` | Stick `GND` |
| Feather header printed **5** (GPIO5) | Stick `DIN`, preferably through a 330 Ω resistor near `DIN` |
| Stick `DOUT` | Leave unconnected |
| Feather STEMMA QT socket | LSM6DSO32 STEMMA QT socket |

**The label “5” means GPIO5, not 5 volts or the fifth header position.**
The resistor is recommended protection on the data wire, not a strict requirement
or an LED brightness resistor. A short-wire bench test can omit it.

Adafruit documents direct LiPo power for short NeoPixel chains, but this stick's
listing specifies 4–7 V. Test visibility and correct colors as the battery runs
down before relying on it for sync; a regulated 5 V supply with a level shifter
is the fallback. See [power details and manufacturer sources](docs/recording.md#direct-lipo-wiring-for-the-first-test).

## First dataset recording

Flash the onboard logger, then initialize its unused storage once over USB:

```bash
./flash-feather.sh
uv run capture/onboard.py --board serial:auto initialize
```

Initialization refuses to erase a nonblank, unrecognized partition. The logger
keeps the preset's existing 1.5 MiB data partition; it does not repartition flash.

For battery operation, join **SkateJudge-XXXX** (password **skate-judge**), then:

```bash
uv run --offline capture/onboard.py record --rider rider-01 --board-name deck-01
```

Open the printed localhost URL. Enable microphone audio if you want audio onset
suggestions. **Record video + sync** starts webcam recording, waits for video
bytes to reach the laptop, starts onboard motion recording, checks its measured
rate, and only then begins the three-second LED countdown.

Acceleration and rotation use **nominal 208 Hz** acquisition through the sensor FIFO and are
saved on the Feather's flash. The page shows actual rates, errors, and free
space. The tested sensor measures about 196.5 Hz, consistent with its factory
clock calibration. Wi-Fi carries commands and status during capture; losing the connection
does not stop motion logging. Download happens after acquisition stops.

Repeat the sync countdown near the end, wait for the flash, then choose
**Stop & save video + board data**. Keep the tab and terminal open until both
are saved. The raw file is checksum-verified, imported into `samples.csv` and
`events.jsonl`, and retained on the board. Match the visible flashes in the
[review tool](docs/trick-review.md) to align video and motion.

Start with **one-minute batches**: the existing flash partition holds minutes,
not an entire outing. Download and verify each batch, then explicitly delete its
onboard copy to reclaim space. See the [first recording guide](docs/first-recording.md)
and [onboard storage/recovery reference](docs/onboard-recording.md).

For a USB bench check, use `--board serial:auto` before `record`. For a separate
phone camera, start that camera yourself and check the external-camera option
before syncing. Sessions are saved under `sessions/` and excluded from Git.

## Review a recorded trick

The [audio/video review guide](docs/trick-review.md) explains onset detection,
editable pop/contact boundaries, and matching LED flashes to the sensor clock.
With FFmpeg installed, run:

```bash
uv run viz/trick_review.py sessions/<session>/webcam-<id>.webm
```

Open the printed local URL, match the LED flashes, then label attempts in the
same page. Choose the trick and outcome, or **Not a trick** for an onset false
positive; saving advances to the next suggestion. Labels save to `labels.jsonl`
with aligned sensor times. **Download saved labels** exports this clip's current
labels. Audio suggestions and unreviewed time remain unlabeled until you decide.

## One-time setup

```bash
brew install arduino-cli uv
arduino-cli core install esp32:esp32 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

The Adafruit LSM6DS library (plus BusIO and Unified Sensor) is installed into
`./.arduino` the first time `flash.sh` runs. NeoPixel builds also install
Adafruit NeoPixel there, so nothing touches your global Arduino sketchbook.

## Flash the board

For onboard capture with this Feather, use `./flash-feather.sh`. The generic
`./flash.sh` defaults to the legacy stream sketch used by the viewer.

Both scripts accept `--compile-only` to build without uploading.

The generic script builds for the `esp32s3` target with USB CDC on boot, so it
talks over the ESP32-S3's native USB port. It probes the usual STEMMA QT pin
pairs at startup and, for Adafruit Feathers, switches on the I2C power rail
first. It prints what it found:

```
I,LSM6DSO32 at 0x6A on SDA=3 SCL=4 (Adafruit Feather ESP32-S3 / Reverse TFT, I2C power on GPIO 7)
```

To try specific pins first: `IMU_SDA=3 IMU_SCL=4 IMU_POWER=7 ./flash.sh`.

For your **Adafruit Feather ESP32-S3, 8 MB flash / no PSRAM**, connect the
eight-pixel SKC6812 RGB stick's data circuit to the header marked **5** (GPIO5).
The preset selects the board, IMU wiring, Wi-Fi, and all eight pixels:

```bash
./flash-feather.sh --compile-only  # build first
./flash-feather.sh                 # upload once connected
```

All eight pixels flash white for 150 ms on `sync`, then turn off. Brightness
defaults to the maximum 255/255; override with `SYNC_LED_BRIGHTNESS`. For an RGBW stick,
use `SYNC_LED_RGBW=1 ./flash-feather.sh`.
See [stick wiring and power](docs/recording.md#eight-pixel-neopixel-stick)
before connecting it. Direct-LiPo wiring uses `BAT`; the Feather's `USB` pin
only supplies 5 V while USB is connected.

## Run the viewer

The orientation viewer requires the legacy stream firmware. Switch explicitly:

```bash
SKETCH=firmware/imu_stream ./flash-feather.sh
```

Return to `./flash-feather.sh` and `capture/onboard.py` for onboard dataset capture.

```bash
uv run viz/imu_viz.py
```

The port is auto-detected; pass `-p /dev/cu.usbmodem101` to pick one, or
`--demo` to run without hardware. Keep the board still for the first two
seconds while the gyro bias is measured.

| Key | Action |
|-----|--------|
| C | re-calibrate the gyro bias (hold the board still) |
| Z | zero the heading (yaw) |
| M | toggle fused (gyro + accel) and tilt-only (accel) modes |
| R | reset the filter, peaks, and clip counters |
| S | save a screenshot |
| drag / wheel | orbit / zoom the camera |
| Esc | quit |

Because the LSM6DSO32 has no magnetometer, heading (yaw) is integrated from the
gyro and slowly drifts; roll and pitch are pinned by gravity and do not. Press
`Z` whenever you want the heading re-zeroed.

The firmware runs the accelerometer at ±32 g and the gyro at ±2000 dps (about
2294 dps before the 16-bit readings actually saturate), so deck landings and
fast flips stay in range. The HUD shows the peak accel and gyro seen so far
against those limits, counts samples that reached full scale, and flashes a red
`CLIPPING` warning when one does. Press `R` to reset the peaks and counters.

## Serial protocol

One line per sample, targeted at 100 per second, accel in m/s^2 and gyro in rad/s:

```
D,<device_us>,<ax>,<ay>,<az>,<gx>,<gy>,<gz>,<temp_C>,<sequence>,<boot_id>
```

`device_us` is the 64-bit monotonic microsecond clock since boot. The sample
sequence is an unsigned 32-bit counter; the random eight-character hex boot ID
changes on reset. The viewer accepts these trailing fields. The recorder also
accepts the original nine-field format and unwraps its 32-bit microsecond clock.

At startup the board also prints `R,<accel_sat_ms2>,<gyro_sat_rads>`, the values
at which its raw readings saturate, which the viewer uses for its clipping
check. `I,...` lines are informational, `E,...` lines are errors. Sending `i`
asks the board to repeat its info lines.

Sending `s` requests a 150 ms pulse. Both LED edges produce:

```
S,<device_us>,<sync_id>,<edge>,<led_enabled>,<boot_id>
```

`edge=1` is the rising edge; `edge=0` is the falling edge. `led_enabled=0`
means only an event was recorded, with no physical flash. Each timestamp is taken
immediately before its LED write; it approximates the physical edge time.

With Wi-Fi enabled, UDP port 5050 accepts single-byte commands `i`, `?`, `s`,
and `k` (heartbeat). A recorder acquires a five-second lease by sending one
of these; packets go back to that client's address and port. The recorder
renews every two seconds. One client owns the lease at a time. UDP can lose
packets; sequence gaps and timing gaps are saved for quality review.

## Checks

```bash
python3 -m unittest discover -s tests -v
node --test tests/test_webcam.mjs tests/test_trick_review.mjs
./flash-feather.sh --compile-only
./flash.sh --compile-only
SKATE_WIFI=1 ./flash.sh --compile-only
```

## Troubleshooting

- **No `/dev/cu.usbmodem*` after plugging in.** On Apple Silicon a new accessory
  first needs the "Allow accessory to connect" approval. Other apps that open
  every USB device (Chrome's WebUSB, Spotify) can also grab the port before the
  serial driver does; unplugging and replugging the board usually fixes it.
- **`./flash.sh` fails while "Connecting..."** The reset into the bootloader can
  re-enumerate the USB device and kill the port node; the script already retries
  once, otherwise replug and rerun.
- **`E,no LSM6DSO32 found`** Check the STEMMA QT cable, and if your board is not
  in the sketch's candidate table, pass the pins explicitly with `IMU_SDA` /
  `IMU_SCL` (and `IMU_POWER` if the port has a power-enable pin).
