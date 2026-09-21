import assert from 'node:assert/strict';
import test from 'node:test';
import {remainingTime, renderStorage, storageView} from '../capture/storage.mjs';

const budget = {state: 'recording', storage: 'flash', remaining_s: 94.9,
  free_bytes: 500000, rate_source: 'measured', limit: 'storage'};

test('remaining time has readable minute, hour and day scales without rounding up', () => {
  for (const [seconds, expected] of [[0, '0:00'], [59.9, '0:59'], [94.9, '1:34'],
    [3600, '1h 0m'], [86460, '1d 0h'], [NaN, '—'], [null, '—'], [-1, '—']]) {
    assert.equal(remainingTime(seconds), expected);
  }
});

test('active recording distinguishes storage capacity from battery or video duration', () => {
  const view = storageView(budget);
  assert.equal(view.title, 'Recording time left');
  assert.equal(view.value, '≈ 1:34');
  assert.match(view.detail, /Internal flash.*MiB free/);
  assert.match(view.note, /measured log rate/);
  assert.match(view.note, /battery life and laptop video storage/);
  assert.equal(view.level, 'normal');
});

test('low-space states use explanatory text as well as color', () => {
  for (const [seconds, level] of [[60, 'low'], [30, 'critical'], [0, 'critical']]) {
    const view = storageView({...budget, remaining_s: seconds});
    assert.equal(view.level, level);
    assert.match(view.note, /stop and save soon/);
  }
});

test('idle SD capacity explains the initial rate and one-file limit', () => {
  const view = storageView({...budget, state: 'ready', storage: 'sd', remaining_s: 800000,
    free_bytes: 30 * 1024 ** 3, limit: 'file', rate_source: 'typical'});
  assert.equal(view.title, 'Time available for next recording');
  assert.equal(view.value, '≈ 9d 6h');
  assert.equal(view.detail, 'SD card · 30.0 GiB free');
  assert.match(view.note, /Initial estimate.*one recording file/);
});

test('not enough space to start is distinct from a running log approaching zero', () => {
  const view = storageView({...budget, state: 'full', remaining_s: 0});
  assert.equal(view.value, '0:00');
  assert.match(view.note, /Not enough space to start.*verify.*delete/);
});

test('offline, unknown, fault and transition states clear old time values', () => {
  for (const state of ['offline', 'unavailable', 'starting', 'stopping', 'fault']) {
    assert.equal(storageView({...budget, state}).value, '—');
  }
  assert.equal(storageView(undefined).value, '—');
});

test('rendering updates the visible panel, clears stale time and hides it for legacy capture', () => {
  const nodes = Object.fromEntries(['budget', 'title', 'value', 'detail', 'note']
    .map(key => [`storage-${key}`, {hidden: true, textContent: '', dataset: {}}]));
  const document = {getElementById: id => nodes[id]};
  renderStorage(document, budget, true);
  assert.equal(nodes['storage-budget'].hidden, false);
  assert.equal(nodes['storage-value'].textContent, '≈ 1:34');
  renderStorage(document, {state: 'offline'}, true);
  assert.equal(nodes['storage-value'].textContent, '—');
  assert.match(nodes['storage-note'].textContent, /disconnected/);
  renderStorage(document, null, false);
  assert.equal(nodes['storage-budget'].hidden, true);
});
