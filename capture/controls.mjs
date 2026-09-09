import {WebcamCapture, recordThenCountdown, request} from './webcam.mjs';

const $ = id => document.getElementById(id);
const clock = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds) % 60).padStart(2, '0')}`;
const capture = new WebcamCapture({changed: renderCamera});
let stream = null;
let opening = false;
let fresh = false;
let submitting = false;
let disconnected = 0;
let onboard = false;
let finishing = false;
let canSync = false;
let canDownload = false;
let syncError = '';
let boardError = '';
const savedIds = new Set();
const supported = !!(navigator.mediaDevices?.getUserMedia && globalThis.MediaRecorder);

function renderCamera() {
  const busy = capture.busy || submitting || finishing;
  $('enable-camera').disabled = !supported || opening || busy;
  $('enable-camera').textContent = stream ? 'Apply camera settings' : 'Enable camera';
  $('disable-camera').disabled = !stream || opening || busy;
  $('camera').disabled = opening || busy || !stream;
  $('audio').disabled = opening || busy;
  $('start-video').disabled = !stream || !fresh || opening || busy;
  $('stop-video').disabled = capture.phase !== 'recording' || submitting || finishing;
  $('external-video').disabled = busy;
  $('countdown').disabled = submitting || finishing || opening || !canSync ||
    (capture.busy && capture.phase !== 'recording');
  $('countdown').textContent = capture.phase === 'recording' || $('external-video').checked
    ? 'Start 3-second sync countdown' : 'Record video + sync';
  $('stop-video').textContent = onboard ? 'Stop & save video + board data' : 'Stop & save video';
  $('retry-board').hidden = !onboard || !canDownload || !(capture.phase === 'saved' || $('external-video').checked);
  $('retry-board').disabled = finishing || submitting;
  $('retry-video').hidden = capture.phase !== 'error';
  $('retry-video').disabled = capture.uploading || !capture.ended;
  $('camera-state').textContent = ({starting: 'Starting…', recording: '● Recording', saving: 'Saving…',
    saved: 'Saved', error: 'Not fully saved'})[capture.phase] || (stream ? 'Preview only — not recording' : 'Camera off');
  $('camera-state').dataset.recording = String(capture.phase === 'recording');
  $('video-time').textContent = clock(capture.elapsed);
  if (capture.message) $('camera-message').textContent = capture.message;
  $('camera-message').dataset.error = String(capture.phase === 'error' || !!capture.warning);
  if (capture.phase === 'saved' && !savedIds.has(capture.id)) {
    savedIds.add(capture.id);
    const item = document.createElement('li');
    item.textContent = capture.file.filename;
    $('saved-clips').append(item);
    $('saved-clips').hidden = false;
  }
}

function releaseCamera() {
  if (stream) for (const track of stream.getTracks()) track.stop();
  stream = null;
  $('preview').srcObject = null;
}

async function openCamera() {
  if (!supported || opening || capture.busy) return;
  opening = true;
  capture.message = 'Waiting for camera permission…';
  renderCamera();
  const selected = $('camera').value;
  releaseCamera();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {width: {ideal: 1920}, height: {ideal: 1080}, frameRate: {ideal: 60},
              ...(selected ? {deviceId: {exact: selected}} : {})},
      audio: $('audio').checked,
    });
    $('preview').srcObject = stream;
    await $('preview').play();
    const track = stream.getVideoTracks()[0];
    const settings = track.getSettings();
    for (const input of stream.getTracks()) input.addEventListener('ended', () => {
      if (capture.busy) capture.stop('Camera or microphone disconnected; video ended early.');
      releaseCamera();
      if (!capture.busy) capture.message = 'Camera disconnected. Enable it again to continue.';
      renderCamera();
    });
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      $('camera').replaceChildren();
      for (const device of devices.filter(device => device.kind === 'videoinput')) {
        const option = document.createElement('option');
        option.value = device.deviceId;
        option.textContent = device.label || 'Camera';
        $('camera').append(option);
      }
      $('camera').value = settings.deviceId;
    } catch { /* The default camera remains usable without device enumeration. */ }
    capture.message = `Preview only: ${settings.width} × ${settings.height}, ${Math.round(settings.frameRate || 0)} fps. ${stream.getAudioTracks().length ? 'Microphone on.' : 'Microphone off.'} Click Start video recording when ready.`;
  } catch (error) {
    releaseCamera();
    capture.message = error.name === 'NotAllowedError'
      ? 'Camera permission denied. Allow camera access for this local page in browser/system settings, then try again. Microphone permission is needed only if enabled.'
      : `Cannot open camera: ${error.message}. Check that it is connected and not in use by another app.`;
  } finally { opening = false; renderCamera(); }
}

async function refresh() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store', signal: AbortSignal.timeout(2500)});
    if (!response.ok) throw new Error('Unavailable');
    const status = await response.json();
    disconnected = 0;
    fresh = !!status.fresh;
    onboard = !!status.onboard;
    canSync = !!status.can_sync;
    canDownload = !!status.can_download;
    $('number').textContent = status.remaining ?? (status.phase === 'done' ? '✓' : status.phase === 'waiting' ? '…' : '—');
    $('message').textContent = syncError || status.message;
    $('session').textContent = status.session ?? 'Waiting for session';
    $('samples').textContent = `${(status.samples ?? 0).toLocaleString()} samples`;
    $('duration').textContent = `${clock(status.duration_s ?? 0)} sensor time`;
    $('demo').hidden = !status.synthetic;
    $('folder').textContent = status.session_path ? `Session folder: ${status.session_path}` : '';
    $('board-status').textContent = boardError || status.board_message || '';
  } catch {
    fresh = false;
    canSync = false;
    $('countdown').disabled = true;
    $('number').textContent = '—';
    $('message').textContent = 'Recorder unavailable. Check the terminal; sensor recording may have stopped.';
    if (++disconnected >= 3 && capture.phase === 'recording') capture.stop('Sensor recorder connection lost; video ended early.');
  } finally {
    renderCamera();
    setTimeout(refresh, 250); // No overlapping polls when the recorder stops responding.
  }
}

$('enable-camera').addEventListener('click', openCamera);
$('disable-camera').addEventListener('click', () => { releaseCamera(); capture.message = 'Camera off.'; renderCamera(); });
$('audio').addEventListener('change', () => { if (stream) void openCamera(); });
$('camera').addEventListener('change', () => { if (stream) void openCamera(); });
$('start-video').addEventListener('click', async () => {
  try { await capture.start(stream); } catch { /* The capture controller displays the error. */ }
});
$('stop-video').addEventListener('click', async () => {
  boardError = '';
  finishing = true; renderCamera();
  try {
    await capture.stopAndSave();
    if (onboard) {
      $('board-status').textContent = 'Closing the onboard log and downloading the recording…';
      await request('/api/board/finish', {}, false, 120000);
    }
  } catch (error) { boardError = error.message; $('board-status').textContent = boardError; }
  finally { finishing = false; renderCamera(); }
});
$('retry-board').addEventListener('click', async () => {
  boardError = '';
  finishing = true; renderCamera();
  try { await request('/api/board/finish', {}, false, 120000); }
  catch (error) { boardError = error.message; $('board-status').textContent = boardError; }
  finally { finishing = false; renderCamera(); }
});
$('retry-video').addEventListener('click', () => capture.retry());
$('countdown').addEventListener('click', async () => {
  syncError = '';
  submitting = true;
  $('countdown').disabled = true;
  try {
    if (!$('external-video').checked && !stream) await openCamera();
    $('message').textContent = 'Starting video and waiting for recorded data…';
    await recordThenCountdown({capture, stream, external: $('external-video').checked});
  } catch (error) { syncError = error.message; $('message').textContent = syncError; }
  finally { submitting = false; renderCamera(); }
});
$('external-video').addEventListener('change', renderCamera);
window.addEventListener('beforeunload', event => {
  if (capture.busy) { event.preventDefault(); event.returnValue = ''; }
});
window.addEventListener('pagehide', releaseCamera);
if (!supported) capture.message = 'Webcam recording is unavailable in this browser. Open the printed localhost URL in a browser with camera/MediaRecorder support, or record with your iPhone separately.';
renderCamera();
void refresh();
