# Full-dataset attempt and landing experiment

The [automatic-contact follow-up](automatic-landing.md) connects the detector to
a CNN trained on automatically aligned inputs, using nested recording holdouts.

The frozen `datasets/a7s-v8` snapshot matches current label hashes and contains
172 eligible attempts and 245 reviewed background ranges across 11 recordings.
It retains the existing explicit `a7s-018` small-gap exception; individual
windows still reject gaps over 30 ms, clipping, and other sensor faults.
No synthetic data, label edits, threshold tuning, or architecture search was
used for these runs.

Both experiments exclude `flatground hand plant` from every training fold and
the final saved model. The one hand-plant make is evaluated separately. Every
test recording is absent from that fold's training data, including background
windows. Other tricks from that recording are also held out. These are
exploratory results on previously examined sessions from one rider and board.

## Continuous inference without supplied timing

```bash
python3 learning/attempt_landing.py train datasets/a7s-v8 \
  --output .build/attempt-landing-v1
python3 learning/attempt_landing.py scan \
  --model .build/attempt-landing-v1/model.json \
  --samples sessions/a7s-030/samples.csv \
  --output .build/a7s-030-attempt-landings.json
```

Use unused output paths. The scan example is a training recording, so it checks
usage and not generalization. Training requires the snapshot's continuous
source recordings and verifies their hashes. Inference reads only the IMU CSV
and saved model, without a video, trick name, annotations, or contact times.

`learning/attempt_landing.py` reuses the existing two-second scanner, its fifteen
rotation-invariant features, 0.1-second stride, sensor-quality checks, and
one-second score-ranked event suppression. One forest distinguishes attempts
from reviewed background. A second forest predicts make versus bail/fall on
the same features, trained only on reviewed attempt windows. Neither predicts
trick identity. The forests each have 300 trees, depth 4, minimum leaf size 3,
and seed 42. Thresholds are fixed at 0.5. Annotation weights give each original
annotation equal weight within its class and balance the classes; overlapping
windows are not counted as independent attempts.

Training windows must contain the reviewed action and lie inside its saved
annotation. Held-out-trick windows become unclassified for training, never
background. Unknown or unreviewed time is not a negative example. The detector
matches one event per attempt under the existing pop/contact ±0.5 s evaluation
rule; extra events remain duplicates. Outcome is assessed at that matched
event, with no manual contact alignment. Undetected attempts count against
end-to-end recall, rather than being credited as correctly predicted misses.

| Scope | Attempt detections | Makes detected and called landed | Misses detected and called not landed | End-to-end balanced recall |
| --- | ---: | ---: | ---: | ---: |
| In-scope tricks | 164/169 | 45/66 | 88/103 | 76.8% |
| Kickflips | 110/111 | 8/21 | 80/90 | 63.5% |
| Held-out hand plant | 0/1 | 0/1 | No examples | Undefined |

Two annotations have invalid or out-of-scope action markers and are explicitly
excluded, leaving 170 evaluable attempts including the hand plant. Valid
attempts without training windows still count in detection evaluation.

There are **16 false detections over 5.27 evaluable background minutes**, or
**3.03/minute**. Thirteen of those events are incorrectly called landed.
An additional nine events are duplicates and 257 occur in unclassified time;
their correctness is unknown. Thus aggregate event precision and whole-outing
false-alarm rate remain unknown. These false detections prevent treating the
scanner's output as a reliable automatic trick/landing log.

The final portable JSON model contains both forests. Its wrapper defines the
outcome tree's binary interpretation as `not_landed`/`landed`; the nested tree
encoding reuses the existing binary attempt-model format. Saved inference
is checked against both fitted scikit-learn forests. `report.json` records
folds, training IDs, configuration, hashes, omissions, and per-trick results;
`attempts.csv` and `events.csv` retain every held-out assessment. Scan output
also reports unscored regions and exact training-file overlap. Suppression is
offline, so this is not a live-latency or firmware integration result.

## CNN with reviewed contact timing

```bash
python3 learning/landing_cnn.py train datasets/a7s-v8 --all-tricks \
  --holdout-trick 'flatground hand plant' --output .build/landing-cnn-all-v1
```

This is the existing six-axis, 6,345-parameter CNN, unchanged in architecture,
window, optimizer, and duration: contact −0.15 through +1.00 seconds, 100 epochs,
seeds 0/1/2, seed 0 primary. The new repeatable `--holdout-trick` option removes
exact normalized trick names from every fit, including normalization, class
weights, feature baselines, and final training. Excluded tricks remain in
session-held-out predictions and are broken out in the JSON report.

Eight attempts cannot provide the required contact window or valid markers,
leaving 164 for this experiment: 163 in-scope and one held-out hand plant.
This differs from the continuous detector's evaluation cohort.

| Contact-aligned model | Kickflip balanced accuracy | Kickflip makes caught | Kickflip misses called landed |
| --- | ---: | ---: | ---: |
| Previous kickflip-only CNN, primary seed | 62.8% | 8/21 | 11/88 |
| All-trick CNN, primary seed 0 | 77.0% | 13/21 | 7/88 |
| All-trick CNN, seed 1 | 77.5% | 13/21 | 6/88 |
| All-trick CNN, seed 2 | 74.0% | 12/21 | 8/88 |

The previous run is `.build/landing-cnn-v2`; all three new evaluations use the
same 109 held-out kickflips as that comparison, with additional other-trick
training examples. The primary all-trick model's in-scope balanced accuracy is
81.5%, but mixed-trick outcome confounding remains: all eligible ollies are
makes and many other classes have only one outcome. The kickflip subset is a
more useful indication of improvement, though only 21 makes limit precision.
The primary CNN calls the held-out hand-plant make not landed. One such example
cannot establish unseen-trick generalization.

This CNN still requires supplied contact timing and does not see the full
airborne trick. It is not connected to the automatic scanner: the scanner's
event centers are not contact times and cannot simply be substituted. A next
experiment should estimate contact from the detected event and train/evaluate
the landing model using that same automatic timing, with whole sessions held
out. New sessions are needed before treating these exploratory improvements as
confirmed. The current results support a useful attempt finder and an improved
contact-aligned landing classifier, with more work needed for automatic judging.

## Verification

```bash
python3 -m unittest discover -s tests -p 'test_attempt_landing.py'
python3 -m unittest discover -s tests -p 'test_landing_cnn.py'
python3 -m unittest discover -s tests -p 'test_attempts.py'
python3 -m unittest discover -s tests -p 'test_trick_cnn.py'
```

Tests cover session and hand-plant exclusion, class/annotation weights, missed
detection accounting, serialized inference parity, label-free scanning, sensor
coverage, existing-output protection, CNN normalization, and existing detector
and multi-task CNN behavior.
