#pragma once
#include <atomic>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

// A read-only copy of the newest log bytes for a live listener. The acquisition
// task is the only writer and never waits: old bytes are overwritten. Offsets
// are byte positions in the recording file, so a reader that keeps up sees
// exactly the file's contents. A reader that falls behind is moved forward and
// must find the next packet boundary itself (magic + CRC).
template <size_t Capacity, size_t MaxWrite> class LiveRing {
  static_assert(Capacity && !(Capacity & (Capacity - 1)), "Power-of-two capacity required");
  static_assert(MaxWrite < Capacity / 2, "A write must be much smaller than the ring");
  uint8_t data_[Capacity];
  std::atomic<uint32_t> head_{0}, generation_{0};
  // Bytes below this offset may be overwritten by a write that is in progress.
  static uint32_t floorFor(uint32_t head) {
    return head + MaxWrite > Capacity ? uint32_t(head + MaxWrite - Capacity) : 0;
  }
  void copyIn(uint32_t position, const void *source, size_t length) {
    size_t offset = position & (Capacity - 1);
    size_t first = length < Capacity - offset ? length : Capacity - offset;
    if (first) memcpy(data_ + offset, source, first);
    if (length > first) memcpy(data_, static_cast<const uint8_t *>(source) + first, length - first);
  }
public:
  // Writer only: a new recording starts again at offset zero.
  void reset() {
    head_.store(0, std::memory_order_release);
    generation_.fetch_add(1, std::memory_order_release);
  }
  // Writer only. Oversized writes are refused rather than corrupting readers.
  bool push(const void *header, size_t headerSize, const void *payload, size_t payloadSize) {
    if (headerSize + payloadSize > MaxWrite) return false;
    uint32_t head = head_.load(std::memory_order_relaxed);
    copyIn(head, header, headerSize);
    copyIn(head + headerSize, payload, payloadSize);
    head_.store(head + uint32_t(headerSize + payloadSize), std::memory_order_release);
    return true;
  }
  // Reader. Copies up to `capacity` bytes starting at `offset`. `offset` is
  // moved forward when the requested bytes are gone, or back to the current
  // head when it lies beyond it (a stale offset from an earlier recording).
  size_t read(uint32_t &offset, void *destination, size_t capacity, uint32_t &head) const {
    for (int attempt = 0; attempt < 4; ++attempt) {
      const uint32_t generation = generation_.load(std::memory_order_acquire);
      head = head_.load(std::memory_order_acquire);
      if (offset > head) { offset = head; return 0; }
      const uint32_t floor = floorFor(head);
      const uint32_t start = offset > floor ? offset : floor;
      size_t length = head - start;
      if (length > capacity) length = capacity;
      size_t position = start & (Capacity - 1);
      size_t first = length < Capacity - position ? length : Capacity - position;
      if (first) memcpy(destination, data_ + position, first);
      if (length > first) memcpy(static_cast<uint8_t *>(destination) + first, data_, length - first);
      // Accept the copy only if no write can have reached it meanwhile.
      const uint32_t after = head_.load(std::memory_order_acquire);
      if (generation_.load(std::memory_order_acquire) == generation && start >= floorFor(after)) {
        offset = start;
        return length;
      }
    }
    head = head_.load(std::memory_order_acquire);
    offset = head;
    return 0;
  }
};
