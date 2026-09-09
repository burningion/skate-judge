#!/usr/bin/env bash
# Adafruit Feather ESP32-S3, 8 MB flash / no PSRAM, with an eight-pixel
# SKC6812 RGB stick on the header marked 5 (GPIO5).
# ./flash-feather.sh --compile-only builds without uploading.
# Override SYNC_LED_RGBW=1 for an RGBW stick.
# SKETCH=firmware/imu_stream selects the legacy live-stream firmware.
set -euo pipefail

export FQBN="${FQBN:-esp32:esp32:adafruit_feather_esp32s3_nopsram:USBMode=hwcdc,CDCOnBoot=cdc,UploadMode=default,PartitionScheme=default_8MB}"
export IMU_SDA="${IMU_SDA:-3}" IMU_SCL="${IMU_SCL:-4}" IMU_POWER="${IMU_POWER:-7}"
export SKATE_WIFI="${SKATE_WIFI:-1}"
export SKETCH="${SKETCH:-firmware/imu_logger}"
export SYNC_LED_PIN="${SYNC_LED_PIN:-5}"
export SYNC_LED_RGB="${SYNC_LED_RGB:-1}"
export SYNC_LED_COUNT="${SYNC_LED_COUNT:-8}"
export SYNC_LED_BRIGHTNESS="${SYNC_LED_BRIGHTNESS:-48}"
if [ "${SYNC_LED_RGBW:-0}" = "1" ]; then export SYNC_LED_RGB=0; fi

exec "$(dirname "$0")/flash.sh" "$@"
