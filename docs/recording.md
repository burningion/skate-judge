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

For the first outing, follow [your first dataset recording](first-recording.md).
This page is the hardware and labeling reference. The primary capture path is
[onboard recording](onboard-recording.md); the streaming commands below are
retained for the legacy live viewer and demos.

## Hardware and mounting

The current setup is an Adafruit Feather ESP32-S3 with 8 MB flash and no PSRAM,
an LSM6DSO32 over STEMMA QT, a 3.7 V 500 mAh LiPo, and an eight-pixel SKC6812 RGB
stick mounted along the side of the deck. The IMU's accelerometer supports
±32 g and gyroscope ±2000 dps nominal; those are the firmware's configured ranges
([ST sensor specifications](https://www.st.com/en/mems-and-sensors/lsm6dso32.html)).
The onboard logger saves both streams at 208 Hz using the sensor FIFO. It checks
read lengths, configuration readback, actual rates, and FIFO overflow. Short
impact peaks can still be missed; inspect raw timing and clipping. The older
live-stream sketch polls at a nominal 100 Hz and is not the dataset recorder.

Rigidly attach the IMU so it follows the deck and cannot rotate independently.
Record its orientation and position in `--mounting`; moving it changes the
data distribution. Secure the board and wiring in an enclosure clear of wheels,
trucks, and the surfaces used for slides. Keep the battery protected from
crushing and abrasion. The Feather supports a 3.7/4.2 V single-cell LiPo through
its battery JST socket; confirm the battery plug has the correct polarity.
The 3.7 V rating is nominal (about 4.2 V when full), and 500 mAh is capacity,
not a maximum output-current specification. Measure runtime and check the
battery's discharge rating for the final setup
([Feather power guide](https://learn.adafruit.com/adafruit-esp32-s3-feather/power-management)).

For a first wireless prototype, enable the ESP32's local access point:

```bash
./flash-feather.sh
```

Join `SkateJudge-XXXX` on the laptop using password `skate-judge`. This is a
shared prototype password, configurable in the sketch's `WiFi.softAP` call.
The network is local and provides no internet connection. The independent
phone camera can record without joining it. The onboard logger uses Wi-Fi for controls/status and post-recording downloads.
Motion is preserved in flash independently of the connection. USB provides
the same controls and download protocol; see [recovery](onboard-recording.md).

## Eight-pixel NeoPixel stick

The sync output supports an eight-pixel SKC6812 / SK6812 stick. All eight pixels
flash white for 150 ms, then turn off together. Sending `sync` in the recorder
triggers the flash and records its timestamp; optional automatic session flashes
also use the whole stick. Pixels stay off between flashes, including after startup.

The [Adafruit eight-pixel RGB stick](https://www.adafruit.com/product/1426)
currently lists SKC6812 LEDs and uses RGB data.

For your **Adafruit Feather ESP32-S3 with 8 MB flash and no PSRAM**
([board 5323](https://www.adafruit.com/product/5323)), the verified data pin is
the header marked **5**, which is GPIO5. This is a digital data pin, not a 5 V
power output or a position counted along the header. It is separate from the
IMU's SDA=3, SCL=4, and power-enable GPIO7. Use the provided preset:

```bash
./flash-feather.sh --compile-only
./flash-feather.sh

# Optional brightness or RGBW variant:
SYNC_LED_BRIGHTNESS=24 ./flash-feather.sh
SYNC_LED_RGBW=1 ./flash-feather.sh
```

This selects the Arduino `adafruit_feather_esp32s3_nopsram` target, its 8 MB
partition layout, native hardware USB CDC, the STEMMA QT pins, and an eight-pixel
RGB stick on GPIO5. It also enables Wi-Fi; use `SKATE_WIFI=0` for USB only.
The board's onboard NeoPixel is separate and is not used as the sync stick.

The RGB configuration sends GRB bytes at 800 kHz; RGBW sends GRBW and flashes
the dedicated white channel. RGB and RGBW sticks need different data framing,
so select the type in the product listing rather than guessing from the chip
family ([Adafruit RGBW stick](https://www.adafruit.com/product/2868)).
`flash.sh` installs the Adafruit NeoPixel library into `.arduino` when either
pixel mode is selected.

Brightness defaults to the maximum `255` out of `255`. Add `SYNC_LED_BRIGHTNESS=48` for a
dimmer flash. Check the camera image for
visibility without washing out the board. This changes the PWM channel value,
not a calibrated current limit. Pixel count defaults to one for compatibility
with a single onboard pixel; `SYNC_LED_COUNT=8` is required for the entire stick.
The supported count is 1–64 and brightness is 1–255. The preset supplies the
eight-pixel count automatically.

### Direct-LiPo wiring for the first test

See the [editable Fritzing project](../hardware/fritzing/README.md) for a
connector-level drawing of this setup, with a bundled-parts `.fzz` file.

Disconnect USB and the battery while making the connections. With the battery
plugged into the Feather, its `BAT` pad exposes battery voltage directly. The
label `5V` on the stick is the positive power input; in this experiment it is
fed from the LiPo rather than a regulated 5 V source.

| Connection | Destination |
| --- | --- |
| 3.7 V 500 mAh LiPo plug | Feather battery JST socket, with matching polarity |
| Feather `BAT` | Stick `5V` / `+` |
| Feather `GND` | Stick `GND` / `−` |
| Feather header **5** (GPIO5) | 330 Ω series resistor near stick `DIN`, then `DIN` / `IN` |
| Stick `DOUT` / `OUT` | Leave unconnected |
| Feather STEMMA QT | LSM6DSO32 STEMMA QT, using the four-wire cable |

The STEMMA connection carries 3.3 V, ground, SDA=GPIO3, and SCL=GPIO4. The
firmware enables its power rail using GPIO7; GPIO7 is not an extra wire to run
to the IMU. The preset leaves the stick's data on GPIO5.

The **330 Ω resistor is recommended, not mandatory** for a short-wire bench
test. It protects the first pixel's data input; the pixels control their own
LED current. Adafruit recommends 300–500 Ω near the first pixel and notes that
newer pixels and small battery projects can work without it. Keep it in the
mounted version if practical
([NeoPixel best practices](https://learn.adafruit.com/adafruit-neopixel-uberguide/best-practices)).

Adafruit documents running short NeoPixel chains directly from a 3.7 V LiPo
with 3.3 V data, so this arrangement can be bench-tested without a boost converter
or level shifter
([connections guide](https://learn.adafruit.com/adafruit-neopixel-uberguide/basic-connections)).
However, the [specific RGB stick listing](https://www.adafruit.com/product/1426)
specifies 4–7 V. Direct-LiPo operation below that range is an experiment, not
a guarantee: flashes can dim, show wrong colors, or stop as the battery runs
down. Keep the default reduced brightness and check operation both when full
and after some discharge before relying on the flashes for video sync. Use the
regulated option below if the flashes are unreliable or insufficiently visible.

Leave the battery connected during a USB bench check with this wiring: USB
powers the Feather, but it does not replace a missing battery on `BAT`. The
Feather charges a compatible attached battery through USB. Its `USB` pad has
5 V only when USB is connected, and its `3V` pad is not the stick's power source
([Feather pinout](https://learn.adafruit.com/adafruit-esp32-s3-feather/pinouts)).

### Regulated 5 V option

This is an alternative if direct battery power does not work reliably across
the charge range. Keep the same LiPo; a boost converter raises its voltage to
5 V for the stick. It does not require a second battery.

| Stick connection | Connect to |
| --- | --- |
| `5V` / `+` | Regulated 5 V supply for the LEDs |
| `GND` / `−` | Supply ground **and** ESP32 ground |
| `DIN` / `IN` | Feather **5** (GPIO5) through a 3.3 V → 5 V logic buffer, then the recommended 330 Ω resistor near `DIN` |
| `DOUT` / `OUT` | Leave disconnected unless adding another stick |

Use an appropriate buffer such as a 74AHCT125 powered at 5 V, with its ground
shared and the selected channel's output enabled. Its input receives the ESP32
GPIO; its output drives the resistor and stick. The data direction must enter
`DIN`, not `DOUT`. Adafruit recommends level shifting for reliable operation
with 3.3 V controllers and 5 V pixels
([connections guide](https://learn.adafruit.com/adafruit-neopixel-uberguide/basic-connections)).

Adafruit recommends a 500–1000 µF capacitor rated at least 6.3 V across the
stick's 5 V and ground near its power input, observing polarity, to buffer power
transients. Size the LED supply for about 0.5 A for eight RGB pixels at full white,
plus separate allowance for the ESP32
if sharing a supply; the default reduced brightness normally draws less.
Power the stick from the supply, not an ESP32 GPIO or its 3.3 V regulator.
See [Adafruit's power guidance](https://learn.adafruit.com/adafruit-neopixel-uberguide/powering-neopixels).

For a **USB bench test**, the Feather header marked `USB` provides USB's 5 V
while a powered USB-C cable is connected. Use that rail for the stick and logic
buffer, subject to the USB source's current budget. The header marked `3V` is
not the strip's power connection.

For **regulated 5 V battery operation**, plug a compatible, correctly polarized
1-cell LiPo into the Feather's battery JST socket. The header marked `BAT` exposes that
battery voltage; route it to the input of a suitable 1-cell LiPo-to-5 V boost
converter. The converter's 5 V output powers the stick and logic buffer, while
the Feather remains powered through its battery socket. Connect all grounds.
Keep this boosted LED rail separate from the Feather's `USB` pad. The `USB` pad
does **not** generate 5 V from the battery. These power pin meanings are from
[Adafruit's Feather pinout](https://learn.adafruit.com/adafruit-esp32-s3-feather/pinouts)
and [power guide](https://learn.adafruit.com/adafruit-esp32-s3-feather/power-management).

### Mounting and other configurations

Mount the stick along the side with the pixels facing the camera, protected by
a clear cover or recess. Add strain relief where wires meet the stick so deck
flex and impacts do not pull on the solder joints.

The generic `flash.sh` does not select a data GPIO; `flash-feather.sh` selects
GPIO5 for this exact board. For other configurations, avoid USB, flash/PSRAM,
and other board-reserved pins; the sketch additionally disables the LED on
invalid output GPIOs or when it conflicts with IMU wiring. The onboard logger
blocks recording/sync if the IMU is unavailable; use `check-sensor` after fixing
its cable. In the legacy streaming sketch, a simple GPIO LED
is supported with `SYNC_LED_PIN=N` alone and an appropriate current-limiting
resistor; add `SYNC_LED_ACTIVE_LOW=1` if needed. That setting does not apply to
NeoPixels.

## Legacy streaming capture and live labels

The commands in this section require the legacy firmware:

```bash
SKETCH=firmware/imu_stream ./flash-feather.sh
```

For new datasets, use [onboard recording](onboard-recording.md) and apply labels
after importing the log. Legacy live labels rely on received samples and do not
apply to onboard capture.

### Record and label attempts

Point the phone or webcam so both feet, the board, the landing area, and the
sync LED are visible. Record at normal playback speed; keep the original video.
The web UI can record a local webcam; an external phone recording is still
manual. The software does not automatically detect flashes or align video.
At 60 frames per second, selecting the first bright frame has roughly a
one-frame timing uncertainty (about 17 ms), plus exposure and rolling-shutter
effects. Visibility and a continuous recording matter more than resolution.

```bash
uv run --offline capture/session.py record --udp 192.168.4.1 --controls \
  --output sessions/first-session \
  --rider rider-01 --board deck-01 --surface smooth-concrete \
  --mounting 'under deck near front truck, sensor X points toward nose' \
  --video IMG_1234.MOV
```

Power the Feather, join its Wi-Fi on the recording computer, and start the
sensor recorder first. Open the printed local controls URL on that same computer,
click **Record video + sync**. The computer displays
3, 2, 1 and sends the LED request; the stick stays off until the white sync pulse.
Sensor acquisition continues throughout. The controls work without internet and
bind only to `127.0.0.1`, not to other devices on the Wi-Fi. The combined button waits for saved video bytes before counting down. For an
external camera, start it yourself and check the already-recording option. The Feather's physical buttons are unchanged.

Without the browser, enter `countdown` in the recorder terminal; `sync` requests
an immediate flash. Repeat near the end while the video and sensor recording
are both still running. For a webcam, Stop & save video and wait for Saved before
entering `quit`. For an external camera, stop it after the final flash. The page disables
the button without fresh samples or while a sync is in progress. A stale stream
cancels the countdown; a missing board acknowledgement is reported as an error.

**Automatic flashes are off by default** (`--sync-every 0`). Opt in with
`--sync-every 30` to request a flash once samples arrive and every 30 seconds
thereafter; this can produce flashes before the camera is ready. Manual control
is recommended for the first session. `--controls-port PORT` optionally fixes
the local page's port; otherwise a free port is selected.

The recorder prints the acknowledged sync ID and whether the firmware enabled
an LED; still check the physical stick and video. Network command delays do not
become video offsets: alignment uses the board's timestamp at the LED write,
not the computer countdown or command-send time. A lost sync packet has no
usable correspondence; use a flash whose ID was recorded. Computer-side
`countdown_start` and `sync_requested` events are diagnostic records, not
substitutes for the board's `sync` event. Demo mode cannot flash a physical LED
or supply video alignment points.

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
| `countdown` | Count down three seconds on the computer, then request an LED pulse |
| `sync` | Request a timestamped LED pulse immediately |
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

## Webcam recording in the web UI

Run the recorder with `--controls` over Wi-Fi, USB, or demo mode and open the
printed URL on that same computer. The page offers a live preview, camera
selector, optional microphone audio, Start video recording, and Stop & save video.
Camera/microphone access is requested only after you click Enable camera;
microphone audio defaults to off. Changing a camera or microphone setting
reopens the preview, and these settings are locked while recording/saving.
Preview alone does not record. For separate iPhone footage, select the explicit already-recording checkbox
before syncing.

The browser requests 1920×1080 at 60 fps as preferences, not requirements; the
actual negotiated settings appear below the preview and in the clip's sidecar.
Preview is not mirrored. Recording prefers H.264 in MP4, with AAC when microphone
audio is enabled, then browser-selected MP4 codecs. Browsers without MP4 recording
support use WebM. The recording status shows the selected container. MP4 is the
preferred input for the [planned Rerun review tool](rerun-review-plan.md);
existing WebM clips can be converted into derived MP4 review copies with timing
verified against the original.
Files are named `webcam-<id>.mp4` or `webcam-<id>.webm` and saved directly
inside the current sensor-session folder. The UI shows the filename after a
successful save. `--video` remains an optional identifier for external footage;
it does not rename a webcam clip. No additional Python dependencies are needed.

Video data is sent in ordered, at-most-1-MiB requests to the local recorder while
capture continues, so an entire session is not accumulated in browser memory.
Retries cannot append the same chunk twice. The video uploader runs separately
from sensor acquisition. Stop & save waits for the final MediaRecorder data and
all upload acknowledgements before marking the clip Saved. The adjacent
`webcam-<id>.json` records the format, camera settings, byte/chunk counts, browser
duration, save status, and any recording warning. Browser times and chunk counts
are **not** sensor alignment points: continue matching visible beginning/end LED
flashes, using the actual webcam filename in `align` and `label`.

Keep the laptop awake, with adequate free disk space, and leave the browser tab
and terminal open until Saved. Do a short webcam test and inspect the original
saved clip in your video editor before collecting a long session. Browser video
containers/editors differ in seeking support; the save acknowledgement confirms
byte delivery, not playback quality or the presence of visible sync flashes.

If camera permission is denied, allow it for this local page in browser and OS
settings, then retry Enable camera. If the camera is busy, close other camera
apps. If MediaRecorder is unavailable, use another supported browser or record
externally. A plain localhost page can request camera access without an internet
connection; see the [camera API's permission requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).
Recording uses the browser's [MediaRecorder API](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder).

On upload failure the UI stops video, retains unsent data in the open tab, and
offers Retry saving video. It also stops if pending uploads exceed 32 MiB;
review the saved warning and record another clip if needed. Do not refresh or
close a tab with unsaved video. A closed tab cannot be resumed; start a new
sensor session after preserving its partial files. One webcam upload is allowed
per sensor session at a time, so a second tab cannot overwrite an active clip.

Ordinary terminal `quit` waits for you to stop/save the active webcam recording.
Ctrl-C, `--duration`, a board reset, or a process crash can still stop the recorder
first. Received video is kept as `webcam-<id>.<format>.part`, and normal shutdown
marks its sidecar incomplete. After a crash a sidecar may still say recording;
neither is a confirmed complete clip, and partial files may not play. Do not
count incomplete clips as usable training data without reviewing/recovering them.
Use Turn camera off after saving to release the webcam. No video is sent to a
cloud service by this application.

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
| `webcam-<id>.webm` / `.mp4` | Completed webcam clip saved by the web UI, when used |
| `webcam-<id>.json` | Camera settings, upload counts, save status, and any warning for that clip |
| `webcam-<id>.<format>.part` | Unfinished webcam upload; not a confirmed complete clip |

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

The [Rerun review and labeling plan](rerun-review-plan.md) describes the proposed
synchronized video/IMU viewer, label controls, and model comparison workflow.

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
