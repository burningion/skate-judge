// LSM6DSO32 -> hardware FIFO -> onboard LittleFS. Wi-Fi never carries the
// acquisition stream. A separate task owns all sensor and log-file operations.
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WebServer.h>
#include <LittleFS.h>
#include <Adafruit_NeoPixel.h>
#include <esp_timer.h>
#include <esp_partition.h>
#include <driver/gpio.h>
#include <esp32-hal-periman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include "log_format.h"

#ifndef IMU_SDA
#define IMU_SDA 3
#endif
#ifndef IMU_SCL
#define IMU_SCL 4
#endif
#ifndef IMU_POWER
#define IMU_POWER 7
#endif
#ifndef SYNC_LED_PIN
#define SYNC_LED_PIN 5
#endif
#ifndef SYNC_LED_COUNT
#define SYNC_LED_COUNT 8
#endif
#ifndef SYNC_LED_BRIGHTNESS
#define SYNC_LED_BRIGHTNESS 255
#endif
#ifndef SYNC_LED_RGBW
#define SYNC_LED_RGBW 0
#endif

static constexpr uint32_t RESERVE_BYTES = 32768; // Footer and filesystem headroom.
static constexpr uint16_t FIFO_BYTES = 896; // 128 raw tagged FIFO words.
static Adafruit_NeoPixel pixels(SYNC_LED_COUNT, SYNC_LED_PIN,
  (SYNC_LED_RGBW ? NEO_GRBW : NEO_GRB) + NEO_KHZ800);
static WebServer server(80);
static uint32_t bootId;
static uint8_t sensorAddress = 0;
static int idleSda = -1, idleScl = -1;
static bool ledReady = false;
static bool storageReady = false;
static bool usbRequest = false;
static String usbQuery;
static portMUX_TYPE stateMux = portMUX_INITIALIZER_UNLOCKED;
enum Phase { IDLE, STARTING, RECORDING, STOPPING, SAVED, FAULT, TESTING_LED };
struct State {
  Phase phase = IDLE;
  char id[33] = {};
  const char *error = "";
  bool sensorReady = false;
  uint32_t accel = 0, gyro = 0, zeroAccel = 0, ioErrors = 0, fifoOverruns = 0, busRetries = 0;
  uint32_t bytes = 0, freeBytes = 0, marker = 0;
  uint32_t ledTests = 0;
  int batteryBeforeMv = -1, batteryOnMv = -1, ledRmtReady = -1;
  uint64_t startedUs = 0, endedUs = 0, lastSyncUs = 0;
  float accelHz = 0, gyroHz = 0;
};
static State state;
enum Action { START, STOP, FLASH, CHECK_SENSOR, TEST_LED };
struct Command { Action action; char id[33]; uint32_t marker; };
static State snapshot() {
  portENTER_CRITICAL(&stateMux); State copy = state; portEXIT_CRITICAL(&stateMux); return copy;
}
static void publish(const State &s) {
  portENTER_CRITICAL(&stateMux); state = s; portEXIT_CRITICAL(&stateMux);
}
static QueueHandle_t commands;
static char sensorError[112] = "sensor_not_found_check_cable_and_power";

// A short/failed read never leaves uninitialized bytes masquerading as motion.
static bool readReg(uint8_t address, uint8_t reg, uint8_t *data, size_t count) {
  Wire.beginTransmission(address); Wire.write(reg);
  uint8_t error = Wire.endTransmission(false);
  if (error != 0) {
    snprintf(sensorError, sizeof(sensorError), "i2c_address_%02x_reg_%02x_error_%u", address, reg, error);
    return false;
  }
  size_t received = Wire.requestFrom(address, count, true);
  if (received != count) {
    snprintf(sensorError, sizeof(sensorError), "i2c_address_%02x_reg_%02x_short_read_%u_of_%u", address, reg, unsigned(received), unsigned(count));
    while (Wire.available()) Wire.read();
    return false;
  }
  for (size_t i = 0; i < count; ++i) data[i] = Wire.read();
  return true;
}
static bool writeReg(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(sensorAddress); Wire.write(reg); Wire.write(value);
  uint8_t error = Wire.endTransmission();
  if (error) snprintf(sensorError, sizeof(sensorError), "i2c_write_reg_%02x_error_%u", reg, error);
  return error == 0;
}
static bool checkedWrite(uint8_t reg, uint8_t value) {
  uint8_t actual = 0;
  if (!writeReg(reg, value) || !readReg(sensorAddress, reg, &actual, 1)) return false;
  if (actual != value) {
    snprintf(sensorError, sizeof(sensorError), "sensor_reg_%02x_expected_%02x_read_%02x", reg, value, actual);
    return false;
  }
  return true;
}
static bool setupSensor() {
  sensorAddress = 0;
  Wire.end();
  if (IMU_POWER >= 0) {
    pinMode(IMU_POWER, OUTPUT); digitalWrite(IMU_POWER, LOW);
    delay(100); digitalWrite(IMU_POWER, HIGH);
  }
  delay(100);
  // Inspect released lines before the I2C peripheral can drive them. A line
  // held low here is a bus/power problem, not a missing WHO_AM_I register.
  pinMode(IMU_SDA, INPUT_PULLUP); pinMode(IMU_SCL, INPUT_PULLUP);
  delay(2);
  idleSda = gpio_get_level(gpio_num_t(IMU_SDA));
  idleScl = gpio_get_level(gpio_num_t(IMU_SCL));
  if (!idleScl || !idleSda) {
    snprintf(sensorError, sizeof(sensorError), "i2c_bus_held_low_sda_%d_scl_%d_check_cable_sensor_and_power", idleSda, idleScl);
    return false;
  }
  // Use the sensor's supported fast-mode bus to leave acquisition headroom.
  // FIFO buffering absorbs a delayed transaction; a failed read still faults.
  if (!Wire.begin(IMU_SDA, IMU_SCL, 400000)) return false;
  Wire.setTimeOut(50);
  for (uint8_t address : {0x6a, 0x6b}) {
    uint8_t who = 0;
    if (readReg(address, 0x0f, &who, 1)) {
      if (who == 0x6c) { sensorAddress = address; break; }
      snprintf(sensorError, sizeof(sensorError), "unexpected_sensor_id_%02x_at_%02x", who, address);
    }
  }
  if (!sensorAddress || !writeReg(0x12, 0x01)) return false;
  uint8_t reset = 1;
  for (int i = 0; i < 50 && (reset & 1); ++i) {
    delay(1); if (!readReg(sensorAddress, 0x12, &reset, 1)) return false;
  }
  if (reset & 1) return false;
  // Datasheet: BDU + auto-increment, 208 Hz, ±32 g, ±2000 dps,
  // I3C disabled, timestamp enabled. Check every configuration readback.
  return checkedWrite(0x12, 0x44) && checkedWrite(0x18, 0x02) &&
    checkedWrite(0x10, 0x54) && checkedWrite(0x11, 0x5c) &&
    checkedWrite(0x19, 0x20) && checkedWrite(0x08, 0) &&
    checkedWrite(0x09, 0) && checkedWrite(0x0a, 0);
}

static FILE *logFile = nullptr;
static uint32_t packetSequence = 0;
static uint8_t fifoBuffer[FIFO_BYTES];
static uint16_t fifoUsed = 0;
static uint8_t fifoSlotCounter = 0xff, fifoSlotKinds = 0;
static bool writePacket(State &s, uint8_t kind, uint64_t stamp, const void *payload, size_t size) {
  LogHeader header = logHeader(kind, size, packetSequence++, stamp, payload);
  if (!logFile || fwrite(&header, 1, sizeof(header), logFile) != sizeof(header) ||
      fwrite(payload, 1, size, logFile) != size) { s.error = "flash_write_failed"; return false; }
  s.bytes += sizeof(header) + size;
  return true;
}
static bool flushFIFO(State &s) {
  if (!fifoUsed) return true;
  bool ok = writePacket(s, FIFO, esp_timer_get_time(), fifoBuffer, fifoUsed);
  fifoUsed = 0;
  return ok;
}
static bool durable(State &s) {
  if (!logFile || fflush(logFile) || fsync(fileno(logFile))) {
    s.error = "flash_flush_failed"; return false;
  }
  s.freeBytes = LittleFS.totalBytes() - LittleFS.usedBytes();
  return true;
}
static bool clockAnchor(State &s) {
  uint8_t payload[12];
  uint64_t before = esp_timer_get_time();
  bool ok = readReg(sensorAddress, 0x40, payload + 8, 4);
  uint64_t after = esp_timer_get_time();
  if (!ok) { ++s.ioErrors; s.error = "timestamp_read_failed"; return false; }
  memcpy(payload, &after, 8);
  return flushFIFO(s) && writePacket(s, CLOCK, before, payload, sizeof(payload));
}
static bool peekFIFO(State &s, uint8_t *tag) {
  // Reading the tag does not consume a FIFO word. Retry this operation only;
  // arbitrary replay of the consuming payload read could lose a sample.
  for (int attempt = 0; attempt < 3; ++attempt) {
    if (readReg(sensorAddress, 0x78, tag, 1)) return true;
    if (attempt < 2) { ++s.busRetries; delay(1); }
  }
  return false;
}
static bool readFIFOWord(State &s, uint8_t *word) {
  if (!peekFIFO(s, word)) return false;
  for (int attempt = 0; attempt < 3; ++attempt) {
    if (readReg(sensorAddress, 0x79, word + 1, 6)) return true;
    uint8_t head = 0;
    // All configured words in a slot have distinct tags. If the head moved,
    // the failed transfer consumed data, so stop rather than skipping it.
    if (attempt == 2 || !peekFIFO(s, &head) || head != word[0]) return false;
    ++s.busRetries; delay(1);
  }
  return false;
}
static bool drainFIFO(State &s) {
  uint8_t status[2];
  if (!readReg(sensorAddress, 0x3a, status, 2)) {
    ++s.ioErrors; s.error = "fifo_status_read_failed"; return false;
  }
  // FIFO mode stops at full rather than overwriting unread data. Either full
  // or overrun means sampling continuity was lost; report and close the log.
  const bool full = status[1] & 0x68;
  if (full) { ++s.fifoOverruns; s.error = "fifo_full_or_overrun"; }
  uint16_t count = status[0] | ((status[1] & 3) << 8);
  for (uint16_t i = 0; i < count; ++i) {
    if (fifoUsed + 7 > FIFO_BYTES && !flushFIFO(s)) return false;
    uint8_t *word = fifoBuffer + fifoUsed;
    // Read the tag separately from the six FIFO output registers. This keeps
    // tag access outside the burst that consumes the FIFO word.
    if (!readFIFOWord(s, word)) {
      ++s.ioErrors; s.error = "fifo_data_read_failed"; return false;
    }
    const uint8_t tag = word[0] >> 3;
    if (__builtin_parity(unsigned(word[0]))) {
      ++s.ioErrors; s.error = "fifo_tag_parity_failed"; return false;
    }
    if (tag != 1 && tag != 2 && tag != 3 && tag != 4) {
      ++s.ioErrors; s.error = "unexpected_fifo_tag"; return false;
    }
    const uint8_t counter = (word[0] >> 1) & 3;
    if (counter != fifoSlotCounter) { fifoSlotCounter = counter; fifoSlotKinds = 0; }
    if (fifoSlotKinds & (1 << tag)) {
      ++s.ioErrors; s.error = "duplicate_fifo_word_in_time_slot"; return false;
    }
    fifoSlotKinds |= 1 << tag;
    if (tag == 1) ++s.gyro;
    if (tag == 2) {
      ++s.accel;
      if (!(word[1] | word[2] | word[3] | word[4] | word[5] | word[6])) ++s.zeroAccel;
    }
    fifoUsed += 7;
  }
  return !full;
}
static void led(bool on) {
  pixels.fill(on ? (SYNC_LED_RGBW ? pixels.Color(0, 0, 0, SYNC_LED_BRIGHTNESS)
    : pixels.Color(SYNC_LED_BRIGHTNESS, SYNC_LED_BRIGHTNESS, SYNC_LED_BRIGHTNESS)) : 0);
  pixels.show();
}
static int batteryMillivolts() {
  // Optional read-only MAX17048 diagnostic, only on the acquisition task while
  // idle. Do not reset the gauge or mix its read errors into IMU health.
  // Adafruit_MAX1704X: VERSION[15:4] == 1; VCELL uses 78.125 uV/LSB, MSB first.
  auto readWord = [](uint8_t reg) -> int {
    Wire.beginTransmission(0x36); Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return -1;
    if (Wire.requestFrom(uint8_t(0x36), size_t(2), true) != 2) {
      while (Wire.available()) Wire.read();
      return -1;
    }
    int high = Wire.read();
    return (high << 8) | Wire.read();
  };
  int version = readWord(0x08);
  if (version < 0 || (version & 0xfff0) != 0x0010) return -1;
  int raw = readWord(0x02);
  return raw < 0 ? -1 : (raw * 5 + 32) / 64;
}
static bool syncEdge(State &s, uint32_t marker, bool on) {
  uint8_t payload[6]; memcpy(payload, &marker, 4); payload[4] = on; payload[5] = ledReady;
  const uint64_t stamp = esp_timer_get_time();
  led(on);
  if (on) { s.marker = marker; s.lastSyncUs = stamp; }
  return flushFIFO(s) && writePacket(s, SYNC, stamp, payload, sizeof(payload));
}
static void stopLog(State &s, bool &lit) {
  const char *firstError = s.error;
  char firstBusError[sizeof(sensorError)];
  snprintf(firstBusError, sizeof(firstBusError), "%s", sensorError);
  s.phase = STOPPING; publish(s);
  if (!writeReg(0x09, 0)) { ++s.ioErrors; s.error = "fifo_stop_failed"; }
  if (lit) { syncEdge(s, s.marker, false); lit = false; }
  drainFIFO(s); flushFIFO(s); clockAnchor(s);
  s.endedUs = esp_timer_get_time();
  if (*firstError) s.error = firstError; // Keep the cause, not a cleanup failure.
  char footer[440];
  snprintf(footer, sizeof(footer),
    "{\"accel_samples\":%lu,\"gyro_samples\":%lu,\"zero_accel\":%lu,\"io_errors\":%lu,\"fifo_overruns\":%lu,\"bus_retries\":%lu,\"error\":\"%s\",\"bus_error\":\"%s\"}",
    (unsigned long)s.accel, (unsigned long)s.gyro, (unsigned long)s.zeroAccel,
    (unsigned long)s.ioErrors, (unsigned long)s.fifoOverruns, (unsigned long)s.busRetries, s.error, s.ioErrors ? firstBusError : "");
  writePacket(s, END, s.endedUs, footer, strlen(footer)); durable(s);
  if (logFile && fclose(logFile)) s.error = "flash_close_failed";
  logFile = nullptr;
  writeReg(0x0a, 0);
  s.phase = *s.error ? FAULT : SAVED; publish(s);
}
static bool startLog(State &s, const Command &command) {
  s = State(); s.sensorReady = true; s.phase = STARTING;
  memcpy(s.id, command.id, sizeof(s.id)); publish(s);
  if (!storageReady) { s.error = "initialize_storage_first"; return false; }
  s.freeBytes = LittleFS.totalBytes() - LittleFS.usedBytes();
  if (s.freeBytes < RESERVE_BYTES + 65536) { s.error = "insufficient_flash_space"; return false; }
  uint8_t temp[2], frequencyFine;
  if (!checkedWrite(0x12, 0x44) || !checkedWrite(0x18, 0x02) ||
      !checkedWrite(0x10, 0x54) || !checkedWrite(0x11, 0x5c) || !checkedWrite(0x19, 0x20) ||
      !readReg(sensorAddress, 0x20, temp, 2) || !readReg(sensorAddress, 0x63, &frequencyFine, 1) ||
      !checkedWrite(0x09, 0) || !checkedWrite(0x0a, 0)) {
    ++s.ioErrors; s.error = "sensor_preflight_failed"; return false;
  }
  char path[80]; snprintf(path, sizeof(path), "/littlefs/%s.bin", s.id);
  int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
  if (fd < 0) { s.error = "log_exists_or_flash_unavailable"; return false; }
  logFile = fdopen(fd, "wb");
  if (!logFile) { close(fd); s.error = "log_open_failed"; return false; }
  fifoUsed = 0; packetSequence = 0;
  fifoSlotCounter = 0xff; fifoSlotKinds = 0;
  s.startedUs = esp_timer_get_time();
  int16_t rawTemp = int16_t(uint16_t(temp[0]) | (uint16_t(temp[1]) << 8));
  char meta[480];
  snprintf(meta, sizeof(meta),
    "{\"schema\":1,\"id\":\"%s\",\"boot_id\":\"%08lx\",\"sensor\":\"LSM6DSO32\",\"odr_hz\":208,\"accel_g_per_lsb\":0.000976,\"gyro_dps_per_lsb\":0.070,\"timestamp_tick_us\":25,\"frequency_fine\":%d,\"initial_temp_C\":%.4f,\"flash_bytes\":%lu,\"psram_bytes\":%lu}",
    s.id, (unsigned long)bootId, int(int8_t(frequencyFine)), 25.0 + rawTemp / 256.0,
    (unsigned long)ESP.getFlashChipSize(), (unsigned long)ESP.getPsramSize());
  if (!writePacket(s, META, s.startedUs, meta, strlen(meta)) || !clockAnchor(s) || !durable(s)) return false;
  // 208 Hz accel + gyro batching, timestamp every slot, temperature 1.6 Hz,
  // stop-on-full FIFO. The sensor continues sampling during ESP32 flash writes.
  if (!checkedWrite(0x09, 0x55) || !checkedWrite(0x0a, 0x51)) {
    ++s.ioErrors; s.error = "fifo_configuration_failed"; return false;
  }
  s.startedUs = esp_timer_get_time(); s.phase = RECORDING; publish(s); return true;
}
static void acquire(void *) {
  State s = snapshot();
  s.sensorReady = setupSensor();
  if (!s.sensorReady) s.error = sensorError;
  publish(s);
  bool lit = false;
  uint64_t lastBatch = 0, lastFlush = 0, lastClock = 0, rateAt = 0;
  uint32_t previousAccel = 0, previousGyro = 0;
  for (;;) {
    Command cmd;
    while (xQueueReceive(commands, &cmd, 0) == pdTRUE) {
      if (cmd.action == TEST_LED && ledReady &&
          (s.phase == IDLE || s.phase == SAVED || s.phase == FAULT)) {
        // This task owns LED writes. Test only while idle; never add fake sync
        // markers or touch a saved recording. Leave LEDs off when finished.
        Phase previous = s.phase;
        s.phase = TESTING_LED; publish(s);
        s.batteryBeforeMv = batteryMillivolts();
        s.batteryOnMv = -1; s.ledRmtReady = 1;
        for (int i = 0; i < 3; ++i) {
          led(true); vTaskDelay(pdMS_TO_TICKS(1000));
          s.ledRmtReady &= perimanGetPinBus(SYNC_LED_PIN, ESP32_BUS_TYPE_RMT_TX) != nullptr &&
                           rmtTransmitCompleted(SYNC_LED_PIN);
          if (i == 0) s.batteryOnMv = batteryMillivolts();
          led(false); vTaskDelay(pdMS_TO_TICKS(1000));
        }
        ++s.ledTests; s.phase = previous; publish(s);
      } else if (cmd.action == CHECK_SENSOR && s.phase != RECORDING && s.phase != STOPPING) {
        s.phase = STARTING; publish(s);
        s.sensorReady = setupSensor();
        s.error = s.sensorReady ? "" : sensorError;
        s.phase = IDLE; publish(s);
      } else if (cmd.action == START && s.phase != RECORDING && s.phase != STOPPING) {
        // Do not reset the sensor or reopen a log on a retried start request.
        if (!strcmp(s.id, cmd.id)) continue;
        if (!s.sensorReady || !startLog(s, cmd)) {
          if (logFile) stopLog(s, lit);
          else { s.phase = FAULT; publish(s); }
        } else {
          lastBatch = lastFlush = lastClock = rateAt = s.startedUs;
          previousAccel = previousGyro = 0;
        }
      } else if (cmd.action == STOP && s.phase == RECORDING && !strcmp(cmd.id, s.id)) {
        stopLog(s, lit);
      } else if (cmd.action == FLASH && s.phase == RECORDING && !strcmp(cmd.id, s.id) && cmd.marker > s.marker && !lit) {
        if (!ledReady) { s.error = "sync_led_unavailable"; stopLog(s, lit); }
        else if (syncEdge(s, cmd.marker, true)) { lit = true; publish(s); }
        else stopLog(s, lit);
      }
    }
    if (s.phase == RECORDING) {
      const uint64_t now = esp_timer_get_time();
      bool ok = true;
      if (lit && now - s.lastSyncUs >= 150000) { ok = syncEdge(s, s.marker, false); lit = false; }
      ok = ok && drainFIFO(s);
      if (now - lastBatch >= 50000) { ok = ok && flushFIFO(s); lastBatch = now; }
      if (now - lastClock >= 1000000) { ok = ok && clockAnchor(s); lastClock = now; }
      if (now - lastFlush >= 250000) { ok = ok && durable(s); lastFlush = now; }
      if (now - rateAt >= 1000000) {
        s.accelHz = (s.accel - previousAccel) * 1e6 / (now - rateAt);
        s.gyroHz = (s.gyro - previousGyro) * 1e6 / (now - rateAt);
        previousAccel = s.accel; previousGyro = s.gyro; rateAt = now;
        if (now - s.startedUs > 3000000 && (s.accelHz < 180 || s.gyroHz < 180)) {
          s.error = "sample_rate_below_180_hz"; ok = false;
        }
        if (s.accel >= 400 && s.zeroAccel > s.accel / 4) {
          s.error = "excessive_zero_acceleration_check_sensor"; ok = false;
        }
      }
      if (s.freeBytes <= RESERVE_BYTES) { s.error = "flash_capacity_reached"; ok = false; }
      if (!ok) stopLog(s, lit);
      else publish(s);
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

static bool validId(const String &id) {
  if (id.length() != 32) return false;
  for (char c : id) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  return true;
}
static String parameter(const char *name) {
  if (!usbRequest) return server.arg(name);
  String prefix = String(name) + "=";
  int begin = 0;
  while (begin <= int(usbQuery.length())) {
    int end = usbQuery.indexOf('&', begin);
    if (end < 0) end = usbQuery.length();
    String field = usbQuery.substring(begin, end);
    if (field.startsWith(prefix)) return field.substring(prefix.length());
    begin = end + 1;
  }
  return "";
}
static void reply(int code, const char *type, const String &body) {
  if (usbRequest) {
    Serial.printf("{\"status\":%d,\"body\":", code); Serial.print(body); Serial.println("}");
  } else server.send(code, type, body);
}
static void errorResponse(int code, const char *error) {
  reply(code, "application/json", String("{\"error\":\"") + error + "\"}");
}
static bool idleOnly() {
  Phase phase = snapshot().phase;
  if (phase == RECORDING || phase == STARTING || phase == STOPPING || phase == TESTING_LED || uxQueueMessagesWaiting(commands)) {
    errorResponse(409, "Board is busy; stop recording or wait for the current operation."); return false;
  }
  return true;
}
static bool controlAllowed() {
  if (!usbRequest && server.hasHeader("Origin") && server.header("Origin").length()) {
    errorResponse(403, "Use the local laptop recorder."); return false;
  }
  return true;
}
static void checkSensor() {
  if (!controlAllowed() || !idleOnly()) return;
  Command cmd{CHECK_SENSOR, {}, 0};
  if (xQueueSend(commands, &cmd, 0) != pdTRUE) { errorResponse(503, "Control queue full; retry."); return; }
  reply(202, "application/json", "{\"accepted\":true}");
}
static void testLED() {
  if (!controlAllowed() || !idleOnly()) return;
  if (!ledReady) { errorResponse(409, "Sync LED is unavailable; check firmware settings."); return; }
  Command cmd{TEST_LED, {}, 0};
  if (xQueueSend(commands, &cmd, 0) != pdTRUE) { errorResponse(503, "Control queue full; retry."); return; }
  reply(202, "application/json", "{\"accepted\":true}");
}
static void statusResponse() {
  State s = snapshot();
  const char *phases[] = {"idle", "starting", "recording", "stopping", "saved", "fault", "testing_led"};
  char response[1400];
  snprintf(response, sizeof(response),
    "{\"protocol\":1,\"phase\":\"%s\",\"id\":\"%s\",\"boot_id\":\"%08lx\",\"error\":\"%s\",\"sensor_ready\":%s,\"storage_ready\":%s,\"led_enabled\":%s,\"accel_samples\":%lu,\"gyro_samples\":%lu,\"zero_accel\":%lu,\"io_errors\":%lu,\"fifo_overruns\":%lu,\"bytes\":%lu,\"free_bytes\":%lu,\"started_us\":%llu,\"ended_us\":%llu,\"device_us\":%llu,\"accel_hz\":%.2f,\"gyro_hz\":%.2f,\"sync_id\":%lu,\"sync_us\":%llu,\"flash_bytes\":%lu,\"psram_bytes\":%lu,\"i2c_idle_sda\":%d,\"i2c_idle_scl\":%d,\"bus_retries\":%lu}",
    phases[s.phase], s.id, (unsigned long)bootId, s.error, s.sensorReady ? "true" : "false",
    storageReady ? "true" : "false", ledReady ? "true" : "false",
    (unsigned long)s.accel, (unsigned long)s.gyro, (unsigned long)s.zeroAccel,
    (unsigned long)s.ioErrors, (unsigned long)s.fifoOverruns, (unsigned long)s.bytes,
    (unsigned long)(s.phase == RECORDING ? s.freeBytes : storageReady ? LittleFS.totalBytes() - LittleFS.usedBytes() : 0),
    s.startedUs, s.endedUs, (uint64_t)esp_timer_get_time(), s.accelHz, s.gyroHz,
    (unsigned long)s.marker, s.lastSyncUs,
    (unsigned long)ESP.getFlashChipSize(), (unsigned long)ESP.getPsramSize(), idleSda, idleScl, (unsigned long)s.busRetries);
  // Report the settings actually flashed, rather than relying on host presets.
  char ledStatus[300];
  snprintf(ledStatus, sizeof(ledStatus),
    ",\"led_pin\":%d,\"led_count\":%d,\"led_format\":\"%s\",\"led_brightness\":%d,\"led_tests\":%lu,\"led_test_battery_before_mv\":%d,\"led_test_battery_on_mv\":%d,\"led_test_rmt_ready\":%d}",
    SYNC_LED_PIN, SYNC_LED_COUNT, SYNC_LED_RGBW ? "GRBW" : "GRB",
    SYNC_LED_BRIGHTNESS, (unsigned long)s.ledTests, s.batteryBeforeMv, s.batteryOnMv, s.ledRmtReady);
  response[strlen(response) - 1] = 0;
  reply(200, "application/json", String(response) + ledStatus);
}
static void enqueueControl(Action action) {
  if (!controlAllowed()) return;
  String id = parameter("id");
  if (!validId(id)) { errorResponse(400, "Expected a 32-character recording ID."); return; }
  State s = snapshot();
  if (action == START) {
    if (!strcmp(s.id, id.c_str())) { statusResponse(); return; }
    if (!idleOnly()) return;
    if (!storageReady || !s.sensorReady || !ledReady) { errorResponse(409, "Sensor, storage, and sync LED must be ready."); return; }
    if (LittleFS.exists(("/" + id + ".bin").c_str())) { errorResponse(409, "Recording already exists; download it."); return; }
  } else if (strcmp(s.id, id.c_str())) { errorResponse(409, "Recording ID does not match."); return; }
  const long marker = parameter("marker").toInt();
  if (action == FLASH && (marker < 1 || s.phase != RECORDING)) { errorResponse(409, "Sync needs an active recording and positive marker."); return; }
  if (action == FLASH && uint32_t(marker) <= s.marker) { statusResponse(); return; }
  Command cmd{action, {}, uint32_t(marker)}; memcpy(cmd.id, id.c_str(), 33);
  if (xQueueSend(commands, &cmd, 0) != pdTRUE) { errorResponse(503, "Control queue full; retry."); return; }
  reply(202, "application/json", "{\"accepted\":true}");
}
static bool blankStoragePartition() {
  const esp_partition_t *p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "spiffs");
  if (!p) return false;
  uint8_t buffer[512];
  for (size_t offset = 0; offset < p->size; offset += sizeof(buffer)) {
    size_t n = min(sizeof(buffer), size_t(p->size - offset));
    if (esp_partition_read(p, offset, buffer, n) != ESP_OK) return false;
    for (size_t i = 0; i < n; ++i) if (buffer[i] != 0xff) return false;
  }
  return true;
}
static void initializeStorage() {
  if (!controlAllowed() || !idleOnly()) return;
  if (!storageReady) {
    if (!blankStoragePartition()) { errorResponse(409, "Data partition is not blank. Back it up; initialization will not erase existing data."); return; }
    storageReady = LittleFS.format() && LittleFS.begin(false);
  }
  if (!storageReady) { errorResponse(507, "Storage initialization failed."); return; }
  statusResponse();
}
static void listFiles() {
  if (!idleOnly()) return;
  if (!storageReady) { errorResponse(409, "Storage is not mounted."); return; }
  String result = "[";
  File root = LittleFS.open("/");
  for (File f = root.openNextFile(); f; f = root.openNextFile()) {
    String name = f.name();
    if (name.startsWith("/")) name.remove(0, 1);
    String id = name.substring(0, 32);
    if (name.length() != 36 || !name.endsWith(".bin") || !validId(id)) continue;
    if (result.length() > 1) result += ',';
    result += "{\"id\":\"" + id + "\",\"bytes\":" + String(f.size()) + "}";
  }
  reply(200, "application/json", result + "]");
}
static void fileRequest(bool infoOnly, bool remove) {
  if ((remove && !controlAllowed()) || !idleOnly()) return;
  String id = parameter("id");
  if (!validId(id)) { errorResponse(400, "Invalid recording ID."); return; }
  String path = "/" + id + ".bin";
  File file = LittleFS.open(path, FILE_READ);
  if (!file) { errorResponse(404, "Recording not found."); return; }
  const size_t size = file.size();
  uint8_t buffer[1024]; uint32_t crc = 0;
  while (file.available()) { size_t n = file.read(buffer, sizeof(buffer)); if (!n) break; crc = logCRC(crc, buffer, n); }
  char hex[9]; snprintf(hex, sizeof(hex), "%08lx", (unsigned long)crc);
  if (infoOnly) {
    reply(200, "application/json", "{\"id\":\"" + id + "\",\"bytes\":" + String(size) + ",\"crc32\":\"" + hex + "\"}"); return;
  }
  if (remove) {
    file.close();
    if (parameter("crc32") != hex) { errorResponse(409, "Download and verify this file before deleting it."); return; }
    if (!LittleFS.remove(path)) { errorResponse(507, "Delete failed."); return; }
    reply(200, "application/json", "{\"deleted\":true}"); return;
  }
  long offset = parameter("offset").toInt();
  if (offset < 0 || size_t(offset) > size) { errorResponse(416, "Offset outside file."); return; }
  file.seek(offset);
  if (usbRequest) {
    Serial.printf("{\"status\":200,\"binary\":%u,\"crc32\":\"%s\"}\n", unsigned(size - offset), hex);
    while (file.available()) {
      size_t n = file.read(buffer, sizeof(buffer)); if (!n) break;
      // USB transfer is allowed only while idle. Bound each write and wait for
      // the host to read; acquisition never calls Serial.write.
      size_t sent = 0; uint32_t deadline = millis() + 5000;
      while (sent < n && millis() < deadline) {
        size_t wrote = Serial.write(buffer + sent, n - sent);
        sent += wrote; if (!wrote) delay(1);
      }
      if (sent != n) break;
    }
    return;
  }
  server.setContentLength(size - offset);
  server.sendHeader("X-Log-CRC32", hex);
  server.send(offset ? 206 : 200, "application/octet-stream", "");
  auto client = server.client();
  while (file.available() && client.connected()) {
    size_t n = file.read(buffer, sizeof(buffer)); if (!n) break;
    if (client.write(buffer, n) != n) break;
  }
}
static void pollUSB() {
  static String line;
  for (int i = 0; i < 256 && Serial.available(); ++i) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (line.length() < 200) line += c;
      continue;
    }
    int space = line.indexOf(' ');
    bool post = line.startsWith("POST ");
    String route = line.substring(space + 1);
    int question = route.indexOf('?');
    usbQuery = question < 0 ? "" : route.substring(question + 1);
    if (question >= 0) route = route.substring(0, question);
    usbRequest = true;
    if (space < 0 || (!post && !line.startsWith("GET "))) errorResponse(400, "Use GET or POST and a route.");
    else if (route == "/status") statusResponse();
    else if (route == "/initialize" && post) initializeStorage();
    else if (route == "/check-sensor" && post) checkSensor();
    else if (route == "/test-led" && post) testLED();
    else if (route == "/start" && post) enqueueControl(START);
    else if (route == "/stop" && post) enqueueControl(STOP);
    else if (route == "/sync" && post) enqueueControl(FLASH);
    else if (route == "/files") listFiles();
    else if (route == "/file-info") fileRequest(true, false);
    else if (route == "/file") fileRequest(false, false);
    else if (route == "/delete" && post) fileRequest(false, true);
    else errorResponse(404, "Unknown route.");
    usbRequest = false; line = "";
  }
}
void setup() {
  Serial.begin(115200); Serial.setTxTimeoutMs(1000); bootId = esp_random();
  ledReady = GPIO_IS_VALID_OUTPUT_GPIO(SYNC_LED_PIN) && SYNC_LED_COUNT >= 1 && SYNC_LED_COUNT <= 64 &&
    SYNC_LED_BRIGHTNESS >= 1 && SYNC_LED_BRIGHTNESS <= 255 &&
    SYNC_LED_PIN != IMU_SDA && SYNC_LED_PIN != IMU_SCL &&
    SYNC_LED_PIN != IMU_POWER && pixels.begin();
  if (ledReady) led(false);
  storageReady = LittleFS.begin(false); // Never autoformat on mount failure.
  commands = xQueueCreate(8, sizeof(Command));
  if (!commands || xTaskCreatePinnedToCore(acquire, "imu-acquire", 8192, nullptr, 3, nullptr, 1) != pdPASS) {
    Serial.println("E,cannot start acquisition task"); return;
  }
  char apName[32]; snprintf(apName, sizeof(apName), "SkateJudge-%04x", unsigned(ESP.getEfuseMac() & 0xffff));
  WiFi.mode(WIFI_AP); WiFi.softAP(apName, "skate-judge");
  const char *headers[] = {"Origin"}; server.collectHeaders(headers, 1);
  server.on("/status", HTTP_GET, statusResponse);
  server.on("/initialize", HTTP_POST, initializeStorage);
  server.on("/check-sensor", HTTP_POST, checkSensor);
  server.on("/test-led", HTTP_POST, testLED);
  server.on("/start", HTTP_POST, [] { enqueueControl(START); });
  server.on("/stop", HTTP_POST, [] { enqueueControl(STOP); });
  server.on("/sync", HTTP_POST, [] { enqueueControl(FLASH); });
  server.on("/files", HTTP_GET, listFiles);
  server.on("/file-info", HTTP_GET, [] { fileRequest(true, false); });
  server.on("/file", HTTP_GET, [] { fileRequest(false, false); });
  server.on("/delete", HTTP_POST, [] { fileRequest(false, true); });
  server.begin();
  Serial.printf("I,onboard logger at http://192.168.4.1; AP=%s; flash=%lu PSRAM=%lu bytes; boot=%08lx\n",
    apName, (unsigned long)ESP.getFlashChipSize(), (unsigned long)ESP.getPsramSize(), (unsigned long)bootId);
}
void loop() {
  server.handleClient();
  pollUSB();
  delay(1);
}
