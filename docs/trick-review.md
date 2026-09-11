# Audio and video trick review

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
3. Choose **Save label**. One background label covers the entire range.
   Untouched suggestions fully contained in it leave the queue, including after
   changing detection settings or reopening the page. Unsaved drafts remain for
   you to resolve. Partially overlapping suggestions remain visible with overlap
   guidance. Saving is blocked if the range overlaps an existing label.

Do not include a trick attempt in a background range, even if it was a bail.
Leave anything you have not watched unlabeled. Skipping suggestions does not
create background labels.

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

If alignment changes, affected labels are flagged for review. Save them again
after checking their windows; downloading labels is blocked until those flags
are resolved. Manual offset/scale changes are exploratory and cannot be used
for saving labels. A stale tab cannot overwrite another tab's or CLI's labels.

## Find and refine pop/contact boundaries

Expand **Onset detection settings** to tune the threshold and permitted gap
(default 0.18–0.90 seconds). Expand **Refine pop/contact markers** to edit those
boundaries, or drag their green/orange markers on the plot. They locate the
action separately from the full attempt window used for training labels.
Threshold changes preserve saved decisions and unsaved drafts in the open tab.
Proposals fully inside a saved, current label window are omitted as duplicate
work. A lower threshold can reveal missed attempts elsewhere.

Audio proposals require human review: speech, footsteps, truck rattle, and
handling the board can all trigger onsets. Some tricks have multiple contacts
or no clear audio pair. Gap settings are search parameters, not universal airtime
limits. **Export review JSON** includes the current suggestions, skips, labels,
and explicit draft status for diagnostics; use **Download saved labels** for
confirmed labels. Save each edited interval before closing the tab.

## Match the LED to the sensor clock

The **Accelerometer + gyroscope** plots are visible beneath the audio timeline
by default, with a sample count, start/finish markers, and the same time window.
Click the motion plot to seek. Expand **Align video and sensor clocks**, then
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

Pairing ranks two sufficiently strong onsets within the gap bounds, favoring
lower activity between them, then selects non-overlapping pairs. The score is
relative strength, **not a confidence probability**. The initial settings produce
21 suggestions on the specified clip; they have not been validated as 21 tricks.
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
