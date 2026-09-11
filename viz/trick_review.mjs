// Pure pairing functions are also used by the Node checks.
export function proposePairs(onsets, strength, step, {threshold, min_gap, max_gap}) {
  const eligible = onsets.filter(p => p.strength >= threshold);
  const options = [];
  for (let i = 0; i < eligible.length; i++) {
    const a = eligible[i];
    for (let j = i + 1; j < eligible.length; j++) {
      const b = eligible[j], gap = b.time_s - a.time_s;
      if (gap > max_gap) break;
      if (gap < min_gap) continue;
      const middle = strength.slice(Math.ceil((a.time_s + .06) / step), Math.floor((b.time_s - .06) / step));
      const activity = middle.reduce((sum, x) => sum + x, 0) / Math.max(1, middle.length);
      const score = Math.sqrt(a.strength * b.strength) / (1 + 3 * activity);
      options.push({id: `auto-${a.time_s.toFixed(4)}-${b.time_s.toFixed(4)}`,
        start_s: a.time_s, end_s: b.time_s, score, status: "suggested", note: ""});
    }
  }
  // Strongest non-overlapping explanation first, then chronological display.
  const selected = [];
  for (const pair of options.sort((a, b) => b.score - a.score)) {
    if (!selected.some(p => pair.start_s <= p.end_s + .1 && pair.end_s >= p.start_s - .1)) selected.push(pair);
  }
  return selected.sort((a, b) => a.start_s - b.start_s);
}

export function mergeReviews(proposals, reviewed) {
  const saved = new Map(reviewed.map(row => [row.id, row]));
  return [...proposals.filter(row => !saved.has(row.id) && !reviewed.some(other =>
    (!row.status || row.status === "suggested") && other.status === "labeled" && !other.label_stale && !other.edited &&
    row.start_s >= other.label_start_s && row.end_s <= other.label_end_s)), ...reviewed]
    .map(row => ({...row})).sort((a, b) => a.start_s - b.start_s);
}

export function overlappingLabels(pair, window, reviewed) {
  return window ? reviewed.filter(other => other.label_id && other.id !== pair.id &&
    window[0] < other.label_end_s && window[1] > other.label_start_s) : [];
}

export function attemptWindow(pair, duration, coverage, reviewed = []) {
  let lo = Math.max(0, coverage ? Math.ceil(coverage[0] * 1000) / 1000 : 0);
  let hi = Math.min(duration, coverage ? Math.floor(coverage[1] * 1000) / 1000 : duration);
  const coverageLo = lo, coverageHi = hi;
  // Trim only suggested context. A human's explicit window is never moved.
  for (const other of reviewed) {
    if (!other.label_id || other.id === pair.id) continue;
    if (other.label_end_s <= pair.start_s) lo = Math.max(lo, Math.ceil(other.label_end_s * 1000) / 1000);
    if (other.label_start_s >= pair.end_s) hi = Math.min(hi, Math.floor(other.label_start_s * 1000) / 1000);
  }
  const start = pair.label_start_s ?? Math.max(lo, pair.start_s - 1);
  const end = pair.label_end_s ?? Math.min(hi, pair.end_s + 2);
  return validInterval(start, end, duration) && start >= coverageLo && end <= coverageHi ? [start, end] : null;
}

export function backgroundWindow(time, duration, coverage, reviewed) {
  const lo = Math.max(0, coverage ? Math.ceil(coverage[0] * 1000) / 1000 : 0);
  const hi = Math.min(duration, coverage ? Math.floor(coverage[1] * 1000) / 1000 : duration);
  if (time < lo || time >= hi || reviewed.some(row => row.label_id && time >= row.label_start_s && time < row.label_end_s)) return null;
  const start = Math.ceil(time * 1000) / 1000;
  const next = Math.min(hi, ...reviewed.filter(row => row.label_id && row.label_start_s >= time).map(row => row.label_start_s));
  const end = Math.floor(Math.min(start + 3, next) * 1000) / 1000;
  return validInterval(start, end, duration) && start >= lo && end <= hi ? [start, end] : null;
}

export function needsReview(row) {
  return Boolean(row.edited || row.label_stale || !["labeled", "rejected"].includes(row.status));
}

export function nextPending(rows, identity) {
  const at = rows.findIndex(row => row.id === identity);
  return [...rows.slice(at + 1), ...rows.slice(0, at)].find(row => needsReview(row))?.id ?? null;
}

export function validInterval(start, end, duration) {
  return Number.isFinite(start) && Number.isFinite(end) && 0 <= start && start < end && end <= duration;
}

export function requireCurrentServer(data, saved) {
  if (data.api_version !== 2 || !saved.label_context || typeof saved.labels_revision !== "string") {
    throw new Error("Restart the review server to load the updated tools: press Ctrl+C in its terminal, rerun the review command, then reload this page. Refreshing the browser alone keeps the older Python server running.");
  }
}

export function stepFrame(times, current, direction) {
  if (!times.length) return current;
  // Find the displayed frame by its presentation interval, not its nominal rate.
  // A tiny tolerance absorbs the decimal rounding in ffprobe timestamps.
  let lo = 0, hi = times.length;
  while (lo < hi) {
    const middle = (lo + hi) >>> 1;
    if (times[middle] <= current + 1e-6) lo = middle + 1;
    else hi = middle;
  }
  const target = direction > 0 ? lo : lo - 2;
  return times[Math.max(0, Math.min(times.length - 1, target))];
}

if (typeof document !== "undefined") init().catch(error => {
  const status = document.querySelector("#status");
  status.textContent = `Could not open review: ${error.message}`;
  status.dataset.error = "true";
});

export async function init() {
  const $ = id => document.getElementById(id);
  async function request(url, options) {
    const response = await fetch(url, options), body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }
  const [data, saved] = await Promise.all([request("/api/data"), request("/api/review")]);
  requireCurrentServer(data, saved);
  const video = $("video"), audio = data.audio, duration = data.duration_s;
  const frameTimes = data.video_timing?.frame_times_s || [];
  let reviewed = saved.intervals, revision = saved.revision, rows = [], selected = null;
  let labelsRevision = saved.labels_revision, labelContext = saved.label_context;
  let lastTrick = [...reviewed].reverse().find(row => row.trick)?.trick || "";
  let windowStart = 0, audition = null, drag = null, dirty = false, saving = false;
  const colors = {start: "#c9f887", end: "#ffb08a", onset: "#bdacff", ink: "#9ebbb0", faint: "#344942"};
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const format = value => `${Math.floor(value / 60)}:${(value % 60).toFixed(3).padStart(6, "0")}`;
  const span = () => Math.min(duration, Number($("zoom").value));
  function settings() {
    return {threshold: Number($("threshold").value), min_gap: Number($("min-gap").value),
      max_gap: Number($("max-gap").value), offset: Number($("offset").value), scale: Number($("scale").value)};
  }
  function status(message, error = false) { $("status").textContent = message; $("status").dataset.error = String(error); }
  function changed() { dirty = true; status("Unsaved changes. Save label to keep them."); }
  function center(time) { windowStart = clamp(time - span() / 2, 0, Math.max(0, duration - span())); }
  function seek(time) { video.currentTime = clamp(time, 0, duration); center(video.currentTime); draw(); }
  async function play() { try { await video.play(); } catch (error) { status(error.message, true); } }
  function selection() { return rows.find(row => row.id === selected); }
  const windowFor = pair => pair && attemptWindow(pair, duration, labelContext.coverage, reviewed);
  const isBackground = pair => pair?.outcome === "background";
  const savedDescription = row => `${isBackground(row) ? "background" : `${row.trick || "trick"} · ${row.outcome}`} (${format(row.label_start_s)}–${format(row.label_end_s)})`;
  function setEditor() {
    const pair = selection();
    for (const id of ["start", "end", "mark-start", "mark-end", "audition", "save", "background", "reject", "note", "trick", "outcome", "label-start", "label-end", "label-mark-start", "label-mark-end"]) $(id).disabled = !pair || saving;
    for (const id of ["previous", "next", "new", "new-background", "open-overlap", "threshold", "min-gap", "max-gap", "offset", "scale", "pending-only", "save-sync"]) $(id).disabled = saving;
    $("save-sync").disabled = saving || !data.sensor.syncs.length;
    $("save").disabled ||= !labelContext.available;
    $("background").disabled ||= !labelContext.available;
    $("reject").disabled ||= Boolean(pair?.label_id);
    $("selected-title").textContent = pair ? pair.label_stale ? "Alignment changed · review label" : pair.status === "labeled" ? "Saved label" : isBackground(pair) ? "Label a background range" : pair.status === "rejected" ? "Skipped suggestion" : "Review suggestion" : "Select an interval";
    $("pop-markers").hidden = isBackground(pair);
    $("audition").textContent = isBackground(pair) ? "Play range [Space]" : "Play attempt [Space]";
    $("start").value = pair ? pair.start_s.toFixed(3) : "";
    $("end").value = pair ? pair.end_s.toFixed(3) : "";
    const window = windowFor(pair);
    $("pair-duration").textContent = pair ? `${(isBackground(pair) && window ? window[1] - window[0] : pair.end_s - pair.start_s).toFixed(3)} s` : "";
    $("note").value = pair?.note || "";
    $("trick").value = pair?.trick ?? lastTrick;
    $("outcome").value = pair?.outcome || "";
    $("trick").disabled ||= pair?.outcome === "background";
    $("label-start").value = pair?.label_start_s ?? (window ? window[0].toFixed(3) : "");
    $("label-end").value = pair?.label_end_s ?? (window ? window[1].toFixed(3) : "");
    $("label-availability").textContent = labelContext.available
      ? window ? `Sensor coverage: ${format(labelContext.coverage[0])}–${format(labelContext.coverage[1])}.`
        : "This window is outside sensor coverage. Adjust the attempt times, or skip this suggestion."
      : labelContext.reason;
    const overlaps = pair ? overlappingLabels(pair, window, reviewed) : [];
    $("window-conflict").hidden = !overlaps.length;
    const recovery = pair?.label_id ? "Adjust this range or open the other label to resolve the overlap."
      : isBackground(pair) ? "Shorten the background range to exclude saved labels, or open the saved label to review it."
      : "If this is the same attempt, skip the suggestion. For a separate event, adjust the ranges before saving.";
    $("window-conflict-text").textContent = overlaps.length
      ? `This range overlaps saved ${overlaps.map(savedDescription).join("; ")}. ${recovery}` : "";
    $("window-help").textContent = isBackground(pair)
      ? "Watch the whole range, set its start / finish with I / O, then Save label. One background range can cover many sounds. Include only time with no trick attempt."
      : "New windows include up to 1 s before pop and 2 s after contact, trimmed at saved labels. Check that the roll-away still fits. Your edited boundaries stay as entered.";
    $("save").disabled ||= overlaps.length > 0;
    $("background").disabled ||= overlaps.length > 0;
  }
  function visibleRows() { return $("pending-only").checked ? rows.filter(needsReview) : rows; }
  function list() {
    const pending = rows.filter(needsReview).length;
    $("count").textContent = `${pending} remaining`;
    $("progress").textContent = `${rows.filter(row => row.status === "labeled" && !row.label_stale).length} labeled · ${rows.filter(row => row.status === "rejected").length} skipped · ${pending} remaining`;
    $("candidates").replaceChildren();
    visibleRows().forEach((row, index) => {
      const button = document.createElement("button");
      button.className = `candidate ${row.status}`; button.type = "button";
      button.setAttribute("aria-pressed", String(selected === row.id));
      const label = document.createElement("span"), detail = document.createElement("small");
      const range = row.label_id || isBackground(row) ? windowFor(row) : null;
      label.textContent = `${String(index + 1).padStart(2, "0")} · ${format(range?.[0] ?? row.start_s)} → ${format(range?.[1] ?? row.end_s)}`;
      detail.textContent = row.edited ? "unsaved" : row.label_stale ? "review alignment" : row.outcome === "background" ? "not a trick" : row.outcome ? `${row.trick || "trick"} · ${row.outcome}` : row.status === "rejected" ? "skipped" : "unlabeled";
      if (needsReview(row) && overlappingLabels(row, windowFor(row), reviewed).length) detail.textContent += " · overlaps saved";
      button.disabled = saving;
      button.append(label, detail); button.onclick = () => choose(row.id); $("candidates").append(button);
    });
    if (!visibleRows().length) $("candidates").textContent = rows.length ? "Queue complete. Uncheck Show only remaining to revise a saved label." : "No suggestions at these settings. Adjust onset detection or add a missed attempt.";
  }
  function stashDraft() {
    const pair = selection();
    if (pair && pair.edited) {
      // Keep unsaved manual edits when thresholds or selection change.
      drafts.set(pair.id, {...pair});
    }
  }
  const drafts = new Map();
  function choose(id) {
    if (saving) return;
    stashDraft(); selected = id; audition = null; video.pause();
    const pair = selection(); if (pair) seek(windowFor(pair)?.[0] ?? Math.max(0, pair.start_s - .7));
    setEditor(); list(); draw();
  }
  function regenerate() {
    stashDraft();
    const opts = settings();
    if (!(opts.min_gap >= .1 && opts.max_gap <= 5 && opts.min_gap < opts.max_gap)) {
      status("Use 0.1 ≤ minimum gap < maximum gap ≤ 5 seconds.", true); return;
    }
    const proposals = proposePairs(audio.onsets, audio.strength, audio.step_s, opts);
    rows = mergeReviews(proposals, reviewed);
    rows = mergeReviews(rows, [...drafts.values()]);
    if (!selection() || ($("pending-only").checked && !needsReview(selection()))) selected = visibleRows()[0]?.id || null;
    $("threshold-value").textContent = opts.threshold.toFixed(2);
    setEditor(); list(); draw();
  }
  function updateBoundary(key, value) {
    const pair = selection(); if (!pair || saving) return;
    const start = key === "start_s" ? value : pair.start_s, end = key === "end_s" ? value : pair.end_s;
    if (!validInterval(start, end, duration)) { status("Start must be before finish, within the video.", true); setEditor(); return; }
    pair[key] = value; pair.edited = true; changed(); setEditor(); list(); draw();
  }
  function canvas(id) {
    const element = $(id), box = element.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
    if (element.width !== Math.round(box.width * ratio) || element.height !== Math.round(box.height * ratio)) {
      element.width = Math.round(box.width * ratio); element.height = Math.round(box.height * ratio);
    }
    const ctx = element.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, box.width, box.height);
    return {ctx, w: box.width, h: box.height};
  }
  function waveform(ctx, w, lo, length, top, height) {
    ctx.strokeStyle = colors.ink; ctx.beginPath();
    for (let pixel = 0; pixel < w; pixel++) {
      const begin = Math.max(0, Math.floor((lo + pixel / w * length) / audio.step_s));
      const end = Math.min(audio.waveform_min.length, Math.max(begin + 1, Math.ceil((lo + (pixel + 1) / w * length) / audio.step_s)));
      let min = 0, max = 0;
      for (let j = begin; j < end; j++) { min = Math.min(min, audio.waveform_min[j]); max = Math.max(max, audio.waveform_max[j]); }
      ctx.moveTo(pixel, top + height / 2 - max * height / 2);
      ctx.lineTo(pixel, top + height / 2 - min * height / 2);
    }
    ctx.stroke();
  }
  function marker(ctx, x, height, color, label) {
    ctx.strokeStyle = color; ctx.lineWidth = label ? 2 : 1;
    ctx.beginPath(); ctx.moveTo(x, label ? 19 : 0); ctx.lineTo(x, height); ctx.stroke(); ctx.lineWidth = 1;
    if (label) { ctx.fillStyle = color; ctx.font = "11px system-ui"; ctx.fillText(label, Math.max(2, x - 15), 13); }
  }
  function drawAttempt(ctx, x, height, pair) {
    const window = windowFor(pair);
    if (!window) return;
    ctx.fillStyle = "#82c9df18";
    ctx.fillRect(x(window[0]), 20, x(window[1]) - x(window[0]), height - 43);
    ctx.strokeStyle = "#82c9df"; ctx.setLineDash([5, 4]);
    ctx.strokeRect(x(window[0]), 20, x(window[1]) - x(window[0]), height - 43);
    ctx.setLineDash([]); ctx.fillStyle = "#82c9df"; ctx.font = "11px system-ui";
    ctx.fillText(isBackground(pair) ? "BACKGROUND RANGE" : "ATTEMPT WINDOW", Math.max(5, x(window[0]) + 5), height - 30);
  }
  function drawSaved(ctx, x, height) {
    for (const row of reviewed) {
      if (!row.label_id || row.id === selected || row.label_end_s < windowStart || row.label_start_s > windowStart + span()) continue;
      ctx.fillStyle = isBackground(row) ? "#9eafbd22" : "#c9f88720";
      ctx.fillRect(x(row.label_start_s), 20, x(row.label_end_s) - x(row.label_start_s), height - 43);
      ctx.strokeStyle = row.label_stale ? "#ffbf92" : "#819b7e";
      ctx.strokeRect(x(row.label_start_s), 20, x(row.label_end_s) - x(row.label_start_s), height - 43);
      ctx.fillStyle = ctx.strokeStyle; ctx.font = "11px system-ui";
      const left = Math.max(0, x(row.label_start_s)), right = Math.min(x(windowStart + span()), x(row.label_end_s));
      ctx.save(); ctx.beginPath(); ctx.rect(left, 0, right - left, 19); ctx.clip();
      ctx.fillText(`${row.label_stale ? "REVIEW" : "SAVED"} · ${isBackground(row) ? "background" : row.trick || row.outcome}`, left + 5, 13);
      ctx.restore();
    }
  }
  function draw() {
    const pair = selection(), opts = settings(), length = span();
    const overview = canvas("overview"), o = overview.ctx;
    for (const row of rows) {
      o.fillStyle = row.status === "rejected" ? "#513b32" : row.id === selected ? "#587346" : "#2c4b35";
      const range = row.label_id || isBackground(row) ? windowFor(row) : null;
      const start = range?.[0] ?? row.start_s, end = range?.[1] ?? row.end_s;
      if (row.label_id) o.fillStyle = isBackground(row) ? "#344650" : "#49633d";
      o.fillRect(start / duration * overview.w, 0, Math.max(2, (end - start) / duration * overview.w), overview.h);
    }
    waveform(o, overview.w, 0, duration, 6, overview.h - 12);
    o.strokeStyle = "#8aa68d"; o.strokeRect(windowStart / duration * overview.w, 1, length / duration * overview.w, overview.h - 2);
    marker(o, video.currentTime / duration * overview.w, overview.h, "#ffffff");
    const {ctx, w, h} = canvas("detail"), x = t => (t - windowStart) / length * w;
    drawSaved(ctx, x, h);
    drawAttempt(ctx, x, h, pair);
    if (pair && !isBackground(pair)) { ctx.fillStyle = "#304432"; ctx.fillRect(x(pair.start_s), 20, x(pair.end_s) - x(pair.start_s), h - 42); }
    waveform(ctx, w, windowStart, length, 26, 108);
    ctx.font = "11px system-ui"; ctx.fillStyle = "#a5b8b3"; ctx.fillText("AUDIO WAVEFORM", 5, 33); ctx.fillText("ONSET STRENGTH", 5, 155);
    const y = value => h - 26 - Math.min(value, 3) / 3 * 87;
    ctx.strokeStyle = colors.onset; ctx.beginPath();
    const from = Math.max(0, Math.floor(windowStart / audio.step_s)), to = Math.min(audio.strength.length, Math.ceil((windowStart + length) / audio.step_s));
    for (let i = from; i < to; i++) { const px = x(i * audio.step_s); if (i === from) ctx.moveTo(px, y(audio.strength[i])); else ctx.lineTo(px, y(audio.strength[i])); }
    ctx.stroke(); ctx.setLineDash([4, 4]); ctx.strokeStyle = "#746796";
    ctx.beginPath(); ctx.moveTo(0, y(opts.threshold)); ctx.lineTo(w, y(opts.threshold)); ctx.stroke(); ctx.setLineDash([]);
    for (const onset of audio.onsets) if (onset.strength >= opts.threshold && onset.time_s >= windowStart && onset.time_s <= windowStart + length) {
      ctx.fillStyle = colors.onset; ctx.fillRect(x(onset.time_s) - .5, 40, 1, 92);
    }
    for (let i = 0; i <= 6; i++) {
      const t = windowStart + i / 6 * length;
      ctx.fillStyle = "#a5b8b3"; ctx.fillText(`${t.toFixed(2)}s`, clamp(i / 6 * w - 18, 0, w - 46), h - 5);
    }
    if (pair && !isBackground(pair)) { marker(ctx, x(pair.start_s), h - 23, colors.start, "POP"); marker(ctx, x(pair.end_s), h - 23, colors.end, "CONTACT"); }
    marker(ctx, x(video.currentTime), h - 23, "#ffffff");
    if (data.sensor.samples.length) drawImu();
  }
  function drawImu() {
    const {ctx, w, h} = canvas("imu"), opts = settings(), length = span();
    const time = row => (row[0] - opts.offset) / opts.scale - data.source_pts_origin_s;
    const samples = data.sensor.samples.filter(row => time(row) >= windowStart && time(row) <= windowStart + length);
    $("sensor-count").textContent = `${samples.length} samples in view`;
    const pair = selection(), plotHeight = h - 22, half = plotHeight / 2;
    const xAt = t => (t - windowStart) / length * w;
    drawSaved(ctx, xAt, h);
    drawAttempt(ctx, xAt, h, pair);
    if (pair && !isBackground(pair)) {
      ctx.fillStyle = "#304432";
      ctx.fillRect(xAt(pair.start_s), 20, xAt(pair.end_s) - xAt(pair.start_s), plotHeight - 20);
    }
    ctx.font = "11px system-ui";
    for (const [axis, color, label, top] of [[1, "#90d8de", "ACCELEROMETER · m/s²", 0], [2, "#f4cc83", "GYROSCOPE · rad/s", half]]) {
      const max = Math.max(axis === 1 ? 9.81 : 1, ...samples.map(row => row[axis]));
      const bottom = top + half - 9, amplitude = half - 38;
      ctx.strokeStyle = colors.faint;
      ctx.beginPath(); ctx.moveTo(0, bottom); ctx.lineTo(w, bottom); ctx.stroke();
      ctx.fillStyle = color; ctx.fillText(`${label} · scale 0–${max.toFixed(1)}`, 3, top + 28);
      ctx.fillText("0", 3, bottom - 4);
      let last = null;
      for (const row of samples) {
        const x = xAt(time(row)), y = bottom - row[axis] / max * amplitude;
        ctx.fillRect(x - 2, y - 2, 4, 4);
        if (last && row[0] - last.row[0] <= .03) { ctx.strokeStyle = color; ctx.beginPath(); ctx.moveTo(last.x, last.y); ctx.lineTo(x, y); ctx.stroke(); }
        last = {x, y, row};
      }
    }
    if (!samples.length) { ctx.fillStyle = "#ffbf92"; ctx.fillText("No sensor coverage in this window at this offset.", 4, half - 25); }
    for (let i = 0; i <= 6; i++) {
      ctx.fillStyle = "#a5b8b3";
      ctx.fillText(`${(windowStart + i / 6 * length).toFixed(2)}s`, clamp(i / 6 * w - 18, 0, w - 46), h - 5);
    }
    for (const sync of data.sensor.syncs) {
      const t = (sync.session_s - opts.offset) / opts.scale - data.source_pts_origin_s;
      if (t >= windowStart && t <= windowStart + length) marker(ctx, xAt(t), plotHeight, "#ffffff", `LED ${sync.id}`);
    }
    if (pair && !isBackground(pair)) {
      marker(ctx, xAt(pair.start_s), plotHeight, colors.start, "POP");
      marker(ctx, xAt(pair.end_s), plotHeight, colors.end, "CONTACT");
    }
    marker(ctx, xAt(video.currentTime), plotHeight, "#ffffff");
  }
  async function savePair(decision, outcome = null) {
    const pair = selection(); if (!pair || saving) return;
    stashDraft();
    const chosenOutcome = outcome ?? $("outcome").value;
    if (decision === "label" && !chosenOutcome) { status("Choose an outcome, or use Not a trick for a false positive.", true); return; }
    if (decision === "label" && (!labelContext.available || !alignmentMatches())) {
      status("Use the saved LED alignment before labeling. Reset the offset and scale to the saved match, or reload.", true); return;
    }
    const clean = {id: pair.id, start_s: pair.start_s, end_s: pair.end_s, note: $("note").value,
      status: decision === "skip" ? "rejected" : "reviewed", outcome: chosenOutcome,
      trick: chosenOutcome === "background" ? "" : $("trick").value.trim(),
      label_start_s: $("label-start").value === "" ? null : Number($("label-start").value),
      label_end_s: $("label-end").value === "" ? null : Number($("label-end").value)};
    if (chosenOutcome === "background" && decision === "label") {
      clean.start_s = clean.label_start_s; clean.end_s = clean.label_end_s;
    }
    if (decision === "label" && (!validInterval(clean.label_start_s, clean.label_end_s, duration))) {
      status("Set an attempt start and finish inside sensor coverage, or skip this suggestion.", true); return;
    }
    const overlaps = overlappingLabels(pair, [clean.label_start_s, clean.label_end_s], reviewed);
    if (decision === "label" && overlaps.length) {
      status(`Overlaps saved ${overlaps.map(savedDescription).join("; ")}. Open the saved label to adjust it, or skip this suggestion if it is the same attempt.`, true); return;
    }
    // Preserve an explicit outcome shortcut in the draft if saving fails.
    if (decision === "label") { Object.assign(pair, clean, {status: pair.status, edited: true}); stashDraft(); dirty = true; }
    saving = true; video.pause(); audition = null; setEditor(); list();
    try {
      const result = await request("/api/label", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({revision, labels_revision: labelsRevision, interval: clean, decision,
          mapping: labelContext.mapping, settings: settings()})});
      reviewed = result.intervals; revision = result.revision; labelsRevision = result.labels_revision;
      labelContext = result.label_context; drafts.delete(pair.id); pair.edited = false;
      if (decision === "label" && clean.trick) lastTrick = clean.trick;
      dirty = drafts.size > 0; regenerate();
      saving = false;
      if ($("auto-next").checked) choose(nextPending(rows, pair.id));
      status(`${decision === "skip" ? "Skipped; no training label saved" : `Saved ${chosenOutcome === "background" ? "not a trick (background)" : chosenOutcome} label`}.${dirty ? " Other edits remain unsaved." : ""}${rows.some(needsReview) ? "" : " Queue complete. Scan the video for missed attempts."}`);
    } catch (error) { status(`Save failed: ${error.message}`, true); }
    finally { saving = false; setEditor(); list(); }
  }
  $("filename").textContent = data.video;
  const fps = data.video_timing?.fps;
  $("video-rate").textContent = fps ? `${fps.toFixed(2)} fps · ${frameTimes.length.toLocaleString()} indexed frames` : "Frame rate unavailable";
  $("back").disabled = $("forward").disabled = !frameTimes.length;
  $("timing").textContent = `All controls use the seekable review video's seconds. Original media PTS = review seconds + ${data.source_pts_origin_s.toFixed(6)} s. The original recording is preserved.`;
  $("sensor-quality").textContent = data.sensor.warnings.join(" ") || "Raw sensor magnitudes; gaps over 30 ms are not connected.";
  $("syncs").textContent = data.sensor.syncs.length ? `Recorded LED rises (session seconds): ${data.sensor.syncs.map(s => `#${s.id} at ${s.session_s.toFixed(3)}`).join(" · ")}` : "No recorded LED flashes.";
  let mapping = labelContext.mapping || data.sensor.mapping;
  data.sensor.mapping = mapping;
  $("offset").value = mapping?.offset_s ?? data.sensor.offset_hint_s;
  $("scale").value = mapping?.scale ?? 1;
  if (saved.settings) for (const [key, id] of [["threshold", "threshold"], ["min_gap", "min-gap"], ["max_gap", "max-gap"], ["offset", "offset"], ["scale", "scale"]]) {
    // A fresh measured mapping takes precedence over an older exploratory overlay.
    if (!mapping || !["offset", "scale"].includes(key)) $(id).value = saved.settings[key];
  }
  function alignmentMatches() {
    const opts = settings();
    return mapping && Math.abs(opts.offset - mapping.offset_s) < 1e-7 && Math.abs(opts.scale - mapping.scale) < 1e-7;
  }
  function alignmentStatus() {
    const matched = alignmentMatches();
    $("alignment").textContent = matched ? `Using ${mapping.points} saved LED sync point(s).${mapping.points === 1 ? " Drift is unmeasured with one point." : ""}` : "UNALIGNED / EXPLORATORY — the offset has not been verified against this clip’s LED flashes.";
    $("alignment").className = matched ? "help" : "help warning";
  }
  alignmentStatus();
  for (const sync of data.sensor.syncs) {
    const option = document.createElement("option"); option.value = sync.id;
    option.textContent = `#${sync.id} · session ${sync.session_s.toFixed(3)} s`;
    $("sync-id").append(option);
  }
  $("save-sync").disabled = !data.sensor.syncs.length;
  $("save-sync").onclick = async () => {
    video.pause(); $("save-sync").disabled = true;
    try {
      mapping = await request("/api/sync", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({sync_id: Number($("sync-id").value), review_s: video.currentTime})});
      data.sensor.mapping = mapping; $("offset").value = mapping.offset_s; $("scale").value = mapping.scale;
      alignmentStatus();
      stashDraft();
      const fresh = await request("/api/review");
      reviewed = fresh.intervals; revision = fresh.revision; labelsRevision = fresh.labels_revision; labelContext = fresh.label_context;
      regenerate(); draw();
      status(`Saved flash correspondence. ${mapping.points} point(s) now align this video with the sensor session.`);
    } catch (error) { status(`Could not save flash: ${error.message}`, true); }
    finally { $("save-sync").disabled = false; }
  };
  $("sensor-panel").hidden = !data.sensor.samples.length;
  video.src = data.media_url; $("seek").max = duration;
  video.addEventListener("error", () => status("Video playback failed. Try a browser that supports this recording’s codec.", true));
  video.addEventListener("loadedmetadata", () => draw());
  $("play").onclick = () => { audition = null; if (video.paused) play(); else video.pause(); };
  function step(direction) {
    video.pause(); audition = null;
    // Seek just inside the presentation interval; ffprobe rounds to microseconds.
    seek(stepFrame(frameTimes, video.currentTime, direction) + 1e-6);
  }
  $("back").onclick = () => step(-1);
  $("forward").onclick = () => step(1);
  $("speed").onchange = () => { video.playbackRate = Number($("speed").value); };
  $("seek").oninput = () => { audition = null; seek(Number($("seek").value)); };
  $("zoom").onchange = () => { center(video.currentTime); draw(); };
  for (const id of ["threshold", "min-gap", "max-gap"]) $(id).oninput = () => { regenerate(); changed(); };
  for (const id of ["offset", "scale"]) $(id).oninput = () => { alignmentStatus(); changed(); draw(); };
  $("imu").onpointerdown = event => {
    const box = $("imu").getBoundingClientRect(); audition = null;
    video.currentTime = clamp(windowStart + (event.clientX - box.left) / box.width * span(), 0, duration);
    draw();
  };
  $("start").onchange = () => updateBoundary("start_s", Number($("start").value));
  $("end").onchange = () => updateBoundary("end_s", Number($("end").value));
  $("mark-start").onclick = () => updateBoundary("start_s", video.currentTime);
  $("mark-end").onclick = () => updateBoundary("end_s", video.currentTime);
  $("note").oninput = () => { const pair = selection(); if (pair) { pair.note = $("note").value; pair.edited = true; changed(); } };
  function next(delta) {
    const visible = visibleRows();
    if (visible.length) choose(visible[clamp(visible.findIndex(row => row.id === selected) + delta, 0, visible.length - 1)].id);
  }
  $("pending-only").onchange = () => { if (!visibleRows().some(row => row.id === selected)) choose(visibleRows()[0]?.id || null); list(); };
  $("previous").onclick = () => next(-1); $("next").onclick = () => next(1);
  $("new").onclick = () => {
    if (saving) return;
    stashDraft(); const start = clamp(video.currentTime, 0, Math.max(0, duration - .5));
    const row = {id: `manual-${crypto.randomUUID()}`, start_s: start, end_s: Math.min(duration, start + .5), status: "suggested", note: "", edited: true};
    rows.push(row); drafts.set(row.id, row); choose(row.id); changed();
  };
  $("new-background").onclick = () => {
    if (saving) return;
    const window = backgroundWindow(video.currentTime, duration, labelContext.coverage, reviewed);
    if (!window) { status("Start background in unlabeled time inside sensor coverage. The playhead may already be inside a saved label.", true); return; }
    stashDraft();
    const row = {id: `manual-${crypto.randomUUID()}`, start_s: window[0], end_s: window[1],
      label_start_s: window[0], label_end_s: window[1], outcome: "background", trick: "",
      status: "suggested", note: "", edited: true};
    rows.push(row); drafts.set(row.id, row); choose(row.id); changed();
    status("Background range started. Watch the stretch, press O at its end, then Save label. You do not need a label for each onset.");
  };
  $("open-overlap").onclick = () => {
    const pair = selection(), other = pair && overlappingLabels(pair, windowFor(pair), reviewed)[0];
    if (other) { $("pending-only").checked = false; choose(other.id); }
  };
  $("audition").onclick = () => {
    const pair = selection(); if (!pair) return;
    const window = windowFor(pair);
    audition = {start: window?.[0] ?? Math.max(0, pair.start_s - 1), end: window?.[1] ?? Math.min(duration, pair.end_s + 2)};
    seek(audition.start); play();
  };
  $("save").onclick = () => savePair("label");
  $("background").onclick = () => savePair("label", "background");
  $("reject").onclick = () => savePair("skip");
  for (const id of ["trick", "outcome"]) $(id).oninput = () => {
    const pair = selection(); if (!pair) return;
    pair[id] = $(id).value; pair.edited = true; changed();
    if (id === "outcome") setEditor();
    list(); draw();
  };
  function updateAttempt(key, value) {
    const pair = selection(); if (!pair || saving) return;
    const start = key === "label_start_s" ? value : Number($("label-start").value);
    const end = key === "label_end_s" ? value : Number($("label-end").value);
    if (!validInterval(start, end, duration)) { status("Attempt start must be before finish, within the video.", true); setEditor(); return; }
    pair.label_start_s = start; pair.label_end_s = end;
    if (isBackground(pair)) { pair.start_s = start; pair.end_s = end; }
    pair.edited = true; changed(); setEditor(); list(); draw();
  }
  $("label-start").onchange = () => updateAttempt("label_start_s", Number($("label-start").value));
  $("label-end").onchange = () => updateAttempt("label_end_s", Number($("label-end").value));
  $("label-mark-start").onclick = () => updateAttempt("label_start_s", video.currentTime);
  $("label-mark-end").onclick = () => updateAttempt("label_end_s", video.currentTime);
  function download(name, content, type) {
    const url = URL.createObjectURL(new Blob([content], {type}));
    const link = document.createElement("a"); link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $("export-labels").onclick = async () => {
    try {
      const result = await request("/api/labels");
      if (!result.labels.length) { status("No saved labels yet. Label an attempt or mark a false positive as Not a trick."); return; }
      download(`${data.video}.labels.jsonl`, result.labels.map(row => JSON.stringify(row)).join("\n") + "\n", "application/x-ndjson");
      status(`Downloaded ${result.labels.length} saved labels for this clip.${dirty ? " Unsaved edits were excluded." : ""}`);
    } catch (error) { status(`Download failed: ${error.message}`, true); }
  };
  $("export").onclick = () => {
    stashDraft();
    const payload = {schema: 1, video: data.video, source: data.source, algorithm: data.algorithm,
      timeline: "review_video_seconds", source_pts_origin_s: data.source_pts_origin_s,
      settings: settings(), imu_alignment: $("alignment").textContent, has_unsaved_edits: dirty,
      intervals: rows.map(({edited, ...row}) => ({...row, ...(edited ? {status: "draft"} : {})}))};
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = `${data.video}.trick-review.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); status("Exported suggestions and reviews with their status and video timestamps.");
  };
  $("overview").onpointerdown = event => {
    const box = $("overview").getBoundingClientRect(); audition = null; seek((event.clientX - box.left) / box.width * duration);
  };
  $("detail").onpointerdown = event => {
    const box = $("detail").getBoundingClientRect(), time = windowStart + (event.clientX - box.left) / box.width * span(), pair = selection();
    video.pause(); audition = null;
    if (pair && !isBackground(pair) && !saving) {
      const tolerance = 12 / box.width * span();
      const keys = ["start_s", "end_s"].sort((a, b) => Math.abs(pair[a] - time) - Math.abs(pair[b] - time));
      if (Math.abs(pair[keys[0]] - time) < tolerance) { drag = keys[0]; $("detail").setPointerCapture(event.pointerId); return; }
    }
    video.currentTime = clamp(time, 0, duration); draw();
  };
  $("detail").onpointermove = event => {
    if (!drag) return;
    const box = $("detail").getBoundingClientRect(), time = clamp(windowStart + (event.clientX - box.left) / box.width * span(), 0, duration);
    updateBoundary(drag, Math.round(time * 1000) / 1000);
  };
  $("detail").onpointerup = () => { drag = null; };
  $("detail").onpointercancel = () => { drag = null; };
  document.addEventListener("keydown", event => {
    if (event.target.closest("input,textarea,select,video") || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === " " && event.target.closest("button")) return;
    if (saving || event.repeat) return;
    const key = event.key.toLowerCase();
    const outcomes = {"1": "make", "2": "bail", "3": "fall", "4": "unknown", "0": "background"};
    if (outcomes[key]) { event.preventDefault(); savePair("label", outcomes[key]); return; }
    const action = {" ": video.paused ? "audition" : "play", arrowleft: "back", arrowright: "forward", i: "label-mark-start", o: "label-mark-end", n: "next", p: "previous", s: "save", x: "reject"}[key];
    if (action) { event.preventDefault(); $(action).click(); }
  });
  window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  new ResizeObserver(() => draw()).observe($("detail"));
  let lastTime = -1;
  function tick() {
    if (audition && !video.paused && video.currentTime >= audition.end) {
      if ($("loop").checked) video.currentTime = audition.start;
      else { video.pause(); audition = null; }
    }
    if (video.currentTime !== lastTime) {
      lastTime = video.currentTime; $("time").textContent = `${format(lastTime)} / ${format(duration)}`; $("seek").value = lastTime;
      if (!drag && (lastTime < windowStart || lastTime > windowStart + span() - .2)) center(lastTime);
      draw();
    }
    requestAnimationFrame(tick);
  }
  video.addEventListener("ended", () => { if (audition && $("loop").checked) { seek(audition.start); play(); } });
  regenerate(); status(`Ready. ${reviewed.filter(row => row.label_id).length} saved labels. Watch an attempt, then choose an outcome or Not a trick.`); requestAnimationFrame(tick);
}
