# Onboard trick-attempt detector: deployment plan

Status: **Draft for review — no implementation or device changes authorized by this document**  
Date: 2026-10-25

## 1. Objective and first release

Run the existing learned attempt detector on the Feather ESP32-S3 while it
records the LSM6DSO32. Save potential-attempt predictions alongside the complete
raw recording, then compare those predictions with video and desktop inference.

The first release is an opt-in **logging-only trial**. Predictions do not control
recording, discard samples, assign training labels, judge landings, name tricks,
or trigger lights/sounds. Existing LED synchronization remains available. Those
later product behaviors require their own decision after field validation.

Implementing this plan starts only after a subsequent instruction to proceed.
Creating this plan does not start implementation, training, compilation, flashing,
or a recording session.

## 2. Frozen baseline

| Item | Baseline to preserve for the first trial |
| --- | --- |
| Model | `.build/attempts-v1/model.json`, trained from `a7s-v7` |
| Model SHA-256 | `b842f5960faa5f7ddbcffa03b188b3d4bd9db528831185a5dfc45d258d24968a` |
| Classifier | 300 decision trees, maximum depth 4; 7,276 total nodes in this artifact |
| Input | Acceleration and gyro samples in physical units with sensor-derived timestamps |
| Features | The same 15 magnitude and temporal-bin features in `learning/attempts.py` |
| Window / stride | Two seconds / 100 ms, aligned to a session-time grid |
| Score cutoff | 0.50; an uncalibrated attempt score |
| Sensor setup | Existing nominal 208 Hz, ±32 g, ±2000 degrees/second settings |
| Hardware | Existing Feather ESP32-S3 without PSRAM; no replacement IMU required |

Export the trained forest into firmware constants. Do not retrain it, quantize
the feature pipeline, substitute the trick/landing CNN, or use raw wake-up and
orientation interrupts as a gate in the first release.

The current held-out evaluation caught 128/135 attempts, with 16 false events
in 4.87 minutes of evaluable reviewed background. Those results used the original
offline event policy. The latest review display combines 29 detections into 18
groups in `a7s-028`; that is a presentation result, not a measured accuracy result.
Neither result establishes the accuracy of the proposed streaming policy.

References: [attempt detector](attempt-detector.md),
[recording architecture](onboard-recording.md),
[review behavior](trick-review.md#potential-trick-attempts-and-raw-board-observations),
and [previous CNN benchmark](trick-cnn.md#device-benchmark).

## 3. Architecture and ownership

The ESP32-S3 runs inference. The IMU continues supplying the raw FIFO data used
by the recorder. Preserve the current sensor configuration and logging pipeline.

1. **Acquisition task:** retains sole ownership of sensor access, packet sequence
   allocation, and the existing raw-log queue. Copy validated FIFO words and
   clock anchors into a separate, bounded inference-input queue without waiting
   for inference. Tag copies with their recording identity.
2. **Inference worker:** reconstructs complete accel/gyro/timestamp slots, builds
   the time window, extracts features, scores the forest, and consolidates
   detections. It does not access I²C, write storage, or enqueue directly into the
   existing raw-log queue.
3. **Result queue:** returns bounded prediction/diagnostic messages to acquisition.
   Acquisition serializes optional messages after handling raw sensor data and
   only when adequate raw-recording queue headroom remains.
4. **Storage task:** continues consuming the existing log queue and using the
   established recovery mechanism.

This preserves the existing queue's single-producer/single-consumer contract.
Adding a second direct producer to `LogQueue` is not part of the design.

An inference backlog, invalid model, or optional-result queue overflow marks
detection as degraded/unavailable and increments diagnostics. It must not block
FIFO draining or turn missing inference into a negative classification. Existing
raw acquisition/storage faults retain their current recording-fault behavior.
Worker inputs, pending groups, and outputs reset between recordings; late results
from an older recording are rejected by recording identity.

## 4. Time, preprocessing, and quality contract

The desktop importer interpolates between measured clock anchors. Firmware must
not approximate the data as exactly 208 samples/second or timestamp samples by
their arrival at the acquisition task.

The initial proposal is to buffer samples until two usable anchors bracket their
sensor ticks, then process that closed interval using the same piecewise-linear
mapping. This adds roughly one anchor interval of delay, but avoids relying on
future clock corrections to repair predictions already emitted. Include this
buffering in memory and latency measurements.

Before coding firmware, specify a streaming reference that defines:

- FIFO slot assembly, missing/duplicate words, tick rollover, physical-unit
  conversion, session origin, and half-open window/bin boundaries;
- which clock-quality checks are possible at each point in the stream, and what
  happens if a later anchor exposes an earlier timing problem;
- exact feature order, numeric precision, float32 classifier input conversion,
  comparisons against exported tree thresholds, and leaf-score aggregation;
- the existing checks for nonfinite or nonmonotonic timestamps, clipping, zero
  acceleration, gaps over 30 ms, insufficient coverage, and sparse temporal bins.

Windows crossing an inference-input drop or invalid sensor interval are unknown.
Suppress inference until a complete valid window is available again. Do not
interpolate across sensor gaps. Delayed or missing anchors cannot cause unbounded
buffer growth: stop inference, report the reason, and resume only after timing
and window coverage recover. Recording continues independently.

Record the mapping version and timing-quality state. Keep event timestamps based
on sensor data separate from the ESP32 time when a prediction becomes available.
Do not promise identical online and final-import timing until replay proves it.

## 5. Streaming event policy

The existing scanner ranks windows over the complete recording before suppressing
nearby detections. That algorithm is not a directly deployable causal event policy.
Maintain two distinct validation contracts:

- **Model parity:** identical timed windows must produce matching features and
  forest scores in Python and C++.
- **Streaming parity:** firmware events must match a new Python streaming reference
  given the same ordered inputs and availability times. Compare its events with
  the old offline detector, but do not require identical event counts or claim
  the old event metrics apply to it.

Proposed policy version `streaming-attempt-v1`:

1. Score every valid completed two-second window at 100 ms intervals. A score at
   least 0.50 is a positive window.
2. Begin a pending episode at the first positive window. Extend it for subsequent
   positive windows whose input intervals overlap the episode or start at most
   one second after its current end. Known unscored regions break continuity.
3. Retain the union of input windows, the strongest score and its center timestamp,
   and the number of positive windows. Store aggregates, not an ever-growing list.
   Sustained motion remains one episode; do not periodically count it as new tricks.
4. Finalize only after the processed-window watermark proves that a future window
   cannot still join the episode. For a two-second window and one-second allowed
   interval gap, this requires processing window ends beyond
   `last_positive_window_end + 3 seconds`.
5. At recording stop, a sensor/inference gap, or timing failure, close an outstanding
   episode with an explicit termination reason and `truncated=true`. Do not claim
   it ended naturally. Discard incomplete input windows without scoring them.

This policy groups all positive windows rather than the offline scanner's
score-suppressed subset. It may produce wider or differently joined episodes;
that difference is an evaluation item, not an implementation error to hide.

Expected finalization delay is about three seconds after the last positive input
window ends, plus clock-anchor buffering, scheduling, and computation. With the
proposed clock approach, budget roughly four seconds plus scheduling margin under
healthy conditions. Measure actual latency distributions before making a product
guarantee. An immediate pop alert is outside this release.

Playback padding remains a review-page concern. It must not silently expand
stored event boundaries or turn them into human pop/contact markers.

## 6. Prediction and diagnostic records

Add a versioned optional prediction packet through the existing CRC-protected
log framing. Select its packet number and binary layout during protocol design;
all payloads must remain within the decoder's existing size bound.

The recording metadata identifies model hash, feature version, streaming-policy
version, score cutoff, window/stride, grouping gap, and timing-mapping version.
An attempt record contains:

- recording-local event ID and event start/end sensor ticks;
- mapped start/end device times, strongest-window center and score;
- emission time, contributing-window count, timing/coverage quality;
- completion/termination reason and whether the event is truncated.

Metadata plus event identity must distinguish model predictions from `board_motion`
observations and human labels. The importer preserves that distinction and maps
predictions onto session time without rewriting their provenance. If final-import
timing differs, retain the emitted values and any derived corrected mapping
explicitly rather than silently replacing what the device reported.

Record inference-enabled state, windows processed/skipped by reason, input/output
drops, queue peaks, runtime maxima, stack/heap diagnostics, and pending-event state.
An optional validation build may record per-window scores for parity investigation;
measure its additional storage/CPU cost separately from normal event-only logging.

Update the importer before any trial firmware is flashed. It must read both old
recordings and the new packets, with tests for CRC failure, malformed payloads,
unknown versions, duplicate IDs, truncation, and power-cut recovery. Older importers
reject unknown packet kinds; do not claim older tools can read the extended format.

The review page should identify **recorded onboard predictions** separately from
**desktop recomputation**, show unavailable/degraded coverage, and allow comparison
against human labels. Predictions must never enter training as ground truth.

## 7. Work packages and completion gates

| Phase | Deliverable | Gate before proceeding |
| --- | --- | --- |
| 0. Freeze specification | Model manifest, timing/numeric contract, packet specification, versioned event policy, fixed test recordings | Resolve the clock/parity and event-latency contract before firmware work |
| 1. Portable inference | Forest exporter, portable C++ feature/scoring library, native replay harness | Score and decision parity on identical timed windows; malformed models and invalid inputs rejected |
| 2. Streaming reference | Python causal replay and portable C++ state machine | Identical streaming events on the same input stream, including stops, gaps, overload, and rollover |
| 3. Recorder and importer integration | Opt-in worker/queues, versioned records, importer, review comparison | Existing recording tests pass; no second producer added to the log queue; old logs remain readable |
| 4. Device benchmark | Compile report, memory map, paired detector-off/on acquisition runs, fault/soak report | Meet resource and recording-integrity criteria below before a field trial |
| 5. Logging-only field trial | New video-aligned sessions, independently reviewed attempts/background, comparison report | Evidence-based decision to retain experimental logging, revise the design, or plan user feedback |

Expected code areas, if implementation is later authorized: `learning/` for the
exporter/reference; new portable inference files under `firmware/`; integration in
`firmware/imu_logger/imu_logger.ino`, `log_format.h`, and the build flags; decoding
in `capture/onboard_log.py`; prediction handling in `capture/` and `viz/`; native,
Python, and review-controller regression tests. Exact filenames are not yet API
commitments.

## 8. Acceptance criteria

The following are proposed engineering targets, not measurements of this model
on the device. Freeze any necessary adjustments before running acceptance tests.

**Portable correctness**

- Export preserves the frozen forest's decisions on recorded feature matrices,
  including cases at or close to tree thresholds and the 0.50 score cutoff.
- Target maximum absolute score difference is `1e-5` for identical features.
  Feature conversion must also preserve decisions on the fixed replay corpus.
  Investigate every discrepancy; do not silently relax tolerance after failures.
- The raw-log replay harness separately reports differences attributable to clock
  reconstruction, feature math, score calculation, and event consolidation.
- Python/C++ streaming implementations agree on IDs, boundaries, scores within
  tolerance, termination reasons, unknown coverage, and window counts.

**Resource and recording integrity**

- Initial added-RAM budget: at most 64 KiB, including inference buffers, queues,
  and worker stack; model constants stay in flash. Confirm the export fits the
  existing application partition without changing the storage partition layout.
- Initial runtime target: scoring plus feature extraction has p99 at most 20 ms,
  with no missed 100 ms processing deadlines in the specified healthy stress runs.
  Measure total worker cost, not just tree traversal. Retain at least 25% unused
  worker-stack space under the same runs.
- Paired baseline/inference runs show no added FIFO overruns, missing/duplicate
  complete slots, acquisition errors, CRC failures, or unexplained timestamp gaps.
  Sample rate remains within the recorder's existing accepted range and is
  compared with the same board's detector-off baseline.
- Complete a minimum 60-minute soak including normal Wi-Fi status polling, LED
  sync pulses, battery reads, repeated start/stop, and normal storage flushing.
  Exercise storage stalls/recovery, input/result saturation, and invalid-clock
  cases separately. Detector failure must leave raw recording functional wherever
  the underlying recorder itself remains healthy.
- Verify bounded shutdown, pending-event handling, and no prediction leakage
  across recording IDs. Measure additional power consumption and log volume.

**Field evaluation**

- Use at least three new outings, totaling at least 100 manually confirmed
  attempts and 60 minutes of fully reviewed background. Include rolling, pushing,
  carrying, pickup/drop/handling, and waiting, plus makes and failed attempts.
- Freeze model, threshold, event policy, and matching rules before these outings.
  Use the existing action-marker matching rule where applicable, one-to-one
  matching, explicit duplicate counts, and separate incomplete/unknown coverage.
- Report attempt recall, false events per reviewed minute, duplicates per attempt,
  detection availability, and finalization delay. Show per-session results,
  uncertainty intervals, and comparison with offline predictions. Do not substitute
  all recorded minutes for actually reviewed/evaluable background exposure.
- Proposed targets for considering a later automatic-feedback release: at least
  95% attempt recall, at most 0.2 false events per fully reviewed background minute,
  and duplicates on at most 5% of attempts. These are goals, not established results;
  meeting them on this small cohort is not universal validation. They are not a
  prerequisite for starting the logging-only trial that collects the evidence.
- If changes are tuned using these outings, reserve additional fresh sessions for
  subsequent evaluation rather than reusing the tuning recordings as a test set.

## 9. Rollout and rollback

Keep inference off by default behind a build/runtime capability that is explicit
in device status and recording metadata. Deploy the compatible importer first,
then the opt-in recorder only after phases 1–4 pass and an implementation/deployment
instruction is given.

Before flashing, preserve the known-good recorder build/configuration and inventory
stored recording IDs and sizes. Use the existing application/storage partition
layout; do not erase, format, or migrate the recording filesystem. Verify retained
recordings after flashing, and download trial logs for comparison.

Rollback disables inference or restores the known-good recorder using the same
partition layout. Keep the updated importer so trial recordings remain readable.
Preserve trial logs and diagnostics even when they reveal a failure. Loss of model
availability must be distinguishable from a successful scan with zero attempts.

## 10. Decisions to settle before implementation

The recommended defaults above make this a concrete proposal. The initial design
review should settle these remaining contracts, without changing the frozen model:

1. Accept the delayed, bracketed-anchor approach for the first trial, or explicitly
   choose a lower-latency causal clock estimate and its different parity contract.
2. Ratify the all-positive-window streaming grouping policy and its finalization
   delay; do not treat desktop global suppression as equivalent.
3. Finalize packet layout, optional-message queue reserve/drop rules, resource
   budgets, and deployment feature flag after a design-level memory calculation.
4. Confirm that automatic feedback remains a later release and ratify its proposed
   field-performance targets before collecting that release's acceptance data.

The outcome of this planning task is this document only. No implementation,
model changes, firmware build, flash, or hardware action has been performed.
