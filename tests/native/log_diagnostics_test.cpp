#include "log_diagnostics.h"
#include <assert.h>
#include <string.h>
#include <limits>

struct FakeIO {
  int flushError = 0, syncError = 0, flushCalls = 0, syncCalls = 0;
  uint64_t clock = 0;
  uint64_t now() { errno = ERANGE; return clock += 10; }
  int flush() { ++flushCalls; errno = flushError; return flushError ? -1 : 0; }
  int sync() { ++syncCalls; errno = syncError; return syncError ? -1 : 0; }
};

int main() {
  StorageDiagnostics diagnostics;
  FakeIO io;
  assert(syncLogFile(io, diagnostics, 100));
  assert(diagnostics.syncedBytes == 100 && diagnostics.lastSyncUs == 30);
  // A flush failure must skip fsync and preserve errno across timing calls.
  io.flushError = ENOSPC;
  assert(!syncLogFile(io, diagnostics, 200));
  assert(io.flushCalls == 2 && io.syncCalls == 1);
  assert(!strcmp(diagnostics.operation, "fflush"));
  assert(diagnostics.errorNumber == ENOSPC && diagnostics.durationUs == 10);
  assert(diagnostics.syncedBytes == 100 && diagnostics.lastSyncUs == 30);
  diagnostics.fail("fclose", EIO, 99, 1);
  assert(!strcmp(diagnostics.operation, "fflush") && diagnostics.errorNumber == ENOSPC);
  assert(!syncLogFile(io, diagnostics, 300));
  assert(io.flushCalls == 2);

  diagnostics = StorageDiagnostics(); io = FakeIO();
  io.syncError = EIO;
  assert(!syncLogFile(io, diagnostics, 400));
  assert(io.flushCalls == 1 && io.syncCalls == 1);
  assert(!strcmp(diagnostics.operation, "fsync") && diagnostics.errorNumber == EIO);
  assert(diagnostics.syncedBytes == 0 && diagnostics.lastSyncUs == 0);
  assert(diagnostics.atUs == 30 && diagnostics.durationUs == 10);

  // Status/footer serialization must fit even at maximum counter widths.
  diagnostics.atUs = diagnostics.durationUs = diagnostics.syncedBytes = diagnostics.lastSyncUs = UINT64_MAX;
  diagnostics.errorNumber = std::numeric_limits<int>::max();
  char json[320];
  size_t size = diagnostics.json(json, sizeof(json), UINT64_MAX);
  assert(size == strlen(json) && size < sizeof(json));
  assert(strstr(json, "\"operation\":\"fsync\"") && json[size - 1] == '}');
}
