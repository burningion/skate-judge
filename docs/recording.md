# From board motion to an audio trigger

The first question to measure is whether the board's motion separates a
successful roll-away from a missed trick well enough for the intended sound
effect. A rider can leave the board while it continues rolling upright; an
IMU cannot directly observe the rider's feet or body contacting the ground.
Keep bails and falls separate in the labels, even if both eventually trigger
the same audio. If actual body falls must be detected, persistent ambiguity
may require a camera during use or an additional rider/contact sensor.

The current implementation collects the evidence needed to make that decision.
It has no trained classifier or automatic audio trigger yet.

## Hardware and mounting

Use the existing ESP32-S3 and LSM6DSO32. Its accelerometer supports ±32 g and
gyroscope ±2000 dps nominal; those are the firmware's configured ranges
([ST sensor specifications](https://www.st.com/en/mems-and-sensors/lsm6dso32.html)).
The sketch reads at approximately 100 Hz from a sensor configured at 208 Hz.
This is polling, without a sensor FIFO or data-ready synchronization; short
impact peaks can be missed. Preserve the raw readings and inspect timing and
clipping before deciding whether to implement faster FIFO acquisition.

Rigidly attach the IMU so it follows the deck and cannot rotate independently.
Record its orientation and position in `--mounting`; moving it changes the
data distribution. Secure the board and wiring in an enclosure clear of wheels,
trucks, and the surfaces used for slides. Keep the battery protected from
crushing and abrasion. Battery connector, polarity, charging support, and LED
pins must be checked against the exact development board; ESP32-S3 identifies
the chip, not those board features. Runtime must be measured on the final setup.

For a first wireless prototype, enable the ESP32's local access point:

```bash
SKATE_WIFI=1 ./flash.sh
```

Join `SkateJudge-XXXX` on the laptop using password `skate-judge`. This is a
shared prototype password, configurable in the sketch's `WiFi.softAP` call.
The network is local and provides no internet connection. The independent
phone camera can record without joining it. Streaming uses the ESP32's
[UDP network API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/network.html).
There is no onboard recording or recovery of missed wireless packets.

For sync, choose a visible LED and set its actual GPIO at build time:

```bash
# Replace N with the verified physical GPIO number for your hardware.
SKATE_WIFI=1 SYNC_LED_PIN=N ./flash.sh

# For a single WS2812 / NeoPixel instead of a simple GPIO LED:
SKATE_WIFI=1 SYNC_LED_PIN=N SYNC_LED_RGB=1 ./flash.sh

# For a simple active-low LED, add SYNC_LED_ACTIVE_LOW=1.
```

No LED pin is selected by default. A simple external LED needs an appropriate
current-limiting resistor. The RGB implementation drives one pixel, not an
entire strip. Confirm any pixel power-enable wiring separately. Avoid USB,
flash/PSRAM, and other board-reserved pins; the sketch additionally disables
the LED when it conflicts with the discovered IMU wiring. If the IMU is absent
at boot, restart after correcting the wiring to enable the LED.

## Record and label attempts

Point the phone or webcam so both feet, the board, the landing area, and the
sync LED are visible. Record at normal playback speed; keep the original video.
The software does not operate the camera or automatically detect flashes.
At 60 frames per second, selecting the first bright frame has roughly a
one-frame timing uncertainty (about 17 ms), plus exposure and rolling-shutter
effects. Visibility and a continuous recording matter more than resolution.

```bash
uv run capture/session.py record --udp 192.168.4.1 \
  --output sessions/first-session \
  --rider rider-01 --board deck-01 --surface smooth-concrete \
  --mounting 'under deck near front truck, sensor X points toward nose' \
  --video IMG_1234.MOV
```

The recorder requests a flash once samples arrive, then every 30 seconds.
Enter `sync` for another. It prints the acknowledged sync ID and whether the
firmware actually enabled an LED. Network command delays do not become video
offsets: alignment uses the board's timestamp at the LED write. A lost sync
packet has no usable correspondence; use a flash whose ID was recorded.

An observer can enter these commands during recording:

| Command | Meaning |
| --- | --- |
| `start ollie` | Start an attempt interval, optionally naming the trick |
| `make` | Rider lands and maintains control through the roll-away |
| `bail` | Attempt missed; rider steps off or jumps clear without falling |
| `fall` | Rider visibly falls to the ground |
| `background` | Non-trick activity, recorded with its own start/end interval |
| `unknown` | Occluded, ambiguous, interrupted, or otherwise unjudgeable |
| `cancel` | Abandon the open interval without assigning an outcome |
| `note text` | Save a session note |
| `sync` | Request a timestamped LED pulse |
| `quit` | Close recording; Ctrl-C also saves and exits |

Start about a second before the attempt and wait about two seconds after the
landing before marking its outcome, where practical. These are initial labeling
conventions to refine, not detection thresholds. Live labels use the latest
received sample and include human reaction/network delay; correct boundaries
from video for training. A stale stream cannot accept a live label. An unfinished
attempt is saved as an event without inventing an outcome.

If recording alone, simply record continuous motion and video, then label
afterward. Collect background such as pushing, rough pavement, braking, carrying
or setting down the board, and stationary board movement. These help expose
false sound triggers. Collect natural misses; falls do not need to be staged.
Begin with a small range of tricks across several separate sessions. Expand
riders, surfaces, and tricks once the capture workflow and labels are consistent.

## Match phone video to sensor time

Review the original video in a player/editor that exposes playback time or frame
positions. Identify a recorded pulse near the beginning and another near the end.
Match IDs by counting the flashes while consulting `events.jsonl`; if a flash is
off-camera or the correspondence is uncertain, do not guess its ID.

```bash
uv run capture/session.py align sessions/first-session \
  --video IMG_1234.MOV --sync-id 1 --video-seconds 4.20

uv run capture/session.py align sessions/first-session \
  --video IMG_1234.MOV --sync-id 11 --video-seconds 304.21
```

Use your measured values; these times and IDs are examples. One point estimates
an offset and assumes equal clock rates; two or more also estimate clock drift.
Re-enter an ID to correct its correspondence. The mapping rejects gross clock
mismatches and inconsistent points, but cannot verify that you selected the
correct flash. LED-disabled and demo markers cannot be used for video alignment.

Then add an interval using the video's timestamps:

```bash
uv run capture/session.py label sessions/first-session \
  --video IMG_1234.MOV --start 12.30 --end 17.10 \
  --outcome bail --trick kickflip --note 'front foot missed the board'
```

Without `--video`, start/end are sensor-session seconds, relative to the first
received sample. Labels must fall inside the recording and cannot overlap other
active labels. To correct a live label or previous review, add `--replace ID`
using its ID from `labels.jsonl`. Revisions are appended under that same ID;
readers must use the last entry per ID. Offline labels require a closed session.

## Files and data quality

Each recording creates a new directory and refuses to overwrite an existing one.

| File | Contents |
| --- | --- |
| `metadata.json` | Rider, board, mounting, surface, video reference, transport, clock origin, counters |
| `samples.csv` | Sensor-relative seconds, device microseconds, host monotonic nanoseconds, sample sequence, boot ID, raw motion and temperature |
| `raw.jsonl` | Every received line and its host arrival time, including malformed lines |
| `events.jsonl` | Sync edges, gaps, errors, notes, and unfinished attempts |
| `labels.jsonl` | Human outcome intervals and append-only revisions, when labels exist |
| `video_sync.jsonl` | Human-matched video/sensor flash correspondences, when supplied |

Sensor time determines intervals. Host arrival time is diagnostic; it is not the
capture timestamp and is not a wall-clock value shared with the camera. Firmware
v2 timestamps are 64-bit. The recorder also accepts legacy firmware with 32-bit
microseconds, but legacy firmware lacks sequence numbers, boot IDs, LED sync,
and wireless streaming. Legacy wrap detection cannot distinguish a reset that
happens exactly around the wrap boundary.

New-firmware boot changes stop a recording, preserving the old session. Start a
new session after a reset. Duplicate/out-of-order sample packets are discarded
from CSV and retained in raw logs. Missing sequences indicate unreceived samples;
time gaps over 30 ms can also reveal firmware stalls. The recorder periodically
flushes raw data and samples; a sudden power loss can still lose buffered output.
If the process is killed without a normal close, retain the raw files for recovery
and start a new session. Normal shutdown finalizes metadata. Do not train on
synthetic demos or windows with substantial gaps/clipping.

## Model and audio work after recordings exist

1. Review labels and motion around takeoff, landing, and roll-away. Measure
   missing packets, saturation, and sensor consistency. Decide whether acquisition
   needs a higher rate or local buffering before collecting a larger dataset.
2. Build a simple baseline from raw acceleration/rotation windows and their
   statistics, preserving background and unknown examples. Detect candidate
   attempts separately from judging their outcome. A large impact alone is not
   a fall classifier: makes can land hard too.
3. Split evaluation by whole recording sessions, and by rider when testing
   generalization. Randomly splitting neighboring windows leaks nearly identical
   motion across training and test sets. Report fall/bail precision and recall,
   false triggers per minute of continuous skating, and delay after the outcome.
   A held-out continuous recording is essential; labeled trick clips alone omit
   much of the false-trigger problem.
4. Choose the audio rule using those results. For any missed trick, bail and fall
   can both qualify; for actual falls, bail remains a separate class. Include an
   uncertain outcome, a confidence threshold tuned on validation sessions, and
   a cooldown so one event does not play repeatedly. A model score is not a
   calibrated probability without checking calibration.
5. Start inference and sound playback on the recording laptop, using its selected
   audio output or connected speaker. Once behavior is measured, decide whether
   to run a compact model on the ESP32 and transmit only events, build an iPhone
   receiver, or add onboard audio hardware. Phone audio requires a receiver app
   or browser workflow; the current UDP recorder is a Python laptop program.

There is no defensible accuracy estimate or final training-set size before
collecting real, varied examples. Record several sessions first, evaluate the
baseline, and gather more examples where it is uncertain or wrong.
