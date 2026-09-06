import assert from 'node:assert/strict';
import test from 'node:test';
import {CHUNK_BYTES, WebcamCapture} from '../capture/webcam.mjs';

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

test('MP4-only browsers negotiate MP4 instead of a hardcoded WebM format', async () => {
  class MP4 extends FakeRecorder { static isTypeSupported(type) { return type === 'video/mp4'; } }
  const {capture, calls} = setup({Recorder: MP4});
  await capture.start(stream);
  assert.equal(calls[0][1].mime_type, 'video/mp4');
  capture.stop();
  await settle();
  assert.equal(capture.phase, 'saved');
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
