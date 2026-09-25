#pragma once
#include "log_format.h"
#include "log_diagnostics.h"
#include <string.h>

struct WriterStatus {
  bool open = false, recovering = false;
  const char *error = "";
  uint64_t written = 0, freeBytes = 0;
  uint32_t crc = 0, maxWriteUs = 0, maxFlushUs = 0;
  uint32_t recoveries = 0, recoveryAttempts = 0, pendingBytes = 0;
  StorageDiagnostics diagnostics;
};

// Retain every byte after the last successful fsync. A failed stdio/FatFs file
// cannot be trusted for another append: reopen, check the committed boundary,
// replay at that offset, fsync, and read back before releasing any RAM.
// IO is also implemented by the native fault-injection tests.
template <class Queue, class IO> class RecoverableLogWriter {
  Queue &queue;
  IO &io;
  size_t staged = 0, boundarySize = 0;
  uint64_t committed = 0, lastFlush = 0, recoverySince = 0, retryAt = 0;
  uint32_t committedCRC = 0;
  bool canRecover = false;
  uint8_t block[4096], check[512], boundary[512];

  void failed(const char *operation, const char *error, int number, uint64_t start) {
    uint64_t at = io.now();
    status.diagnostics.fail(operation, number, at, at - start);
    if (!canRecover) { status.error = error; return; }
    if (!status.recovering) {
      status.recovering = true;
      recoverySince = at;
    }
    retryAt = at + 250000;
  }
  bool writeSome(size_t limit = sizeof(block)) {
    size_t length = queue.peek(block, limit < sizeof(block) ? limit : sizeof(block), staged);
    if (!length) return true;
    uint64_t start = io.now();
    errno = 0;
    size_t accepted = io.write(block, length);
    int number = errno;
    uint32_t duration = uint32_t(io.now() - start);
    if (duration > status.maxWriteUs) status.maxWriteUs = duration;
    staged += accepted;
    status.written += accepted;
    status.crc = logCRC(status.crc, block, accepted);
    if (accepted == length) return true;
    failed("fwrite", "storage_write_failed", number, start);
    return false;
  }
  bool flush() {
    StorageDiagnostics attempt;
    uint64_t start = io.now();
    bool ok = syncLogFile(io, attempt, status.written);
    uint32_t duration = uint32_t(io.now() - start);
    if (duration > status.maxFlushUs) status.maxFlushUs = duration;
    if (!ok) {
      status.diagnostics.fail(attempt.operation, attempt.errorNumber, attempt.atUs, attempt.durationUs);
      failed(attempt.operation, "storage_flush_failed", attempt.errorNumber, start);
    }
    return ok;
  }
  void commit() {
    if (staged) {
      boundarySize = staged < sizeof(boundary) ? staged : sizeof(boundary);
      queue.peek(boundary, boundarySize, staged - boundarySize);
      committed += staged;
      committedCRC = status.crc;
      queue.consume(staged);
      staged = 0;
    }
    lastFlush = io.now();
    status.diagnostics.syncedBytes = committed;
    status.diagnostics.lastSyncUs = lastFlush;
  }
  int readMatches(const uint8_t *expected, size_t length) {
    while (length) {
      size_t n = length < sizeof(check) ? length : sizeof(check);
      if (io.read(check, n) != n) return -1;
      if (memcmp(check, expected, n)) return 0;
      expected += n; length -= n;
    }
    return 1;
  }
  void recover() {
    // Five seconds leaves some headroom in the ~seven-second RAM queue.
    // Driver calls have their own timeouts; acquisition still faults on full.
    if (io.now() - recoverySince >= 5000000) {
      status.error = "storage_recovery_failed"; return;
    }
    if (io.now() < retryAt) return;
    ++status.recoveryAttempts;
    retryAt = io.now() + 500000;
    if (!io.reopen()) return;
    int64_t size = io.size();
    if (size < 0) return;
    if (uint64_t(size) < committed || uint64_t(size) > committed + queue.used()) {
      status.error = "storage_recovery_size_mismatch"; return;
    }
    if (!io.seek(committed - boundarySize)) return;
    int boundaryMatch = readMatches(boundary, boundarySize);
    if (boundaryMatch < 0) return;
    if (!boundaryMatch) {
      // Never overwrite a file whose known-good boundary has changed.
      status.error = "storage_recovery_prefix_mismatch"; return;
    }
    if (!io.seek(committed)) return;
    staged = 0; status.written = committed; status.crc = committedCRC;
    const size_t target = queue.used();
    while (staged < target) if (!writeSome(target - staged)) return;
    if (!flush()) return;
    if (!io.seek(committed)) return;
    for (size_t offset = 0; offset < staged;) {
      size_t n = queue.peek(block, staged - offset < sizeof(block) ? staged - offset : sizeof(block), offset);
      if (readMatches(block, n) != 1) return;
      offset += n;
    }
    if (!io.seek(committed + staged)) return;
    commit();
    status.recovering = false;
    ++status.recoveries;
  }
public:
  WriterStatus status;
  RecoverableLogWriter(Queue &queue, IO &io) : queue(queue), io(io) {}
  void begin(const char *path, bool recoverSD) {
    status = WriterStatus(); staged = boundarySize = 0;
    committed = 0; committedCRC = 0; canRecover = recoverSD;
    lastFlush = io.now();
    errno = 0;
    if (!io.create(path)) {
      int number = errno;
      uint64_t at = io.now();
      status.diagnostics.fail("open", number, at, at - lastFlush);
      status.error = "log_exists_or_storage_unavailable";
    } else status.open = true;
  }
  void step(bool forceSync = false) {
    if (!status.open || *status.error) return;
    if (status.recovering) recover();
    else if (writeSome() && (forceSync || io.now() - lastFlush >= 250000)) {
      if (flush()) commit();
    }
    status.pendingBytes = queue.used();
  }
  void close() {
    if (status.open) {
      uint64_t start = io.now();
      errno = 0;
      int result = io.close(), number = errno;
      if (result) {
        uint64_t at = io.now();
        status.diagnostics.fail("fclose", number, at, at - start);
        if (!*status.error) status.error = "storage_close_failed";
      }
    }
    status.open = false;
  }
};
