#pragma once
#include <stdint.h>

struct BatteryReading {
  int millivolts = -1;
  float percent = -1;
  bool available() const { return millivolts >= 0; }
};

// MAX17048 VERSION, VCELL and SOC are big-endian 16-bit registers.
// Read only: never reset/quick-start the gauge or change its battery model.
// https://www.analog.com/media/en/technical-documentation/data-sheets/MAX17048-MAX17049.pdf
template <typename ReadWord>
BatteryReading readMAX17048(ReadWord readWord) {
  int version = readWord(0x08);
  if (version < 0 || (version & 0xfff0) != 0x0010) return {};
  int voltage = readWord(0x02);
  if (voltage < 0) return {};
  int charge = readWord(0x04);
  if (charge < 0) return {};
  int millivolts = (voltage * 5 + 32) / 64;
  // Reject readings outside the gauge's specified single-cell supply range.
  if (millivolts < 2500 || millivolts > 4500) return {};
  float percent = charge / 256.0f;
  return {millivolts, percent > 100 ? 100 : percent};
}
