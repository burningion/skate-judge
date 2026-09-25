import assert from 'node:assert/strict';
import test from 'node:test';
import {batteryView, renderBattery} from '../capture/battery.mjs';

const battery = {state: 'ready', percent: 75.5, voltage_mv: 4001};

test('shows estimated battery charge and voltage without inventing a runtime or charging state', () => {
  const view = batteryView(battery);
  assert.equal(view.value, '76%');
  assert.equal(view.detail, '4.00 V');
  assert.match(view.note, /Estimated remaining charge/);
  assert.equal(view.level, 'normal');
});

test('low and empty batteries have actionable text as well as color', () => {
  for (const [percent, level] of [[20, 'low'], [10, 'critical'], [0, 'critical']]) {
    const view = batteryView({...battery, percent});
    assert.equal(view.value, `${percent}%`);
    assert.equal(view.level, level);
    assert.match(view.note, /save|Save/);
  }
});

test('offline, stale, unsupported and malformed readings clear the last known charge', () => {
  for (const value of [undefined, ...['offline', 'stale', 'unsupported', 'unavailable']
    .map(state => ({...battery, state})), {...battery, percent: null},
    {...battery, percent: 150}, {...battery, voltage_mv: NaN}]) {
    const view = batteryView(value);
    assert.equal(view.value, '—');
    assert.equal(view.level, 'normal');
  }
  assert.match(batteryView({state: 'offline'}).note, /disconnected/);
  assert.match(batteryView({state: 'unsupported'}).note, /firmware/);
});

test('disconnect clears a displayed low battery and legacy capture hides the panel', () => {
  const nodes = Object.fromEntries(['status', 'value', 'detail', 'note']
    .map(key => [`battery-${key}`, {hidden: true, textContent: '', dataset: {}}]));
  const document = {getElementById: id => nodes[id]};
  renderBattery(document, {...battery, percent: 10}, true);
  assert.equal(nodes['battery-status'].hidden, false);
  assert.equal(nodes['battery-value'].textContent, '10%');
  renderBattery(document, {...battery, state: 'offline'}, true);
  assert.equal(nodes['battery-value'].textContent, '—');
  assert.equal(nodes['battery-status'].dataset.level, 'normal');
  renderBattery(document, null, false);
  assert.equal(nodes['battery-status'].hidden, true);
});
