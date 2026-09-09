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
  return [...proposals.filter(row => !saved.has(row.id)), ...reviewed]
    .map(row => ({...row})).sort((a, b) => a.start_s - b.start_s);
}

export function validInterval(start, end, duration) {
  return Number.isFinite(start) && Number.isFinite(end) && 0 <= start && start < end && end <= duration;
}

if (typeof document !== "undefined") init().catch(error => {
  const status = document.querySelector("#status");
  status.textContent = `Could not open review: ${error.message}`;
  status.dataset.error = "true";
});

async function init() {
  const $ = id => document.getElementById(id);
  async function request(url, options) {
    const response = await fetch(url, options), body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }
  const [data, saved] = await Promise.all([request("/api/data"), request("/api/review")]);
  const video = $("video"), audio = data.audio, duration = data.duration_s;
  let reviewed = saved.intervals, revision = saved.revision, rows = [], selected = null;
  let windowStart = 0, audition = null, drag = null, dirty = false, saving = false, alignmentEdited = false;
  const colors = {start: "#c9f887", end: "#ffb08a", onset: "#bdacff", ink: "#9ebbb0", faint: "#344942"};
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const format = value => `${Math.floor(value / 60)}:${(value % 60).toFixed(3).padStart(6, "0")}`;
  const span = () => Math.min(duration, Number($("zoom").value));
  function settings() {
    return {threshold: Number($("threshold").value), min_gap: Number($("min-gap").value),
      max_gap: Number($("max-gap").value), offset: Number($("offset").value), scale: Number($("scale").value)};
  }
  function status(message, error = false) { $("status").textContent = message; $("status").dataset.error = String(error); }
  function changed() { dirty = true; status("Unsaved changes. Save reviewed pair to keep them."); }
  function center(time) { windowStart = clamp(time - span() / 2, 0, Math.max(0, duration - span())); }
  function seek(time) { video.currentTime = clamp(time, 0, duration); center(video.currentTime); draw(); }
  async function play() { try { await video.play(); } catch (error) { status(error.message, true); } }
  function selection() { return rows.find(row => row.id === selected); }
  function setEditor() {
    const pair = selection();
    for (const id of ["start", "end", "mark-start", "mark-end", "audition", "save", "reject", "note"]) $(id).disabled = !pair || saving;
    $("selected-title").textContent = pair ? `${pair.status === "suggested" ? "Suggested" : pair.status === "reviewed" ? "Reviewed" : "Rejected"} pair` : "Select a pair";
    $("start").value = pair ? pair.start_s.toFixed(3) : "";
    $("end").value = pair ? pair.end_s.toFixed(3) : "";
    $("pair-duration").textContent = pair ? `${(pair.end_s - pair.start_s).toFixed(3)} s` : "";
    $("note").value = pair?.note || "";
  }
  function list() {
    $("count").textContent = `${rows.filter(row => row.status === "suggested").length} suggestions`;
    $("candidates").replaceChildren();
    rows.forEach((row, index) => {
      const button = document.createElement("button");
      button.className = `candidate ${row.status}`; button.type = "button";
      button.setAttribute("aria-pressed", String(selected === row.id));
      const label = document.createElement("span"), detail = document.createElement("small");
      label.textContent = `${String(index + 1).padStart(2, "0")} · ${format(row.start_s)} → ${format(row.end_s)}`;
      detail.textContent = row.status === "suggested" ? `${(row.end_s - row.start_s).toFixed(2)} s` : row.status;
      button.append(label, detail); button.onclick = () => choose(row.id); $("candidates").append(button);
    });
    if (!rows.length) $("candidates").textContent = "No pairs at these settings. Lower the threshold or add a manual pair.";
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
    stashDraft(); selected = id; audition = null; video.pause();
    const pair = selection(); if (pair) seek(Math.max(0, pair.start_s - .7));
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
    if (!selection()) selected = rows[0]?.id || null;
    $("threshold-value").textContent = opts.threshold.toFixed(2);
    setEditor(); list(); draw();
  }
  function updateBoundary(key, value) {
    const pair = selection(); if (!pair) return;
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
  function draw() {
    const pair = selection(), opts = settings(), length = span();
    const overview = canvas("overview"), o = overview.ctx;
    for (const row of rows) {
      o.fillStyle = row.status === "rejected" ? "#513b32" : row.id === selected ? "#587346" : "#2c4b35";
      o.fillRect(row.start_s / duration * overview.w, 0, Math.max(2, (row.end_s - row.start_s) / duration * overview.w), overview.h);
    }
    waveform(o, overview.w, 0, duration, 6, overview.h - 12);
    o.strokeStyle = "#8aa68d"; o.strokeRect(windowStart / duration * overview.w, 1, length / duration * overview.w, overview.h - 2);
    marker(o, video.currentTime / duration * overview.w, overview.h, "#ffffff");
    const {ctx, w, h} = canvas("detail"), x = t => (t - windowStart) / length * w;
    if (pair) { ctx.fillStyle = "#304432"; ctx.fillRect(x(pair.start_s), 20, x(pair.end_s) - x(pair.start_s), h - 42); }
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
    if (pair) { marker(ctx, x(pair.start_s), h - 23, colors.start, "START"); marker(ctx, x(pair.end_s), h - 23, colors.end, "FINISH"); }
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
    if (pair) {
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
    if (pair) {
      marker(ctx, xAt(pair.start_s), plotHeight, colors.start, "START");
      marker(ctx, xAt(pair.end_s), plotHeight, colors.end, "FINISH");
    }
    marker(ctx, xAt(video.currentTime), plotHeight, "#ffffff");
  }
  async function savePair(state) {
    const pair = selection(); if (!pair || saving) return;
    if (!validInterval(pair.start_s, pair.end_s, duration)) { status("Invalid pair boundaries.", true); return; }
    const clean = {id: pair.id, start_s: pair.start_s, end_s: pair.end_s, note: $("note").value, status: state};
    const next = [...reviewed.filter(row => row.id !== pair.id), clean];
    saving = true; setEditor();
    try {
      const result = await request("/api/review", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({revision, intervals: next, settings: settings()})});
      reviewed = result.intervals; revision = result.revision; drafts.delete(pair.id); pair.edited = false;
      dirty = drafts.size > 0; regenerate();
      status(`Saved ${state} pair at ${format(clean.start_s)}–${format(clean.end_s)}.${dirty ? " Other edits remain unsaved." : ""}`);
    } catch (error) { status(`Save failed: ${error.message}`, true); }
    finally { saving = false; setEditor(); }
  }
  $("filename").textContent = data.video;
  $("timing").textContent = `All controls use the seekable review video's seconds. Original media PTS = review seconds + ${data.source_pts_origin_s.toFixed(6)} s. The original recording is preserved.`;
  $("sensor-quality").textContent = data.sensor.warnings.join(" ") || "Raw sensor magnitudes; gaps over 30 ms are not connected.";
  $("syncs").textContent = data.sensor.syncs.length ? `Recorded LED rises (session seconds): ${data.sensor.syncs.map(s => `#${s.id} at ${s.session_s.toFixed(3)}`).join(" · ")}` : "No recorded LED flashes.";
  let mapping = data.sensor.mapping;
  $("offset").value = mapping?.offset_s ?? data.sensor.offset_hint_s;
  $("scale").value = mapping?.scale ?? 1;
  if (saved.settings) for (const [key, id] of [["threshold", "threshold"], ["min_gap", "min-gap"], ["max_gap", "max-gap"], ["offset", "offset"], ["scale", "scale"]]) $(id).value = saved.settings[key];
  function alignmentStatus() {
    const opts = settings();
    const matched = mapping && !alignmentEdited && Math.abs(opts.offset - mapping.offset_s) < 1e-7 && Math.abs(opts.scale - mapping.scale) < 1e-7;
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
      alignmentEdited = false; alignmentStatus(); draw();
      status(`Saved flash correspondence. ${mapping.points} point(s) now align this video with the sensor session.`);
    } catch (error) { status(`Could not save flash: ${error.message}`, true); }
    finally { $("save-sync").disabled = false; }
  };
  $("sensor-panel").hidden = !data.sensor.samples.length;
  video.src = data.media_url; $("seek").max = duration;
  video.addEventListener("error", () => status("Video playback failed. Try a browser that supports this recording’s codec.", true));
  video.addEventListener("loadedmetadata", () => draw());
  $("play").onclick = () => { audition = null; if (video.paused) play(); else video.pause(); };
  $("back").onclick = () => { video.pause(); audition = null; seek(video.currentTime - 1 / 60); };
  $("forward").onclick = () => { video.pause(); audition = null; seek(video.currentTime + 1 / 60); };
  $("speed").onchange = () => { video.playbackRate = Number($("speed").value); };
  $("seek").oninput = () => { audition = null; seek(Number($("seek").value)); };
  $("zoom").onchange = () => { center(video.currentTime); draw(); };
  for (const id of ["threshold", "min-gap", "max-gap"]) $(id).oninput = () => { regenerate(); changed(); };
  for (const id of ["offset", "scale"]) $(id).oninput = () => { alignmentEdited = true; alignmentStatus(); changed(); draw(); };
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
  function next(delta) { if (rows.length) choose(rows[clamp(rows.findIndex(row => row.id === selected) + delta, 0, rows.length - 1)].id); }
  $("previous").onclick = () => next(-1); $("next").onclick = () => next(1);
  $("new").onclick = () => {
    stashDraft(); const start = clamp(video.currentTime, 0, Math.max(0, duration - .5));
    const row = {id: `manual-${crypto.randomUUID()}`, start_s: start, end_s: Math.min(duration, start + .5), status: "suggested", note: "", edited: true};
    rows.push(row); drafts.set(row.id, row); choose(row.id); changed();
  };
  $("audition").onclick = () => {
    const pair = selection(); if (!pair) return;
    audition = {start: Math.max(0, pair.start_s - .75), end: Math.min(duration, pair.end_s + 1)};
    seek(audition.start); play();
  };
  $("save").onclick = () => savePair("reviewed"); $("reject").onclick = () => savePair("rejected");
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
    if (pair) {
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
    if (event.target.closest("input,textarea,select,button,video") || event.metaKey || event.ctrlKey || event.altKey) return;
    const action = {" ": "play", ArrowLeft: "back", ArrowRight: "forward", i: "mark-start", o: "mark-end", n: "next", p: "previous"}[event.key];
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
  regenerate(); status(`Ready. ${reviewed.length} saved reviews. Select a pair to inspect it.`); requestAnimationFrame(tick);
}
