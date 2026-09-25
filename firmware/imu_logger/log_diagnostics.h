#pragma once
#include <errno.h>
#include <stdint.h>
#include <stdio.h>

// Keep the first failure intact while stop/close performs cleanup. Times use the
// ESP32 monotonic clock; accepted bytes are not necessarily durable bytes.
struct StorageDiagnostics {
  const char *operation = "";
  int errorNumber = 0;
  uint64_t atUs = 0, durationUs = 0, syncedBytes = 0, lastSyncUs = 0;

  void fail(const char *op, int error, uint64_t at, uint64_t duration) {
    if (*operation) return;
    operation = op; errorNumber = error; atUs = at; durationUs = duration;
  }

  size_t json(char *output, size_t capacity, uint64_t acceptedBytes) const {
    return snprintf(output, capacity,
      "{\"operation\":\"%s\",\"errno\":%d,\"at_us\":%llu,\"duration_us\":%llu,\"accepted_bytes\":%llu,\"synced_bytes\":%llu,\"last_sync_us\":%llu}",
      operation, errorNumber, (unsigned long long)atUs, (unsigned long long)durationUs,
      (unsigned long long)acceptedBytes, (unsigned long long)syncedBytes, (unsigned long long)lastSyncUs);
  }
};

// The same sequence is exercised with injected stdio/fsync failures in native
// tests. Capture errno immediately: timing and cleanup may change it.
template <class IO> bool syncLogFile(IO &io, StorageDiagnostics &diagnostics, uint64_t acceptedBytes) {
  if (*diagnostics.operation) return false;
  uint64_t start = io.now();
  errno = 0;
  if (io.flush() != 0) {
    int error = errno;
    uint64_t at = io.now();
    diagnostics.fail("fflush", error, at, at - start);
    return false;
  }
  start = io.now();
  errno = 0;
  if (io.sync() != 0) {
    int error = errno;
    uint64_t at = io.now();
    diagnostics.fail("fsync", error, at, at - start);
    return false;
  }
  diagnostics.syncedBytes = acceptedBytes;
  diagnostics.lastSyncUs = io.now();
  return true;
}
