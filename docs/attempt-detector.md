# Detect trick attempts in continuous IMU recordings

`learning/attempts.py` detects short trick attempts, with makes, bails, and falls
in one positive class. It scans acceleration and gyro data without audio, video,
labels, trick names, or manually supplied contact times at inference. It returns
approximate event timestamps and uncalibrated attempt scores. This is an offline
detector; it does not name tricks, judge landings, or trigger feedback on the board.

The [review page](trick-review.md#potential-trick-attempts-and-raw-board-observations)
loads `.build/attempts-v1/model.json` by default (override with `--attempt-model`)
and shows these learned detections in its event panel. Raw sensor interrupts
remain available in a separate view; their fixed thresholds are not the attempt
model. New labels require a new dataset export and training run before they
affect predictions.
For navigation and playback, the review page groups overlapping detection windows
and windows at most one second apart, without crossing known unscored regions.
This presentation step leaves the saved scan events and evaluation metrics below
unchanged; the queue score filter still uses the original detections.

## Run it

```bash
uv run learning/attempts.py train datasets/a7s-v7 --output .build/attempts-v1
uv run learning/attempts.py scan --model .build/attempts-v1/model.json \
  --samples sessions/a7s-027/samples.csv --output .build/a7s-027-attempts.json
```

Outputs must not already exist. The script declares pinned dependencies for
`uv`; Python 3.11+ with NumPy 2.4.6, SciPy 1.16.3, and scikit-learn 1.8.0 also
works. All processing is local. To train on more labels, first build a new
[dataset snapshot](dataset.md), then use a new run directory.

Training needs the full source recordings because exported `samples.csv` only
contains labeled windows. The command reads the snapshot's original sessions
root, or `--sessions-root /path/to/sessions`, and verifies the continuous CSV,
metadata, and video-alignment hashes before scanning. Snapshot labels remain
authoritative even if newer label revisions exist in the source session.

The scan command needs only a saved model and a CSV with `t_s,ax,ay,az,gx,gy,gz`
columns; additional columns are accepted. Units are sensor seconds, m/s², and
rad/s. Returned `time_s` values are sensor time, not video-player time. Each
event includes its two-second input window and a `score`. Times are approximate
event centers, not estimated pop or contact boundaries.
`scored_center_ranges_s` lists continuous runs of valid window centers so review
filters can distinguish no detection from missing or unusable sensor coverage.

The example scans a training recording. Its output is a usage check, not an
independent performance measurement. `training_session_overlap` flags an exact
sensor-file hash match to a training recording, even if the file was renamed.

## First held-out result

The v7 snapshot has 137 eligible attempts and 228 reviewed background ranges.
Two attempts have unusable or out-of-scope action markers, leaving **135** for
training/evaluation eligibility. Every session is scanned by a model trained
using only the other seven sessions.

| Detector | Attempts caught | Recall | False events in evaluable background | False events / reviewed minute |
| --- | ---: | ---: | ---: | ---: |
| Random forest | 128 / 135 | 94.8% | 16 | 3.29 |
| Fixed motion threshold | 135 / 135 | 100.0% | 233 | 47.89 |

The forest catches **39/45 makes, 87/88 bails, and 2/2 falls**. The two falls
are too few to establish general fall-detection performance. It catches 98/99
kickflips and 14/16 ollies. Four other misses are one each of pop shuvit,
flatground hand plant, front shuv it, and half cab. Coverage of uncommon tricks
is still sparse; this is not evidence that every kind of trick will be detected.

There are **12 duplicate detections** near known attempts and **146 unclassified
detections** elsewhere. They remain visible in `events.csv`. Overall event
precision is unknown because those unclassified detections have not been judged.

Reviewed background totals **12.53 minutes**, but only **4.87 minutes of window
center exposure** is evaluable after requiring each full two-second input to lie
inside reviewed background. The false-event rate uses that smaller denominator.
Fourteen of the sixteen false events occur in `a7s-018`, showing substantial
variation between sessions. Three sessions have no reviewed background at all;
their false-event rate is undefined, not zero.

The detector is useful as an experimental attempt finder. False triggers remain
too frequent to treat each detection as a confirmed trick. Background annotations
are selected intervals, mostly without activity subtypes, rather than fully
reviewed outings. Review the unclassified events and longer continuous background
to establish a realistic full-session false-alarm rate. All recordings identify
the same rider and board; adjacent recordings from one outing can also remain
correlated despite session holdouts.

## Fixed method

The first configuration was chosen before inspecting attempt-detection results.
Two-second windows advance on a sensor-time grid every 0.1 seconds. Fifteen
features describe motion magnitude and its order within each window:

- Whole window: acceleration peak and RMS deviation from 1 g; gyro peak and RMS;
  fraction below 0.5 g and above 2 g; integrated angular-speed magnitude.
- Four consecutive half-second bins: RMS acceleration deviation and RMS gyro
  magnitude in each bin.

These measurements are invariant to a fixed rotation of the sensor coordinate
system. They preserve coarse timing but do not estimate rider pose or board
orientation. No metadata, annotation lengths, or label identities enter the model.

The [random forest](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestClassifier.html)
has 300 trees, maximum depth 4, minimum leaf size 3, square-root feature sampling,
and seed 42. Each original annotation receives equal total training weight within
its class; the two classes have equal total weight. Overlapping windows do not
count as independent labeled attempts. Weights are computed using only each
training fold. All windows from the held-out session are excluded from fitting.

The score threshold is fixed at 0.5. Score-ranked suppression keeps detections
at least one second apart, consolidating nearby high-scoring windows. A sustained
sequence may still generate several events. Suppression uses the complete scan,
so this implementation does not claim real-time latency. Scores are uncalibrated
and should not be displayed as percentage confidence.

The fixed baseline uses the same windows, quality checks, and suppression. It
fires when peak acceleration is at least 3 g **or** peak gyro magnitude is at
least 4 rad/s. No thresholds or forest settings were optimized against these
held-out outcomes. Future tuning needs new evaluation sessions or nested grouped
validation.

## Annotation and evaluation rules

Human pop/contact markers are used only to assign training labels and assess
detected events. They are not supplied to the scanner or model.

- Positive training windows must be fully inside a reviewed attempt label,
  contain its pop/contact interval, and have their center within 0.25 seconds of
  the action midpoint. Makes, bails, and falls use the same target.
- Negative windows must be fully inside a reviewed background label.
- All other windows are unclassified, including pre/post-action padding, unknown
  outcomes, excluded annotations, and unreviewed time. They never become negatives.
- Valid attempts that supply no training window still count in held-out event
  recall. A short annotation does not silently remove a difficult evaluation case.
- An event matches an attempt if its center is between 0.5 seconds before pop
  and 0.5 seconds after contact, clipped to the original annotation. One event
  counts as a match; additional events in that interval count as duplicates.
- A background false event must have its entire input window inside reviewed
  background. Evaluable duration is measured on the grid of valid window centers,
  clipping the 0.1-second exposure cells at the eroded annotation boundaries.
- Detections outside these evaluable regions remain unclassified. They cannot
  establish either precision or a false-event rate for unreviewed time.

The source dataset's eligibility checks remain in effect, including its explicit
`a7s-018` small-gap exception. Scanner windows crossing gaps over 30 ms, clipping,
nonfinite readings, or zero acceleration are skipped. Timestamps must be finite
and strictly increasing. No interpolation fills gaps. Quality diagnostics report
skipped windows; a scan with no valid windows returns `status: "unknown"`.

Two v7 annotations are excluded without editing their source labels:

| Example | Reason |
| --- | --- |
| `a7s-027/c92548f675844162b910bc5d46c68e5c` | Saved pop marker precedes the attempt window |
| `a7s-027/1b65acf9c600419dbc0f8113d603825a` | Pop/contact markers are 11.8 s apart, beyond the 1.5 s action scope |

The second interval needs video review; this implementation does not support
long-duration actions such as arbitrary grinds or manuals. The JSON report lists
all annotation exclusions and labels that supplied no training windows.

## Artifacts and verification

The `.build/attempts-v1/` run contains:

- `report.md` and `report.json`: held-out event metrics, per-session, per-trick,
  and per-outcome results, exclusions, quality, settings, versions, and hashes.
- `events.csv`: held-out detections for both methods, with matched, duplicate,
  reviewed-background, or unclassified assessments.
- `attempts.csv`: every evaluable attempt, its original outcome, and whether
  each detector found it.
- `windows.csv`: all valid scanned windows, held-out scores, training-target
  eligibility (`-1` means unclassified), and background exposure.
- `model.json`: the forest fitted to all eligible training windows, serialized
  as trees and leaf scores. Inference does not load pickle files.

The trained model and held-out predictions serve different purposes: predictions
from the final model on its own training recordings are not independent tests.
Source labels and existing output paths are never overwritten. Dataset and run
artifacts remain local and ignored by Git.

Run checks with the script's dependencies installed:

```bash
python3 -m unittest discover -s tests -p 'test_attempts.py' -v
python3 -m unittest discover -s tests -p 'test_landing.py' -v
python3 -m unittest discover -s tests -p 'test_dataset.py' -v
```

Tests cover continuous scanning, unknown-time handling, background exposure,
session isolation, annotation weighting, clock conversion, sensor faults,
duplicate events, model serialization parity, source integrity, and immutable
outputs.
