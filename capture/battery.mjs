export function batteryView(battery) {
  const percent = battery?.percent;
  const voltage = battery?.voltage_mv;
  const ready = battery?.state === 'ready' && Number.isFinite(percent) && percent >= 0 && percent <= 100
    && Number.isFinite(voltage) && voltage >= 2500 && voltage <= 4500;
  if (!ready) return {
    value: '—', detail: 'Battery level unavailable', level: 'normal',
    note: ({offline: 'Board disconnected. Reconnect to check the battery.',
      stale: 'Waiting for a fresh battery reading.',
      unsupported: 'Update the board firmware to see its battery level.'})[battery?.state]
      || 'Waiting for a reading from the battery monitor.',
  };
  return {
    value: `${Math.round(percent)}%`, detail: `${(voltage / 1000).toFixed(2)} V`,
    level: percent <= 10 ? 'critical' : percent <= 20 ? 'low' : 'normal',
    note: percent <= 10 ? 'Battery very low. Stop and save, then recharge.'
      : percent <= 20 ? 'Battery low. Save your session soon and recharge.'
      : 'Estimated remaining charge. Updates every 5 seconds.',
  };
}

export function renderBattery(document, battery, visible) {
  const panel = document.getElementById('battery-status');
  panel.hidden = !visible;
  if (!visible) return;
  const view = batteryView(battery);
  panel.dataset.level = view.level;
  for (const key of ['value', 'detail', 'note']) {
    document.getElementById(`battery-${key}`).textContent = view[key];
  }
}
