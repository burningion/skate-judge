#pragma once
#include <stdint.h>

// LSM6DSO32 datasheet Rev 1, sections 9.41–9.48. These are board-motion
// observations, never rider-fall/landing labels. Inactivity power modes stay off.
// Existing ±32 g / ±2000 dps / 208 Hz FIFO settings are not changed.
static constexpr uint32_t MOTION_POLL_US = 250000;
template <typename Write> bool configureMotionEvents(Write checkedWrite) {
  return checkedWrite(0x56, 0x41) && // LIR + INT_CLR_ON_READ
         checkedWrite(0x58, 0x80) && // basic interrupts on; no ODR/power switching
         checkedWrite(0x59, 0x00) && // 6D at 68 degrees, all three axes
         checkedWrite(0x5b, 0x02) && // slope wakeup: 2 * (32g / 64) = 1g
         checkedWrite(0x5c, 0x20) && // wake duration 1 ODR; freefall high bit zero
         checkedWrite(0x5d, 0x50) && // freefall: 312mg, 10 ODR (~48ms nominal)
         checkedWrite(0x5e, 0x34);   // Route FF/WU/6D to INT1: required for latching.
  // AN5473 §5.1: latching is inactive without routing, even when LIR=1.
  // INT1 need not be wired for these observations. Poll reads reset detector
  // state; 250ms leaves room for the 10-ODR freefall duration between reads.
  // An interrupt wire/read-on-event is needed before using this as a reliable gate.
}

inline bool hasMotionEvent(uint8_t wake, uint8_t orientation) {
  return (wake & 0x28) || (orientation & 0x40);
}
