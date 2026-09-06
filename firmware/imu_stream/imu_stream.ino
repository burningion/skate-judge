// imu_stream.ino
//
// Streams LSM6DSO32 accelerometer + gyroscope samples over USB serial for
// viz/imu_viz.py.  Output, 100 lines per second:
//
//   D,<micros>,<ax>,<ay>,<az>,<gx>,<gy>,<gz>,<temp_C>   accel m/s^2, gyro rad/s
//   R,<accel_sat_ms2>,<gyro_sat_rads>                   where the int16 readings saturate
//   I,<text>                                            informational
//   E,<text>                                            error
//
// Send "i" (or "?") to get the info lines again.
//
// The sketch does not assume a particular ESP32-S3 board: it tries the usual
// STEMMA QT / Qwiic I2C pin pairs until it finds the sensor.  To force a pair,
// build with  -DIMU_SDA=<pin> -DIMU_SCL=<pin> [-DIMU_POWER=<pin>]  (flash.sh reads
// IMU_SDA / IMU_SCL / IMU_POWER from the environment and does this for you).

#include <Wire.h>
#include <Adafruit_LSM6DSO32.h>

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
  Serial.printf("I,imu_stream on %s\n", ARDUINO_BOARD);
  if (found) {
    Serial.printf("I,LSM6DSO32 at 0x%02X on SDA=%d SCL=%d (%s)\n", foundAddr, foundSda, foundScl, foundNote);
    Serial.println("I,accel +/-32 g, gyro +/-2000 dps, 208 Hz ODR, 100 Hz stream");
    Serial.printf("R,%.2f,%.4f\n", ACCEL_SAT_MS2, GYRO_SAT_RADS);
  } else {
    Serial.println("E,no LSM6DSO32 found on any candidate I2C pins");
  }
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
  Serial.begin(115200);
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);  // never stall the loop when nothing is reading the USB port
#endif
  delay(300);
  found = findSensor();
  if (found) configureSensor();
  printInfo();
}

void loop() {
  static uint32_t nextSample = micros();
  static uint32_t lastRetry  = 0;

  while (Serial.available()) {
    int c = Serial.read();
    if (c == 'i' || c == '?') printInfo();
  }

  if (!found) {
    if (millis() - lastRetry > 1000) {
      lastRetry = millis();
      found = findSensor();
      if (found) configureSensor();
      printInfo();
    }
    return;
  }

  uint32_t now = micros();
  if ((int32_t)(now - nextSample) < 0) return;
  nextSample += SAMPLE_PERIOD_US;
  if ((int32_t)(now - nextSample) > (int32_t)(10 * SAMPLE_PERIOD_US)) nextSample = now;  // fell behind: resync

  sensors_event_t accel, gyro, temp;
  if (!imu.getEvent(&accel, &gyro, &temp)) {
    Serial.println("E,sensor read failed, rescanning");
    found = false;
    return;
  }
  Serial.printf("D,%lu,%.4f,%.4f,%.4f,%.5f,%.5f,%.5f,%.1f\n",
                (unsigned long)now,
                accel.acceleration.x, accel.acceleration.y, accel.acceleration.z,
                gyro.gyro.x, gyro.gyro.y, gyro.gyro.z,
                temp.temperature);
}
