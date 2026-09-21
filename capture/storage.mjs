// Storage time is separate from the three-second LED sync countdown.
export function remainingTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const whole = Math.floor(seconds);
  if (whole >= 86400) return `${Math.floor(whole / 86400)}d ${Math.floor(whole % 86400 / 3600)}h`;
  if (whole >= 3600) return `${Math.floor(whole / 3600)}h ${Math.floor(whole % 3600 / 60)}m`;
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

function space(bytes) {
  if (!Number.isFinite(bytes)) return '';
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GiB free`
    : `${(bytes / 1024 ** 2).toFixed(2)} MiB free`;
}

export function storageView(budget) {
  const state = budget?.state ?? 'unavailable';
  const active = state === 'recording';
  const available = ['ready', 'recording', 'full'].includes(state) && Number.isFinite(budget?.remaining_s);
  const seconds = available ? budget.remaining_s : null;
  const level = seconds !== null && seconds <= 30 ? 'critical'
    : seconds !== null && seconds <= 60 ? 'low' : 'normal';
  const backend = ({sd: 'SD card', flash: 'Internal flash'})[budget?.storage] || 'Board storage';
  let note = {
    offline: 'Board disconnected. Reconnect to update the estimate.',
    unavailable: 'Waiting for storage information from the board.',
    starting: 'Starting the recording…', stopping: 'Closing and saving the recording…',
    testing_led: 'LED test in progress.', fault: 'Recording stopped with a fault. Check the board message.',
    full: 'Not enough space to start. Download, verify, then delete saved board logs.',
  }[state];
  if (note === undefined) {
    note = budget.rate_source === 'measured' ? 'Based on the measured log rate.' : 'Initial estimate; updates once recording gets underway.';
    if (budget.limit === 'file') note += ' Limited by the size of one recording file.';
    if (active && level !== 'normal') note += ' Space is running low—stop and save soon.';
  }
  return {
    title: active ? 'Recording time left' : 'Time available for next recording',
    value: available ? `${state === 'full' ? '' : '≈ '}${remainingTime(seconds)}` : '—',
    detail: [backend, space(budget?.free_bytes)].filter(Boolean).join(' · '),
    note: `${note} Storage estimate only; battery life and laptop video storage are separate.`,
    level,
  };
}

export function renderStorage(document, budget, visible) {
  const panel = document.getElementById('storage-budget');
  panel.hidden = !visible;
  if (!visible) return;
  const view = storageView(budget);
  panel.dataset.level = view.level;
  for (const key of ['title', 'value', 'detail', 'note']) {
    document.getElementById(`storage-${key}`).textContent = view[key];
  }
}
