#include "log_queue.h"
#include "log_writer.h"
#include <cassert>
#include <vector>
#include <algorithm>

// Simulate the accepted-but-not-durable tail disappearing on remount. Errors
// abort an open file, as FatFs can; even a subsequent successful sync on that
// object must never be taken as recovery. Reopen is required.
struct FakeDisk {
  std::vector<uint8_t> bytes, durable;
  uint64_t clock = 0;
  size_t position = 0;
  int writeErrors = 0, flushErrors = 0, syncErrors = 0, reopenErrors = 0;
  int readErrors = 0, reopens = 0, writes = 0, closes = 0;
  bool broken = false, dropTail = true, corruptBoundary = false, shrink = false, grow = false;
  bool corruptReplay = false, failCreate = false;
  uint64_t now() { return clock += 10; }
  bool create(const char *) { errno = EEXIST; return !failCreate; }
  size_t write(const void *data, size_t length) {
    ++writes;
    assert(!broken);
    size_t accepted = length;
    if (writeErrors) { --writeErrors; accepted /= 3; broken = true; errno = EIO; }
    if (bytes.size() < position + accepted) bytes.resize(position + accepted);
    memcpy(bytes.data() + position, data, accepted);
    position += accepted;
    return accepted;
  }
  int flush() {
    if (flushErrors) { --flushErrors; broken = true; errno = ENOSPC; return -1; }
    return 0;
  }
  int sync() {
    if (syncErrors) { --syncErrors; broken = true; errno = EIO; return -1; }
    // Model a misleading successful retry on an aborted/dirty file object.
    if (!broken) durable = bytes;
    return 0;
  }
  bool reopen() {
    ++reopens; ++closes;
    if (reopenErrors) { --reopenErrors; return false; }
    if (dropTail) bytes = durable;
    if (corruptBoundary) bytes.back() ^= 1;
    if (shrink) bytes.resize(0);
    if (grow) bytes.resize(100000);
    broken = false; position = 0;
    return true;
  }
  int64_t size() { return bytes.size(); }
  bool seek(uint64_t offset) { position = offset; return true; }
  size_t read(void *data, size_t length) {
    if (readErrors) { --readErrors; return 0; }
    size_t n = std::min(length, bytes.size() - position);
    memcpy(data, bytes.data() + position, n);
    if (corruptReplay && position >= 600) static_cast<uint8_t *>(data)[0] ^= 1;
    position += n; return n;
  }
  int close() { ++closes; return 0; }
};

using Queue = LogQueue<32768>;
using Writer = RecoverableLogWriter<Queue, FakeDisk>;
static void append(Queue &queue, std::vector<uint8_t> &expected, size_t size, uint8_t seed) {
  std::vector<uint8_t> data(size);
  for (size_t i = 0; i < size; ++i) data[i] = uint8_t(seed + i * 13);
  assert(queue.push(data.data(), size, nullptr, 0));
  expected.insert(expected.end(), data.begin(), data.end());
}
static void drain(Writer &writer, Queue &queue, FakeDisk &disk) {
  for (int i = 0; i < 60 && !*writer.status.error && (queue.used() || writer.status.recovering); ++i) {
    disk.clock += 250000; writer.step(true);
  }
}

int main() {
  for (int mode = 0; mode < 6; ++mode) {
    Queue queue; FakeDisk disk; Writer writer(queue, disk);
    std::vector<uint8_t> expected;
    writer.begin("session", true);
    append(queue, expected, 600, 1); writer.step(true);
    assert(queue.used() == 0 && disk.durable == expected);
    // Cover partial fwrite, failed fflush, and failed fsync, both with a
    // disappeared tail and with the unconfirmed bytes still on the medium.
    disk.dropTail = mode < 3;
    if (mode % 3 == 0) disk.writeErrors = 1;
    if (mode % 3 == 1) disk.flushErrors = 1;
    if (mode % 3 == 2) disk.syncErrors = 1;
    append(queue, expected, 1300, 2); writer.step(true);
    assert(writer.status.recovering && !*writer.status.error);
    assert(writer.status.diagnostics.syncedBytes == 600 && queue.used() == 1300);
    auto firstError = writer.status.diagnostics.atUs;
    // Acquisition continues, including more than one writer chunk, while
    // the first remount fails. Everything is replayed exactly once.
    disk.reopenErrors = 1;
    append(queue, expected, 11000, 3);
    drain(writer, queue, disk);
    assert(!*writer.status.error && !writer.status.recovering);
    assert(writer.status.recoveries == 1 && disk.reopens == 2);
    assert(queue.used() == 0 && disk.durable == expected);
    assert(writer.status.diagnostics.atUs == firstError);
    assert(writer.status.crc == logCRC(0, expected.data(), expected.size()));
    // A second interruption can recover too; original diagnostics survive.
    disk.syncErrors = 1;
    append(queue, expected, 1800, 4); writer.step(true);
    drain(writer, queue, disk);
    assert(writer.status.recoveries == 2 && disk.durable == expected);
    assert(writer.status.diagnostics.atUs == firstError);
    append(queue, expected, 700, 5); drain(writer, queue, disk); writer.close();
    assert(!writer.status.open && !*writer.status.error && disk.durable == expected);
    assert(writer.status.written == expected.size());
  }
  // Replay and validation also work across repeated ring-buffer boundaries.
  {
    Queue queue; FakeDisk disk; Writer writer(queue, disk); std::vector<uint8_t> expected;
    writer.begin("session", true);
    // Repeated wraparound with another sync failure during recovery, then a
    // failed read of the committed boundary. All are transient.
    for (int round = 0; round < 20; ++round) {
      append(queue, expected, 18000, uint8_t(round));
      disk.syncErrors = 2;
      writer.step(true);
      disk.readErrors = round ? 1 : 0;
      drain(writer, queue, disk);
      assert(!*writer.status.error && queue.used() == 0);
      assert(writer.status.recoveries == uint32_t(round + 1));
      assert(disk.durable == expected);
      assert(writer.status.crc == logCRC(0, expected.data(), expected.size()));
    }
  }
  // Permanent outage: bounded retries, no discarded queue, no fake progress.
  {
    Queue queue; FakeDisk disk; Writer writer(queue, disk); std::vector<uint8_t> expected;
    writer.begin("session", true);
    append(queue, expected, 600, 1); writer.step(true);
    disk.syncErrors = 1; disk.reopenErrors = 100;
    append(queue, expected, 1300, 2); writer.step(true);
    append(queue, expected, 31000, 3);
    uint8_t extra[1000] = {};
    assert(!queue.push(extra, sizeof(extra), nullptr, 0));
    drain(writer, queue, disk);
    assert(!strcmp(writer.status.error, "storage_recovery_failed"));
    assert(queue.used() == 32300 && writer.status.diagnostics.syncedBytes == 600);
    assert(disk.durable.size() == 600 && disk.reopens < 12);
    writer.close();
    assert(queue.used() == 32300);
  }
  // A missing/changed committed prefix must never be extended or overwritten.
  for (int mode = 0; mode < 3; ++mode) {
    Queue queue; FakeDisk disk; Writer writer(queue, disk); std::vector<uint8_t> expected;
    writer.begin("session", true);
    append(queue, expected, 600, 1); writer.step(true);
    disk.syncErrors = 1;
    append(queue, expected, 1300, 2); writer.step(true);
    disk.corruptBoundary = mode == 0; disk.shrink = mode == 1; disk.grow = mode == 2;
    int writes = disk.writes;
    drain(writer, queue, disk);
    assert(*writer.status.error && disk.writes == writes && queue.used() == 1300);
  }
  // Read-back failures never free the pending bytes or count as recovery.
  {
    Queue queue; FakeDisk disk; Writer writer(queue, disk); std::vector<uint8_t> expected;
    writer.begin("session", true);
    append(queue, expected, 600, 1); writer.step(true);
    disk.syncErrors = 1;
    append(queue, expected, 1300, 2); writer.step(true);
    disk.corruptReplay = true;
    drain(writer, queue, disk);
    assert(*writer.status.error && !writer.status.recoveries && queue.used() == 1300);
    assert(writer.status.diagnostics.syncedBytes == 600);
  }
  // Internal flash retains fail-fast behavior; no SD remount is attempted.
  {
    Queue queue; FakeDisk disk; Writer writer(queue, disk); std::vector<uint8_t> expected;
    writer.begin("session", false); disk.syncErrors = 1;
    append(queue, expected, 600, 1); writer.step(true);
    assert(!strcmp(writer.status.error, "storage_flush_failed"));
    assert(queue.used() == 600 && disk.reopens == 0);
  }
  // A failed exclusive create cannot reopen/overwrite an existing recording.
  {
    Queue queue; FakeDisk disk; Writer writer(queue, disk);
    disk.failCreate = true; writer.begin("exists", true); writer.step(true);
    assert(!writer.status.open && *writer.status.error && disk.reopens == 0);
  }
}
