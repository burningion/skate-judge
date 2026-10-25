#pragma once
#include <cmath>
#include <cstdio>
#include <cstring>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "trick_model_data.h"

// Standalone benchmark. This is not connected to the logger's acquisition task.
// Static storage lives in internal SRAM; no PSRAM allocation is made.
#ifndef TRICK_ARENA_BYTES
#define TRICK_ARENA_BYTES 65536
#endif
alignas(16) static uint8_t trickArena[TRICK_ARENA_BYTES];

static bool runTrickBenchmark(char *report, size_t capacity) {
  auto fail = [&](const char *reason) {
    snprintf(report, capacity, "{\"status\":\"error\",\"reason\":\"%s\"}", reason);
    return false;
  };
  const tflite::Model *model = tflite::GetModel(kTrickModel);
  if (model->version() != TFLITE_SCHEMA_VERSION) return fail("model_schema");
  tflite::MicroMutableOpResolver<7> resolver;
  if (resolver.AddConv2D() != kTfLiteOk || resolver.AddDepthwiseConv2D() != kTfLiteOk ||
      resolver.AddFullyConnected() != kTfLiteOk || resolver.AddMean() != kTfLiteOk ||
      resolver.AddPad() != kTfLiteOk || resolver.AddLogistic() != kTfLiteOk ||
      resolver.AddSoftmax() != kTfLiteOk) return fail("operator_registration");
  tflite::MicroInterpreter interpreter(model, resolver, trickArena, sizeof(trickArena));
  if (interpreter.AllocateTensors() != kTfLiteOk) return fail("tensor_arena_allocation");
  TfLiteTensor *input = interpreter.input(0);
  if (input->type != kTfLiteInt8 || input->bytes != sizeof(kExampleInput) ||
      input->dims->size != 4 || input->dims->data[0] != 1 || input->dims->data[1] != 1 ||
      input->dims->data[2] != 230 || input->dims->data[3] != kInputChannels) return fail("input_contract");
  memcpy(input->data.int8, kExampleInput, sizeof(kExampleInput));
  if (interpreter.Invoke() != kTfLiteOk) return fail("warmup_invoke");
  TfLiteTensor *landed = nullptr, *tricks = nullptr;
  for (size_t i = 0; i < interpreter.outputs_size(); ++i) {
    TfLiteTensor *tensor = interpreter.output(i);
    if (tensor->type != kTfLiteInt8) return fail("output_type");
    if (tensor->bytes == 1) landed = tensor;
    else if (tensor->bytes == kTrickClasses) tricks = tensor;
    else return fail("output_shape");
  }
  if (!landed || !tricks || kTrickClasses <= 1) return fail("multi_task_outputs_required");
  auto value = [](const TfLiteTensor *tensor, int i) {
    return (int(tensor->data.int8[i]) - tensor->params.zero_point) * tensor->params.scale;
  };
  float landedScore = value(landed, 0);
  if (fabsf(landedScore - kExpectedLanded[0]) > 2 * landed->params.scale + 1e-6f) return fail("landing_parity");
  float trickDifference = 0;
  for (int i = 0; i < kTrickClasses; ++i) {
    trickDifference = fmaxf(trickDifference, fabsf(value(tricks, i) - kExpectedTricks[i]));
  }
  if (trickDifference > 2 * tricks->params.scale + 1e-6f) return fail("trick_parity");
  constexpr int trials = 30;
  uint64_t total = 0, minimum = UINT64_MAX, maximum = 0;
  for (int i = 0; i < trials; ++i) {
    const uint64_t before = esp_timer_get_time();
    if (interpreter.Invoke() != kTfLiteOk) return fail("timed_invoke");
    const uint64_t elapsed = esp_timer_get_time() - before;
    total += elapsed;
    if (elapsed < minimum) minimum = elapsed;
    if (elapsed > maximum) maximum = elapsed;
  }
#if defined(CONFIG_ESP_TFLITE_MICRO_USE_ESP_NN) && CONFIG_ESP_TFLITE_MICRO_USE_ESP_NN
  const char *runtime = "esp_tflite_micro_esp_nn";
#else
  const char *runtime = "tflite_micro_reference";
#endif
  snprintf(report, capacity,
    "{\"status\":\"ok\",\"runtime\":\"%s\",\"model_bytes\":%u,\"arena_reserved_bytes\":%u,"
    "\"arena_used_bytes\":%u,\"internal_heap_free_bytes\":%u,\"trials\":%d,"
    "\"invoke_mean_us\":%.2f,\"invoke_min_us\":%llu,\"invoke_max_us\":%llu,"
    "\"landed_score\":%.6f,\"trick_parity_max_difference\":%.6f,\"includes_preprocessing\":false}",
    runtime, kTrickModelBytes, unsigned(sizeof(trickArena)), unsigned(interpreter.arena_used_bytes()),
    unsigned(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)), trials,
    double(total) / trials, (unsigned long long)minimum, (unsigned long long)maximum,
    double(landedScore), double(trickDifference));
  return true;
}
