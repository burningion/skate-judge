#pragma once
#include <SD.h>
#include <SPI.h>
#include "log_queue.h"

#ifndef SKATE_SD
#define SKATE_SD 1
#endif
#ifndef SD_CS_PIN
#define SD_CS_PIN 10
#endif
#ifndef SD_SCK_PIN
#define SD_SCK_PIN 36
#endif
#ifndef SD_MOSI_PIN
#define SD_MOSI_PIN 35
#endif
#ifndef SD_MISO_PIN
#define SD_MISO_PIN 37
#endif
#ifndef SD_SPI_HZ
#define SD_SPI_HZ 4000000
#endif

struct LogStorage {
  const char *name;
  fs::FS *fs;
  const char *mount;
  const char *directory;
  bool ready = false;
  uint64_t freeBytes() const {
    if (!ready) return 0;
    uint64_t total = fs == &SD ? SD.totalBytes() : LittleFS.totalBytes();
    uint64_t used = fs == &SD ? SD.usedBytes() : LittleFS.usedBytes();
    return total > used ? total - used : 0;
  }
  String path(const String &id) const { return String(directory) + "/" + id + ".bin"; }
};
static LogStorage flashStorage{"flash", &LittleFS, "/littlefs", ""};
static LogStorage sdStorage{"sd", &SD, "/sd", "/skate-judge"};
static LogStorage *recordingStorage = &flashStorage;
static const char *storageWarning = "";

static void setupStorage() {
  flashStorage.ready = LittleFS.begin(false); // Never format on a mount failure.
#if SKATE_SD
  const int pins[] = {SD_CS_PIN, SD_SCK_PIN, SD_MOSI_PIN, SD_MISO_PIN};
  bool valid = SD_SPI_HZ >= 100000 && SD_SPI_HZ <= 20000000;
  for (size_t i = 0; i < 4; ++i) {
    valid &= GPIO_IS_VALID_GPIO(pins[i]) && (i == 3 || GPIO_IS_VALID_OUTPUT_GPIO(pins[i]));
    valid &= pins[i] != IMU_SDA && pins[i] != IMU_SCL && pins[i] != IMU_POWER && pins[i] != SYNC_LED_PIN;
    for (size_t j = 0; j < i; ++j) valid &= pins[i] != pins[j];
  }
  if (valid) {
    pinMode(SD_CS_PIN, OUTPUT); digitalWrite(SD_CS_PIN, HIGH);
    SPI.begin(SD_SCK_PIN, SD_MISO_PIN, SD_MOSI_PIN, SD_CS_PIN);
    // Card must be inserted before boot. Never initialize/format an unknown card.
    if (SD.begin(SD_CS_PIN, SPI, SD_SPI_HZ, "/sd", 5, false) && SD.cardType() != CARD_NONE) {
      File directory = SD.open(sdStorage.directory);
      sdStorage.ready = directory ? directory.isDirectory() : SD.mkdir(sdStorage.directory);
    }
  }
  if (!sdStorage.ready) {
    SD.end();
    storageWarning = valid ? "SD unavailable; using internal flash. Insert FAT32 card before boot."
                           : "SD pin configuration invalid; using internal flash.";
  }
#else
  storageWarning = "SD disabled in firmware; using internal flash.";
#endif
  recordingStorage = sdStorage.ready ? &sdStorage : &flashStorage;
}

static constexpr size_t LOG_QUEUE_BYTES = 32768;
static LogQueue<LOG_QUEUE_BYTES> logQueue;
struct WriterStatus {
  bool open = false;
  const char *error = "";
  uint64_t written = 0, freeBytes = 0;
  uint32_t crc = 0, maxWriteUs = 0, maxFlushUs = 0;
};
static WriterStatus writerStatus;
static portMUX_TYPE writerMux = portMUX_INITIALIZER_UNLOCKED;
static WriterStatus writerSnapshot() {
  portENTER_CRITICAL(&writerMux); WriterStatus copy = writerStatus; portEXIT_CRITICAL(&writerMux); return copy;
}
static void publishWriter(const WriterStatus &value) {
  portENTER_CRITICAL(&writerMux); writerStatus = value; portEXIT_CRITICAL(&writerMux);
}
enum WriteAction { OPEN_LOG, SYNC_LOG, CLOSE_LOG };
struct WriteCommand { WriteAction action; char path[100]; };
static QueueHandle_t writeCommands, writeReplies;

// The writer owns the FILE throughout capture. Acquisition never waits for disk
// during RECORDING, including fsync and FAT allocation; only start/stop wait.
static void storageWriter(void *) {
  FILE *file = nullptr;
  static char stdioBuffer[4096];
  uint8_t block[4096];
  WriterStatus result;
  uint64_t lastFlush = 0;
  auto fail = [&](const char *message) { if (!*result.error) result.error = message; };
  auto writePending = [&]() {
    size_t size = logQueue.pop(block, sizeof(block));
    if (size && file && !*result.error) {
      uint64_t start = esp_timer_get_time();
      size_t written = fwrite(block, 1, size, file);
      result.maxWriteUs = max(result.maxWriteUs, uint32_t(esp_timer_get_time() - start));
      result.crc = logCRC(result.crc, block, written);
      result.written += written;
      if (written != size) fail("storage_write_failed");
    }
    return size;
  };
  auto sync = [&]() {
    if (file && !*result.error) {
      uint64_t start = esp_timer_get_time();
      if (fflush(file) || fsync(fileno(file))) fail("storage_flush_failed");
      result.freeBytes = recordingStorage->freeBytes();
      result.maxFlushUs = max(result.maxFlushUs, uint32_t(esp_timer_get_time() - start));
      if (result.freeBytes <= RESERVE_BYTES) fail("storage_capacity_reached");
      lastFlush = esp_timer_get_time();
    }
  };
  for (;;) {
    WriteCommand command;
    if (xQueueReceive(writeCommands, &command, pdMS_TO_TICKS(5)) == pdTRUE) {
      if (command.action == OPEN_LOG) {
        result = WriterStatus();
        int fd = open(command.path, O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (fd < 0) fail("log_exists_or_storage_unavailable");
        else {
          file = fdopen(fd, "wb");
          if (!file) { close(fd); fail("log_open_failed"); }
          else { setvbuf(file, stdioBuffer, _IOFBF, sizeof(stdioBuffer)); result.open = true; }
        }
        result.freeBytes = recordingStorage->freeBytes();
        lastFlush = esp_timer_get_time();
      } else {
        while (writePending()) {}
        sync();
        if (command.action == CLOSE_LOG && file) {
          if (fclose(file)) fail("storage_close_failed");
          file = nullptr; result.open = false;
        }
      }
      publishWriter(result);
      bool ok = !*result.error;
      xQueueSend(writeReplies, &ok, portMAX_DELAY);
    }
    if (file) {
      writePending();
      if (esp_timer_get_time() - lastFlush >= 250000) sync();
      publishWriter(result);
    }
  }
}

// Called only before sensor batching starts or after it has stopped.
static bool writerControl(WriteAction action, const char *path = "") {
  WriteCommand command{action, {}};
  snprintf(command.path, sizeof(command.path), "%s", path);
  bool ok = false;
  xQueueSend(writeCommands, &command, portMAX_DELAY);
  xQueueReceive(writeReplies, &ok, portMAX_DELAY);
  return ok;
}
