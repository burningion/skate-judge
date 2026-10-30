# Waist/back jump peak experiment

Record video with the existing IMU worn snugly at the waist or lower back. The
experimental jump view finds likely flight intervals and pauses at an estimated
peak frame for each jump. It works offline with the existing firmware, recorder,
session import, and LED alignment. Audio and a trained model are not required.

## First recording

1. Secure the enclosed IMU so it moves with the torso, without bouncing in a
   loose pocket. Keep the sync LED visible to the camera for both sync flashes.
2. Use the [iPhone recorder](../ios/README.md) or the existing
   [desktop recorder](first-recording.md). Record in a new session. Film the
   whole body from the side at 60 fps or higher if available.
3. Stand still briefly, then record about ten ordinary upright jumps with
   pauses. Include some walking and standing to check false detections. Keep
   filming through the landing of the last jump and the ending sync flash.
4. Finish and save. For an iPhone recording, extract the complete exported ZIP
   into a session folder and keep its files together. Review automatically
   verifies and imports the motion data when you open that folder or its video.

Start with upright jumps on the ground. Then try ordinary trampoline bounces
as a separate recording; check the actual separation from and return to the mat.
The current thresholds were tuned on one ground-jump recording, including leg
tucks. They have not been validated across people, mounts, or trampolines.

## Jump to each peak

Replace the example path with your recorded video:

```bash
uv run viz/trick_review.py sessions/jumps-001/clip.mov --jump-peaks
# An extracted iPhone session folder also works:
uv run viz/trick_review.py sessions/jumps-001 --jump-peaks
```

Open the printed local URL. The focused jump view has the video, peak navigation,
an overview, and sensor/sync controls. It does not show the skateboard labeling
queue or load the skateboard attempt model.

1. Under **Match LED flashes**, pause at the first visible lit frame, select
   the corresponding recorded flash, and save the match. Repeat near the end
   to measure drift. Existing saved flash matches load automatically.
2. Choose **Next peak [N]** or **Previous peak [P]**. Each action pauses on the
   nearest indexed video frame to that jump's estimated peak. **Left/right**
   step through actual adjacent frames; **Space** plays or pauses.
3. Blue bands show likely flight intervals; vertical blue lines show estimated
   peaks. Click a flight band or a listed peak to inspect it. The peak list
   follows the visible window; next/previous navigation covers the entire clip.
4. **Detection → More sensitive** (default) includes jumps with interrupted
   unloading, using the surrounding push-off and landing. **Conservative**
   shows only sustained low-g flights from the original detector. Switch freely
   without changing labels or alignment. Additional candidates display
   **Push-off / landing estimate** when selected; check them against the video.

After updating the detector, stop and restart the review command, then reload
the page. Refreshing the browser alone does not rescan the sensor recording.

Peak navigation requires saved LED alignment. Changing the offset or scale by
hand temporarily disables it until those values match the saved alignment.
Slow playback does not change timestamps. Selecting a peak creates no labels.
Regular trick review also offers **Jump peaks (waist/back IMU · experimental)**
in **Events to show**.

## What the estimate means

The detector in `capture/jumps.py` operates on raw acceleration magnitude,
including gravity: approximately 1 g at rest and near 0 g during free flight.
The free-fall behavior is described in [NXP's accelerometer application
note](https://www.nxp.com/docs/en/application-note/AN3151.pdf). It uses the raw
sample stream rather than the firmware's coarsely timed free-fall flags.

The conservative pass retains these experimental defaults:

- Three-sample centered median smoothing; a 0.65 g crossing bounds each interval.
- At least 50 ms continuously below 0.45 g, and a flight interval of 0.18–3 s.
- A load of at least 1.2 g within 250 ms before and after the interval.
- At least 50 Hz median sampling, with no gaps over 30 ms in the flight or
  required surrounding context. All-six-axis exact-zero packets split coverage.
- Incomplete flights at recording boundaries, unfinished or explicitly
  incomplete recordings, and nonfinite sensor data produce no peak suggestions.

The more sensitive pass adds a second strategy for body movement that interrupts
weightlessness at the sensor:

- Smooth over 50 ms, using timestamps rather than a fixed sample count.
- Pair load peaks 0.30–0.95 s apart. Push-off must reach 1.5 g and remain above
  1.4 g for at least 100 ms. The landing must reach 2 g and at least 80% of the
  push-off peak. This screens out brief rebound pulses and weak recovery loads.
- Estimate boundaries at the first downward and last upward 1.2 g crossings
  between those loads. Require at least 50 ms total below 0.75 g, covering at
  least 12% of the estimated interval; unloading may be interrupted.
- Keep the same minimum interval, recording-context, gap, and invalid-data
  checks. Retain the conservative estimate when both passes find the same
  flight; suppress overlapping additional suggestions.

On the local `first-jumps` recording this returns eight candidates, compared with
one from the conservative pass. This is a tuning check on one recording, not an
independent accuracy evaluation. Soft landings or unusually strong takeoffs may
still fail the additional pass; vigorous non-jump motion may resemble a jump.

The peak estimate is `(takeoff + landing) / 2`. Those boundaries are themselves
acceleration-based estimates, not exact foot-contact measurements. The midpoint
assumes ballistic motion and similar center-of-mass height at takeoff and
landing. Body posture, tucks, rotation, mounting motion, or a moving trampoline
surface can change that relationship. A running stride can also resemble a jump.
No position, jump height, or confidence percentage is inferred.

Compare every suggested frame with the video and scan for missed jumps. For the
first trial, note missed/extra detections and how many frames each estimate is
early or late. Tune the defaults from that recording before relying on them.

## Verification

```bash
PYTHONPATH=tests python3 -m unittest test_jumps test_trick_review test_video test_board_motion -q
node --test tests/test_trick_review.mjs
```

Checks cover synthetic flights, interrupted unloading at 50–400 Hz, rebound
rejection, sensitivity switching, repeated bounces, noise, gaps, invalid data,
partial flights, silent video preparation, clock drift and media origins,
nearest-frame selection, navigation, alignment gating, and existing review
behavior. These checks do not establish real-world detection accuracy.
