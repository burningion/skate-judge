# Multi-task CNN, int8 export, and sensor events

The original landing CNN implemented only the six-channel, single-output
offline model. The expanded implementation adds magnitude channels, a trick
head, depthwise-separable convolutions, full-integer TFLite export, and device
benchmarks. The logger can record the LSM6DSO32's built-in motion events.
These additions do **not** establish a dependable landing judge.

## Results

The frozen `a7s-v7` snapshot supplies 131 complete contact windows: 42 makes and
89 bails/falls across eight sessions. The kickflip subset is unchanged at 97
attempts, with 14 makes. Four architectures use the same mixed-trick examples
and session splits. Seeds 0, 1, and 2 were fixed before this run; seed 0 remains
primary and is used for saved inference and int8 export.

| Model, primary seed | All-trick balanced accuracy | Kickflip balanced accuracy | Kickflip makes detected | Kickflip false landed |
| --- | ---: | ---: | ---: | ---: |
| Six channels, landing only | 73.0% | 63.6% | 5/14 | 7/83 |
| Eight channels, landing only | 74.3% | 57.1% | 4/14 | 12/83 |
| Eight channels, two heads | 75.0% | 58.2% | 5/14 | 16/83 |
| Eight channels, two heads, separable | 78.5% | 61.2% | 5/14 | 11/83 |
| Same mixed-trick data, feature logistic | 70.7% | 65.3% | 9/14 | 28/83 |
| Same mixed-trick data, feature forest | 59.1% | 46.3% | 3/14 | 24/83 |
| Previous kickflip-only feature forest | — | 64.8% | 6/14 | 11/83 |

The last row has different training data and is provided as the existing
kickflip reference. The preceding rows form the matched-data comparison.
Magnitudes and the trick head did not consistently improve kickflip outcome
detection. The separable model spans 60.7–67.8% kickflip balanced accuracy
across seeds. Its primary run still misses nine of fourteen makes.

Mixed-trick scores are confounded: every eligible ollie is a make, and many rare
tricks have only one outcome. The primary separable trick head has 59.5% naming
accuracy and 9.8% macro recall across 15 labels. Always naming kickflip would
score 74.0% accuracy. Twelve held-out attempts have labels absent from that
fold's training data and count as errors. This is not a validated trick recognizer.

Small sample size, outcome confounding, window placement, and model design all
need investigation. The contact-centered window can omit much of the aerial
trick, and global average pooling loses explicit early/late phase information
that the feature baseline retains. The experiments do not establish data
quantity as the sole problem. These are development results on previously
explored sessions, not independent confirmation.

## Training and prediction

```bash
uv run learning/trick_cnn.py train datasets/a7s-v7 --output .build/trick-cnn-v1
uv run learning/trick_cnn.py predict \
  --model .build/trick-cnn-v1/models/raw8_multi_separable/model.json \
  --samples sessions/a7s-001/samples.csv --contact-s 96.36942686781973
```

Use a new output directory for each run. Prediction requires contact time but
**does not take a trick label**. The example recording belongs to the training
data; the command checks usage, not generalization. Invalid or incomplete input
returns `unknown`. Both output scores are uncalibrated.

Input retains the original quality checks and timestamp-based resampling:
230 points at 200 Hz over contact −0.15 to +1.00 seconds. Magnitudes are computed
from the resampled physical axes before training-fold normalization. The heads
share three convolution blocks, global averaging, and training-only dropout.
The auxiliary loss weight is fixed at 0.25. Normalization, both sets of class
weights, and trick vocabulary use training data only. No held-out labels select
epochs, seeds, thresholds, or checkpoints.

`report.md`, `report.json`, and `predictions.csv` retain every seed's results,
per-class recall, folds, exclusions, source hashes, and baseline comparisons.
`training_history.csv` records losses. `models/` contains final primary models;
`folds/` retains primary fold models for int8 evaluation. Arrays load without
pickle. Existing runs and source labels are unchanged.

## Actual int8 conversion

```bash
uv run learning/export_trick_cnn.py .build/trick-cnn-v1 \
  --variant raw8_multi_separable --output .build/trick-int8-v1
```

The export script uses a separate pinned environment: TensorFlow 2.16.2 requires
NumPy 1.26.4. It copies trained weights into an equivalent graph and verifies
float parity, including symmetric convolution padding. The resulting TFLite
graph has integer input, intermediate tensors, and outputs. Each held-out fold
is calibrated using only that fold's training attempts, and reloaded float
predictions are checked against the original experiment.

The separable model has 2,168 parameters; the actual TFLite file is **10,952
bytes**. Conversion changed one held-out landing decision: kickflip results
became **5/14 makes, 12/83 false landed**, or **60.6% balanced accuracy**. Mean
absolute score difference is 0.0122; maximum is 0.0967. All-trick balanced
accuracy is 77.9%, subject to the confounding above. Quantization did not
improve accuracy.

The export includes `model.tflite`, JSON normalization/tensor scales, per-fold
graphs and results, and `trick_model_data.h` with a benchmark input and expected
outputs. Layout is `[1, 1, 230, channels]`, time-major NHWC. Normalize with the
saved mean/scale, then quantize with the tensor scale/zero point, rounding and
int8 saturation. The header fixture is already normalized and quantized. It
comes from the final model's training cohort and is only a parity/benchmark
fixture, not a field-accuracy test.

## Device benchmark

Measured on the connected Feather ESP32-S3 (240 MHz, no PSRAM), using the
Arduino reference kernels: three batches of 30 invocations averaged **8.346 ms**,
with an observed range of **8.341–8.390 ms**. Both output heads passed the
exported parity check. Tensor-arena use was **7,848 bytes** within the benchmark's
64 KiB reservation. These figures exclude preprocessing and concurrent logging;
they do not measure ESP-NN acceleration. Raw results are in
`.build/trick-device-v1.json`, linked to the exported graph's SHA-256.

Install the Arduino reference runtime once and compile without uploading:

```bash
ARDUINO_DIRECTORIES_USER="$PWD/.arduino" arduino-cli lib install 'Chirale_TensorFLowLite@2.0.0'
python3 learning/build_benchmark.py .build/trick-int8-v1
```

The script stages the sketch/header under `.build/`. The standalone firmware
checks both outputs against the fixture, then reports JSON with 30-run
mean/min/max inference time, actual tensor-arena use, reserved arena size, and
remaining internal heap. It reserves 64 KiB in internal SRAM, uses no PSRAM,
and reports allocation/parity failures explicitly. Timing excludes resampling,
normalization, acquisition, Wi-Fi, and storage contention.

After uploading the staged sketch, collect reports with:

```bash
python3 learning/benchmark_device.py --port /dev/cu.usbmodem22401 \
  --export .build/trick-int8-v1 --output .build/trick-device-v1.json
```

An ESP-IDF project uses the same benchmark with Espressif's TFLite Micro 1.4.1
and ESP-NN enabled. With ESP-IDF installed and its environment activated:

```bash
idf.py -C firmware/trick_benchmark_idf \
  -DTRICK_MODEL_DIR="$PWD/.build/trick-int8-v1" set-target esp32s3
idf.py -C firmware/trick_benchmark_idf \
  -DTRICK_MODEL_DIR="$PWD/.build/trick-int8-v1" build
```

ESP-IDF is not installed in this workspace; that optimized build path remains
unverified. The Arduino benchmark compiled for the Feather S3 without PSRAM.
The standalone benchmark is separate from the logger. Continuous detection,
contact estimation, and inference alongside acquisition remain integration
work; this is not a live end-to-end judge.

## LSM6DSO32 motion observations

The corrected profile was tested on the connected board for 30 seconds while
the board was tilted and returned flat. It recorded **five orientation events
and one wake-up event**, alongside **5,928 complete sample pairs at 196.014 Hz**.
The imported recording passed quality checks with no missing slots, zero
samples, I/O errors, or FIFO overruns. No free-fall event was observed; free-fall
detection is not validated by this check. Results are in
`.build/motion-device-v2.json`; the raw log and imported data are in
`sessions/s3-motion-smoke-20261023-v2/`, without training labels.

The board was left running the corrected logger with motion events enabled.
Both original recording IDs and byte counts were verified after restoration;
the two bench recordings were also retained on the board and downloaded.
The normal build default remains off; use `IMU_MOTION_EVENTS=1` to retain this
experimental capability when flashing again.

```bash
IMU_MOTION_EVENTS=1 ./flash-feather.sh --compile-only
```

The opt-in logger uses built-in free-fall, wake-up, and 6D orientation detection:
312 mg for ten nominal ODR periods, 1 g slope wake-up for one ODR period, and the
68-degree orientation setting. These are initial engineering values, not
thresholds learned from skating. Acquisition range, ODR, and FIFO settings
remain the same; inactivity power switching is off.

Events are routed to INT1 to activate latching, even though the pin is not wired
to the MCU. Latched source registers are polled approximately every 250 ms and saved as
versioned `MOTION` packets. The importer emits `board_motion` entries in
`events.jsonl`, with raw flags and bounds from the previous read through the
current read's completion. Events can coalesce. This is observation timing,
not exact pop/contact timing. Old logs remain readable; old importers cannot
read the new packet kind.

The first hardware check exposed a missing interrupt route: setting LIR alone
does not latch events. The corrected profile also lengthens polling because
source reads reset detector state. Polling can still interrupt duration-based
detection; it is only an observation path, not a reliable inference gate. A
wired interrupt and read-on-event handling need separate validation. Metadata
records the profile revision and polling interval. This behavior follows
[AN5473 §5.1](https://www.st.com/resource/en/application_note/dm00696324-lsm6dso32-alwayson-3d-accelerometer-and-3d-gyroscope-stmicroelectronics.pdf).

These flags describe board motion, never automatic rider-fall or landing
labels. No custom FSM bytecode or MLC model is loaded. Historical recordings
lack these flags, so they are not CNN inputs and have not been credited with
improving accuracy. Logging defaults off pending field validation.

The [video review timeline](trick-review.md#recorded-board-events) now displays
the imported observations after LED alignment, preserving their uncertain
timing. New [dataset snapshots](dataset.md#recorded-board-observations) include
`motion_events.jsonl`, joined to eligible labeled windows, plus availability
and counts in the manifest. This carries the additional recording data through
review and export for future experiments; it does not change the model's input
or the results above.

Register definitions follow the [LSM6DSO32 datasheet](https://www.st.com/resource/en/datasheet/lsm6dso32.pdf),
sections 9.23, 9.25, and 9.41–9.48. Export follows
[full-integer quantization](https://developers.google.com/edge/litert/conversion/tensorflow/quantization/post_training_integer_quant).
The optimized runtime follows [Espressif's TFLite Micro component](https://github.com/espressif/esp-tflite-micro).

## Verification

```bash
python3 -m unittest discover -s tests -p 'test_trick_cnn.py'
python3 -m unittest discover -s tests -p 'test_motion_events.py'
# Use the export environment for this test, which needs TensorFlow:
python3 -m unittest discover -s tests -p 'test_trick_export.py'
```

Tests cover matched folds, train-only statistics/vocabulary/weights, both
outputs without a trick input, saved-model integrity/parity, ordinary and
separable float/int8 conversion, motion packet corruption/timing bounds, and
native register configuration/failure handling. Existing landing, dataset,
and onboard-log tests also pass.
