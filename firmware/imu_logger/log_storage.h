#pragma once
#include <SD.h>
#include <SPI.h>
#include "log_queue.h"
#include "log_diagnostics.h"
#include "log_writer.h"

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

struct LogFileIO {
  FILE *file = nullptr;
  char path[100] = {}, buffer[4096];
  uint64_t now() { return esp_timer_get_time(); }
  bool openFile(int flags) {
    int fd = ::open(path, flags, 0600);
    if (fd < 0) return false;
    file = fdopen(fd, "r+b");
    if (!file) { int error = errno; ::close(fd); errno = error; return false; }
    setvbuf(file, buffer, _IOFBF, sizeof(buffer));
    return true;
  }
  bool create(const char *value) {
    snprintf(path, sizeof(path), "%s", value);
    return openFile(O_RDWR | O_CREAT | O_EXCL);
  }
  bool reopen() {
    close();
    // A FatFs I/O error can abort the file object and the SPI driver can mark
    // the card uninitialized. Reset both; never format, create, or truncate.
    SD.end();
    if (!SD.begin(SD_CS_PIN, SPI, SD_SPI_HZ, "/sd", 5, false) || SD.cardType() == CARD_NONE) return false;
    return openFile(O_RDWR);
  }
  int64_t size() {
    struct stat info;
    return fstat(fileno(file), &info) == 0 ? info.st_size : -1;
  }
  bool seek(uint64_t offset) { return fseeko(file, off_t(offset), SEEK_SET) == 0; }
  size_t write(const void *data, size_t length) { return fwrite(data, 1, length, file); }
  size_t read(void *data, size_t length) { return fread(data, 1, length, file); }
  int flush() { return fflush(file); }
  int sync() { return fsync(fileno(file)); }
  int close() {
    if (!file) return 0;
    int result = fclose(file); file = nullptr; return result;
  }
};

// The writer owns the FILE throughout capture. Acquisition never waits for disk
// during RECORDING, including fsync and FAT allocation; only start/stop wait.
static void storageWriter(void *) {
  static LogFileIO io;
  static RecoverableLogWriter<decltype(logQueue), LogFileIO> writer(logQueue, io);
  WriterStatus &result = writer.status;
  auto step = [&](bool forceSync) {
    uint64_t previousSync = result.diagnostics.lastSyncUs;
    writer.step(forceSync);
    if (!*result.error && !result.recovering && result.diagnostics.lastSyncUs != previousSync) {
      result.freeBytes = recordingStorage->freeBytes();
      if (result.freeBytes <= RESERVE_BYTES) result.error = "storage_capacity_reached";
    }
    publishWriter(result);
  };
  for (;;) {
    WriteCommand command;
    if (xQueueReceive(writeCommands, &command, pdMS_TO_TICKS(5)) == pdTRUE) {
      if (command.action == OPEN_LOG) {
        writer.begin(command.path, recordingStorage == &sdStorage);
        result.freeBytes = recordingStorage->freeBytes();
      } else {
        while (result.open && !*result.error && (logQueue.used() || result.recovering)) {
          step(true);
          if (result.recovering) vTaskDelay(pdMS_TO_TICKS(5));
        }
        if (command.action == CLOSE_LOG) writer.close();
      }
      publishWriter(result);
      bool ok = !*result.error;
      xQueueSend(writeReplies, &ok, portMAX_DELAY);
    }
    if (result.open) step(false);
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
