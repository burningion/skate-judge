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

## Find and refine boundaries

The original video plays with its audio above two linked plots: a waveform
overview and a zoomed waveform/onset-strength view. Click either plot to seek.
Choose a proposed pair, then **Play pair + context** to hear the pop and contact
with approach and roll-away. Slow playback and optional looping help with review.

- Adjust **Onset threshold** to include more or fewer sound transients.
- Set the permitted time between pop and contact; defaults are 0.18–0.90 seconds.
- Drag the green start and orange finish markers, edit their time fields, or
  use **I**/**O** to capture the playhead. Add a manual pair for a missed attempt.
- **Save reviewed pair** or **Reject suggestion** persists that decision. Tuning
  thresholds does not erase saved reviews. Unsaved edits remain in the open tab
  and trigger a warning on leaving; save each edited pair before closing.
- **Export review JSON** downloads the current proposals and reviews, with
  explicit suggested/reviewed/rejected/draft status and timestamp metadata.

Space toggles playback; left/right step 1/60 second; N/P choose the next/previous
pair. Steps are time increments, not guaranteed source frames, because webcam
frame intervals can vary. The visible video should decide final boundaries.

These are **pop/contact proposals**, not automatic trick labels or make/bail
judgments. Speech, footsteps, trucks rattling, and handling the board can create
false pairs. Some tricks have multiple contacts or no clear audio pair. The
default gap bounds are adjustable search settings, not universal airtime limits.
Outcome labeling may require a longer interval through roll-away.

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
rejected before writing. A single point assumes no drift.

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
| `analysis.json` | Cached waveform, onsets, timing, algorithm version, source identity |
| `review.json` | Saved human boundaries, rejected proposals, settings, revision |

The original recording and acquisition files remain unchanged. Flash matches
append to `video_sync.jsonl`; reviewed boundaries are separate from training
`labels.jsonl`. The API checks revision numbers to prevent stale tabs from
overwriting another tab's saved review. `--rebuild` recreates derived media/audio
without deleting reviews. `--prepare-only` prepares files without starting a server.

```bash
python3 -m unittest discover -s tests -v
node --test tests/test_trick_review.mjs tests/test_webcam.mjs
```

Audio tests need NumPy/SciPy; when missing, run them with
`uv run --with numpy --with scipy python -m unittest discover -s tests -v`.
Tests cover transient detection, silence, delayed audio, media byte ranges,
review persistence, stale revisions, and LED correspondence validation.
