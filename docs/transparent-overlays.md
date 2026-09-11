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
of attempts. Each clip starts at its saved range start. Playback is real time by
default. Duration is rounded up to a whole output frame (less than 1/30 second
at the default rate); the original timestamp ranges remain in the manifest.

## Controls

```bash
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

## What the skateboard motion represents

The model's **rotation is estimated from the recorded IMU**. The gyroscope is
integrated using measured sample intervals, with a Mahony-style gravity
correction only when acceleration is near gravity and rotation is slow. Quiet
samples from the first two seconds provide an initial gyro-bias estimate when
available. The implementation follows the same approach as the live viewer;
see the [Mahony filter equations](https://ahrs.readthedocs.io/en/latest/filters/mahony.html).

The skateboard has **fixed position**. No jump height, horizontal travel, wheel
speed, or rider motion is invented. Without a magnetometer, heading can drift;
each clip begins with its heading facing the presentation camera. Roll and
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
missing samples, latest label revisions, and stale alignment. The generated
MOVs should also be decoded and checked for nonconstant alpha before delivery.
