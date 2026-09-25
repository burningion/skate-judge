#pragma once
#include <atomic>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

// One acquisition producer and one storage consumer. A packet is published only
// after both header and payload have been copied. Full queues never overwrite.
template <size_t Capacity> class LogQueue {
  static_assert(Capacity && !(Capacity & (Capacity - 1)), "Power-of-two capacity required");
  uint8_t data_[Capacity];
  std::atomic<uint32_t> head_{0}, tail_{0};
  void copyIn(uint32_t position, const void *source, size_t length) {
    size_t offset = position & (Capacity - 1);
    size_t first = length < Capacity - offset ? length : Capacity - offset;
    if (first) memcpy(data_ + offset, source, first);
    if (length > first) memcpy(data_, static_cast<const uint8_t *>(source) + first, length - first);
  }
public:
  // Only while the producer and consumer are idle.
  void reset() { head_.store(0); tail_.store(0); }
  size_t used() const {
    // The producer calls used(); the consumer can only increase free space.
    uint32_t head = head_.load(std::memory_order_relaxed);
    return head - tail_.load(std::memory_order_acquire);
  }
  bool push(const void *header, size_t headerSize, const void *payload, size_t payloadSize) {
    if (headerSize > Capacity || payloadSize > Capacity - headerSize) return false;
    const size_t length = headerSize + payloadSize;
    uint32_t head = head_.load(std::memory_order_relaxed);
    if (length > Capacity - (head - tail_.load(std::memory_order_acquire))) return false;
    copyIn(head, header, headerSize);
    copyIn(head + headerSize, payload, payloadSize);
    head_.store(head + length, std::memory_order_release);
    return true;
  }
  // The consumer may inspect/replay bytes, but releases them only after fsync.
  size_t peek(void *destination, size_t capacity, size_t skip = 0) const {
    uint32_t tail = tail_.load(std::memory_order_relaxed);
    size_t length = head_.load(std::memory_order_acquire) - tail;
    if (skip >= length) return 0;
    tail += skip;
    length -= skip;
    if (length > capacity) length = capacity;
    size_t offset = tail & (Capacity - 1);
    size_t first = length < Capacity - offset ? length : Capacity - offset;
    if (first) memcpy(destination, data_ + offset, first);
    if (length > first) memcpy(static_cast<uint8_t *>(destination) + first, data_, length - first);
    return length;
  }
  void consume(size_t length) {
    tail_.store(tail_.load(std::memory_order_relaxed) + length, std::memory_order_release);
  }
  size_t pop(void *destination, size_t capacity) {
    size_t length = peek(destination, capacity);
    consume(length);
    return length;
  }
};
