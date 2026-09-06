# Your first dataset recording

The goal of this first session is a usable, synchronized recording: raw board
motion, a video showing the outcome, and human labels. It is a pilot to check
the setup, not enough data to claim a working make/fall detector. Automatic
classification and audio playback are not implemented yet.

Use the Feather ESP32-S3 **8 MB flash / no PSRAM**, LSM6DSO32, **3.7 V 500 mAh
LiPo**, and **eight-pixel SKC6812 RGB stick**. All terminal commands below run
from the repository root. Use a new session directory for every recording;
the recorder will refuse to overwrite an existing one.

## 1. Wire and mount the hardware

Start with the [direct-LiPo pinout](recording.md#direct-lipo-wiring-for-the-first-test):

| From | To |
| --- | --- |
| LiPo | Feather battery JST, with matching polarity |
| Feather `BAT` | Stick `5V` / `+` |
| Feather `GND` | Stick `GND` |
| Feather pad printed **5** (GPIO5) | Stick `DIN`, preferably through a 330 Ω resistor near `DIN` |
| Feather STEMMA QT | LSM6DSO32 STEMMA QT |
| Stick `DOUT` | Leave unconnected |

Wire with power disconnected. **“5” is a data GPIO, not a 5 V power pin.**
The resistor is recommended data-input protection; a short-wire bench test
can omit it. Direct LiPo power uses no level shifter, but this stick's specified
4–7 V range does not guarantee operation as the battery drops below 4 V. Check
the flashes before recording and after some discharge. The [hardware reference](recording.md#regulated-5-v-option)
has the regulated 5 V alternative and manufacturer sources.

Rigidly mount the IMU and record its position/orientation. Mount the stick on
the side facing the camera, with wire strain relief and protection from impacts.
Keep the battery enclosed and protected from crushing or abrasion. For a USB
bench test with the wiring above, leave the LiPo connected so the stick has
its battery supply.

## 2. Prepare the laptop and flash the Feather

Do this while the laptop has internet access, before joining the board's Wi-Fi.
If the tools and ESP32 core are already installed, skip their installation:

```bash
brew install arduino-cli uv
arduino-cli core install esp32:esp32 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

Run a short demo to install/cache the recorder's Python dependencies:

```bash
uv run capture/session.py record --demo --duration 5 --output sessions/demo-001
```

The demo writes synthetic motion. It does not talk to the Feather or flash
the physical stick. Do not include this session in the training dataset.

To practice webcam recording and the countdown without a Feather, run another
demo without a time limit:

```bash
uv run capture/session.py record --demo --controls --output sessions/demo-webcam-001
```

Open its printed local URL, enable the camera, start video recording, and click
the countdown button. A demo sync is reported as LED-disabled, not as a physical
flash. Stop & save video, wait for Saved, then enter `quit` in the terminal.
The webcam footage is real, but its paired motion is synthetic; keep this
session out of the training dataset.

Connect the Feather to the laptop over USB-C, close the orientation viewer or
any serial monitor, and build/upload:

```bash
./flash-feather.sh --compile-only
./flash-feather.sh
```

The preset selects this exact board, enables Wi-Fi, and configures eight RGB
pixels on GPIO5 at brightness 48/255. The IMU uses SDA=3, SCL=4, and power-enable
GPIO7 through STEMMA QT. For the separate RGBW stick variant, use
`SYNC_LED_RGBW=1 ./flash-feather.sh` instead. No special firmware change is
needed when choosing direct battery power versus the regulated 5 V circuit.

## 3. Check motion and all eight LEDs on the bench

With USB and the LiPo connected, run:

```bash
uv run --offline capture/session.py record --serial auto --controls \
  --output sessions/bench-001 --duration 30
```

Hold the board still for a few seconds, then tilt it gently. Open the controls
URL printed in the terminal on this laptop and click **Start 3-second countdown**.
After the computer counts down, all eight pixels should flash white for about
150 ms. The pixels stay off during the countdown. You can also enter `countdown`
in the terminal, or `sync` for an immediate flash, each followed by Enter.
The console should acknowledge `Sync <ID>: LED flash`. That confirms the firmware enabled
its LED output; also visually check the actual stick.

The recording ends automatically after 30 seconds and prints a sample/gap
summary. Inspect the files:

```bash
head -n 5 sessions/bench-001/samples.csv
python3 -m json.tool sessions/bench-001/metadata.json
```

Look for increasing `t_s`, changing motion readings when tilted, and roughly
100 samples per second. A quiet bench test should have no board resets or sensor
errors and ideally no missing sequences or gaps. Keep this bench session out of
the skating dataset. If only one pixel flashes, reflash with the Feather preset;
if the recorder says the LED is disabled, check the preset and IMU connection.
Wrong colors can indicate the wrong RGB/RGBW setting, low voltage, or wiring.
If the console acknowledges a flash but the stick stays dark, check the battery
connection to `BAT`, common ground, and that the data wire enters `DIN`.

If a flash is overexposed or too dim on camera, adjust brightness while still
on the bench, for example `SYNC_LED_BRIGHTNESS=24 ./flash-feather.sh` for dimmer
flashes. Complete this check before starting a real session.

## 4. Switch to battery power and connect the recording computer

After the bench recorder exits, unplug USB and confirm the Feather remains
powered by the LiPo. Join **SkateJudge-XXXX** on the laptop using password
**skate-judge**. Stay connected even though this network has no internet.
The recorder streams to the laptop; the board has no onboard recording and
cannot recover samples missed while out of range. Keep the laptop awake and
within the range checked on site.

Set up the iPhone or webcam with a fixed view of both feet, the board, landing
area, and the side-mounted stick. Use ordinary video at normal playback speed;
60 fps is useful if available. Keep the original video without trimming or
slow-motion edits. The web UI can record a webcam attached to the computer;
an iPhone recording separately still needs its own camera app.

You can power up and connect Wi-Fi before starting either recording. There is
no startup sync flash to catch. The control button is on the recording computer,
not on the Feather; its BOOT and RESET buttons are unchanged. In the next step,
start sensor recording first, then video, then the countdown.

## 5. Record a short skating session

Choose about 10–15 minutes and 10–20 attempts at one or two familiar tricks for
this first workflow check. These are practical pilot targets, not a model's
minimum training-set size. Keep one rider, board mounting, and surface for this
session; change the example metadata to describe the actual setup:

```bash
uv run --offline capture/session.py record --udp 192.168.4.1 --controls \
  --output sessions/pilot-001 \
  --rider rider-01 --board deck-01 --surface smooth-concrete \
  --mounting 'under deck near front truck, sensor X points toward nose'
```

Open the printed `http://127.0.0.1:PORT` URL in a browser **on the recording
computer**. It works without internet; it is not a page to open on the iPhone.
Once the sample count is increasing:

1. For a webcam, click **Enable camera** and allow browser access. Select the
   camera, check the live framing, then click **Start video recording**. Wait
   for **Recording**; preview alone does not save footage. Microphone audio is
   off by default. If recording separately on an iPhone, start its camera app
   instead and leave the web UI's webcam off.
2. Click **Start 3-second countdown** on the computer. The page displays 3, 2, 1
   while motion recording continues, then sends the flash request to the Feather.
3. Wait for the acknowledged sync ID and check that the white flash was visible
   to the camera. Save the ID for alignment later.

No flashes are requested automatically by default. You can repeat the button
at any time when the stream is fresh, including near the end of the video.
Without the browser, enter `countdown` in the recorder terminal for the same
sequence; `sync` skips the countdown. The countdown itself does not start video.
For webcam footage, the page will show its generated `webcam-<id>.webm` or
`.mp4` filename when saved. For a separate iPhone clip, copy the original into
the session folder as `pilot-001.mov` later. `--video pilot-001.mov` is an
optional metadata identifier for that external clip, not a camera control.

Keep the laptop awake and the browser tab and terminal open. The preview reports
the camera's actual resolution/frame rate; 1080p/60 fps is requested but not
guaranteed. Check a short saved webcam clip before the skating session. See
[webcam troubleshooting and file details](recording.md#webcam-recording-in-the-web-ui).

**The first ID may not be 1**: the counter continues across
recordings until the Feather reboots. Use the IDs printed in this session and
saved in its `events.jsonl`; do not assume an ID from an example.

Start with several seconds of the board still, then record background such as
pushing, rolling, stopping, carrying the board, and setting it down. Follow
with attempts, leaving enough roll-away footage to see whether control was
maintained. Record natural bails and falls when they occur; there is no need to
manufacture falls or force equal counts in this pilot.

An observer can enter `start ollie` about a second before an attempt, then enter
the outcome about two seconds after landing, each followed by Enter:

| Outcome command | What the video shows |
| --- | --- |
| `make` | Rider lands and maintains control through the roll-away |
| `bail` | Missed attempt; rider steps or jumps off without falling |
| `fall` | Rider falls to the ground |
| `unknown` | Outcome is ambiguous or obscured |
| `background` | An interval of non-trick activity |

Every outcome closes a previously opened `start` interval. For background,
enter `start` before the activity, then `background` afterward. `cancel`
abandons an open interval; `note text` saves an observation. These are human
labels, not automatic predictions. If recording alone, leave the recorder
running and add all labels from the video afterward.

If the recorder reports no fresh motion data, stop attempting tricks until the
connection returns. A countdown cancels if the stream goes stale. If no flash
acknowledgement arrives, check the connection and try again; the computer's
countdown alone is not a video sync marker. If the board resets, the recorder
stops; begin a new sensor session and preferably a new video clip. Avoid skating
with a USB tether.

## 6. End, inspect, and save the session

Finish or cancel any open attempt. While the camera still records and the stick
is visible, click **Start 3-second countdown** again (or enter `countdown` in
the terminal). Wait for the acknowledged flash and save the printed sync ID.
For webcam recording, click **Stop & save video**, wait for **Saved**, and note
the generated filename. Then enter `quit` in the terminal. For an external
iPhone recording, stop its video after the final flash and quit the recorder.
You can use **Turn camera off** to release the webcam after saving.

Do not close the page while recording or saving. If saving fails, leave the
page and terminal open, resolve the error, and click **Retry saving video**.
Ordinary `quit` refuses to exit while a webcam upload is active; Ctrl-C, a
duration limit, a crash, or a board restart can still interrupt it. An interrupted
upload is retained as `.part` and is not a confirmed complete video.

Review the summary and metadata:

```bash
python3 -m json.tool sessions/pilot-001/metadata.json
rg '"kind": "sync"' sessions/pilot-001/events.jsonl
```

Check that `synthetic` is `false`, `samples` and `duration_s` are plausible,
and `closed_utc` is present. Review `missing_sequences`, `gaps_over_30ms`,
`malformed`, and any errors before using the recording for training. Compare the
reported duration with the time you recorded; do not treat a session with long
gaps as continuous evidence. `led_enabled: true` is a configuration indication,
not proof that the camera saw a flash.

Webcam recordings and their `webcam-<id>.json` sidecars are already in the session
folder; check the sidecar says `status: saved` and review any `warning`. If you
used a separate iPhone, copy its original into `sessions/pilot-001/pilot-001.mov`.
Back up the whole session folder. Session folders are ignored by Git, so a code
commit does not back up your data.

## 7. Align video and review labels

In a video player/editor with frame stepping and timestamps, find the first
bright frame of an identifiable recorded pulse near the beginning and another
near the end. Match them to actual rising-edge (`edge: 1`) sync IDs in
`events.jsonl`. If you cannot confidently match a flash, use another one.

The following IDs, filename, and times are **examples only**. Replace them with
values from your video and session before running. For webcam footage, replace
`pilot-001.mov` with the actual `webcam-<id>.webm` or `.mp4` filename everywhere
below, including the label commands. Each separately recorded clip needs its
own beginning/end sync points.

```bash
uv run capture/session.py align sessions/pilot-001 \
  --video pilot-001.mov --sync-id 1 --video-seconds 4.20

uv run capture/session.py align sessions/pilot-001 \
  --video pilot-001.mov --sync-id 2 --video-seconds 604.21
```

Two points allow correction for clock drift as well as offset. A single point
only gives an offset and assumes equal clock rates. Alignment is based on the
board's LED timestamp, not the network arrival time or the phone's wall clock.

Watch each attempt and mark a video interval beginning about a second before
the attempt and ending about two seconds after landing/roll-away. Adjust to the
footage and keep intervals from overlapping. For example, replacing these
times with an actual interval from your video:

```bash
uv run capture/session.py label sessions/pilot-001 \
  --video pilot-001.mov --start 12.30 --end 17.10 \
  --outcome bail --trick kickflip --note 'front foot missed the board'
```

Use `--outcome background` without `--trick` to label non-trick intervals and
`--outcome unknown` when the footage is inconclusive. Keep bails and falls
separate, even if they eventually trigger the same sound.

If a live label already covers the attempt, find its ID in `labels.jsonl` and
add `--replace THAT_ID` to the command. This corrects the existing interval
instead of creating an overlapping label. Corrections are appended with the
same ID; the last entry for that ID is the active label. Re-run `align` with
the same flash ID to correct a mistaken correspondence, then re-enter affected
video labels with `--replace` because existing labels are not remapped automatically.

## What a completed pilot contains

A usable pilot has its original video, raw motion, valid beginning/end sync
correspondences, and reviewed labels for the attempted tricks and some
background. Keep ambiguous outcomes marked unknown, note data gaps, and retain
all original files. The recorder creates `metadata.json`, `samples.csv`,
`raw.jsonl`, and `events.jsonl`; alignment adds `video_sync.jsonl`, and labeling
adds `labels.jsonl`.

The next step is to inspect motion around these outcomes and collect additional
separate sessions. Keep whole sessions together for later training/evaluation
splits. See the [model plan](recording.md#model-and-audio-work-after-recordings-exist)
for how this becomes an evaluated audio trigger.
