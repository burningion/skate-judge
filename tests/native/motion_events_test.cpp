#include "motion_events.h"
#include <cassert>
#include <utility>
#include <vector>
int main() {
  std::vector<std::pair<int, int>> writes;
  assert(configureMotionEvents([&](uint8_t reg, uint8_t value) {
    writes.emplace_back(reg, value);
    assert(reg >= 0x56 && reg <= 0x5e); // No acquisition/FIFO register writes.
    return true;
  }));
  const std::vector<std::pair<int, int>> expected = {{0x56,0x41},{0x58,0x80},{0x59,0},{0x5b,2},{0x5c,0x20},{0x5d,0x50},{0x5e,0x34}};
  assert(MOTION_POLL_US > 10 * 1000000 / 180); // Do not reset duration logic faster than it can mature.
  assert(writes == expected);
  int calls = 0;
  assert(!configureMotionEvents([&](uint8_t, uint8_t) { return ++calls < 3; }));
  assert(calls == 3);
  assert(hasMotionEvent(0x20, 0));
  assert(hasMotionEvent(0x08, 0));
  assert(hasMotionEvent(0, 0x40));
  assert(!hasMotionEvent(0x07, 0x3f));
}
