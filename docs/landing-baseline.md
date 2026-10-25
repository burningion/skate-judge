# First offline landing baseline

`learning/landing.py` trains a small kickflip outcome classifier from a frozen
IMU dataset. It answers: given a known kickflip and its reviewed contact time,
does the board motion look like a landed attempt? Makes become `landed`; bails
and falls become `not_landed`. Background and unknown outcomes are not misses.

A [raw-signal CNN comparison](landing-cnn.md) now uses the same cohort and contact
window. The original feature baseline and its saved artifacts remain available.

This is an offline experiment. It does not recognize trick types, find contact
automatically, judge a continuous session, or change labels in the review UI.
Contact with the ground alone does not establish that the rider stayed on.

## Run it

The current snapshot is `datasets/a7s-v7`, created from the latest labels,
including the timing correction made after v6. To build another snapshot:

```bash
python3 capture/dataset.py build --labeled-only --allow-sample-gaps a7s-018 \
  --output datasets/a7s-v8
```

Train, evaluate, and save a model:

```bash
uv run learning/landing.py train datasets/a7s-v7 --output .build/landing-kickflip-v1
```

Both commands require unused output directories. The script declares pinned
NumPy, SciPy, and scikit-learn dependencies for `uv`; Python 3.11+ with those
dependencies already installed also works. It uses only local data.

Kickflips are the default scope. `--trick ollie` selects another trick, but
training fails if any training fold lacks either outcome. `--all-tricks` is an
explicit exploratory option; the resulting model may exploit differences in
trick type because the collection has unequal outcome coverage across tricks.
The report includes metrics per trick as well as overall.

## Initial result

The v7 snapshot has 137 eligible attempts and 228 background ranges across eight
sessions. There are 100 eligible kickflips: 14 makes and 86 misses. The feature
checks exclude three misses, leaving **97 attempts: 14 makes and 83 misses**.

Leave-one-session-out predictions at the fixed 0.5 decision threshold:

| Measure | Contact model | Always predict miss |
| --- | ---: | ---: |
| Landed attempts detected | 9 / 14 (64.3%) | 0 / 14 |
| Misses correctly rejected | 48 / 83 (57.8%) | 83 / 83 |
| Misses incorrectly called landed | 35 | 0 |
| Precision when predicting landed | 20.5% | Undefined: no landed predictions |
| Balanced accuracy | 61.1% | 50.0% |
| Overall accuracy | 58.8% | 85.6% |

Pooled held-out ROC AUC is 0.707. The baseline that chooses the majority outcome
from each training fold also always predicts miss on this dataset. Balanced
accuracy averages the recall for makes and misses, so the numerous misses
cannot dominate that metric.

This suggests a signal worth investigating, with substantial uncertainty from
only 14 makes. The false-positive rate is too high for automatic judging. The
model also misses all three makes in sessions `a7s-025` and `a7s-026`, showing
that performance varies across recordings. All examples identify `rider-01`
and `deck-01`; this is not evidence of performance on new riders or boards.
Two recordings came from the same outing, so holding out sessions does not
fully establish independence between training and evaluation conditions.

The local run is in `.build/landing-kickflip-v1/`. Predictions in its CSV are
strictly from held-out sessions. The separately saved `model.json` is fitted
to all 97 examples for subsequent inference; its predictions on those same
examples must not be presented as held-out results.

## Fixed features and evaluation

The first configuration was selected before examining model results. It uses
eight nonnegative features, transformed with `log1p`:

| Phase relative to contact | Measurements |
| --- | --- |
| Impact: −0.15 to +0.20 s | Maximum acceleration magnitude in g; RMS deviation of acceleration magnitude from 1 g; maximum gyro magnitude in rad/s |
| Early settling: +0.20 to +0.60 s | RMS acceleration-magnitude deviation from 1 g; RMS gyro magnitude |
| Late settling: +0.60 to +1.00 s | RMS acceleration-magnitude deviation from 1 g; RMS gyro magnitude |
| Combined settling: +0.20 to +1.00 s | Norm of the three acceleration-axis standard deviations, in g |

These features are invariant to a fixed rotation of the sensor coordinate
system. They do not measure the rider directly. No trick name, outcome,
session identity, or variable label duration enters the feature vector.

The classifier is logistic regression with L2 regularization, `C=1`, balanced
class weights, and a fixed 0.5 threshold. Each fold fits its own standard scaler
and classifier using only the other sessions. Every included example receives
exactly one held-out score. Folds with a single test class have undefined
balanced accuracy and ROC AUC, recorded as `null`; they still contribute their
predictions to the pooled counts. A training fold with only one class aborts
the run instead of silently dropping that fold.

This follows scikit-learn's [LeaveOneGroupOut evaluation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.LeaveOneGroupOut.html)
and [training-only preprocessing guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).
No hyperparameters or thresholds were selected using these held-out outcomes.
Further changes need fresh evaluation data or nested grouped validation.

## Coverage and exclusions

The original dataset eligibility checks remain active. The baseline additionally
requires valid saved pop/contact markers and enough of the saved label window
to cover −0.15 through +1.00 seconds relative to contact. Video markers are
converted with the saved scale, offset, and source PTS origin. It does not
interpolate, extend labels, or read beyond exported windows to fill missing data.
At the sampled boundaries, it tolerates one median sample interval, at most
30 ms. It rejects nonfinite readings, nonmonotonic timestamps, zero acceleration,
clipping, or gaps over 30 ms in and immediately across the feature window.

Three v7 kickflip misses are excluded by these extra checks:

| Example | Reason |
| --- | --- |
| `a7s-018/001cb8fc2c704874ba28d4de48db78c1` | Only 0.971 s after contact in saved label |
| `a7s-025/f94a0d50f71e4482b63048be97ef1274` | Only 0.834 s after contact in saved label |
| `a7s-027/c92548f675844162b910bc5d46c68e5c` | Pop marker precedes the saved attempt window |

Review these in the video before correcting them. None of their source labels
were changed by this experiment. The snapshot also retains the existing
`a7s-018` small-gap exception and the separate clipped-attempt exclusion.

## Score a known contact

Supply **sensor time in seconds**, not the video player's time. The CSV is the
session's original `samples.csv`, with acceleration in m/s² and gyro in rad/s.
For example, this is a training-data smoke check, not an independent evaluation:

```bash
uv run learning/landing.py predict \
  --model .build/landing-kickflip-v1/model.json \
  --samples sessions/a7s-001/samples.csv \
  --contact-s 96.36942686781973 --trick kickflip
```

Output includes `prediction`, `landed_score`, and the decision threshold. The
score is **uncalibrated** and must not be displayed as a percentage confidence
that the rider landed. Missing coverage or invalid sensor data returns
`status: "unknown"` with a reason. A trick outside the model's scope also returns
unknown. Malformed input files or incompatible model artifacts fail explicitly.

## Artifacts and tests

Each run writes:

- `report.md`: readable metrics, fold results, limitations, and feature exclusions.
- `report.json`: detailed counts, fold membership, per-trick results, dataset and
  code hashes, dependency versions, and training example IDs.
- `predictions.csv`: held-out scores and decisions, original labels, session IDs,
  sensor contact times, video names, and video contact times for reviewing errors.
- `features.csv`: the eight log-transformed inputs for each example.
- `model.json`: final fitted scaler and coefficients, feature schema, scope, and
  provenance. No pickle loading is needed for inference.

Snapshots and `.build/` outputs are local and ignored by Git. Source labels and
existing run directories are never overwritten. To run the relevant checks:

```bash
uv run --with numpy==2.4.6 --with scipy==1.16.3 --with scikit-learn==1.8.0 \
  python -m unittest discover -s tests -p 'test_landing.py' -v
python3 -m unittest discover -s tests -p 'test_dataset.py' -v
```

The checks cover session isolation and scaler fitting, clock conversion,
exclusion of background/unknown labels, incomplete windows, sensor faults,
imbalanced metrics, portable-model parity, and immutable output artifacts.
