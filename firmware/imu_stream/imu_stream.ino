// imu_stream.ino
//
// Streams LSM6DSO32 accelerometer + gyroscope samples over USB serial for
// viz/imu_viz.py.  Output, 100 lines per second:
//
//   D,<device_us>,<ax>,<ay>,<az>,<gx>,<gy>,<gz>,<temp_C>,<sequence>,<boot_id>
//                                                     accel m/s^2, gyro rad/s
//   R,<accel_sat_ms2>,<gyro_sat_rads>                   where the int16 readings saturate
//   I,<text>                                            informational
//   E,<text>                                            error
//
// Send "i" (or "?") to get the info lines again.
// Send "s" for a timestamped 150 ms LED sync pulse. New D lines append a
// sequence number and boot ID; the orientation viewer ignores these fields.
// For an eight-pixel SKC6812 RGB stick, set SYNC_LED_RGB=1, SYNC_LED_COUNT=8,
// and SYNC_LED_PIN to its data GPIO. SYNC_LED_RGBW=1 selects a GRBW stick.
// Optional SKATE_WIFI=1 starts a local AP and streams UDP to a subscribing
// recorder on port 5050. USB remains available.
//
// The sketch does not assume a particular ESP32-S3 board: it tries the usual
// STEMMA QT / Qwiic I2C pin pairs until it finds the sensor.  To force a pair,
// build with  -DIMU_SDA=<pin> -DIMU_SCL=<pin> [-DIMU_POWER=<pin>]  (flash.sh reads
// IMU_SDA / IMU_SCL / IMU_POWER from the environment and does this for you).

#include <Wire.h>
#include <Adafruit_LSM6DSO32.h>
#include <esp_timer.h>
#include <esp_system.h>
#include <driver/gpio.h>
#include <stdarg.h>

#ifndef SKATE_WIFI
#define SKATE_WIFI 0
#endif
#ifndef SYNC_LED_PIN
#define SYNC_LED_PIN -1
#endif
#ifndef SYNC_LED_RGB
#define SYNC_LED_RGB 0
#endif
#ifndef SYNC_LED_ACTIVE_LOW
#define SYNC_LED_ACTIVE_LOW 0
#endif
#ifndef SYNC_LED_RGBW
#define SYNC_LED_RGBW 0
#endif
#ifndef SYNC_LED_COUNT
#define SYNC_LED_COUNT 1
#endif
#ifndef SYNC_LED_BRIGHTNESS
#define SYNC_LED_BRIGHTNESS 255  // maximum PWM channel value for visible sync flashes
#endif

#if SYNC_LED_RGB || SYNC_LED_RGBW
#if SYNC_LED_COUNT < 1 || SYNC_LED_COUNT > 64
#error "SYNC_LED_COUNT must be between 1 and 64"
#endif
#if SYNC_LED_BRIGHTNESS < 1 || SYNC_LED_BRIGHTNESS > 255
#error "SYNC_LED_BRIGHTNESS must be between 1 and 255"
#endif
#if SYNC_LED_PIN >= 0
#include <Adafruit_NeoPixel.h>
static Adafruit_NeoPixel syncPixels(SYNC_LED_COUNT, SYNC_LED_PIN,
                                   (SYNC_LED_RGBW ? NEO_GRBW : NEO_GRB) + NEO_KHZ800);
#endif
#endif

#if SKATE_WIFI
#include <WiFi.h>
#include <NetworkUdp.h>
static NetworkUDP udp;
static IPAddress recorderIP;
static uint16_t recorderPort = 0;
static uint32_t lastHeartbeat = 0;
static bool wifiReady = false;
static char apName[32];
#endif

static uint32_t bootId;
static uint32_t sampleSequence = 0;
static uint32_t syncSequence = 0;
static bool syncOn = false;
static bool ledReady = false;
static int64_t syncOffAt = 0;

static void emit(const char *format, ...) {
  char line[240];
  va_list args;
  va_start(args, format);
  vsnprintf(line, sizeof(line), format, args);
  va_end(args);
  if (Serial) Serial.print(line);
#if SKATE_WIFI
  if (wifiReady && recorderPort && millis() - lastHeartbeat < 5000) {
    if (udp.beginPacket(recorderIP, recorderPort)) {
      udp.write((const uint8_t *)line, strlen(line));
      udp.endPacket();
    }
  }
#endif
}

static void setSyncLED(bool on) {
  if (!ledReady) return;
#if SYNC_LED_PIN >= 0
#if SYNC_LED_RGB || SYNC_LED_RGBW
  // Fill the whole chain, then latch one frame so all eight pixels flash
  // together. RGBW sticks use their dedicated white channel.
  uint8_t level = on ? SYNC_LED_BRIGHTNESS : 0;
  uint32_t color = SYNC_LED_RGBW ? syncPixels.Color(0, 0, 0, level)
                               : syncPixels.Color(level, level, level);
  syncPixels.fill(color);
  syncPixels.show();
#else
  digitalWrite(SYNC_LED_PIN, on != (bool)SYNC_LED_ACTIVE_LOW ? HIGH : LOW);
#endif
#endif
}

static void syncEdge(bool on) {
  // Timestamp the physical operation, before USB/network output can delay it.
  int64_t at = esp_timer_get_time();
  setSyncLED(on);
  syncOn = on;
  if (on) syncOffAt = at + 150000;
  emit("S,%llu,%lu,%d,%d,%08lx\n", (unsigned long long)at,
       (unsigned long)syncSequence, on ? 1 : 0, ledReady ? 1 : 0,
       (unsigned long)bootId);
}

#ifndef IMU_SDA
#define IMU_SDA -1
#endif
#ifndef IMU_SCL
#define IMU_SCL -1
#endif
#ifndef IMU_POWER
#define IMU_POWER -1
#endif

static const uint32_t SAMPLE_PERIOD_US = 10000;  // 100 Hz stream

// Where the raw int16 readings saturate for the ranges set in configureSensor():
// 0.976 mg/LSB at +/-32 g, 70 mdps/LSB at +/-2000 dps (the "2000" is nominal).
static const float ACCEL_SAT_MS2 = 32767.0f * 0.976e-3f * 9.80665f;  // ~313.6 m/s^2 (~32 g)
static const float GYRO_SAT_RADS = 32767.0f * 0.070f * DEG_TO_RAD;   // ~40.0 rad/s (~2294 dps)

struct PinPair {
  int8_t sda;
  int8_t scl;
  int8_t power;  // GPIO that must be HIGH to power the STEMMA QT port (Adafruit Feathers), or -1
  const char *note;
};

// Pins 19/20 (USB), 26-37 (flash/PSRAM) and 43/44 (UART0) are deliberately absent.
static const PinPair CANDIDATES[] = {
  { IMU_SDA, IMU_SCL, IMU_POWER, "build flags" },
  { SDA,     SCL,     -1, "board default" },
  { 3,  4,   7, "Adafruit Feather ESP32-S3 / Reverse TFT, I2C power on GPIO 7" },
  { 42, 41, 21, "Adafruit Feather ESP32-S3 TFT, I2C power on GPIO 21" },
  { 8,  9,  -1, "ESP32-S3 DevKitC / SparkFun Thing Plus / Unexpected Maker" },
  { 41, 40, -1, "Adafruit QT Py ESP32-S3 STEMMA QT port" },
  { 5,  6,  -1, "Seeed XIAO ESP32S3" },
  { 1,  2,  -1, "DFRobot FireBeetle 2 ESP32-S3" },
  { 47, 48, -1, "Adafruit Metro ESP32-S3" },
};
static const uint8_t ADDRESSES[] = { 0x6A, 0x6B };  // 0x6B when the DO/SDO jumper is closed

Adafruit_LSM6DSO32 imu;
static bool        found     = false;
static int         foundSda  = -1;
static int         foundScl  = -1;
static uint8_t     foundAddr = 0;
static const char *foundNote = "";

static void printInfo() {
  emit("I,imu_stream on %s; boot=%08lx; protocol=2\n", ARDUINO_BOARD, (unsigned long)bootId);
  if (found) {
    emit("I,LSM6DSO32 at 0x%02X on SDA=%d SCL=%d (%s)\n", foundAddr, foundSda, foundScl, foundNote);
    emit("I,accel +/-32 g, gyro +/-2000 dps, 208 Hz ODR, 100 Hz stream\n");
    emit("R,%.2f,%.4f\n", ACCEL_SAT_MS2, GYRO_SAT_RADS);
  } else {
    emit("E,no LSM6DSO32 found on any candidate I2C pins\n");
  }
  emit("I,sync LED pin=%d rgb=%d rgbw=%d pixels=%d brightness=%d enabled=%d\n",
       SYNC_LED_PIN, SYNC_LED_RGB, SYNC_LED_RGBW, SYNC_LED_COUNT,
       SYNC_LED_BRIGHTNESS, ledReady ? 1 : 0);
#if SKATE_WIFI
  if (wifiReady) emit("I,WiFi AP=%s; UDP=192.168.4.1:5050\n", apName);
  else emit("E,WiFi AP or UDP initialization failed\n");
#endif
}

static void command(int c) {
  if (c == 'i' || c == '?') printInfo();
  if (c == 's' && !syncOn) {
    ++syncSequence;
    syncEdge(true);
  }
}

static void pollCommands() {
  // Bound work so a busy client cannot indefinitely postpone sampling.
  for (int n = 0; n < 32 && Serial.available(); ++n) command(Serial.read());
#if SKATE_WIFI
  if (!wifiReady) return;
  int size = udp.parsePacket();
  if (size > 0) {
    IPAddress senderIP = udp.remoteIP();
    uint16_t senderPort = udp.remotePort();
    int c = udp.read();
    udp.clear();
    // One recorder at a time; an active lease cannot be stolen by another client.
    bool same = senderIP == recorderIP && senderPort == recorderPort;
    if (size == 1 && (c == 'i' || c == '?' || c == 'k' || c == 's') &&
        (same || !recorderPort || millis() - lastHeartbeat >= 5000)) {
      recorderIP = senderIP;
      recorderPort = senderPort;
      lastHeartbeat = millis();
      command(c);
    }
  }
#endif
}

static bool probe(int sda, int scl, uint8_t addr) {
  Wire.end();
  if (!Wire.begin(sda, scl, 400000)) return false;
  Wire.beginTransmission(addr);
  if (Wire.endTransmission() != 0) return false;  // nothing ACKed this address
  return imu.begin_I2C(addr, &Wire);              // library checks WHO_AM_I == 0x6C
}

static bool findSensor() {
  for (const PinPair &p : CANDIDATES) {
    if (p.sda < 0 || p.scl < 0 || p.sda == p.scl) continue;
    if (p.power >= 0) {
      pinMode(p.power, OUTPUT);
      digitalWrite(p.power, HIGH);
      delay(50);  // let the sensor boot after power-up
    }
    for (uint8_t addr : ADDRESSES) {
      if (probe(p.sda, p.scl, addr)) {
        foundSda  = p.sda;
        foundScl  = p.scl;
        foundAddr = addr;
        foundNote = p.note;
        return true;
      }
    }
    if (p.power >= 0) pinMode(p.power, INPUT);  // not this board: release the pin
  }
  Wire.end();
  return false;
}

static void configureSensor() {
  imu.setAccelRange(LSM6DSO32_ACCEL_RANGE_32_G);  // deck landings can exceed 8 g
  imu.setGyroRange(LSM6DS_GYRO_RANGE_2000_DPS);
  imu.setAccelDataRate(LSM6DS_RATE_208_HZ);
  imu.setGyroDataRate(LSM6DS_RATE_208_HZ);
}

void setup() {
  bootId = esp_random();
  Serial.begin(115200);
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);  // never stall the loop when nothing is reading the USB port
#endif
  delay(300);
  found = findSensor();
  if (found) configureSensor();
  // A pin must be selected explicitly. Do not assume that LED_BUILTIN is an
  // ordinary LED (many S3 boards use an addressable RGB LED).
#if SYNC_LED_PIN >= 0
  ledReady = found && GPIO_IS_VALID_OUTPUT_GPIO(SYNC_LED_PIN) &&
             SYNC_LED_PIN != foundSda && SYNC_LED_PIN != foundScl;
  for (const PinPair &p : CANDIDATES) {
    if (p.sda == foundSda && p.scl == foundScl && p.power == SYNC_LED_PIN) ledReady = false;
  }
  if (ledReady) {
#if SYNC_LED_RGB || SYNC_LED_RGBW
    ledReady = syncPixels.begin() && syncPixels.numPixels() == SYNC_LED_COUNT;
    if (!ledReady) emit("E,NeoPixel initialization failed\n");
#else
    pinMode(SYNC_LED_PIN, OUTPUT);
#endif
    setSyncLED(false);
  }
#endif
#if SKATE_WIFI
  snprintf(apName, sizeof(apName), "SkateJudge-%04x", (unsigned int)(ESP.getEfuseMac() & 0xffff));
  WiFi.mode(WIFI_AP);
  // Local prototype network. This is a shared setup password, not a private
  // network; change it here before using it around untrusted clients.
  wifiReady = WiFi.softAP(apName, "skate-judge") && udp.begin(5050);
#endif
  printInfo();
}

void loop() {
  static int64_t nextSample = esp_timer_get_time();
  static uint32_t lastRetry  = 0;

  if (syncOn && esp_timer_get_time() >= syncOffAt) syncEdge(false);
  pollCommands();

  if (!found) {
    if (millis() - lastRetry > 1000) {
      lastRetry = millis();
      found = findSensor();
      if (found) configureSensor();
      printInfo();
    }
    return;
  }

  int64_t now = esp_timer_get_time();
  if (now < nextSample) return;
  nextSample += SAMPLE_PERIOD_US;
  // Do not emit a burst of repeated readings to catch up after a stall.
  if (nextSample <= now) nextSample = now + SAMPLE_PERIOD_US;

  sensors_event_t accel, gyro, temp;
  if (!imu.getEvent(&accel, &gyro, &temp)) {
    emit("E,sensor read failed, rescanning\n");
    found = false;
    return;
  }
  emit("D,%llu,%.4f,%.4f,%.4f,%.5f,%.5f,%.5f,%.1f,%lu,%08lx\n",
                (unsigned long long)now,
                accel.acceleration.x, accel.acceleration.y, accel.acceleration.z,
                gyro.gyro.x, gyro.gyro.y, gyro.gyro.z,
                temp.temperature, (unsigned long)sampleSequence++, (unsigned long)bootId);
}
