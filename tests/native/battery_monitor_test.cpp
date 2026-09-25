#include "battery_monitor.h"
#include <assert.h>
#include <map>
#include <vector>

int main() {
  std::map<int, int> registers{{0x08, 0x0012}, {0x02, 51200}, {0x04, 0x4b80}};
  std::vector<int> reads;
  auto read = [&](int reg) { reads.push_back(reg); return registers.at(reg); };
  auto battery = readMAX17048(read);
  assert(battery.available() && battery.millivolts == 4000 && battery.percent == 75.5f);
  assert((reads == std::vector<int>{0x08, 0x02, 0x04}));

  // Missing or different gauge: do not interpret unrelated registers as SOC.
  for (int version : {-1, 0, 0xffff, 0x0020}) {
    registers[0x08] = version; reads.clear();
    assert(!readMAX17048(read).available());
    assert(reads.size() == 1);
  }
  registers[0x08] = 0x0012;
  // A partial read failure must not retain a previous good percentage/voltage.
  for (int reg : {0x02, 0x04}) {
    int previous = registers[reg]; registers[reg] = -1;
    battery = readMAX17048(read);
    assert(!battery.available() && battery.percent == -1 && battery.millivolts == -1);
    registers[reg] = previous;
  }
  registers[0x04] = 0;
  assert(readMAX17048(read).percent == 0); // Empty is valid, not unavailable.
  registers[0x04] = 0x6500;
  assert(readMAX17048(read).percent == 100); // Gauge estimates can exceed 100%.
  for (int voltage : {0, 30000, 60000, 65535}) {
    registers[0x02] = voltage;
    assert(!readMAX17048(read).available());
  }
}
