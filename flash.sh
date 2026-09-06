#!/usr/bin/env bash
# Compile firmware/imu_stream and flash it to the ESP32-S3 over its native USB port.
#
#   ./flash.sh                                   auto-detect /dev/cu.usbmodem*
#   ./flash.sh /dev/cu.usbmodem101               explicit port
#   IMU_SDA=3 IMU_SCL=4 IMU_POWER=7 ./flash.sh   force the I2C pins instead of auto-probing
#   FQBN=esp32:esp32:esp32s3:CDCOnBoot=cdc ./flash.sh   override the board definition
#   SYNC_LED_PIN=N SYNC_LED_RGB=1 SYNC_LED_COUNT=8 ./flash.sh  RGB stick; replace N with verified GPIO
#   SYNC_LED_RGBW=1 selects an RGBW stick instead; SYNC_LED_BRIGHTNESS=48 is the default (1-255)
#   ./flash.sh --compile-only                  build without uploading
#
# Needs arduino-cli with the esp32 core:
#   brew install arduino-cli
#   arduino-cli core install esp32:esp32 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
# The Adafruit libraries are installed into ./.arduino on the first run.
set -euo pipefail
cd "$(dirname "$0")"

export ARDUINO_DIRECTORIES_USER="$PWD/.arduino"
FQBN="${FQBN:-esp32:esp32:esp32s3:CDCOnBoot=cdc}"
PORT="${1:-${PORT:-}}"

if ! command -v arduino-cli >/dev/null 2>&1; then
  echo "arduino-cli not found. Install it with:  brew install arduino-cli" >&2
  exit 1
fi
if ! arduino-cli core list 2>/dev/null | grep -q '^esp32:esp32'; then
  echo "The esp32 core is not installed. Install it with:" >&2
  echo "  arduino-cli core install esp32:esp32 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json" >&2
  exit 1
fi
if [ ! -d "$ARDUINO_DIRECTORIES_USER/libraries/Adafruit_LSM6DS" ]; then
  echo "== installing Adafruit LSM6DS (+ BusIO, Unified Sensor) into $ARDUINO_DIRECTORIES_USER"
  arduino-cli lib install "Adafruit LSM6DS"
fi

EXTRA=()
DEFINES=()
if [ -n "${IMU_SDA:-}" ] && [ -n "${IMU_SCL:-}" ]; then
  DEFINES+=("-DIMU_SDA=${IMU_SDA}" "-DIMU_SCL=${IMU_SCL}" "-DIMU_POWER=${IMU_POWER:--1}")
fi
for key in SKATE_WIFI SYNC_LED_PIN SYNC_LED_RGB SYNC_LED_RGBW SYNC_LED_ACTIVE_LOW SYNC_LED_COUNT SYNC_LED_BRIGHTNESS; do
  value="${!key:-}"
  if [ -n "$value" ]; then
    if ! [[ "$value" =~ ^-?[0-9]+$ ]]; then
      echo "$key must be an integer" >&2
      exit 1
    fi
    DEFINES+=("-D${key}=${value}")
  fi
done
if [ ${#DEFINES[@]} -gt 0 ]; then
  EXTRA=(--build-property "compiler.cpp.extra_flags=${DEFINES[*]}")
fi

if [ "${SYNC_LED_RGB:-0}" != "0" ] || [ "${SYNC_LED_RGBW:-0}" != "0" ]; then
  if [ ! -d "$ARDUINO_DIRECTORIES_USER/libraries/Adafruit_NeoPixel" ]; then
    echo "== installing Adafruit NeoPixel into $ARDUINO_DIRECTORIES_USER"
    arduino-cli lib install "Adafruit NeoPixel@1.15.4"
  fi
fi

echo "== compiling for $FQBN"
arduino-cli compile --fqbn "$FQBN" ${EXTRA[@]+"${EXTRA[@]}"} firmware/imu_stream
if [ "$PORT" = "--compile-only" ]; then exit 0; fi

find_port() { ls /dev/cu.usbmodem* 2>/dev/null | head -n 1 || true; }
[ -n "$PORT" ] || PORT=$(find_port)
if [ -z "$PORT" ]; then
  echo "No /dev/cu.usbmodem* port found. Plug the board in, or pass the port as the first argument." >&2
  exit 1
fi

# esptool's reset can make the chip re-enumerate mid-connect, which kills the port node on
# macOS. The chip is then usually sitting in the bootloader, so one retry normally succeeds.
echo "== uploading to $PORT"
if ! arduino-cli upload --fqbn "$FQBN" -p "$PORT" firmware/imu_stream; then
  echo "== upload failed, waiting for the port and retrying once"
  sleep 3
  PORT=$(find_port)
  if [ -z "$PORT" ]; then
    echo "The port did not come back. Unplug and replug the board, then rerun ./flash.sh" >&2
    exit 1
  fi
  arduino-cli upload --fqbn "$FQBN" -p "$PORT" firmware/imu_stream
fi
echo "== done. Run the viewer with:  uv run viz/imu_viz.py"
