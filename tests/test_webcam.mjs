import assert from 'node:assert/strict';
import test from 'node:test';
import {CHUNK_BYTES, WebcamCapture, recordThenCountdown} from '../capture/webcam.mjs';

class FakeRecorder {
  static isTypeSupported(type) { return type.startsWith('video/webm'); }
  constructor(stream, options) { this.mimeType = options.mimeType; this.state = 'inactive'; }
  start() { this.state = 'recording'; this.onstart(); }
  emit(data) { this.ondataavailable({data: new Blob([data])}); }
  stop() {
    this.state = 'inactive';
    queueMicrotask(() => { this.emit('tail'); this.onstop(); });
  }
}
const stream = {
  getAudioTracks: () => [],
  getVideoTracks: () => [{label: 'Test camera', getSettings: () => ({width: 1280, height: 720, frameRate: 30})}],
};
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve)); };
function setup(options = {}) {
  const calls = [];
  const capture = new WebcamCapture({Recorder: FakeRecorder, newId: () => 'a'.repeat(32),
    send: async (path, data) => { calls.push([path, data]); return {filename: 'webcam-test.webm'}; }, ...options});
  return {capture, calls};
}

test('video preserves final blob and finishes only after every ordered upload', async () => {
  const {capture, calls} = setup();
  await capture.start(stream);
  assert.equal(capture.phase, 'recording');
  capture.recorder.emit('head');
  capture.stop();
  await settle();
  assert.equal(capture.phase, 'saved');
  const chunks = calls.filter(([path]) => path.includes('/chunk/'));
  assert.deepEqual(await Promise.all(chunks.map(([, blob]) => blob.text())), ['head', 'tail']);
  assert.equal(calls.at(-1)[0], '/api/video/finish');
  assert.equal(calls.at(-1)[1].bytes, 8);
  assert.equal(calls[0][1].capture.audio, false);
  assert.equal(capture.pendingBytes, 0);
});

test('large MediaRecorder blobs are split into bounded requests without losing bytes', async () => {
  const {capture, calls} = setup();
  await capture.start(stream);
  capture.recorder.emit(new Uint8Array(CHUNK_BYTES + 7));
  capture.stop();
  await settle();
  const chunks = calls.filter(([path]) => path.includes('/chunk/'));
  assert.deepEqual(chunks.map(([, blob]) => blob.size), [CHUNK_BYTES, 7, 4]);
  assert.equal(capture.phase, 'saved');
});

test('failed upload stops capture, retains pending bytes, and can retry before finalizing', async () => {
  let fail = true;
  const uploads = [];
  const {capture} = setup({send: async (path, data) => {
    if (path.includes('/chunk/')) {
      if (fail) throw new Error('disk full');
      uploads.push(await data.text());
    }
    return {filename: 'retry.webm'};
  }});
  await capture.start(stream);
  capture.recorder.emit('head');
  await settle();
  assert.equal(capture.phase, 'error');
  assert.equal(capture.recorder.state, 'inactive');
  assert.equal(capture.pendingBytes, 8);
  assert.match(capture.message, /NOT fully saved/);
  fail = false;
  capture.retry();
  await settle();
  assert.equal(capture.phase, 'saved');
  assert.deepEqual(uploads, ['head', 'tail']);
});

test('finishing while an earlier chunk is in flight cannot skip the tail', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const calls = [];
  const {capture} = setup({send: async (path, data) => {
    if (path.endsWith('/0')) await gate;
    calls.push([path, data]);
    return {filename: 'ordered.webm'};
  }});
  await capture.start(stream);
  capture.recorder.emit('head');
  capture.stop();
  await settle();
  assert.equal(capture.phase, 'saving');
  assert.equal(calls.length, 1);
  release();
  await settle();
  assert.equal(capture.phase, 'saved');
  assert.equal(calls.at(-1)[1].chunks, 2);
});

test('failed final acknowledgement is retryable without re-uploading video', async () => {
  let fail = true;
  let chunks = 0;
  const {capture} = setup({send: async path => {
    if (path.includes('/chunk/')) chunks++;
    if (path.endsWith('/finish') && fail) throw new Error('lost response');
    return {filename: 'saved.webm'};
  }});
  await capture.start(stream);
  capture.stop();
  await settle();
  assert.equal(capture.phase, 'error');
  fail = false;
  capture.retry();
  await settle();
  assert.equal(capture.phase, 'saved');
  assert.equal(chunks, 1);
});

test('unsupported recording format fails without allocating a server video', async () => {
  class Unsupported extends FakeRecorder { static isTypeSupported() { return false; } }
  const {capture, calls} = setup({Recorder: Unsupported});
  await assert.rejects(capture.start(stream), /cannot record/);
  assert.equal(capture.phase, 'idle');
  assert.equal(calls.length, 0);
});

test('camera startup failure abandons only its allocated video and never says saved', async () => {
  class Broken extends FakeRecorder { start() { throw new Error('camera disconnected'); } }
  const {capture, calls} = setup({Recorder: Broken});
  await assert.rejects(capture.start(stream), /camera disconnected/);
  assert.equal(capture.phase, 'idle');
  assert.equal(calls.at(-1)[0], '/api/video/abandon');
});

test('recording prefers compatible MP4 codecs while retaining audio and WebM fallback', async t => {
  const cases = [
    {name: 'H.264 MP4 over WebM', audio: false,
      supported: ['video/mp4;codecs=avc1', 'video/mp4', 'video/webm;codecs=vp8'],
      expected: 'video/mp4;codecs=avc1'},
    {name: 'H.264 and AAC with microphone enabled', audio: true,
      supported: ['video/mp4;codecs=avc1,mp4a.40.2', 'video/mp4;codecs=avc1', 'video/mp4', 'video/webm'],
      expected: 'video/mp4;codecs=avc1,mp4a.40.2'},
    {name: 'browser-selected MP4 codecs over WebM', audio: false,
      supported: ['video/mp4', 'video/webm;codecs=vp8'], expected: 'video/mp4'},
    {name: 'MP4-only browser with microphone enabled', audio: true,
      supported: ['video/mp4'], expected: 'video/mp4'},
    {name: 'WebM-only browser', audio: false,
      supported: ['video/webm;codecs=vp8'], expected: 'video/webm;codecs=vp8'},
    {name: 'WebM-only browser with microphone enabled', audio: true,
      supported: ['video/webm;codecs=vp8,opus'], expected: 'video/webm;codecs=vp8,opus'},
  ];
  for (const scenario of cases) await t.test(scenario.name, async () => {
    class Recorder extends FakeRecorder {
      static isTypeSupported(type) { return scenario.supported.includes(type); }
    }
    const {capture, calls} = setup({Recorder});
    await capture.start({...stream, getAudioTracks: () => scenario.audio ? [{}] : []});
    assert.equal(calls[0][1].mime_type, scenario.expected);
    assert.equal(calls[0][1].capture.audio, scenario.audio);
    assert.match(capture.message, scenario.expected.startsWith('video/mp4') ? /MP4/ : /WebM/);
    capture.stop();
    await settle();
    assert.equal(capture.phase, 'saved');
  });
});

test('upload backlog stops recording and retains all emitted data for saving', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const {capture} = setup({send: async path => {
    if (path.includes('/chunk/')) await gate;
    return {filename: 'backlog.webm'};
  }});
  await capture.start(stream);
  capture.recorder.emit(new Uint8Array(33 * CHUNK_BYTES));
  await settle();
  assert.equal(capture.recorder.state, 'inactive');
  assert.equal(capture.phase, 'saving');
  assert.match(capture.warning, /fell behind/);
  release();
  await settle();
  assert.equal(capture.phase, 'saved');
  assert.equal(capture.bytes, 33 * CHUNK_BYTES + 4);
  assert.equal(capture.pendingBytes, 0);
});

test('sync waits for asynchronous recorder startup and a saved video chunk', async () => {
  let startRecorder, releaseUpload;
  class DelayedRecorder extends FakeRecorder {
    start() { startRecorder = () => { this.state = 'recording'; this.onstart(); }; }
  }
  const gate = new Promise(resolve => { releaseUpload = resolve; });
  const {capture} = setup({Recorder: DelayedRecorder, send: async path => {
    if (path.includes('/chunk/')) await gate;
    return {filename: 'sync.webm'};
  }});
  const syncCalls = [];
  const work = recordThenCountdown({capture, stream, send: async path => { syncCalls.push(path); }});
  await settle();
  assert.equal(capture.phase, 'starting');
  assert.deepEqual(syncCalls, []);
  startRecorder();
  await settle();
  assert.deepEqual(syncCalls, []);
  capture.recorder.emit('first video data');
  await settle();
  assert.deepEqual(syncCalls, []);
  releaseUpload();
  await work;
  assert.deepEqual(syncCalls, ['/api/prepare', '/api/countdown']);
  capture.stop(); await settle();
});

test('another sync keeps the existing video, and a failed sensor preflight cannot flash', async () => {
  const {capture} = setup();
  await capture.start(stream); capture.recorder.emit('first'); await settle();
  const recorder = capture.recorder, calls = [];
  await assert.rejects(recordThenCountdown({capture, stream, send: async path => {
    calls.push(path); throw new Error('sensor read failed');
  }}), /sensor read failed/);
  assert.equal(capture.recorder, recorder);
  assert.deepEqual(calls, ['/api/prepare']);
  assert.equal(capture.phase, 'recording');
  capture.stop(); await settle();
});

test('stopped video and missing camera cannot trigger a countdown', async () => {
  const {capture} = setup(); let sent = false;
  await assert.rejects(recordThenCountdown({capture, stream: null, send: async () => { sent = true; }}), /camera/);
  await capture.start(stream); capture.stop(); await settle();
  await assert.rejects(capture.waitForFirstChunk(), /stopped|not recording/);
  assert.equal(sent, false);
});
