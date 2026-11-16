# Live trick detection

The iPhone app can detect trick attempts while a session records, play a sound
when it calls one landed, and cut the session's video down to the attempts.
This is a first version built on the existing
[attempt forest, impact rule, and landing CNN](automatic-landing.md). It is
experimental: on held-out recordings from one rider and board it misses about
four in ten landed kickflips and calls about one in six missed kickflips landed.

Raw recording and LED sync are unchanged. Detection reads a copy of the data;
the board logs everything whether or not anyone is listening.

## How it works

1. **Board.** The logger copies every packet it queues for storage into a
   32 KiB RAM ring (`firmware/imu_logger/live_ring.h`). `GET /live?id=<recording>&offset=<bytes>`
   returns the bytes from that file offset. The acquisition task never waits
   for a listener; old bytes are overwritten. A listener more than about six
   seconds behind is moved forward and resynchronizes on the next valid packet.
2. **Phone.** The app polls `/live` several times a second, decodes samples
   (`LiveDecoder`), and feeds them to `LiveJudge`, which scores two-second
   windows every 0.1 s, picks the strongest impact, waits one second after it,
   and runs the landing CNN.
3. **Result.** Each event is shown, appended to `live_events.jsonl` in the
   session folder, and exported with the session. A landed call plays a rising
   chime and a not-landed call a lower falling tone.

The reply body is `LIV1`, the actual start offset (uint32), the recording's
current end offset (uint32), then up to 8192 bytes. Offsets are positions in
the recording file, so a listener that keeps up receives exactly the file.

Sample times come from the board's clock anchors without looking ahead: each
sample is extrapolated from the newest anchor at the rate measured over the
last six. Replaying `a7s-030` this way differs from the desktop importer by
5 µs on average and 71 µs at most.

## The live event policy

`learning/streaming_landing.py` is the reference. The offline scanner ranks
every window in a finished recording; a live judge cannot. Instead:

- the first window scoring at least 0.5 opens a candidate;
- the candidate keeps its strongest window for 0.8 s;
- the event waits for one second of data after the estimated contact;
- another candidate cannot open until one second has passed **and** the score
  has dropped below 0.5, so one burst of motion is one event.

Replayed over the eleven `a7s-v8` recordings, each judged by models that never
saw it (`.build/streaming-landing-v1`):

| | Offline | Live policy |
| --- | ---: | ---: |
| Attempts found | 164/169 | 162/169 |
| Makes called landed | 49/66 | 49/66 |
| Misses called not landed | 83/103 | 83/103 |
| Kickflip makes called landed | 12/21 | 12/21 |
| False events in 5.3 reviewed background minutes | 16 | 12 |
| Total events | 446 | 250 |
| Delay after contact, median / 90th percentile | n/a | 1.03 s / 1.27 s |

The re-arm rule and the 0.8 s hold were both chosen after replaying these same
recordings (the first version held 0.5 s and produced 76 repeat events inside
single attempts). Treat these as development results until new sessions confirm
them. The delay excludes Wi-Fi polling and phone processing.

```bash
python3 learning/streaming_landing.py evaluate datasets/a7s-v8 \
  --run .build/automatic-landing-v1 --output .build/streaming-landing-v2
python3 learning/streaming_landing.py scan --model .build/automatic-landing-v1/model.json \
  --samples sessions/a7s-030/samples.csv --output .build/a7s-030-streaming.json
```

## Updating the model in the app

```bash
python3 learning/export_live_model.py --model .build/automatic-landing-v1/model.json \
  --raw sessions/a7s-030/onboard-<id>.bin --start-s 96 --duration-s 44
swift test --package-path ios/SkateJudgeCore --scratch-path .build/ios-core
```

This writes the app's model (`ios/SkateJudgeCore/Sources/SkateJudgeCore/Resources/live_model.json`)
and a test fixture: a 44-second excerpt of a real raw recording and the events
the Python reference produces from it. The Swift tests replay the excerpt and
must reproduce those events, so a model update that changes behaviour without a
matching fixture fails. The fixture checks that the two implementations agree,
not that the model is right.

## Trick edit

For a finished in-app video, **Make trick edit** maps every detected attempt
onto the movie with the phone/board clock estimate saved during recording,
keeps two seconds either side of each contact, merges overlaps, and stitches
the ranges without re-encoding. It writes:

- `trick_cuts.json` in the session folder: kept ranges in original-movie
  seconds, with each attempt's call and scores;
- `Documents/TrickEdits/tricks-<session>.mov` on the phone, to play or share.

When the motion file has been downloaded, attempts come from replaying that
complete file rather than the live events, which can have Wi-Fi gaps. This
also works for sessions recorded without live detection. It needs in-app video
on iOS 18.2 or later; external-camera footage has no clock estimate.

## How much data a better model needs

`learning/learning_curve.py` refits both models on random subsets of the other
recordings and scores each held-out recording (`.build/learning-curve-v1.json`):

| Training recordings | Attempts (makes) | Attempt recall | False events / background min | Landing balanced | Kickflip balanced |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 29 (11) | 94% | 5.0 | 58% | 52% |
| 4 | 59 (20) | 93% | 4.6 | 65% | 56% |
| 6 | 86 (35) | 95% | 5.3 | 71% | 65% |
| 8 | 119 (44) | 97% | 4.5 | 76% | 69% |
| 10 | 149 (56) | 97% | 3.0 | 77% | 71% |

Finding attempts is saturated; its weakness is false events, and only 5.3
minutes of background have been reviewed. Landing accuracy is still rising by
roughly 8–10 points per doubling. Extrapolating that trend, which may flatten,
puts about 85% across tricks near 400–500 attempts and about 85% on kickflips
past 1,000. Balance matters more than volume: there are 21 landed kickflips
against 91 missed, and every ollie is a make. All of it is one rider and board.

## What has and has not been checked

Checked on the Mac: the ring under a concurrent writer and reader; the firmware
compiles for the Feather; the Swift pipeline reproduces the Python events from
a real recording excerpt; the app, in the simulator, detects a landed trick
from a fake board replaying that excerpt in real time
(`SKATEJUDGE_LIVE_REPLAY=… tests/mobile_board_fixture.py`); stitching cuts
without re-encoding on a synthetic 120 fps HEVC movie.

Not checked, and needing the board and a phone: the new firmware on hardware
(including that live polling leaves acquisition healthy); live latency over the
board's Wi-Fi; the chime while the camera records, its loudness and route; the
trick edit on real iPhone footage; any of it while skating.
