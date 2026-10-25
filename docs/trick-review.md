# Audio, motion, and video trick review

Open a closed recording with audio:

```bash
brew install ffmpeg  # one-time, if ffmpeg/ffprobe are not installed
uv run viz/trick_review.py sessions/20260909-124749-8caf7e/webcam-670cc5c86ff8460883e75d5b6a4557f4.webm
```

Open **http://127.0.0.1:8766** on the same computer. The command prints the
address; use `--port 0` to choose a free port. Plain `python3` also works if
NumPy and SciPy are already installed. No cloud service or frontend build is
needed. Ctrl+C stops the server; saved edits remain on disk.

## Label a session quickly

After matching the LED flashes, use the same page to label every attempt. For
example, open the A7S clip with:

```bash
uv run viz/trick_review.py sessions/a7s-001/C0642.MP4
```

1. Select a suggestion and choose **Play attempt** to watch it with approach and
   roll-away. Slow playback and looping help when an outcome is unclear.
2. Check the **Range start / finish** fields. New windows include up to one
   second before the proposed pop and two seconds after contact, clipped to
   sensor coverage and neighboring saved labels. Use **I/O** to mark these
   boundaries at the playhead, or edit the times. Include enough roll-away to
   judge the outcome. The blue outline
   shows the full window; the green/orange markers are the pop/contact pair.
   Saved ranges appear in both the audio and sensor plots and in the overview.
3. Enter the trick name, then choose an outcome and **Save label**, or use a
   shortcut below. The last saved trick name carries forward for repeated
   attempts. Each new suggestion requires an explicit outcome.
4. For a false positive, choose **Not a trick** after watching the whole range.
   This immediately saves its window as `background` with an empty trick name.
   To describe the activity first, select **Not a trick** in the Outcome dropdown,
   choose a **Background activity**, then save.
   You do not need a separate background label for every onset. **Skip suggestion**
   leaves a proposal unlabeled; use it for duplicates or proposals you cannot
   judge. Existing rejections are not automatically converted to background.
5. **Advance after saving** is on by default. **Show only remaining** hides
   completed decisions; uncheck it to revise them. Revisions keep the same label
   ID. Only automatically proposed padding is trimmed; explicit edits are preserved.
   If the action itself or an edited range overlaps a saved label, the editor
   shows its name and times before saving. **Open overlapping saved label** lets
   you revise that label. Skip a duplicate suggestion; for separate attempts,
   adjust their ranges so each includes its own outcome without overlapping.
6. Scan the rest of the video for missed attempts, then use **+ Missed attempt**
   at the playhead and refine its window. Use **+ Background range at playhead**
   for pushing, rolling, carrying, waiting, or other observed background (see below).
   Unreviewed time and untouched suggestions never become background automatically.

## Label background by range

Label the real attempts first, then review stretches between them. Onsets are
places to look, not a checklist to complete.

1. Seek to the start of a stretch with no trick attempt and choose
   **+ Background range at playhead**. This creates an unsaved range starting
   there, initially up to three seconds long, ending before the next saved label
   or the end of sensor coverage.
2. Use **Play / pause** to watch the stretch. Pause at its end and press **O**
   (or **Finish at playhead**). **I** adjusts its start. The range can cover many
   onsets; pop/contact markers do not apply to background and are hidden.
3. Choose **Background activity**: rolling, pushing, carrying, waiting / stationary,
   board handling / pickup / drop, mixed activities, or other. Leave it
   **Unspecified** if you cannot tell. Prefer separate ranges when the activity
   changes; use mixed for a watched stretch containing several activities.
4. Choose **Save label**. One background label covers the entire range.
   Untouched suggestions fully contained in it leave the queue, including after
   changing detection settings or reopening the page. Unsaved drafts remain for
   you to resolve. Partially overlapping suggestions remain visible with overlap
   guidance. Saving is blocked if the range overlaps an existing label.

Do not include a trick attempt in a background range, even if it was a bail.
Leave anything you have not watched unlabeled. Skipping suggestions does not
create background labels.

Activities are human annotations, not automatically detected classes. They save
as an optional `activity` field alongside `outcome: "background"`; they never
become missed tricks. Existing background labels without the field remain
unspecified. Reopen a saved background label to add or correct its activity.
The dataset exporter preserves subtypes and reports their eligible counts.
This supplies examples for a future activity classifier and helps measure which
activities cause false sound triggers.

## Shortcuts and saved labels

| Shortcut | Action |
| --- | --- |
| Space | Play the selected range / pause |
| 1 / 2 / 3 / 4 | Save make / bail / fall / unknown |
| 0 | Save not a trick (`background`) |
| S | Save the chosen outcome |
| X | Skip without a training label |
| I / O | Set range start / finish at the playhead |
| N / P | Next / previous interval |
| Left / right | Previous / next recorded frame |

Shortcuts apply outside text fields, dropdowns, and the native video controls.
Space on a focused button activates that button. Frame stepping uses the review
video's decoded presentation timestamps, including variable frame intervals.
For the A7S 120 fps recording, the measured rate is 119.88 fps (120000/1001),
so adjacent frames are about 8.342 ms apart. The player displays the measured
rate and indexed frame count. Slow playback leaves label/sync timestamps in
original-speed seconds; it does not stretch the alignment.

The first launch indexes the review video's frames and caches `frames.json`.
This does not re-encode the clip or replace saved flash matches and labels.
Subsequent launches reuse the index unless the review media changes.

After updating the review tool, **restart the Python server**, then reload the
browser. Refreshing alone can load new page assets from an older running
server. The page detects incompatible responses and shows restart instructions.

**Make** means the rider lands and maintains control through the roll-away.
**Bail** means they step or jump clear without falling; **fall** means they
visibly fall to the ground. Use **unknown** when the outcome is unjudgeable.

Saving writes directly to the session's `labels.jsonl`, using the measured LED
alignment to map the attempt window onto `samples.csv` time. Reopening the page
restores saved labels and skips. **Download saved labels** exports the latest
revision of each label for this clip as JSONL, including background, original
video timestamps, mapped sensor timestamps, and review provenance. Unsaved
edits, skips, and unreviewed proposals are excluded. Use these intervals with
`samples.csv` to build training examples; this tool does not train a model.
To accumulate labels across clips and sessions, run
`python3 capture/dataset.py status`, then use the
[dataset export command](dataset.md) to create a versioned snapshot.

If alignment changes, affected labels are flagged for review. Save them again
after checking their windows; downloading labels is blocked until those flags
are resolved. Manual offset/scale changes are exploratory and cannot be used
for saving labels. A stale tab cannot overwrite another tab's or CLI's labels.

<a id="recorded-board-events"></a>

## Potential trick attempts and raw board observations

The event panel below the IMU plot defaults to **Potential attempts (learned
model)**. It scans the full `samples.csv` using the saved
[attempt detector](attempt-detector.md), whose decision-tree thresholds were
learned from reviewed attempts (makes, bails, and falls) and background. The
model's score cutoff is 0.50. Raw hardware interrupts do not qualify an attempt.
No audio, video, or labels from the recording under review are needed at inference.

The default model is `.build/attempts-v1/model.json`. To use another trained run:

```bash
uv run viz/trick_review.py sessions/a7s-028/C0771.MP4 \
  --attempt-model .build/attempts-v1/model.json
```

Save the LED flash alignment first so the event bands use the video's clock.
Click a listed event, click the event plot, or use **Previous event** /
**Next event** to inspect the corresponding video. The review page groups
overlapping two-second detection windows, including chains of neighboring windows,
and joins windows separated by at most one second. A band spans the entire group;
its score is the highest original detection score, and its button reports how
many detections were combined. Known gaps in scored sensor coverage keep groups
separate. These bands are not estimated pop/contact boundaries, and scores are
uncalibrated, not confidence percentages.
Selecting a listed event or using the event navigation buttons also selects it
for **Play attempt** / **Space**, even when the audio review queue is empty.
Playback covers the full group, with half a second of lead-in and one second
of follow-through. It stops after that preview, or repeats the entire preview
with **Loop**; it does not stop at the individual windows inside a group.
Context is clipped to the video and neighboring groups. **Next event** advances
to the next group rather than another detection of the same motion.
Raw observations use **Play event**, with one second before and two seconds after
their interval, clipped to the video. Queue drafts are preserved; selecting a
queue suggestion restores its label editor and playback window. While previewing
an event, select a queue suggestion or use **+ Missed attempt** to begin labeling.
Very closely spaced distinct tricks can be grouped, and false detections remain
possible. Navigation does not save human labels or change the audio suggestion
queue. To filter that queue using these detections, choose **Audio filtered by
attempt model** under **Candidate detection settings**. Use the existing label
controls after checking the action in the video.
The queue filter uses original window scores, so a strong detection in a group
does not raise the score of its weaker neighbors or treat the gap as a detection.

Model results are cached in the video's review directory as `attempts.json`.
Changing the sensor file or model invalidates the cache by content hash on the
next launch. Missing models, unfinished imports, and unusable sensor windows
show an unavailable/unknown state, rather than turning raw motion into attempts.
Scans of a model's own training recording are identified in the panel. A new
label does not retrain the model automatically; export a new dataset snapshot
and train a new run to incorporate additional reviewed examples.

Choose **All raw board observations** to inspect the LSM6DSO32's free-fall,
wake-up, and orientation interrupts, including everyday board movement.
These use fixed sensor thresholds and can greatly outnumber actual attempts.

Each band covers the possible event interval between register reads, usually
about 250 ms for the current profile. It is not an exact pop/contact marker.
Free-fall describes the board's sensor reading, not whether the rider fell;
confirm trick and outcome from the video. Events can be missed or combined by
polling. Incomplete recordings show a warning, and older recordings explicitly
say event recording was not enabled instead of claiming zero events.

The tool reads the imported session's `events.jsonl` and scans its IMU on startup.
Restart the Python review server and reload the page after updating or importing a recording.
Existing raw IMU plots, candidate modes, and human label controls continue to
work with recordings that have no board events, as does learned attempt detection. The
[dataset exporter](dataset.md#recorded-board-observations) includes these
observations with each eligible labeled window in new snapshots.

## Find and refine pop/contact boundaries

Expand **Candidate detection settings** to choose **Audio only** (default) or
one of the experimental modes below, tune their thresholds, and set the permitted gap
(default 0.18–0.90 seconds). Expand **Refine pop/contact markers** to edit those
boundaries, or drag their green/orange markers on the plot. They locate the
action separately from the full attempt window used for training labels.
Threshold changes preserve saved decisions and unsaved drafts in the open tab.
Proposals fully inside a saved, current label window are omitted as duplicate
work. A lower threshold can reveal missed attempts elsewhere.

Both audio and motion proposals require human review: speech, footsteps, truck rattle, and
handling the board can all trigger onsets. Some tricks have multiple contacts
or no clear audio pair. Gap settings are search parameters, not universal airtime
limits. **Export review JSON** includes the current suggestions, skips, labels,
and explicit draft status for diagnostics; use **Download saved labels** for
confirmed labels. Save each edited interval before closing the tab.

### Filter audio suggestions using the trained attempt model

Choose **Audio filtered by attempt model** under **Candidate detection settings**.
It keeps audio pop/contact pairs that overlap a two-second learned detection
window with a score at or above **Minimum attempt score**. The default is **0.50**;
raise it up to 1.00 for a stricter filter. Scores are uncalibrated, not confidence
percentages. This filters existing audio suggestions; it does not invent action
boundaries or add detections that have no audio pair. Those remain accessible in
the **Potential trick attempts** event panel.

Nearby supported audio suggestions for the same model-detection group are
consolidated when their action intervals are separated by at most one second.
The strongest audio pair remains the representative, with its original ID and
pop/contact markers; its suggested playback/training window includes the whole
cluster plus the usual context. Explicitly edited window boundaries are preserved.
Grouping uses each detection's own score at the selected cutoff and never crosses
unscored coverage. More distant suggestions remain separate. The queue shows how
many suggestions were grouped, and **Show filtered suggestions** restores the
alternatives for inspection. Saved decisions and human edits remain visible.

Use **Show filtered suggestions** to inspect hidden pairs, or select **Audio
only** to restore them. Original IDs, timing, saved labels, skipped decisions,
and unsaved human edits are preserved. No hidden suggestion becomes a background
label automatically. The mode, cutoff, and visibility settings are saved with
the next label or skip, matching the other detection controls.

LED alignment and an available trained model are required. Missing models and
unverified alignment keep the audio queue visible with an explanation. Where
the model skipped sensor windows, candidates without sufficient scored coverage
also stay visible as **model unchecked**. A successful scan with no matching
detection can hide a candidate only in regions the model actually scored.

### Filter audio suggestions using motion

Choose **Audio filtered by motion (experimental)** to keep an audio candidate
when either an acceleration peak or a gyro peak supports its proposed pop.
The filter looks from 150 ms before to 200 ms after that pop, using a default
relative peak threshold of **0.40**. It does not require both channels or a
second impact at the proposed contact. The original audio candidates, their
IDs, and their boundaries are preserved; the filter only controls visibility.

Use **Show filtered suggestions** to inspect hidden candidates, or switch back
to **Audio only** to restore the full search. Raising **Required motion near pop**
hides more candidates and can hide real attempts. Saved labels and unsaved
human edits stay visible even if their motion would fail the filter. Hidden
candidates are never labeled as background or rejected automatically.

Missing alignment, insufficient sample quality, missing coverage around pop,
zero acceleration readings, and acquisition gaps keep audio candidates visible.
A fully sampled quiet interval is different from missing IMU evidence.

On `a7s-v3`, using the original audio settings (threshold 0.65, gap 0.18–0.90 s):

| Mode | Candidates across all four clips | Saved pops matched, of 67 | Candidates inside reviewed background |
| --- | ---: | ---: | ---: |
| Audio only | 385 | 58 | 92 |
| Motion filter, 0.25 | 283 | 58 | 45 |
| Motion filter, **0.40** | **237** | **58** | **37** |
| Motion filter, 0.65 | 187 | 55 | 28 |

The 0.40 filter removes **55/92 (60%)** of the confirmed background candidates
and **148/385 (38%)** of all suggestions while retaining the 58 previously
matched pops. The nine audio misses remain missed. Nineteen candidates without
enough IMU coverage are retained rather than judged as quiet. The stricter 0.65
setting loses three known attempts, including one make.

This is a retrospective development comparison, not held-out validation. A pop
match is within 150 ms of a saved human pop marker; it does not validate the
contact boundary or landing outcome. All labeled background in this snapshot
comes from `a7s-018`, so background rejection still needs evaluation on other
sessions. Other unmatched candidates remain unclassified, not assumed false
positives. Audio-only remains the default until this has been checked on new
recordings.

The [full audit](candidate-filter-a7s-v3.json) includes per-session results,
make/bail/fall counts, lost label IDs, and the snapshot manifest hash. Reproduce it:

```bash
uv run viz/evaluate_candidates.py datasets/a7s-v3 --output /tmp/candidate-audit.json
```

This requires Node.js and the existing cached review audio. It reads labels from
the immutable snapshot, verifies the original sensor/alignment file hashes and
video identities, and runs the same candidate/filter code as the UI. It does
not alter sessions, labels, or review state. Use `--sessions-root` if the source
sessions have moved.

### Add motion suggestions to audio

With saved LED alignment and usable sensor data, **Audio + extra motion candidates (experimental)**:

- Retains the audio candidates and their timestamps. Nearby acceleration peaks
  (within 100 ms) and angular-speed peaks (within 200 ms) add evidence to them.
- Adds extra candidate pairs supported by both acceleration and gyro activity,
  including impacts without a strong audio onset. Rotation by itself never
  creates a pair. Gyro support is not required for the retained audio candidates.
- Shows each suggestion's evidence as **audio**, **accel**, and/or **gyro**, plus
  peak ticks in the motion plot. These indicate nearby signals, not proof of a pop,
  airborne phase, successful landing, or a calibrated confidence score.

Motion suggestions are disabled for exploratory offsets, missing alignment,
invalid timestamps, or sparse/zero-filled sensor data. They do not span known
sensor gaps over 30 ms. Adjusting the offset away from a saved match falls back
to audio; saved labels and drafts remain intact. Matching flashes regenerates
the suggestions at the measured alignment.

Expect extra false positives from board handling and landing rebounds. Review
the video before accepting a proposed pop/contact pair; overlapping alternatives
can be skipped or disappear inside a subsequently saved label range.

In an exploratory comparison on the 67 eligible attempts in `a7s-v3`, combined
mode matched 59 saved pops versus 58 for audio alone (within 150 ms of the saved
pop marker), while increasing total suggestions from 385 to 436 across the four
clips. Six additional pairs fell wholly inside reviewed background ranges.
Unreviewed stretches prevent treating all extra suggestions as false positives.
This was development data, not a held-out evaluation, and does not demonstrate
better review efficiency. Audio-only remains the default; saved mode choices are
restored when reopening a review.

## Match the LED to the sensor clock

The **Accelerometer + gyroscope** plots are visible beneath the audio timeline
by default, with a sample count, start/finish markers, and the same time window.
Click the motion plot to seek. **LED sync & sensors** beneath the video jumps
to the plots and the expanded **Match LED flashes · align video and sensors** controls. Then
pause at the first frame where a known
LED flash lights up, select its recorded sync ID, and click **Match flash to
current frame**. Match another flash near the end to estimate drift. The app
stores correspondences in the existing session `video_sync.jsonl` and uses the
same alignment validation as `capture/session.py align`. Re-matching an ID
updates its correspondence with an appended revision. Inconsistent fits are
rejected before writing. A single point assumes no drift. The terminal asks for
a second flash only when one distinct flash is matched; with two or more it
reports the active drift correction. Re-matching a flash updates its existing
point rather than counting it as an additional flash.

If sensor data is missing, this section stays visible and identifies the missing
`samples.csv` or `events.jsonl`. Download or import the onboard recording into
the same session folder (see [onboard recovery](onboard-recording.md#record-and-download)),
then restart the review server and reload the page. Flash matching requires recorded
LED events and a closed/imported session. Recovered recordings retain acquisition
and storage warnings, including an incomplete recording after a failed stop.

The motion plots show raw acceleration and angular-speed magnitudes.
When no measured alignment exists, the suggested offset comes only from file
creation times and is explicitly **unaligned / exploratory**. Manually adjusting
offset/scale also gives an exploratory overlay. Sync matches must refer to
flashes actually visible in the selected clip; flashes from another clip cannot
establish this clip's alignment by themselves.

The viewer leaves gaps over 30 ms unconnected and reports sensor-quality issues.
LED sync aligns clocks; it cannot recover samples lost or corrupted at acquisition.
For the September 9 session, the data averages about 8 Hz instead of the intended
100 Hz, and about 55% of samples have all-zero acceleration. There are no missing
sequence numbers. That is a reason to investigate acquisition before using this
IMU stream as ground truth for audio/video timing.

## Method and timing

FFmpeg makes a seekable stream-copy review file, adding duration and an index
to browser WebM recordings. The original file is preserved. Both tracks receive
the same timestamp shift. Mono audio is decoded at 16 kHz; leading silence and
timestamp gaps are retained. The relationship is recorded explicitly:

```text
original_media_pts = review_video_seconds + source_pts_origin_s
session_seconds = scale * original_media_pts + offset
```

The audio features use centered 32 ms FFT windows with 10 ms steps. Positive
changes in log spectral magnitude between 180 Hz and 7.5 kHz, above a one-second
local median, emphasize transients. Strength is scaled to the clip's 99.5th
percentile. This is a spectral-flux baseline, following the general approach
described in [librosa's onset-strength documentation](https://librosa.org/doc-playground/latest/_modules/librosa/onset.html).
Timestamp padding uses [FFmpeg's audio resampler](https://www.ffmpeg.org/ffmpeg-resampler.html).

The motion heuristic uses raw acceleration and angular-speed magnitudes in
sensor seconds. It finds peaks above a one-second local median, with minimum
excesses of 15 m/s² for acceleration and 2 rad/s for gyro, separated by at least
approximately 100 ms per channel. Strength is normalized by the larger of that
floor and the channel's 99.5th percentile excess. Continuous runs are processed
separately across acquisition gaps. These are initial inspection settings;
sensor mounting, riding style, and surface can change their usefulness.

Pairing ranks two sufficiently strong onsets within the gap bounds, favoring
lower activity between them, then selects non-overlapping pairs. The score is
relative strength, **not a confidence probability**.
Sound travel, microphone processing, and windowing limit boundary precision.

## Files and checks

Derived files live in `sessions/<session>/review/<video-stem>/`:

| File | Contents |
| --- | --- |
| `media.webm` or `media.mp4` | Seekable review copy |
| `frames.json` | Cached video frame timestamps, frame rate, and review-media identity |
| `analysis.json` | Cached waveform, onsets, timing, algorithm version, source identity |
| `review.json` | Skipped proposals, legacy boundary reviews, settings, revision |

The original recording and acquisition files remain unchanged. Flash matches
append to `video_sync.jsonl`. Human outcome/background labels and their revisions
append to the session's `labels.jsonl`; they retain both the full attempt window
and pop/contact markers. The latest row per label ID is authoritative. Legacy
boundary-only reviews remain separate until an outcome is explicitly saved.
The API checks review revisions, label-file revisions, and measured alignment
to prevent stale saves; local file locks serialize writers. `--rebuild` recreates derived media/audio
without deleting reviews. `--prepare-only` prepares files without starting a server.

```bash
python3 -m unittest discover -s tests -v
node --test tests/test_trick_review.mjs tests/test_webcam.mjs
```

Audio tests need NumPy/SciPy; when missing, run them with
`uv run --with numpy --with scipy python -m unittest discover -s tests -v`.
Tests cover transient detection, silence, delayed audio, media byte ranges,
review persistence, UI outcome/background actions, skip semantics, append-only label
corrections, stale revisions, alignment changes, and LED correspondence validation.
