# Rerun review and labeling plan

Use Rerun as an optional desktop analysis tool for synchronized skateboard video,
six-axis IMU readings, and model predictions. Keep capture and saved labels
independent of it. An embedded labeling panel is one possible review interface;
a browser video player with linked plots is another. This is an implementation
plan; the viewer, exporter, and labeling panel have not been built.

## Primary capture workflow: tripod iPhone and Mac

Use the iPhone on a tripod as the Mac's camera through Continuity Camera. Run
the recorder and its browser UI on the Mac. Record batches of approximately
ten attempts, with spoken trick/outcome annotations and breaks between clips.
This workflow uses the existing local UI; phone-hosted browser capture and
remote-control networking are optional alternatives.

Apple supports wired and wireless Continuity Camera, and the iPhone microphone
is a separate input. If the Mac is near the tripod, use a USB connection to the
iPhone for the first pilot, while the board remains wireless. For wireless
Continuity Camera, test simultaneous operation with the Mac connected to the
ESP32 access point before collecting a batch. Apple documents that wireless
Continuity Camera can affect the iPhone's Wi-Fi connection.
See [Apple's setup and requirements](https://support.apple.com/en-us/102546).

The current browser UI selects the camera but requests the default microphone.
Set the iPhone as the Mac's input under System Settings > Sound > Input (and
check browser microphone settings), then enable microphone audio and reopen
the camera. A useful future UI addition is explicit microphone selection and
an input-level meter. For now, make a short recording and play it back to verify
that speech is captured clearly from the actual attempt location.

For each batch:

1. Frame the entire trick area, rider, board, and roll-away, with the sync stick
   visible for the start/end markers. Prefer a fixed view for consistent review.
2. With the sensor recorder running, start video recording on the Mac.
3. Trigger the existing three-second countdown and capture its LED flash with
   the board in frame. This is a separate action from starting video today.
4. Walk to the board and perform roughly ten attempts. Announce "Attempt
   kickflip" before each, then "Result make" or "Result fail" after its outcome
   is clear. Preserve the full attempt and roll-away between announcements.
5. Return to the Mac, ensure the board's stick is visible, and trigger another
   countdown. Wait for the final flash, then click Stop & save video and wait
   for Saved.
6. Take a break and repeat. Each video start creates a new clip. The sensor
   recorder can remain running across clips and breaks; stopping video does
   not stop sensor recording. Quit the terminal recorder after the final save.

Align each clip using its own visible start/end flashes. Continuous sensor
data may cover periods without video; retain those coverage boundaries. Do not
assume every unreviewed break is a labeled background interval.

Treat batches from the same outing as one group when splitting training and
evaluation data, even if recordings were restarted into separate session folders.
Ten-attempt batches make capture manageable; they are not independent tests of
generalization. The first batch should validate framing, voice audibility,
complete video saves, sync visibility, and sensor continuity before scaling up.

## Scope and audio

Rerun is useful for inspecting the relationship between board motion, visible
events, and model errors. It does not provide our complete attempt-labeling
workflow, and its current video implementation has no audio playback support.
For reviewing a clip with sound, use a browser video player; an embedded Rerun
view would require a separately synchronized audio player. Audio waveform or
model-score plots can still be displayed as scalar time series.
See [Rerun's video limitations](https://rerun.io/docs/concepts/logging-and-ingestion/video#other-limitations).

Prioritize reliable capture and a review screen that can play sound. Build the
Rerun exporter when synchronized motion/prediction inspection is useful; decide
whether embedding it is worthwhile after testing one real session.

The capture UI supports microphone audio through an unchecked-by-default
checkbox. When enabled, audio is included in the saved video container. The
ESP32/IMU hardware in this repo does not capture audio.

## Spoken annotations to reduce review work

Use the recorded microphone track to announce the intended trick before an
attempt and its outcome afterward. Treat these as human-reported annotations
that transcription turns into editable proposals. This is the next proposed
labeling aid; speech recognition and automatic label creation are not implemented.

A short, consistent vocabulary should simplify parsing:

```text
"Attempt kickflip"
    [perform the attempt and observe the outcome]
"Result make"            or "Result fail"
```

Optional attempt numbers can disambiguate repeated or interrupted announcements:
"Attempt twelve, kickflip" / "Result twelve, make". Also accept explicit bail,
fall, unknown, and cancel commands. Initially parse only explicit command
phrases; ordinary conversation should not become annotations. A missing outcome,
conflicting result, unmatched number, or multiple motion events between commands
goes to review. A new attempt must not silently provide the previous attempt's
outcome.

Proposed processing pipeline:

1. Record continuous video with microphone audio and the usual LED sync flashes.
   The existing microphone checkbox supports this today. A phone's separate
   camera recording with audio is also usable after transfer and alignment.
   Check that speech is intelligible at the actual microphone distance outdoors.
2. After recording, transcribe locally on the laptop using a timestamp-producing
   speech model. For example, `faster-whisper` supports word timestamps and voice
   activity detection; download its model before going offline. Supply a small
   trick vocabulary as transcription context, then normalize recognized names.
   See the [implementation documentation](https://github.com/SYSTRAN/faster-whisper).
3. Preserve the media timeline when extracting audio. Retain any audio/video
   track offset and restore original timestamps if silence is removed. Map speech
   timestamps through the same video-to-sensor alignment used for review.
4. Parse paired commands into provisional attempt intervals. Store intended
   trick, reported outcome, original transcript, source clip, speech timestamps,
   alignment revision, and transcription model/version. Speech timing brackets
   the action approximately; it does not identify takeoff or landing precisely.
5. Search each interval for motion/CV candidates and include the approach and
   roll-away when proposing the training window. Flag intervals with no clear
   candidate or multiple candidates rather than forcing a single event.
6. Show the proposed clip, transcript, and label for quick acceptance or correction.
   First validate a small batch against manual review; measure pairing accuracy,
   outcome accuracy, and review time saved before increasing automation.

Keep raw transcript events and parsed proposals in separate sidecars, such as
`voice_events.jsonl` and `label_proposals.jsonl`. Reprocessing must preserve human
corrections and avoid duplicate proposals. Accepting a proposal should reuse the
existing label validation and revision path.

The current outcome schema accepts make/bail/fall/background/unknown, but not a
generic fail. Preserve a spoken "fail" as a binary failure with unspecified
subtype in the proposal. Add an explicit coarse-failure representation before
accepting it into training labels; do not silently convert fail to bail or fall.
Store the announced trick as intended, with observed trick identity separate if
that becomes a training target.

Use reviewed labels for evaluation. If audio or video-with-audio becomes a model
input later, exclude spoken annotations (especially the reported outcome) from
the input: learning to recognize the word "make" would not demonstrate landing
detection. Motion-only training can use the spoken annotations as label metadata.

Local transcription after capture avoids depending on browser speech services
while connected to the ESP32's network. Some browser recognition implementations
require a remote service; see [SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition).

## Phone access to the laptop recorder

This is an optional alternative to the primary Continuity Camera workflow.
Phone access is proposed and is not currently implemented. The server binds to
`127.0.0.1` and checks its exact Host and Origin. Add explicit LAN serving,
configured allowed origins, and session pairing for remote access.

With the current firmware, the phone and laptop can join the ESP32's
`SkateJudge-XXXX` access point. The laptop remains the sole UDP recorder; the
phone connects to the laptop's address on that network. Verify client-to-client
connectivity and sample loss on the actual hardware. Internet access is not
required. Espressif's `softAP` API defaults to four connected clients:
[Wi-Fi API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/wifi.html#softap).

Two phone roles need different implementation work:

| Role | Required behavior |
| --- | --- |
| Phone camera and microphone | Phone captures media and uploads to the laptop; serve HTTPS with a certificate trusted by the phone |
| Remote for laptop recording | Phone sends commands to the laptop's open capture page; add command delivery and shared recording status |

Opening the existing UI on another device would select that device's media,
not automatically control the laptop's camera. Phone camera/microphone access
requires a secure context: ordinary HTTP to the laptop's LAN address does not
get localhost's exception. See [getUserMedia requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia#privacy_and_security).

Continuous phone video uploads share the ESP32 access point's radio with motion
data. Test that workload before relying on it; recording video locally on the
phone and transferring it afterward reduces this traffic. A shared router is
another network option, requiring firmware support for joining that network.

## Why it fits

Rerun supports video frame references, scalar time-series views, and reusable
layouts. Video frames can have a recording timeline timestamp different from
their timestamp inside the video, which fits our existing LED-based alignment.
See the [frame reference API](https://rerun.io/docs/reference/types/archetypes/video_frame_reference),
[scalar plots](https://rerun.io/docs/reference/types/archetypes/scalars), and
[blueprints](https://rerun.io/docs/concepts/visualization/blueprints).

Use Rerun for playback and inspection. Implement our own controls for attempt
boundaries, outcome selection, notes, and saving revisions. The JavaScript
viewer exposes playback-time callbacks and time setters, so buttons can capture
the current playhead or jump to an attempt. Use the JavaScript package for this
integration; the simple hosted iframe lacks programmable viewer control.
See [embedding](https://rerun.io/docs/howto/integrations/embed-web) and the
[WebViewer API](https://ref.rerun.io/docs/js/stable/web-viewer/classes/WebViewer.html).

## First milestone: synchronized replay

Build a Python exporter for one closed session, producing a derived `.rrd`
recording and a reusable layout. Its inputs are `samples.csv`, `metadata.json`,
`events.jsonl`, optional `labels.jsonl`, the selected video, and
`video_sync.jsonl`. Keep the original session files as the source data; the
Rerun export can be regenerated.

Arrange the review screen as:

| View | Contents |
| --- | --- |
| Video | Original scene, with CV overlays when available |
| Acceleration | `ax`, `ay`, `az`, and magnitude, with units |
| Rotation | `gx`, `gy`, `gz`, and magnitude, with units |
| Review context | Outcome labels, sync markers, missing samples, and recording errors |
| Predictions, added later | Attempt and outcome scores from each model |

Use a single duration timeline named `session_time`, based on CSV `t_s`.
For each frame, use its presentation timestamp within the video and the existing
`capture.session.video_mapping` calculation:

```text
session_time_seconds = scale * original_video_seconds + offset
```

The frame reference still points to the timestamp inside the video asset.
Do not substitute host arrival time or derive video times from an assumed frame
rate. The JavaScript time API uses nanoseconds for time timelines; convert
explicitly when writing labels in seconds.

Require measured sync points before presenting video as aligned. Provide an
alignment step that lets the reviewer locate a visible flash and associate its
video time with a recorded sync ID. Two separated flashes estimate drift; an
additional flash can check the fit. An unaligned preview must display that state.

The current `AssetVideo` file path supports MP4. The webcam recorder now prefers
H.264 MP4 when supported, with WebM as a browser-compatibility fallback.
Produce a derived H.264 MP4 review copy when the source format needs conversion,
including the existing WebM recordings. Preserve the original, record any
timestamp rebasing, and map review timestamps back to original video time.
Verify visible flashes before
using the copy for labels. The native viewer's H.264 path requires FFmpeg;
browser decoding depends on browser support.
See [Rerun video support](https://rerun.io/docs/concepts/logging-and-ingestion/video).

Show gaps and missing-video intervals explicitly. Do not interpolate over long
sensor gaps or leave the last video frame looking current outside its coverage.
When reading labels, use the last revision per ID, as the recorder already does.

Acceptance: opening a session shows video and plots on the same playhead;
beginning/end flashes agree with the logged sync events; gaps and limited video
coverage are visible. The existing short webcam recording can exercise playback
and sync if it contains identifiable flashes; it cannot establish model accuracy.

## Optional second milestone: embedded labeling

Serve a local review page for closed sessions with the embedded Rerun viewer
and a compact labeling panel. Bundle viewer assets locally and pin matching
Python SDK and JavaScript viewer versions. This adds a small frontend build;
it does not require React. Validate version and browser compatibility during
the first replay prototype.

Provide Mark start, Mark end, outcome selection (`make`, `bail`, `fall`,
`background`, `unknown`), trick name, note, Save, and previous/next attempt.
Allow optional takeoff, board-contact, and outcome-confirmed timestamps for
reviewing segmentation and decision delay. These phase timestamps require a
schema extension or a sidecar keyed by attempt ID; they are not currently
supported by the recorder's label command.

Reuse the existing label validation and append-only revision behavior through a
shared backend function. Store confirmed outcomes in `labels.jsonl`. Keep model
suggestions separate until a reviewer accepts them. Record original video
timestamps and the alignment revision used; changing sync must flag affected
labels for remapping/review instead of silently leaving stale sensor times.

Acceptance: mark, save, reload, and revise an attempt without changing its ID;
invalid or overlapping intervals are rejected; restarting the review app retains
labels. Test time-unit conversion, alignment drift, and label revision handling.

## Third milestone: compare algorithms

Import CV proposals through a small adapter containing the source video,
timestamps, model version, outcome scores, and optional boxes/keypoints. Define
its exact fields after inspecting the CV repo. Map its source timestamps through
the same alignment as the video.

Log candidate attempt boundaries and predictions from the threshold/state-machine
baseline, feature-based classifier, and time-series model. Include model version
and the time at which each prediction would become available during live use.
Keep reviewed outcomes visible alongside predictions so disagreements can be
inspected quickly.

Export training examples directly from original sensor data and reviewed labels,
retaining session/rider IDs for grouped evaluation. The Rerun recording remains
a review artifact. Evaluate complete held-out sessions, including background,
for missed attempts, false triggers, make/fail errors, and decision delay.

The first deliverable is one session that can be aligned, replayed, labeled, and
reopened reliably. Model training follows that verified review workflow.
