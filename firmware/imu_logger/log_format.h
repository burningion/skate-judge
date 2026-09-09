#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>

// Versioned, little-endian packets. CRC covers the first 20 header bytes and
// payload, so a power-cut tail can be detected without inventing missing data.
enum LogKind : uint8_t { META = 0, FIFO = 1, CLOCK = 2, SYNC = 3, END = 4 };
struct __attribute__((packed)) LogHeader {
  char magic[4];
  uint8_t kind, version;
  uint16_t length;
  uint32_t sequence;
  uint64_t deviceUs;
  uint32_t crc;
};
static_assert(sizeof(LogHeader) == 24, "Stable on-disk header");
inline uint32_t logCRC(uint32_t crc, const void *data, size_t length) {
  crc = ~crc;
  const auto *p = static_cast<const uint8_t *>(data);
  while (length--) {
    crc ^= *p++;
    for (int bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1U)));
  }
  return ~crc;
}
inline LogHeader logHeader(uint8_t kind, uint16_t length, uint32_t sequence,
                           uint64_t stamp, const void *payload) {
  LogHeader h{{'S','K','L','1'}, kind, 1, length, sequence, stamp, 0};
  h.crc = logCRC(logCRC(0, &h, 20), payload, length);
  return h;
}
