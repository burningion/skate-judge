# skate-judge

Record skateboard motion from an Adafruit LSM6DSO32 (accelerometer + gyro)
wired to an ESP32-S3 over STEMMA QT / Qwiic. Label makes, bails, and falls,
and align independent phone/webcam video using timestamped LED flashes.
The original live 3D orientation viewer is also included.

This is the **data collection stage**: it does not yet detect tricks, train
a model, or play automatic audio. A board-mounted IMU measures the board's
motion, which does not always reveal whether the rider stayed on or fell.
See [the recording workflow and model plan](docs/recording.md).

```
firmware/imu_stream/   Arduino sketch: 100 Hz USB / optional Wi-Fi motion stream and LED sync
capture/session.py    recorder, human outcome labels, phone/video clock alignment
viz/imu_viz.py         pygame + OpenGL viewer: Mahony sensor fusion, calibration, 3D board with axes
flash.sh               compile + upload with arduino-cli
tests/                 recording integrity and video alignment checks
```

## Record a session

```bash
# Exercise the recorder without hardware (synthetic data, not training data).
uv run capture/session.py record --demo --duration 5

# USB bench recording; do not skate while tethered to the laptop.
uv run capture/session.py record --rider rider-01 --board deck-01
```

While recording, enter `start ollie` before an attempt, then `make`, `bail`,
or `fall` after observing the outcome. Use `background` for non-trick intervals,
`unknown` for ambiguous attempts, `sync` for a video marker, and `quit` to save
and exit. Each command is followed by Enter. Keep the viewer closed when
recording over USB; only one application should own the serial port.

For battery-powered recording, build with `SKATE_WIFI=1 ./flash.sh`, join the
board's `SkateJudge-XXXX` Wi-Fi network (prototype password `skate-judge`), then:

```bash
uv run capture/session.py record --udp 192.168.4.1 --rider rider-01 --board deck-01
```

The board streams to the laptop; it does not store data onboard. Sessions are
saved under `sessions/` and excluded from Git. LED flashes require an explicitly
configured pin; see [hardware and video setup](docs/recording.md).

## One-time setup

```bash
brew install arduino-cli uv
arduino-cli core install esp32:esp32 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

The Adafruit LSM6DS library (plus BusIO and Unified Sensor) is installed into
`./.arduino` the first time `flash.sh` runs, so nothing touches your global
Arduino sketchbook.

## Flash the board

```bash
./flash.sh
```

Use `./flash.sh --compile-only` to build without uploading.

The sketch is built for the generic `esp32s3` target with USB CDC on boot, so it
talks over the ESP32-S3's native USB port. It probes the usual STEMMA QT pin
pairs at startup and, for Adafruit Feathers, switches on the I2C power rail
first. It prints what it found:

```
I,LSM6DSO32 at 0x6A on SDA=3 SCL=4 (Adafruit Feather ESP32-S3 / Reverse TFT, I2C power on GPIO 7)
```

To skip the probing, force the pins: `IMU_SDA=3 IMU_SCL=4 IMU_POWER=7 ./flash.sh`.

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
