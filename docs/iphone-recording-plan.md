# Native iPhone session recording plan

Draft, 2026-10-26. Scope: replace the laptop recording workflow with an iPhone
app. Confirmed requirements: iPhone video is the default, using the highest
supported recording frame rate, and an external-camera option is available in
the first release. This document is a plan.

Implementation update, 2026-10-27: the first development version is in
[`ios/`](../ios/README.md), including camera capture, external-camera controls,
verified motion downloads, session export, and desktop import. The app guide
records implemented behavior and remaining physical-device validation; the
acceptance gates below remain the release criteria.

The first useful release should let someone take just their board and iPhone to
the skatepark, record several sessions without internet, and bring complete,
recoverable recordings back into the existing desktop review workflow.

**Recommended approach:** SwiftUI interface, AVFoundation camera capture, and a
Swift client for the board's existing HTTP protocol. Keep motion acquisition on
the ESP32 and keep the existing Python decoder as the authority for sensor time
and dataset quality in the first release.

**What already exists**

| Existing component | Role in the iPhone plan |
| --- | --- |
| [`firmware/imu_logger`](../firmware/imu_logger/imu_logger.ino) | Keep onboard acquisition, SD/flash storage, timestamped LED edges, battery/status reporting, and HTTP commands. |
| [`capture/onboard.py`](../capture/onboard.py) | Port the board client, readiness checks, recording identity handling, and verified downloads to Swift. |
| [`capture/onboard_log.py`](../capture/onboard_log.py) | Reuse on desktop for binary decoding, sensor clock reconstruction, CSV/events generation, and quality checks. |
| [`capture/controls.mjs`](../capture/controls.mjs), [`capture/webcam.mjs`](../capture/webcam.mjs) | Use recording/countdown behavior as a reference for the native coordinator. |
| [`capture/video.py`](../capture/video.py), [`capture/session.py`](../capture/session.py) | Preserve compatible session metadata, clip sidecars, and alignment conventions. |
| [`viz/trick_review.py`](../viz/trick_review.py) | Continue reviewing exported video and matching LED flashes; it already accepts MOV and MP4. |

The board currently records nominal 208 Hz motion locally, and continues through
a client disconnect. Wi-Fi carries controls/status during acquisition and files
after acquisition stops. The current protocol already supports byte-offset
downloads and CRC32 verification. No firmware change is expected for the first
end-to-end prototype; confirm this against the actual iPhone in milestone 1.

**First release experience**

Three screens are sufficient:

1. **Connect:** enter or remember the board's `SkateJudge-XXXX` network; join it;
   show board battery, SD/flash destination, free recording capacity, sensor
   readiness, and a test-flash button. Distinguish a board connection from
   internet availability. Select rider and deck, remembering the last choices.
2. **Record:** large camera preview, elapsed time, separate video and board
   recording indicators, battery/storage warnings, and three primary actions:
   Record, Sync flash, and Finish & save. Offer **iPhone camera** (default) and
   **External camera** before starting a session. The iPhone defaults to **Maximum
   FPS**, prioritizing frame rate over resolution and displaying the selected
   camera, resolution, and rate. Offer supported frame rates and resolutions as
   explicit choices above the preview for smaller files, plus optional microphone
   audio. Remember these choices and show any rate adjustment needed when changing
   resolution before recording. Keep
   orientation consistent within a clip and record the actual capture settings.
   Decide the minimum iOS version after checking the target phone.
3. **Sessions:** local recordings with thumbnail, date, duration, rider/deck,
   video status, motion-download status, and export/recovery actions. Include
   a board-recordings view for retrieving logs from earlier interrupted runs.
   Offer explicit removal of a board copy only after its local raw file passes
   verification. Keep local deletion a separate action.

External-camera mode ships with the first complete recording flow. It skips
iPhone video capture and asks the person to confirm their separate camera is
recording before the first sync. It keeps the same board controls, recovery, and
export flow, with video marked external/pending and an optional camera/clip note
for matching footage later. Finishing reminds the person to stop the external
camera after the final flash. The app does not claim to start or stop that camera.
Automatic import or control of the external camera is a separate feature.

**Maximum-frame-rate capture**

Discover the phone's capabilities at runtime. Do not impose a 60, 120, or 240 fps
ceiling or infer the available rate solely from the iPhone model name. Enumerate
available rear cameras and formats, prefer the highest recordable frame rate,
then the highest resolution at that rate; prefer the main rear camera when those
are equal. Show any lens/resolution tradeoff before recording. An explicit lens
choice recomputes the maximum for that lens. Freeze the choice for each clip.

Use AVFoundation's
[`videoSupportedFrameRateRanges`](https://developer.apple.com/documentation/avfoundation/avcapturedevice/format/videosupportedframerateranges)
and configure `activeFormat` and the matching minimum/maximum frame durations
under the device configuration lock. Verify that the chosen format, codec, movie
output, and optional audio input work together. Avoid applying a session preset
afterward that resets the selected configuration. Apple's
[capture-format guidance](https://developer.apple.com/documentation/avfoundation/capture-device-formats)
describes explicit format selection for high frame rates, and its
[frame-duration documentation](https://developer.apple.com/documentation/avfoundation/avcapturedevice/activevideominframeduration)
describes locking and supported values.

Use HEVC when required by the selected format and confirm desktop decoding and
review playback. Keep HDR, stabilization, and other optional processing from
reducing the maximum rate; configure only combinations that preserve it. Disable
automatic frame-rate adjustment where supported when requesting a fixed rate;
[Apple documents](https://developer.apple.com/documentation/avfoundation/avcapturedevice/isautovideoframerateenabled)
that automatic adjustment can lower capture rates in low light. High frame rates
also shorten the available exposure time. Show low-light, thermal, and storage
limitations and offer a deliberate lower-rate choice, rather than silently
lowering the requested rate. If the configured pipeline cannot sustain it, flag
the affected clip and offer a lower-rate new clip with another sync.

Preserve every recorded frame and the original real-time presentation timestamps:
one second of skating remains one second of media time, regardless of frame rate.
Apply slow motion only as a review playback setting. Do not bake slow-motion
retiming into the original MOV or export a 30/60 fps copy that discards frames.
The existing alignment fit accepts clock scale only within 2% of real time, so
retimed footage would break the current synchronization contract. External
footage also needs its original real-time timeline for the initial review path.

Persist requested frame rate, configured frame duration, camera/format,
resolution, codec, and any observed capture warnings. During desktop import,
measure delivered cadence and timestamp gaps from the saved video's actual frame
timestamps; a selected camera rate alone does not prove every frame was recorded.
Verify that review can step through each captured frame and play it slowly with
correct audio and LED alignment. For browser-incompatible codecs, add a derived
review copy that preserves frame timestamps and frame count, retaining the
original file and its timing relationship.

**Recording behavior and synchronization**

Model board state, camera state, and transfer state separately. Persist the
session identity and each significant transition so relaunching the app can
reconcile local files with `/status` and `/files`.

The normal flow is:

1. Check permissions for the selected mode, phone disk space, board readiness,
   storage, and existing active recordings. Create and durably save a unique
   session/recording ID. External-camera mode does not require camera/mic access.
2. Start video. Confirm that capture is producing media and the output is
   advancing before starting motion recording. A responsive preview alone is
   insufficient. Prove the exact readiness signal in the camera prototype. In
   external-camera mode, require the person's recording confirmation instead.
3. Send `/start` with the saved ID, then poll until that same recording is healthy
   using the existing rate/error rules. The current accepted rate is 180–230 Hz;
   do not require the real sensor to run at exactly 208 Hz.
4. Run the three-second countdown and request a uniquely identified LED flash.
   Extra Sync actions use the same recording and video.
5. Finish requests an ending countdown/flash while both recordings are running.
   Keep video rolling through the light pulse with a short margin. Establish the
   usable acknowledgement in the prototype; verify physical edges from the
   downloaded log. If the board is unreachable, allow the person to save video
   immediately and mark the ending sync and board stop as pending/unconfirmed.
6. Stop motion and finalize video, tracking their results independently. Poll the
   board until acquisition has stopped before beginning any bulk download.
7. Download and verify the raw file, then mark the assets saved. Preserve fault
   diagnostics and incomplete files for recovery.

Starting the two devices together does not establish accurate synchronization.
The October 30 update adds optional clock evidence for in-app recordings on
iOS 18.2+: the actual AVFoundation start PTS and timed board-clock exchanges,
converted into the movie's capture-clock timeline. Review applies this as an
explicit estimate with network uncertainty; wall time and button time remain
diagnostics. Beginning/end LED matches can refine or replace the estimate and
remain the fallback for older iOS versions, older exports, and external cameras.
See [the implemented timing behavior](../ios/README.md). Automatic LED detection
and an in-app alignment editor remain deferred.

**Native implementation**

Create `ios/SkateJudge/` with a small SwiftUI app and separable services:

| Component | Responsibility |
| --- | --- |
| `BoardClient` | Serialized HTTP requests, typed status, timeout handling, protocol checks, and board/boot/storage identity checks. |
| `CaptureService` | Camera format discovery and maximum-FPS selection, AVFoundation camera/audio configuration, movie recording, media readiness, interruption handling, and finalization. Keep blocking capture setup off the main thread. |
| `RecordingCoordinator` | Start/finish/countdown sequencing, durable recording IDs, reconciliation after uncertain command results, and independent camera/board states. |
| `DownloadManager` | Stream raw bytes to disk, persist progress, resume using the board's offset API, and verify size, CRC32, and packet identity/integrity. |
| `SessionStore` | Atomic manifests, clip sidecars, status journal, fault snapshots, and session library. Store original media in durable app storage, not cache. |
| `SessionExporter` | Package originals and metadata for Files/share sheet transfer, with a versioned manifest and checksums. |

Use Apple's [AVCam sample architecture](https://developer.apple.com/documentation/avfoundation/avcam-building-a-camera-app)
as a reference for SwiftUI and isolated capture services, adopting only APIs
available on the selected deployment target. Start with
[`AVCaptureMovieFileOutput`](https://developer.apple.com/documentation/avfoundation/avcapturemoviefileoutput)
and QuickTime MOV output. Keep movie fragments enabled and test recovery of a
terminated recording; Apple documents that fragments permit playback through the
last written fragment. Do not promise preservation of the unfinished tail.
See [`movieFragmentInterval`](https://developer.apple.com/documentation/avfoundation/avcapturemoviefileoutput/moviefragmentinterval).
Move to sample-buffer capture with `AVAssetWriter` only if the prototype exposes
a concrete maximum-frame-rate, readiness, timing, or recovery requirement the
simpler output cannot meet. Prove the highest supported recording format early,
before treating the capture implementation as settled.

Use `URLSession` against the existing `http://192.168.4.1` API. There is no need
for Bluetooth, a cloud account, or a server to record a session. The key routes
are `/status`, `/start`, `/stop`, `/sync`, `/files`, `/file-info`, `/file`,
`/test-led`, and `/delete`. Maintain explicit `storage=sd|flash` selection when
addressing saved files. Respect current restrictions on file operations while
recording.

Implement resume with `/file?...&offset=N` and the saved local byte count. The
firmware's offset protocol and `X-Log-CRC32` header are the contract; do not assume
standard HTTP Range support or automatic URLSession resume data will work.
Verify total bytes, whole-file CRC32, packet CRCs/sequences, and embedded recording
identity before promoting `.part` to a saved raw file. Preserve incomplete-log
flags even when the transfer itself verifies successfully. A verified transfer
does not mean the recording is suitable for training.

For Wi-Fi setup, use a remembered or entered SSID with
[`NEHotspotConfigurationManager`](https://developer.apple.com/documentation/networkextension/nehotspotconfigurationmanager)
and the Hotspot Configuration capability, with manual Settings instructions as a
fallback. Joining invokes an iOS approval prompt; validate the board with an HTTP
request afterward. Treat camera, optional microphone, and local-network access
as separate permissions. Include
[`NSLocalNetworkUsageDescription`](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy)
and a narrowly scoped local HTTP configuration based on Apple's
[ATS local-network guidance](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking).
Test denied access and reconnecting to an access point with no internet on a real
iPhone. Request Photos permission only if a later save-to-Photos action needs it.

**Files and desktop compatibility**

Use an ordinary session directory as the source of truth, with an optional ZIP
for transfer:

```text
session-<id>/
  metadata.json
  mobile_session.json       # versioned app lifecycle/export manifest
  onboard-<id>.bin          # original, verified board recording
  iphone-<clip-id>.mov      # original video, when using the phone camera
  iphone-<clip-id>.json     # capture details and completion/interruption state
  board_status.jsonl
  board_fault.json          # when a fault was observed
```

Keep existing metadata fields including `onboard_id`, `boot_id`, storage,
rider/deck information, and final board status. Store mobile-only lifecycle
fields in the separate versioned manifest. Preserve actual media duration,
orientation, requested/configured frame rate, measured frame cadence when
available, camera/format, resolution, codec, audio presence, capture mode, and
original filenames. A session can have several clips after an interruption; each
needs its own sync correspondences.

Add a small desktop import entry point that validates the bundle, safely extracts
it into a new directory, and calls the existing `import_log`. It produces the
usual `samples.csv`, `events.jsonl`, and enriched `metadata.json`. Then the current
review tool opens the video and writes `video_sync.jsonl` and labels as today.
Preserve `onboard_stop_error` and fault snapshots through this path. Never infer
missing sensor-clock data from phone timing.

The current review preparation rejects video without an audio track. Update it
to accept silent iPhone and external-camera clips, retaining manual review,
alignment, and IMU suggestions while clearly marking audio suggestions
unavailable. Do not fabricate a microphone track or require a lower frame rate
just to satisfy this existing restriction. Its frame-stepper already indexes
actual presentation timestamps and its normal preparation copies media streams;
verify that both preserve high-frame-rate originals and any derived review copy.

This keeps recording and storage independent of a laptop at the park. Detailed
sensor quality assessment, alignment, labeling, dataset building, and model
training stay on desktop initially. The app must distinguish **saved and transfer
verified** from **decoded and quality checked**. A later Swift decoder can add
in-app motion plots and quality results, but must first match Python outputs on
reference logs, including clock discontinuities, calibration, rollover, missing
packets, partial logs, and motion events.

Export a complete folder/archive through Files or the share sheet. Validate
AirDrop after leaving the board network and verify large exports without
transcoding. Do not make a Photos-only video export the backup of the session.
Check available disk space before creating an archive because it may duplicate
the original assets temporarily.

**Failure behavior is part of the first release**

| Situation | Required behavior |
| --- | --- |
| Wi-Fi disconnects | Keep video recording; show board state as last known/unconfirmed. Reconcile the recording and boot IDs after reconnecting. |
| Start/stop request times out | Query status before retrying. Never create a new ID just because an acknowledgement was lost; do not stop a different active recording. |
| App backgrounds, phone locks, call arrives, or camera fails | Mark the video interruption, finalize what is available, and retain the board session. On return, reconcile the board and offer a new clip plus sync, or finish/download. |
| App is terminated | Recover saved manifests, video fragments where usable, and partial transfers on launch. Show unresolved assets explicitly. |
| Board reboots | Mark the original session interrupted, recover its stored file, and require a new session for the new boot. |
| Board fills or faults | Preserve video and fault diagnostics; download whatever valid raw recording exists once acquisition has stopped. |
| Phone runs low on storage or overheats | Warn early; finalize video when necessary and preserve recovery information. Avoid silently changing capture settings during a clip. |
| Download is interrupted | Resume the same recording/storage/checksum from its persisted byte offset. |
| Download verifies but raw log is incomplete | Preserve it as recoverable/incomplete; do not report a clean recording. |
| User wants to reclaim board space | Require an explicit action and a matching verified local file; send the checksum required by `/delete`. |

Ordinary camera use can be
[interrupted when an iPhone app enters the background](https://developer.apple.com/documentation/avfoundation/avcapturesession/interruptionreason/videodevicenotavailableinbackground).
Keep the screen awake during active video capture, while still handling explicit
locking and system interruptions. Background execution is not a guarantee of
continued video, board polling, or file transfer. Board acquisition remains
independent; reconcile it on return rather than assuming a background stop ran.

**Delivery sequence and acceptance gates**

| Milestone | Deliverable | Acceptance gate |
| --- | --- | --- |
| 1. Board connection and capture feasibility | Xcode app, permissions/setup, board status, start/sync/stop, board file list, verified resumable raw download; camera capability discovery and a short maximum-FPS capture. | On a real phone, record a short board-only session, disconnect/reconnect, interrupt/resume a download, and decode it with Python. Inspect a maximum-FPS movie's actual frame timestamps, real-time duration, codec, and optional audio. |
| 2. Complete recording flow | iPhone maximum-FPS capture and external-camera mode, durable coordinator, countdowns, final flash, separate save statuses, repeat sessions. | Record several one-minute sessions with both flashes visible at the maximum supported rate, plus a session using a separate camera. Export playable originals and intact logs; no false saved state after either subsystem fails. |
| 3. Desktop handoff | Session library, versioned export bundle, desktop import command, diagnostics, explicit board deletion, silent-video review, and high-FPS codec compatibility. | A maximum-FPS phone session and an external-camera session import, align, receive manual labels, and pass existing dataset checks when eligible. Verify frame stepping, slow playback, and a no-audio clip; incomplete fixtures stay flagged. |
| 4. Field reliability and beta | Recovery UI, battery/storage warnings, physical-device fault checks, installation/distribution setup. | Complete several real skating sessions and a 20–30 minute SD recording with maximum-FPS iPhone video; measure achieved cadence and thermal/storage limits. Test phone lock, app termination, denied permissions, board reboot, and transfer interruption. Validate flash drift and video/audio playback in desktop review. |

Start with deployment from Xcode to the target phone. Use TestFlight for a repeatable
beta installation once signing/account setup is available; App Store packaging
is a later release task. Estimates should follow milestone 1, when the target
phone, supported iOS versions, network behavior, and download throughput are known.

Test the Swift state machine against a fake board with lost acknowledgements,
stale responses, changed boot IDs, and interrupted transfers. Use small reference
binary logs to verify checksums and partial-file handling, then run the existing
onboard/video/review tests for the desktop import changes. Camera, permissions,
Wi-Fi, thermal behavior, and interruption tests require physical hardware.

Later additions can include manual alignment and labeling on the phone,
automatic LED detection with human confirmation, a parity-tested Swift decoder,
motion plots, and model inference. The first release is complete when it can
reliably collect and export sessions without bringing a laptop to the park.
