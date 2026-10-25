# Accumulate a labeled dataset

The [multi-task CNN guide](trick-cnn.md) covers matched-session comparisons,
training-only int8 calibration, and the limits of the current trick/outcome labels.

The session directories are the source dataset. Keep each recording's
`samples.csv`, `events.jsonl`, `metadata.json`, raw `.bin`, video, `video_sync.jsonl`, and
`labels.jsonl` together. Saving in the review UI already accumulates labels;
there is no need to download and combine the clip exports manually. Label
corrections append a revision with the same ID. Count the last revision per ID,
not lines in `labels.jsonl`.

## Collection loop

1. Record into a new session directory such as `sessions/a7s-005`.
2. Open its video with `uv run viz/trick_review.py sessions/a7s-005/<video>.MP4`.
   Match the LED flashes, review each attempt, save its trick and outcome, and
   label observed background ranges. Skipped proposals remain unlabeled.
3. Run `python3 capture/dataset.py status` from the repository root. It scans
   every `sessions/a7s-*` directory and reports unique saved labels, eligible
   attempts, background, unknown outcomes, exclusions, and trick/outcome counts.
4. Build a snapshot when you want to train or compare versions:

   ```bash
   python3 capture/dataset.py build --output datasets/a7s-v1
   ```

Continue labeling in the original sessions. Status always reads their current
saved state; exports are frozen. Build `datasets/a7s-v2` after adding data or
correcting labels. Existing snapshot directories cannot be overwritten, so a
model can refer to the exact dataset version it used. No training is implemented
by this command, and export is explicitly run rather than automatic on each save.

Use `--sessions /path/to/sessions --pattern 'pilot-*'` to select another collection.
The default deliberately omits bench recordings outside `a7s-*`. Python 3.10+
is sufficient; no scientific packages or cloud service are required.

To refresh all saved labels when the collection also contains unfinished or
failed unlabeled recordings, use:

```bash
python3 capture/dataset.py status --labeled-only
python3 capture/dataset.py build --labeled-only --output datasets/a7s-v2
```

Choose a new, unused version directory for each build. `--labeled-only` skips
directories with missing or empty `labels.jsonl`; the report records them in
`skipped_unlabeled_sessions`. Malformed label files and missing or unreadable
sensor data in labeled sessions still block the build. All eligibility rules
below remain in effect, so compare saved, eligible, and excluded counts after
every refresh. Excluded labels remain in the manifest but have no exported
sensor samples. Run `status` without the flag to inspect the entire collection.

## Export format and eligibility

| File | Contents |
| --- | --- |
| `manifest.jsonl` | One row per latest saved label, including excluded labels and their reasons; original label/provenance, session ID, rider, board, sample count, eligibility flags, and board-event availability/count |
| `samples.csv` | Original six-axis samples for eligible windows, joined to the manifest by `example_id`, with sensor and window-relative seconds |
| `motion_events.jsonl` | Recorded board observations overlapping eligible windows, joined by `example_id`, with full timing bounds, raw flags, and detector profile |
| `report.json` | Counts, session issues, source paths, and SHA-256 hashes of metadata, labels, alignment, sensor CSV, and source `events.jsonl` |
| `README.txt` | Units, joins, and evaluation reminders |

`example_id` combines session name and label ID. Windows include samples at
`start_s <= t_s < end_s`; adjacent windows do not share an endpoint sample.
Acceleration is m/s² and angular velocity is rad/s. Samples retain their
recorded rate, axes, and variable window lengths; export does not resample,
normalize mounting orientation, extract features, or assign train/test splits.

Only video-reviewed labels with current measured alignment and available source
video qualify. Synthetic/unverified or unclosed recordings and onboard sessions
whose quality check is not usable are excluded by default; the explicit
sample-gap exception below can admit complete recordings with that sole issue.
Other exclusions include stale
video identity, out-of-coverage or overlapping labels, windows with fewer than
two samples, gaps over 30 ms (including boundary gaps), zero acceleration
samples, and channels within 1% of the configured LSM6DSO32 raw full scale.
Nonfinite channels or nonmonotonic timestamps make a session unreadable. These
are conservative integrity checks, not verification that a human label is right.
Clipping thresholds assume the project's ±32 g / ±2000 dps configuration.

Unknown outcomes remain in the manifest and, if eligible, in the sensor export;
they have `outcome_training: false`. For supervised outcome training, select
`outcome_training: true`, then join the samples. Background ranges remain a
separate class. Neither background nor unknown counts toward the attempt total.
Unreviewed time, skipped proposals, and audio suggestions never create examples.

A single flash match is accepted with a warning because drift is unmeasured;
match a second flash when possible. A missing trick name is also reported.
Fix stale labels in the review UI and save again. Session-level quality flags
require investigating the recording; the exporter does not clear them. In
particular, a flash-capacity stop should be reviewed before deciding whether
any recorded portion can be salvaged. Unreadable source files block building a
snapshot rather than silently dropping a session.

Keep and back up original session folders: the export contains labeled IMU
windows, not copies of videos, raw logs, or complete continuous recordings.
Both `sessions/` and `datasets/` are excluded from Git and are not uploaded.

### Recorded board observations

New snapshots include the LSM6DSO32 free-fall, wake-up, and orientation flags
when the recording firmware enabled them. The importer writes these to the
session's `events.jsonl`; no extra annotation step is needed. The review UI
also [shows these observations alongside video](trick-review.md#recorded-board-events).
Human trick/outcome labels remain authoritative, and the current forest/CNN
training code continues to use raw IMU data without these optional flags.

In `motion_events.jsonl`, `earliest_s`, `observed_s`, and `latest_s` use the
session sensor clock; `relative_*_s` subtract the label's start time. The bounds
cover the previous register read through completion of the current read.
They are not exact contact times. Raw register values, device timestamps,
detector profile, and polling interval are retained for later analysis.

An observation is included when its uncertainty interval overlaps an eligible
label window. It can appear for both adjacent windows with the same `event_id`;
deduplicate by `(session_id, event_id)` when counting across windows.
`fully_contained` identifies intervals entirely inside the window.
`available_by_window_end` is false when the register read did not finish before
the window ended; exclude such observations when evaluating features that must
be available by that time. Bounds are never clipped to make them fit a label.

The manifest's `board_motion.count` is `null` when event recording was disabled,
missing, unverified, incomplete, or the label was excluded. Zero means a complete
enabled recording had no observed packets overlapping that window; it does not
prove no physical motion occurred. Polling can miss events or combine several
into one packet. `observed_packets` retains the number actually found when an
eligible window has only partial event data. Session availability and warnings
appear in `report.json`, including whether the event file existed.

Older recordings cannot acquire these flags retrospectively. Their raw samples
and eligible labels still export normally, and existing snapshots stay frozen.
Build a new snapshot to get the added fields and event file.

### Accepting a session with small sample gaps

The onboard importer flags `sample_gaps_over_10ms` if any interval between
consecutive sensor samples exceeds 10 ms. By default this marks the entire
session unusable, even if most labeled windows are unaffected. The exporter
also checks each window independently, rejecting gaps over 30 ms, including
gaps crossing a window boundary.

After inspecting the recording, use a session-specific exception to accept
the importer gap flag while retaining the window checks:

```bash
python3 capture/dataset.py status --labeled-only --allow-sample-gaps a7s-018
python3 capture/dataset.py build --labeled-only --allow-sample-gaps a7s-018 \
  --output datasets/a7s-v3
```

Use an unused output directory on every refresh and repeat the option to retain
the exception in future snapshots. It applies only if the named session is
complete and its only onboard issue is `sample_gaps_over_10ms`. Other acquisition
or storage faults still exclude the session. All alignment, coverage, clipping,
and per-window gap checks remain active; no samples are interpolated or changed.
The source metadata stays intact, `report.json` records `allow_sample_gaps`, and
session and label warnings identify the exception. Repeat the option for another
reviewed session; unlisted sessions keep the original policy.

For `a7s-018`, ten intervals were approximately 10.1–10.5 ms, compared with a
typical 5.1 ms interval. The recording completed with no reported I/O errors or
FIFO overruns and no gaps over 30 ms. The exception includes its valid windows;
the landed backside 180 remains excluded for sensor clipping near full scale.

## Planning the first model

Treat **100 labeled attempts as a collection milestone**, not a required minimum
or proof that a model is ready. Start building the feature/training pipeline
earlier; use more varied data to evaluate whether it works. For a small pilot,
focus on one or two tricks and collect both makes and natural misses for each
across several sessions. Track trick × outcome counts, not just the grand total.
If every ollie is a make and every kickflip a bail, an outcome classifier may
learn the trick instead of whether it was landed.

Collect observed background such as pushing, rolling, carrying, and waiting.
The review UI's **Background activity** selector saves these as optional
`activity` subtypes of `background`, including handling, mixed, and other.
Old labels remain unspecified until reviewed; nothing is inferred from unlabeled
time. `report.json` includes eligible `background_activities` counts, and the
manifest preserves the subtype. Subtypes do not increase the attempt count.
Keep makes, bails, and falls separate in source labels even if an initial model
combines bail/fall as a miss. Do not stage falls to balance a class.

Hold out whole sessions; use rider groups when testing new-rider generalization.
Randomly splitting sensor samples or neighboring windows would put closely
related motion into both training and test data. See
[scikit-learn's grouped cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data).
Keep continuous held-out recordings to measure false triggers as well as
accuracy on preselected trick windows.

Once several labeled sessions contain the classes you want to predict, compare
a simple feature-based baseline at increasing dataset sizes. Use validation
performance and a [learning curve](https://scikit-learn.org/stable/modules/learning_curve.html)
to decide what more data helps. Board motion may identify a trick while leaving
rider outcome ambiguous; there is no promised dataset size that solves that.

Offline implementations are available. The [attempt detector](attempt-detector.md)
combines makes, bails, and falls into one class and scans continuous recordings
against reviewed background. The [landing baseline](landing-baseline.md) predicts
landed versus not landed for known kickflip contacts. The [raw-signal CNN](landing-cnn.md)
compares a small convolutional model with those feature baselines on the same
windows and sessions. The guides include preprocessing, held-out evaluation,
commands, and initial results.
