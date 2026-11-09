# Attempt detection, automatic contact, and landing CNN

`learning/automatic_landing.py` connects the continuous attempt forest to the
landing CNN. It estimates contact directly from the IMU and trains the CNN on
windows centered using that same automatic rule. Scanning requires no video,
trick name, labels, or supplied pop/contact times.

```bash
python3 learning/automatic_landing.py train datasets/a7s-v8 \
  --output .build/automatic-landing-v1
python3 learning/automatic_landing.py scan \
  --model .build/automatic-landing-v1/model.json \
  --samples sessions/a7s-030/samples.csv \
  --output .build/a7s-030-automatic-landings-v1.json
```

Use unused output paths. The example recording is part of training; its scan
checks the saved inference path, not field accuracy. Exact training-file
overlap is flagged even if a CSV is renamed. The model directory must retain
its `landing/model.json` and `landing/weights.npz` files.

## Fixed method

The existing detector scans two-second windows every 0.1 s. It uses the same
300-tree depth-4 forest, 15 rotation-invariant features, annotation weights,
0.5 score threshold, sensor-quality gates, and one-second event suppression
as the [previous full-data scanner](full-classifier.md).

For each detection, the contact rule chooses the largest acceleration magnitude
in that two-second window, breaking exact ties at the earliest sample. It
requires at least 3 g. This is a fixed initial hypothesis, not an assertion
that the largest peak is always the landing: it may select a pop or a bounce.
There is no fallback to a human contact marker or to the event center.

The CNN resamples contact −0.15 through +1.00 s at 200 Hz, using the existing
six-axis, 6,345-parameter architecture. Training uses 100 epochs and seeds
0/1/2, with seed 0 fixed as primary. Channel normalization and class weights
are fitted only to each training fold. No contact-rule, threshold, architecture,
epoch, checkpoint, or seed selection uses held-out results.

Missing impacts, clipping, gaps over 30 ms, or insufficient contact-window
coverage produce `prediction: "unknown"`, an absent landing score, and a
reason. The event remains visible. Unscored portions of a recording are
reported separately; a missing detection there is not negative evidence.

The saved scanner exports the detector as portable JSON trees and loads the
CNN through verified JSON/NPZ artifacts. It checks the CNN metadata and weight
hashes and does not load pickle files. Training verifies serialized inference
against both fitted models before publishing the output directory.

## Evaluation without timing leakage

The v8 snapshot contains 172 eligible attempts and 245 reviewed background
ranges across 11 recordings. It retains the prior explicit `a7s-018` small-gap
exception; individual sensor-window checks remain active. Two annotations have
invalid/out-of-scope action markers, leaving 170 evaluable attempts. Hand plants
are excluded from every detector/CNN fit and reported separately.

Each outer fold holds out a complete recording. Within its remaining recordings,
an inner recording holdout generates the CNN's training events. That detector
is trained without **both** the inner event recording and the outer test
recording. The automatic contact rule then extracts the CNN input. Human
annotations assign the event's outcome and assess detection/timing; they never
choose an inference timestamp. The final model uses the same process with
leave-one-recording-out proposals from all permitted recordings.

Only the highest-scoring matched event per training attempt supplies an outcome
example; duplicate, background, unknown, unreviewed, held-out-trick, and
unusable events do not train the landing CNN. Shifted training windows that
overlap another reviewed attempt are also omitted. Unlike the older CNN
experiment, resampling uses the full continuous recording rather than requiring
the entire input to fit in the exported annotation. Cohort sizes can therefore
differ. All evaluable held-out attempts remain in the end-to-end denominator,
including attempts missing from CNN training or those producing unknown output.

CNN training sees imperfect detector/contact outputs from other recordings.
It does not train on in-sample detector proposals or human-aligned windows.
The audit retains every training example and the recordings excluded from its
proposal detector, as well as every detector fit's annotation IDs.

`end_to_end_balanced_recall` averages the fraction of makes both detected and
correctly called landed and the fraction of misses both detected and correctly
called not landed. Undetected and unknown attempts are failures for this metric.
Conditional landing metrics use only attempts with a landing score, and their
smaller denominator is explicit.

The primary network is also evaluated with the same matched held-out events
recentered at reviewed contacts as a diagnostic. This diagnostic uses the
**automatically trained** network; it is not a model trained on perfect timing,
and it is not an automatic-inference result. Its timing fields retain the
original automatic estimates for comparison. No diagnostic prediction changes
the automatic results or selects a model.

Background false events use the existing eroded full-window exposure rule.
Duplicates and unclassified events remain separate; unreviewed detections are
not silently treated as false. These selected background intervals cannot
establish whole-outing event precision or false-alarm rate. The CNN does not
reject an attempt: an outcome of `not_landed` remains an attempt detection.

These are exploratory same-rider/board results on previously examined sessions,
not new-rider validation. The strongest-impact estimator, offline suppression,
and required post-contact observation do not establish streaming latency or
firmware readiness.

## First results

The saved run is `.build/automatic-landing-v1`. All 164 matched detections
produce valid landing windows. The detector finds 164/169 in-scope attempts
and misses the single held-out hand plant. There is also one unknown outcome
on an unclassified event whose post-contact window encounters clipping.

| In-scope primary-seed result | Previous contact-free forests | Automatic-contact CNN |
| --- | ---: | ---: |
| Attempt detections | 164/169 | 164/169 |
| Makes detected and called landed | 45/66 | 49/66 |
| Misses detected and called not landed | 88/103 | 83/103 |
| End-to-end balanced recall | 76.8% | 77.4% |
| Background events called landed | 13 | 1 |

| Kickflip primary-seed result | Previous contact-free forests | Automatic-contact CNN |
| --- | ---: | ---: |
| Attempt detections | 110/111 | 110/111 |
| Makes called landed | 8/21 | 12/21 |
| Detected misses called landed | 9/89 | 13/89 |
| Misses detected and called not landed | 80/90 | 76/90 |
| End-to-end balanced recall | 63.5% | 70.8% |

One kickflip miss is undetected in both methods. The 89 versus 90 denominators
distinguish outcome errors on detected attempts from end-to-end failures.
The automatic CNN improves make recall while accepting more false-landed
kickflips; its primary kickflip landed precision is only 48%. The three seeds
give kickflip end-to-end balanced recall of 70.8%, 70.1%, and 71.1%; in-scope
results are 77.4%, 79.1%, and 76.0%. Seed 0 remains the saved primary model.
The mixed-trick score is still confounded by tricks with only one outcome.

The detector still produces **16 false events over 5.27 evaluable background
minutes (3.03/minute)**, plus nine duplicate and 257 unclassified events.
Those false detections persist even when their landing prediction changes.
The primary CNN calls one of the 16 background events landed and the other
15 not landed; seeds 1 and 2 call four and one landed respectively. Unclassified
events have not been reviewed, so these counts do not establish overall
precision or full-session false-landed rates.

Across the 164 detected attempts, median absolute contact error is **61.6 ms**;
105/164 (64.0%) are within 100 ms, and the 90th-percentile error is **379 ms**.
Kickflip median error is 57.2 ms, with 75/110 within 100 ms. The strongest-peak
rule is a useful baseline but has substantial timing outliers.

For the automatically trained primary network, substituting reviewed contact
times on the same detections gives 79.3% in-scope end-to-end balanced recall
and 69.4% on kickflips. This diagnostic does not show that perfect contact alone
would fix landing classification; on kickflips its thresholded make recall is
lower even though fewer misses are called landed. It is not directly comparable
to the earlier reviewed-contact-trained CNN's 77.0% conditional kickflip balanced
accuracy: training alignment and the evaluated cohort differ.

This completes a label-free offline detector/contact/CNN scan. Improving contact
timing remains plausible, but background rejection and new-session evaluation
are also needed before automatic judging. No contact rules were revised after
seeing this run.

## Artifacts and tests

The run contains `model.json`, the final `landing/` CNN, primary-seed CNNs under
`folds/`, `report.json`, `report.md`, `events.csv`, `attempts.csv`,
`training_history.csv`, and `reviewed_contact_diagnostic.csv`. The report records
contact errors, outcome metrics by seed and scope, unknown reasons,
background false-landed counts, source hashes, and nested training membership.

```bash
python3 -m unittest discover -s tests -p 'test_automatic_landing.py'
python3 -m unittest discover -s tests -p 'test_attempt_landing.py'
python3 -m unittest discover -s tests -p 'test_landing_cnn.py'
python3 -m unittest discover -s tests -p 'test_attempts.py'
```

Tests cover sensor-only contact extraction, missing/clipped/gapped support,
unknown and missed-attempt accounting, nested recording and hand-plant
exclusion, immutable datasets and outputs, saved inference without annotation
files, integrity failures, and existing scanner/CNN behavior.
