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

The [editable Fritzing project and preview](../hardware/fritzing/README.md)
show the direct-LiPo layout below. Use the `.fzz` file to move parts or change wires.

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

## 2. Install the logger and initialize storage

With USB connected and serial monitors closed:

```bash
./flash-feather.sh --compile-only
./flash-feather.sh
uv run capture/onboard.py --board serial:auto initialize
uv run capture/onboard.py --board serial:auto status
```

Install `arduino-cli`, its ESP32 core, and `uv` using the README if needed.
Run `uv` while online once to cache dependencies. The preset selects SDA=3,
SCL=4, IMU power GPIO7, and eight RGB pixels on GPIO5. Use
`SYNC_LED_RGBW=1 ./flash-feather.sh` for an RGBW stick.

Initialization only formats a blank data partition. It never overwrites an
existing recording. Status must show `sensor_ready`, `storage_ready`, and
`led_enabled` as true. It also reports detected flash and enabled PSRAM sizes.

## 3. Record a short bench check

Leave the LiPo connected to power the stick and run:

```bash
uv run --offline capture/onboard.py --board serial:auto record --output sessions/bench-001
```

Open the printed local URL on this computer. Frame the stick and board, enable
microphone audio if desired, and click **Record video + sync**. Accept camera
permissions. Video starts first; the first uploaded video chunk must be saved
before the board starts recording. Both motion streams must reach at least
180 Hz before the countdown begins. The nominal rate is 208 Hz, about one
reading every 4.8 ms.

Check that all eight pixels flash white after the countdown. Gently tilt the
board for 15–30 seconds. The page should show rates near 208 Hz, no read errors,
and no FIFO overruns. Repeat the countdown while video continues, wait for the
flash, then click **Stop & save video + board data**.

Wait for the video to say Saved and the board status to report saved samples.
The board's original remains on flash. Inspect `metadata.json` for
`onboard_quality.usable` and its detailed counters. Review the saved video to
verify visible beginning/end flashes and microphone audio. A timestamped LED
command cannot prove the physical stick was powered or visible.

Enter `quit` after saving. Keep bench sessions separate from skating data.
Use a new output folder and a new recorder command for each batch.

## 4. Record on battery power

Disconnect USB after the bench recorder exits. Join **SkateJudge-XXXX** on the
laptop with password **skate-judge**, then run:

```bash
uv run --offline capture/onboard.py record --output sessions/pilot-001 \
  --rider rider-01 --board-name deck-01
```

Use the same **Record video + sync** / final sync / **Stop & save** sequence.
Start with one-minute batches and watch remaining storage. The existing
1.5 MiB data partition holds only a few minutes; previously saved files occupy
space until explicitly deleted. Motion is stored onboard even if Wi-Fi drops.
Reconnect to stop and download; keep the laptop's video recording running.

For separate phone footage, start the phone recording yourself and select
**I'm already recording on a separate camera** before syncing. After the final
flash, use **Stop & download board data** and stop the phone camera.

## 5. Review, align, and preserve

```bash
uv run viz/trick_review.py sessions/pilot-001/webcam-<id>.webm
```

Use the actual `.webm` or `.mp4` filename. Match the first bright frame of each
recorded LED pulse to its sync ID. Two or more matches can estimate video/board
clock drift. The accelerometer and gyro plots then share the video timeline.
Review audio onset suggestions and adjust trick boundaries; sound alone does
not judge a make, bail, or fall. See [trick review](trick-review.md).

Keep the original raw `.bin`, video, and metadata. A verified board copy can be
explicitly removed to reclaim capacity using the commands in
[onboard recording and recovery](onboard-recording.md). That guide also covers
interrupted downloads, USB recovery, and power-loss limitations.
