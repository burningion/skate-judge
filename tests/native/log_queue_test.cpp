#include "log_queue.h"
#include <cassert>
#include <thread>
#include <vector>

int main() {
  // A stalled writer must not let the producer overwrite unread bytes or
  // publish half a packet. Then consume across both ring boundaries.
  LogQueue<32> small;
  const uint8_t header[] = {1, 2, 3, 4};
  const uint8_t payload[] = {5, 6, 7, 8};
  for (int i = 0; i < 4; ++i) assert(small.push(header, 4, payload, 4));
  assert(small.used() == 32);
  assert(!small.push(header, 4, payload, 4));
  uint8_t block[32];
  assert(small.peek(block, 13, 5) == 13);
  for (int i = 0; i < 13; ++i) assert(block[i] == (i + 5) % 8 + 1);
  assert(small.used() == 32 && !small.push(header, 4, payload, 4));
  assert(small.pop(block, 13) == 13);
  for (int i = 0; i < 13; ++i) assert(block[i] == i % 8 + 1);
  assert(small.push(header, 4, payload, 4));
  assert(!small.push(header, 4, payload, 4));
  assert(small.pop(block, 32) == 27);
  for (int i = 0; i < 27; ++i) assert(block[i] == (i + 13) % 8 + 1);
  assert(small.used() == 0);
  assert(!small.push(header, 33, payload, 0));
  small.reset();

  // Exercise the actual firmware queue with concurrent producer/consumer,
  // differently sized chunks and repeated backpressure. Compare every byte.
  LogQueue<32768> queue;
  std::vector<uint8_t> expected, actual;
  constexpr int count = 12000;
  for (int n = 0; n < count; ++n) {
    for (int i = 0; i < 24; ++i) expected.push_back(uint8_t(n + i));
    for (int i = 0; i < 7 * (n % 128 + 1); ++i) expected.push_back(uint8_t(n ^ i));
  }
  std::thread producer([&] {
    for (int n = 0; n < count; ++n) {
      uint8_t h[24], p[896];
      for (int i = 0; i < 24; ++i) h[i] = uint8_t(n + i);
      const int size = 7 * (n % 128 + 1);
      for (int i = 0; i < size; ++i) p[i] = uint8_t(n ^ i);
      while (!queue.push(h, 24, p, size)) std::this_thread::yield();
    }
  });
  uint8_t output[4096];
  while (actual.size() < expected.size()) {
    size_t read = queue.peek(output, 1 + actual.size() % 4096);
    std::this_thread::yield(); // Producer must not overwrite staged bytes.
    uint8_t replay[4096];
    assert(queue.peek(replay, read) == read && !memcmp(output, replay, read));
    queue.consume(read);
    actual.insert(actual.end(), output, output + read);
    std::this_thread::yield();
  }
  producer.join();
  assert(actual == expected);
}
