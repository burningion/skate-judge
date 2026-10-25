#include <cstdio>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "benchmark.h"

extern "C" void app_main() {
  char report[700];
  for (;;) {
    runTrickBenchmark(report, sizeof(report));
    printf("%s\n", report);
    vTaskDelay(pdMS_TO_TICKS(5000));
  }
}
