# Transparent skateboard and sensor overlays

Render the saved video labels as editing-ready graphics:

```bash
uv run viz/render_overlay.py sessions/a7s-001 --video C0642.MP4 \
  --nose-axis x --up-axis=-z --width 1080 --height 1920 \
  --output sessions/a7s-001/overlays/C0642-vertical
```

FFmpeg must be installed (`brew install ffmpeg`). NumPy and Pillow are declared
in the script for `uv`; plain `python3` works when they are already installed.
No browser, display server, Blender, or cloud rendering is required.

The command above exports **1080×1920 vertical video at 30 fps** for DaVinci
Resolve. The upper 840 pixels stay clear; the skateboard sits above three graphs
in the lower part of the frame. Without width/height options, the default is
1920×1080 landscape with the skateboard beside the graphs. The layout switches
automatically for portrait dimensions. Graph panels are
translucent for readability over footage. Each includes a synchronized
playhead, and the motion panels show instantaneous sensor X/Y/Z values. Saved
pop/contact markers and the human trick/outcome label come from the review.

## Files and editing

Files are written to `sessions/<session>/overlays/<video-stem>/`:

- One **ProRes 4444 `.mov`** for each saved attempt (latest revision only).
- With **`--reel`**, **`attempts-reel.mov`** contains the clips concatenated in chronological order,
  with no re-encoding. Background labels and unlabeled stretches are excluded.
- A transparent `.png` still and a `-preview.jpg` for each clip. The JPEG has
  a checkerboard background for inspection; it is not the overlay asset.
- **`manifest.json`**: source hashes, saved labels, source/video/sensor clock
  mapping, clip boundaries, frame counts, speed, mounting, and filter settings.

In Resolve, use a **1080×1920** timeline and place a MOV on a video track above
your talking-head footage. The alpha channel
reveals the video below it; no chroma key is needed. If an editor asks for alpha
interpretation, select **straight/unmatted**. The files contain no audio, so your
narration remains independent. Some players display transparency as black;
check compositing inside the editor instead. ProRes MOV decoding is listed in
[Blackmagic's supported formats](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_20_Supported_Codec_List.pdf).

Use the individual attempts for editing, or add `--reel` for a continuous sequence
of attempts. Each clip starts at its saved range start unless context is requested. Playback is real time by
default. Duration is rounded up to a whole output frame (less than 1/30 second
at the default rate); the original timestamp ranges remain in the manifest.

## Controls

```bash
# Three seconds before and after, matching a 24 fps Resolve timeline
uv run viz/render_overlay.py sessions/a7s-001 --video C0642.MP4 \
  --nose-axis x --up-axis=-z --flat-syncs --width 1080 --height 1920 \
  --fps 24 --before 3 --after 3 \
  --output sessions/a7s-001/overlays/C0642-vertical-context3

# Four-times-slower motion, including the graphs and playhead
uv run viz/render_overlay.py sessions/a7s-001 --video C0642.MP4 \
  --nose-axis x --up-axis=-z --width 1080 --height 1920 \
  --speed .25 --output sessions/a7s-001/overlays/slow

# One attempt, using its saved label ID or unique prefix
uv run viz/render_overlay.py sessions/a7s-001 --label 9dd4fef8

# The entire sensor-covered video interval, including time between attempts
uv run viz/render_overlay.py sessions/a7s-001 --mode session \
  --output sessions/a7s-001/overlays/session

# Inspect stills before exporting movies
uv run viz/render_overlay.py sessions/a7s-001 --preview-only \
  --output sessions/a7s-001/overlays/stills
```

Other options include `--fps 60`, `--width 3840 --height 2160`, `--font`, and
`--onset-threshold .65`. Larger dimensions increase export time and file size.
The graphics scale uniformly within the chosen portrait or landscape layout and
stay at the bottom of the canvas. `--antialias`
controls supersampling (1–3, default 2). Existing generated files with matching
names are replaced on rerun; capture files, sync matches, and labels are read only.

`--before` and `--after` add context in original-video seconds (both default to
zero), capped at the available video and sensor coverage. The trick name,
attempt number, and outcome appear only inside the original saved attempt window;
they disappear during added context. The board, graphs, and timestamp remain
visible throughout. Saved pop/contact markers still mark the physical event
inside that window. Padding does not change labels or the board's presentation
heading reference. The manifest records both the exported range and label range.

Adjacent padded files may overlap in source time. When inserting them into one
Resolve track, cut at the midpoint of shared context and trim the neighboring
clips there. This keeps the full attempt windows and continuous context visible
without stacking two translucent overlays. Keep the full files for extra editing
handles, and place trimmed clips using their corresponding source in-points.

## What the skateboard motion represents

The model's **rotation is estimated from the recorded IMU**. The gyroscope is
integrated using measured sample intervals, with a Mahony-style gravity
correction only when acceleration is near gravity and rotation is slow. Quiet
samples from the first two seconds provide an initial gyro-bias estimate when
available. The implementation follows the same approach as the live viewer;
see the [Mahony filter equations](https://ahrs.readthedocs.io/en/latest/filters/mahony.html).

The skateboard has **fixed position**. No jump height, horizontal travel, wheel
speed, or rider motion is invented. Without a magnetometer, heading can drift;
each clip uses the saved attempt's starting heading as its presentation-camera
reference (the clip start in session mode). Roll and
pitch are preserved, including full flips. The presentation camera pulls back
when needed to keep the model clear of captions. This is an explanatory orientation
visualization, not a motion-capture reconstruction of the trick.

The A7S recording uses sensor X along the deck and Y across it. Resting gravity
points toward sensor −Z, so `--nose-axis x --up-axis=-z` maps the model correctly.
The sign of the nose direction can be reversed with `--nose-axis=-x`. Without
explicit axes, the script infers deck-up from initial gravity and the long axis
from the dominant kickflip rotation axis when labeled kickflips exist. This
cannot distinguish the nose from the tail; the assumption is recorded in the
manifest. The displayed X/Y/Z numbers always remain in the original sensor frame.

### Known-flat flash calibration

When the board was **level and still at every matched flash**, add `--flat-syncs`:

```bash
uv run viz/render_overlay.py sessions/a7s-001 --video C0642.MP4 \
  --nose-axis x --up-axis=-z --flat-syncs --width 1080 --height 1920 \
  --output sessions/a7s-001/overlays/C0642-vertical-flat-sync
```

This is an explicit recording assumption; matching a flash for timing alone
does not enable it. A board resting on a slope is not necessarily level.
The estimator checks a one-second sensor window centered on each latest saved
match and rejects moving windows, missing coverage, or inconsistent deck-up.
Those checks support the assumption but cannot prove that the board was level
or distinguish stillness from smooth constant-speed rolling.

The windows establish an empirical deck-level reference and estimate the gyro's
zero-rate bias at each flash. Bias is interpolated between flashes. After gyro
integration and gated gravity correction, a small tilt correction is interpolated
between the flat constraints; corrections and bias are held constant outside
the first/last anchor. Full rotations remain in the quaternion trajectory.
The manifest records each window's statistics, before/after tilt, and the
effective board-to-sensor transform. Raw sensor values and graph traces stay
in their original frame.

For `a7s-001 / C0642.MP4`, the original estimate tilted the deck about **1.4° and
2.0°** at the two declared-flat flashes. The corrected estimate satisfies the
flat constraints. A near-zero residual there is **enforced**, not an independent
measurement of accuracy during tricks. This is a modest reference correction;
the two flashes do not validate all motion over the roughly 110 seconds between
them. One repeated flat pose also cannot separate accelerometer bias, scale
error, and physical sensor mounting tilt.

This option supplies no absolute heading or position measurement and does not
estimate jump height. More known-level, still moments near individual attempts
would provide closer orientation anchors. Inferring zero linear velocity needs
additional evidence; see [OpenVINS' discussion of inertial stillness detection](https://docs.openvins.com/update-zerovelocity.html).

## Timing and data integrity

```text
original_video_seconds = review_seconds + source_pts_origin_s
sensor_seconds = scale × original_video_seconds + offset
review_seconds = clip_start + output_frame / fps × playback_speed
```

The saved LED mapping drives both the animation and the graphs. Rendering
rejects stale label alignment, changed source media, nonfinite or out-of-order
sensor data, and labels outside sensor coverage. Gaps longer than 30 ms are
left unconnected in the graphs; the board is hidden during the gap and no
missing rotation is synthesized. Attitude uncertainty after a gap remains.

The graph traces show raw magnitudes and the cached review onset strength,
with common full-session scales so clips are comparable. Downsampling retains
local minima and maxima to preserve brief impacts. Audio onsets remain candidate
transients, not independently verified tricks. Only saved labels name a trick.

Video is encoded using FFmpeg's `prores_ks`, profile `4444`, with a 16-bit alpha
plane. See [FFmpeg's ProRes encoder documentation](https://www.ffmpeg.org/ffmpeg-codecs.html#ProRes).

```bash
python3 -m unittest discover -s tests -p test_overlay.py -v
```

Checks cover full flips, nonuniform sensor timing, mounting, stationary bias,
flat anchors with changing gyro bias, rejected invalid anchors, missing samples,
latest label/sync revisions, and stale alignment. The generated
MOVs should also be decoded and checked for nonconstant alpha before delivery.
