#include <Arduino.h>
#include <Chirale_TensorFlowLite.h>
#include "benchmark.h"

void setup() {
  Serial.begin(115200);
  delay(2000);
}

void loop() {
  char report[700];
  runTrickBenchmark(report, sizeof(report));
  Serial.println(report);
  delay(5000);
}
