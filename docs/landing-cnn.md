# Raw-signal landing CNN

This is the original single-head experiment. The [expanded implementation](trick-cnn.md)
adds magnitude inputs, a trick head, separable convolutions, actual int8 export,
device benchmarks, and sensor event logging, with new matched-session results.

`learning/landing_cnn.py` implements a small convolutional network for **landed
versus not landed**, using six raw IMU channels around a known contact time.
Makes are positive; bails and falls are negative. The default training scope is
kickflips, matching the original landing baseline. This is an offline experiment,
with saved float32 inference, rather than firmware deployment.

## Train and predict

```bash
uv run learning/landing_cnn.py train datasets/a7s-v7 --output .build/landing-cnn-v1
```

Use an unused output directory for each run. The script declares pinned PyTorch,
NumPy, SciPy, and scikit-learn dependencies; it requires Python 3.11 or 3.12. With
the dependencies already installed, `python3` also works. It processes local
data and never modifies labels or an existing model run.

The default experiment uses seeds **0, 1, and 2**, with **seed 0 designated as
primary before evaluation**. It trains for 100 fixed epochs. `--seed` can be
repeated, with the first seed remaining primary; `--epochs` changes the fixed
training duration. Changing settings after inspecting results is model
development and requires new evaluation data or nested validation.

Score a known contact using sensor time in seconds:

```bash
uv run learning/landing_cnn.py predict \
  --model .build/landing-cnn-v1/model.json \
  --samples sessions/a7s-001/samples.csv \
  --contact-s 96.36942686781973 --trick kickflip
```

This particular example uses training data and is only a usage check. A new
recording should supply its own CSV and contact time. Output includes the
prediction and an **uncalibrated** sigmoid score, not a measured confidence.
Insufficient coverage, bad sensor data, or a trick outside the model's scope
returns `status: "unknown"`. Malformed model artifacts fail explicitly.

`--trick NAME` selects a different training scope; `--all-tricks` runs an
exploratory mixed-trick experiment. Neither adds trick identity as a network
input. The current data have strong trick/outcome imbalance, so mixed-trick
scores need particular care. The saved v1 model is kickflip-only.

## First result

The v7 dataset yields the same **97 attempts across eight sessions** as the
feature baseline: 14 makes and 83 misses. No extra attempts were lost during
resampling. Each session is predicted by a model trained only on the other
sessions. Feature logistic regression and the previously explored shallow
random forest are refitted on precisely the same examples and folds.

| Model | Balanced accuracy | Makes detected | Misses incorrectly called landed | Landed precision | ROC AUC |
| --- | ---: | ---: | ---: | ---: | ---: |
| CNN, primary seed 0 | 57.7% | 3 / 14 | 5 / 83 | 37.5% | 0.641 |
| Feature logistic regression | 61.1% | 9 / 14 | 35 / 83 | 20.5% | 0.707 |
| Feature random forest | 64.8% | 6 / 14 | 11 / 83 | 35.3% | 0.753 |
| Always predict miss | 50.0% | 0 / 14 | 0 / 83 | Undefined | 0.500 |

The other CNN seeds reach **59.5%** and **69.6%** balanced accuracy. Seed 2
catches 7/14 makes with 9 false landed predictions. This spread shows sensitivity
to initialization on the limited data. The three runs reuse the same examples;
they are not independent replications or a confidence interval. The highest
scoring seed was not selected for the saved model.

The first CNN does not establish a consistent improvement over the feature
models. The primary model reduces false landed predictions but misses most
makes. Only 14 makes constrain what can be learned and how precisely performance
can be estimated. These results do not identify data quantity as the sole issue:
window alignment, sensor mounting, model architecture, and the information
available from a board-mounted sensor can also matter.

## Input and architecture

Input is a tensor of shape **6 × 230**: `ax, ay, az, gx, gy, gz`. Acceleration
uses m/s², gyro uses rad/s. The window spans **−0.15 to +1.00 seconds** relative
to reviewed contact. At 200 Hz this requires 230 samples; 130 would cover only
0.65 seconds.

The exporter retains the original timestamps, with observed sampling around
196 Hz. The CNN resamples those timestamps linearly to 200 Hz, using bin-center
times −0.1475, −0.1425, …, +0.9975 seconds. It never extrapolates at an edge or
bridges an acquisition gap over 30 ms. The original clipping, zero-acceleration,
mapping, and coverage checks remain in effect. Interpolation cannot restore
unrecorded detail.

Channel means and standard deviations are fitted over training attempts and
time points only, separately in every fold. The same training statistics
normalize that fold's held-out session. There is no per-attempt centering, so
differences in orientation and offsets remain available to the model. Raw axes
depend on mounting orientation; the model is not guaranteed to work after a
sensor remount. Magnitude channels are not included in this first experiment.

| Layer | Output shape, excluding batch |
| --- | --- |
| Input | 6 × 230 |
| Conv1d, 16 filters, kernel 5, stride 2, padding 2; ReLU | 16 × 115 |
| Conv1d, 24 filters, kernel 5, stride 2, padding 2; ReLU | 24 × 58 |
| Conv1d, 32 filters, kernel 5, stride 2, padding 2; ReLU | 32 × 29 |
| Global average pooling | 32 |
| Dropout 0.1, training only | 32 |
| Dense output | 1 logit |
| Sigmoid, prediction only | 1 score |

This totals **6,345 parameters**. Ordinary convolutions are used; depthwise
convolutions are a possible later comparison. The training loss is
[BCEWithLogitsLoss](https://docs.pytorch.org/docs/2.4/generated/torch.nn.BCEWithLogitsLoss.html),
with positive weight equal to the number of training misses divided by training
makes. AdamW uses learning rate 0.001, weight decay 0.001, and shuffled batches
of 16. CPU training uses deterministic algorithms and recorded seeds. Exact
numerical reproduction across different library versions or platforms is not
assumed; versions are stored with the run.

Training epochs, the architecture, and the 0.5 threshold were fixed before
examining this CNN's results. No held-out labels select epochs or checkpoints.
The auxiliary trick head, augmentation, magnitude channels, and threshold
tuning are deliberately separate experiments.

## Artifacts and deployment boundary

The `.build/landing-cnn-v1/` directory contains:

- `report.md` and `report.json`: held-out results for every seed and baseline,
  fold membership, normalization, exclusions, settings, versions, and hashes.
- `predictions.csv`: every held-out score, its original label, and review identifiers.
- `training_history.csv`: training loss per epoch, fold, and seed; the final fit
  is identified separately as `final_fit_all_data`.
- `model.json` and `weights.npz`: the primary-seed model fitted to all eligible
  attempts, with channel normalization and integrity checks. Arrays load with
  `allow_pickle=False`; a pickled Python model is not executed.

The NPZ file is **27,490 bytes**, containing **25,380 bytes of float32 parameters**
plus container overhead. The 6,272 weight values would occupy 6,272 bytes at int8;
that is a storage estimate, not an exported quantized graph. Biases, quantization
scales, graph metadata, activations, scratch buffers, and runtime code add to
deployment requirements. Int8 conversion and ESP32-S3 memory/latency measurements
remain separate work. The existing firmware is unchanged.

The final all-data model's predictions on its training recordings are not the
held-out scores. It still requires a known contact time. The attempt detector's
event centers are not contact estimates; a complete pipeline must separately
test detector-centered windows, missed attempts, and false detections. Sensor
FSM events and a multi-task trick head are not inputs to this model.

The snapshot, model runs, and weights are local and ignored by Git. Retain the
source sessions and snapshot alongside the code to reproduce the experiment.

## Tests

With the script's dependencies installed:

```bash
python3 -m unittest discover -s tests -p 'test_landing_cnn.py' -v
python3 -m unittest discover -s tests -p 'test_landing.py' -v
python3 -m unittest discover -s tests -p 'test_dataset.py' -v
```

The checks cover timestamp-based interpolation, rejection of extrapolation and
gaps, normalization and class weighting confined to training folds, session
isolation, repeatable seeds, exact parameter count, saved-model prediction
parity, artifact integrity, prediction abstention, and immutable output paths.
