# skate-judge: IMU orientation viewer

Live 3D orientation of an Adafruit LSM6DSO32 (accelerometer + gyro) wired to an
ESP32-S3 over STEMMA QT / Qwiic, rendered on the Mac.

```
firmware/imu_stream/   Arduino sketch: finds the sensor, streams accel + gyro at 100 Hz over USB serial
viz/imu_viz.py         pygame + OpenGL viewer: Mahony sensor fusion, calibration, 3D board with axes
flash.sh               compile + upload with arduino-cli
```

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

One line per sample, 100 per second, accel in m/s^2 and gyro in rad/s:

```
D,<micros>,<ax>,<ay>,<az>,<gx>,<gy>,<gz>,<temp_C>
```

At startup the board also prints `R,<accel_sat_ms2>,<gyro_sat_rads>`, the values
at which its raw readings saturate, which the viewer uses for its clipping
check. `I,...` lines are informational, `E,...` lines are errors. Sending `i`
asks the board to repeat its info lines.

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
