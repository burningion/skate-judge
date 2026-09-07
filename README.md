# Judgy Skateboard

Record skateboard motion from an Adafruit LSM6DSO32 (accelerometer + gyro)
wired to an ESP32-S3 over STEMMA QT / Qwiic. Record webcam video in the web UI,
label makes, bails, and falls, and align webcam or phone footage using timestamped LED flashes.
The original live 3D orientation viewer is also included.

![hardware layout](./hardware/fritzing/skate-judge-direct-lipo.png)

This is the **data collection stage**: it does not yet detect tricks, train
a model, or play automatic audio. A board-mounted IMU measures the board's
motion, which does not always reveal whether the rider stayed on or fell.
Start with [your first dataset recording](docs/first-recording.md). The
[hardware and recording reference](docs/recording.md) covers wiring, file formats,
and the model plan.

```
firmware/imu_stream/   Arduino sketch: 100 Hz USB / optional Wi-Fi motion stream and LED sync
capture/session.py    recorder, human outcome labels, phone/video clock alignment
capture/controls.html local webcam recording and countdown UI (--controls)
viz/imu_viz.py         pygame + OpenGL viewer: Mahony sensor fusion, calibration, 3D board with axes
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

Follow the [step-by-step first-session guide](docs/first-recording.md) for setup,
a USB bench check, battery/Wi-Fi recording, and video labels. Run the demo while
the laptop still has internet access so `uv` can download its dependencies:

```bash
# Exercise the recorder without hardware (synthetic data, not training data).
uv run capture/session.py record --demo --duration 5

# USB bench recording; do not skate while tethered to the laptop.
uv run capture/session.py record --rider rider-01 --board deck-01
```

While recording, enter `start ollie` before an attempt, then `make`, `bail`,
or `fall` after observing the outcome. Use `background` for non-trick intervals,
`unknown` for ambiguous attempts, `countdown` for a video marker after three
seconds (`sync` skips the countdown), and `quit` to save and exit. Each command
is followed by Enter. Keep the viewer closed when
recording over USB; only one application should own the serial port.

For this Feather and stick, build with `./flash-feather.sh`, join the
board's `SkateJudge-XXXX` Wi-Fi network (prototype password `skate-judge`), then:

```bash
uv run --offline capture/session.py record --udp 192.168.4.1 --controls --rider rider-01 --board deck-01
```

Open the printed controls URL **on the recording computer**. With the Feather
powered, Wi-Fi connected, and sensor recording running, click **Enable camera**,
allow browser access, and click **Start video recording**. Preview alone is not
recording. Microphone audio is off unless you opt in. Then click **Start 3-second
countdown**; the Feather flashes the stick and supplies its timestamp.

Repeat the countdown near the end, then click **Stop & save video**. Wait for
**Saved** before entering `quit` in the terminal. Video is written directly to
the sensor-session folder as `webcam-<id>.webm` or `.mp4`, with a matching JSON
sidecar. Keep the browser tab and terminal open until saving finishes. Match the
visible flashes afterward using that actual filename; webcam recording does not
automatically align the clocks. See [webcam details](docs/recording.md#webcam-recording-in-the-web-ui).

You can still record separately on an iPhone and leave the webcam off.
There are no automatic flashes by default.
The Feather's physical buttons are unchanged. Without `--controls`, the terminal
`countdown` command does the same thing.

The board streams to the laptop; it does not store data onboard. Sessions are
saved under `sessions/` and excluded from Git. The Feather preset selects GPIO5
and eight pixels; the generic `flash.sh` leaves the sync pin disabled by default.

## One-time setup

```bash
brew install arduino-cli uv
arduino-cli core install esp32:esp32 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

The Adafruit LSM6DS library (plus BusIO and Unified Sensor) is installed into
`./.arduino` the first time `flash.sh` runs. NeoPixel builds also install
Adafruit NeoPixel there, so nothing touches your global Arduino sketchbook.

## Flash the board

For this Feather and stick, use `./flash-feather.sh`; for other boards, the
generic entry point is `./flash.sh`.

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
defaults to 48/255; override with `SYNC_LED_BRIGHTNESS`. For an RGBW stick,
use `SYNC_LED_RGBW=1 ./flash-feather.sh`.
See [stick wiring and power](docs/recording.md#eight-pixel-neopixel-stick)
before connecting it. Direct-LiPo wiring uses `BAT`; the Feather's `USB` pin
only supplies 5 V while USB is connected.

## Run the viewer

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
