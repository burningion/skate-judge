// Pure pairing functions are also used by the Node checks.
export function proposePairs(onsets, strength, step, {threshold, min_gap, max_gap}, gaps = []) {
  const eligible = onsets.filter(p => p.strength >= threshold);
  const options = [];
  for (let i = 0; i < eligible.length; i++) {
    const a = eligible[i];
    for (let j = i + 1; j < eligible.length; j++) {
      const b = eligible[j], gap = b.time_s - a.time_s;
      if (gap > max_gap) break;
      if (gap < min_gap) continue;
      const sources = [...new Set([...(a.sources || ["audio"]), ...(b.sources || ["audio"])])];
      if (sources.includes("accel") && gaps.some(([lo, hi]) => a.time_s < hi && b.time_s > lo)) continue;
      const middle = strength.slice(Math.ceil((a.time_s + .06) / step), Math.floor((b.time_s - .06) / step));
      const activity = middle.reduce((sum, x) => sum + x, 0) / Math.max(1, middle.length);
      const score = Math.sqrt(a.strength * b.strength) * (1 + .35 * (sources.length - 1)) / (1 + 3 * activity);
      const prefix = (a.sources && !a.sources.includes("audio")) || (b.sources && !b.sources.includes("audio")) ? "imu" : "auto";
      options.push({id: `${prefix}-${a.time_s.toFixed(4)}-${b.time_s.toFixed(4)}`,
        start_s: a.time_s, end_s: b.time_s, score, sources, status: "suggested", note: ""});
    }
  }
  // Strongest non-overlapping explanation first, then chronological display.
  const selected = [];
  for (const pair of options.sort((a, b) => b.score - a.score)) {
    if (!selected.some(p => pair.start_s <= p.end_s + .1 && pair.end_s >= p.start_s - .1)) selected.push(pair);
  }
  return selected.sort((a, b) => a.start_s - b.start_s);
}

export function motionTimeline(motion, mapping, origin, duration) {
  if (!mapping || !motion || motion.unavailable_reason) return {events: [], gaps: [], coverage: null};
  const time = t => (t - mapping.offset_s) / mapping.scale - origin;
  return {events: motion.events.map(row => ({...row, time_s: time(row.time_s)}))
    .filter(row => row.time_s >= 0 && row.time_s < duration),
    gaps: (motion.gaps || []).map(([lo, hi]) => [time(lo), time(hi)]),
    coverage: motion.coverage ? motion.coverage.map(time) : null};
}

export function boardEventTimeline(context, mapping, origin, duration) {
  if (!mapping || !Number.isFinite(mapping.scale) || mapping.scale <= 0 || !Number.isFinite(mapping.offset_s)) return [];
  const time = t => (t - mapping.offset_s) / mapping.scale - origin;
  return (context?.events || []).map(row => ({...row,
    earliest_s: time(row.earliest_s), observed_s: time(row.observed_s), latest_s: time(row.latest_s)}))
    .filter(row => [row.earliest_s, row.observed_s, row.latest_s].every(Number.isFinite) &&
      row.earliest_s <= row.observed_s && row.observed_s <= row.latest_s && row.latest_s >= 0 && row.earliest_s < duration)
    .sort((a, b) => a.earliest_s - b.earliest_s);
}

export function attemptEventTimeline(context, mapping, origin, duration) {
  if (context?.availability !== "ready" || !Number.isFinite(context.threshold)) return [];
  return boardEventTimeline({events: context.events.filter(row =>
    Number.isFinite(row.score) && row.score >= context.threshold).map(row => ({
      event_id: `attempt-${context.model_sha256}-${row.time_s}`, potential_attempt: true,
      earliest_s: row.window_start_s, observed_s: row.time_s, latest_s: row.window_end_s,
      score: row.score,
    }))}, mapping, origin, duration);
}

export function jumpEventTimeline(context, mapping, origin, duration, sensitivity = "sensitive") {
  if (context?.availability !== "ready") return [];
  return boardEventTimeline({events: context.events.filter(row =>
    sensitivity !== "conservative" || row.detection_method !== "load_pair").map(row => ({...row, estimated_peak: true,
    earliest_s: row.takeoff_s, observed_s: row.peak_s, latest_s: row.landing_s,
  }))}, mapping, origin, duration).filter(row => row.observed_s >= 0 && row.observed_s < duration);
}

export function nearestFrame(times, target) {
  if (!times.length) return target;
  let lo = 0, hi = times.length;
  while (lo < hi) {
    const middle = (lo + hi) >>> 1;
    if (times[middle] < target) lo = middle + 1;
    else hi = middle;
  }
  if (!lo) return times[0];
  if (lo === times.length) return times.at(-1);
  return target - times[lo - 1] <= times[lo] - target ? times[lo - 1] : times[lo];
}

export function groupAttemptEvents(events, scoredRanges = null, gap = 1) {
  // Keep the original detections for score filtering;
  // a strong peak must not lend its score to a weaker neighboring window.
  const groups = [];
  for (const event of [...events].sort((a, b) => a.earliest_s - b.earliest_s)) {
    const previous = groups.at(-1);
    const lastCenter = previous?.detections.at(-1).observed_s;
    const continuous = scoredRanges === null || scoredRanges.some(([lo, hi]) =>
      lastCenter >= lo - 1e-7 && event.observed_s <= hi + 1e-7);
    if (previous && continuous && event.earliest_s <= previous.latest_s + gap + 1e-7) {
      previous.latest_s = Math.max(previous.latest_s, event.latest_s);
      previous.detections.push({...event});
      if (event.score > previous.score) {
        previous.score = event.score;
        previous.observed_s = event.observed_s;
      }
    } else groups.push({...event, detections: [{...event}]});
  }
  return groups;
}

export function attemptFilterTimeline(context, mapping, origin, duration) {
  const unavailable = message => ({events: [], scored_ranges: [], unavailable_reason: message});
  if (context?.availability !== "ready") return unavailable(context?.message || "Attempt model is unavailable.");
  if (!mapping || !Number.isFinite(mapping.scale) || mapping.scale <= 0 || !Number.isFinite(mapping.offset_s)) {
    return unavailable("Match LED flashes to enable the attempt-model filter.");
  }
  if (!Array.isArray(context.scored_center_ranges_s)) return unavailable("Restart the review server to load model coverage.");
  const time = t => (t - mapping.offset_s) / mapping.scale - origin;
  return {events: attemptEventTimeline(context, mapping, origin, duration),
    scored_ranges: context.scored_center_ranges_s.map(range => range.map(time)), unavailable_reason: ""};
}

export function screenAttemptCandidates(pairs, attempts, threshold = .5) {
  return pairs.map(pair => {
    if (attempts.unavailable_reason) return {...pair, attempt_filter: "unchecked",
      filter_reason: `${attempts.unavailable_reason} Kept for review.`};
    const nearby = attempts.events.filter(event => pair.start_s < event.latest_s && pair.end_s > event.earliest_s);
    const score = nearby.length ? Math.max(...nearby.map(event => event.score)) : null;
    if (score !== null && score >= threshold) return {...pair, attempt_filter: "supported", attempt_score: score,
      filter_reason: `Overlaps a learned attempt window (score ${score.toFixed(2)}). Check the action in the video.`};
    if (!attempts.scored_ranges.some(([start, end]) => pair.start_s >= start && pair.end_s <= end)) {
      return {...pair, attempt_filter: "unchecked", attempt_score: score,
        filter_reason: "Model coverage is incomplete around this suggestion; kept for review."};
    }
    return {...pair, attempt_filter: "suppressed", attempt_score: score,
      filter_reason: `No overlapping model detection at or above ${threshold.toFixed(2)}. Hidden unless Show filtered suggestions is enabled.`};
  });
}

function consolidateAttemptCandidates(pairs, attempts, threshold, gap = 1) {
  // Screen individual windows first, then collapse nearby supported audio pairs.
  // Group scores must never promote a weak neighbor or an unscored interval.
  const events = groupAttemptEvents((attempts.events || []).filter(event => event.score >= threshold),
    attempts.scored_ranges || []);
  const clusters = [];
  for (const pair of pairs.filter(row => row.attempt_filter === "supported")) {
    const coverage = attempts.scored_ranges.findIndex(([lo, hi]) => pair.start_s >= lo && pair.end_s <= hi);
    const group = events.findIndex(event => event.detections.some(detection =>
      pair.start_s < detection.latest_s && pair.end_s > detection.earliest_s));
    const previous = clusters.at(-1);
    if (coverage >= 0 && group >= 0 && previous?.coverage === coverage && previous.group === group &&
        pair.start_s <= previous.end_s + gap + 1e-7) {
      previous.pairs.push(pair);
      previous.end_s = Math.max(previous.end_s, pair.end_s);
    } else clusters.push({pairs: [pair], group, coverage, start_s: pair.start_s, end_s: pair.end_s});
  }
  for (const cluster of clusters.filter(row => row.pairs.length > 1)) {
    // Keep an original audio pair and its ID; grouping only adds review context.
    const [primary, ...alternatives] = [...cluster.pairs].sort((a, b) =>
      b.score - a.score || b.attempt_score - a.attempt_score || a.start_s - b.start_s);
    primary.attempt_cluster_start_s = cluster.start_s;
    primary.attempt_cluster_end_s = cluster.end_s;
    primary.attempt_cluster_count = cluster.pairs.length;
    primary.filter_reason += ` ${cluster.pairs.length} nearby audio suggestions grouped; playback covers the whole cluster. Show filtered suggestions to inspect alternatives.`;
    for (const alternative of alternatives) {
      alternative.attempt_filter = "suppressed";
      alternative.attempt_duplicate_of = primary.id;
      alternative.filter_reason = "Nearby alternative for the same potential attempt; grouped with another audio suggestion. Kept in Show filtered suggestions for review.";
    }
  }
  return pairs;
}

const attemptEventKinds = [["potential_attempt", "Potential attempt", "#c9f887"]];
const jumpEventKinds = [["estimated_peak", "Likely flight · line marks estimated peak", "#90d8de"]];
const boardEventKinds = [
  ["free_fall", "Free-fall", "#bdacff"],
  ["wake_up", "Wake-up", "#f4cc83"],
  ["orientation_changed", "Orientation", "#90d8de"],
];

function boardEventName(row) {
  if (row.estimated_peak) return `Estimated peak ${row.observed_s.toFixed(3)} s · flight ${(row.latest_s - row.earliest_s).toFixed(3)} s`;
  if (row.potential_attempt) return `Potential attempt · score ${row.score.toFixed(2)}`
    + (row.detections?.length > 1 ? ` · ${row.detections.length} detections grouped` : "");
  return boardEventKinds.filter(([key]) => row[key]).map(([, name]) => name).join(" + ");
}

export function screenAudioCandidates(pairs, motion, threshold = .4) {
  // This screens already selected audio pairs, preserving their identities and
  // boundaries. No evidence is treated as negative unless coverage is valid.
  return pairs.map(pair => {
    const lo = pair.start_s - .15, hi = pair.start_s + .2;
    if (!motion.coverage || lo < motion.coverage[0] || hi > motion.coverage[1] ||
        motion.gaps.some(([start, end]) => lo <= end && hi >= start)) {
      return {...pair, motion_filter: "unchecked", filter_reason: "IMU coverage is incomplete near pop; kept for review."};
    }
    const nearby = motion.events.filter(event => event.time_s >= lo && event.time_s <= hi);
    const support = {accel: 0, gyro: 0};
    for (const event of nearby) if (event.kind in support) support[event.kind] = Math.max(support[event.kind], event.strength);
    const sources = ["audio", ...Object.keys(support).filter(kind => support[kind] >= threshold)];
    const suppressed = sources.length === 1;
    return {...pair, sources, motion_support: support, motion_filter: suppressed ? "suppressed" : "supported",
      filter_reason: suppressed ? "No acceleration or gyro peak above the filter threshold near pop."
        : "Motion supports the proposed pop; check the video."};
  });
}

export function proposeCandidates(audio, motion, opts, attempts = {unavailable_reason: "Attempt model is unavailable."}) {
  const audioPairs = proposePairs(audio.onsets, audio.strength, audio.step_s, opts);
  if (opts.candidate_mode === "audio_attempts") {
    const threshold = opts.attempt_threshold ?? .5;
    return consolidateAttemptCandidates(screenAttemptCandidates(audioPairs, attempts, threshold), attempts, threshold)
      .filter(pair => opts.show_filtered || pair.attempt_filter !== "suppressed");
  }
  if (opts.candidate_mode === "audio_filtered") {
    return screenAudioCandidates(audioPairs, motion, opts.filter_threshold ?? .4)
      .filter(pair => opts.show_filtered || pair.motion_filter !== "suppressed");
  }
  if (opts.candidate_mode === "audio" || !motion.events.length) {
    return audioPairs;
  }
  const events = audio.onsets.filter(row => row.strength >= opts.threshold)
    .map(row => ({...row, sources: ["audio"]}));
  const eligible = motion.events.filter(row => row.strength >= opts.motion_threshold);
  for (const peak of eligible.filter(row => row.kind === "accel")) {
    const nearby = events.filter(row => row.sources.includes("audio") && Math.abs(row.time_s - peak.time_s) <= .1)
      .sort((a, b) => Math.abs(a.time_s - peak.time_s) - Math.abs(b.time_s - peak.time_s))[0];
    if (nearby) {
      if (!nearby.sources.includes("accel")) nearby.sources.push("accel");
    } else events.push({...peak, strength: peak.strength * opts.threshold / opts.motion_threshold, sources: ["accel"]});
  }
  // Angular-speed peaks can follow the pop. They corroborate an impact;
  // rotating a carried board alone does not create a pop/contact pair.
  const gyro = eligible.filter(row => row.kind === "gyro");
  for (const event of events) if (gyro.some(peak => Math.abs(peak.time_s - event.time_s) <= .2)) event.sources.push("gyro");
  const candidates = proposePairs(events.sort((a, b) => a.time_s - b.time_s), audio.strength, audio.step_s, opts, motion.gaps);
  // Keep the existing audio search intact: a louder landing or rebound must
  // not displace its proposed pop. Motion adds evidence and extra places to
  // inspect. Nearby alternatives disappear once a human labels their range.
  const supported = audioPairs.map(pair => ({...pair, sources: [...new Set(events
    .filter(event => event.time_s === pair.start_s || event.time_s === pair.end_s)
    .flatMap(event => event.sources))]}));
  const extra = candidates.filter(pair => pair.sources.includes("accel") && pair.sources.includes("gyro") &&
    !audioPairs.some(other => Math.abs(pair.start_s - other.start_s) < .15 ||
      (pair.start_s >= other.start_s && pair.start_s <= other.end_s + .1)));
  return [...supported, ...extra].sort((a, b) => a.start_s - b.start_s);
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
  const start = pair.label_start_s ?? Math.max(lo, Math.min(pair.start_s, pair.attempt_cluster_start_s ?? pair.start_s) - 1);
  const end = pair.label_end_s ?? Math.min(hi, Math.max(pair.end_s, pair.attempt_cluster_end_s ?? pair.end_s) + 2);
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
  if (data.api_version !== 9 || !saved.label_context || typeof saved.labels_revision !== "string") {
    throw new Error("Restart the review server to load the updated tools: press Ctrl+C in its terminal, rerun the review command, then reload this page. Refreshing the browser alone keeps the older Python server running.");
  }
}

export function alignmentDescription(mapping, matched = true) {
  if (!mapping || !matched) return "UNALIGNED / EXPLORATORY — no saved alignment is applied to this overlay.";
  if (mapping.source === "phone_clock") {
    const uncertainty = (mapping.network_uncertainty_s * 1000).toFixed(1);
    return `ESTIMATED phone timing · network uncertainty near clock checks: ±${uncertainty} ms. ${mapping.drift_estimated ? "Clock drift estimated." : "Clock drift unmeasured."} LED matches can refine alignment.`;
  }
  if (mapping.source === "led_phone_clock") return "Using 1 saved LED match for offset and phone timing for estimated drift. Match an ending flash to measure drift with LEDs.";
  return `Using ${mapping.points} saved LED sync point(s).${mapping.points === 1 ? " Drift is unmeasured with one point." : " Offset and drift come from LED matches."}`;
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
  const jumpReview = data.review_mode === "jumps";
  if (jumpReview) {
    $("board-event-mode").value = "jumps";
    $("board-event-mode-field").hidden = true;
    $("page-title").textContent = "Find your jump peaks";
    $("page-description").textContent = "Review jumps recorded with a waist or lower-back IMU. Match the LED flashes, then step through the estimated peaks.";
    $("workspace").className = "workspace jump-review";
    $("review-queue").hidden = true;
    $("detail").hidden = $("detail-legend").hidden = true;
    $("detail-title").textContent = "Sensor detail";
    $("video-panel").append($("status"));
    $("overview").setAttribute("aria-label", "Whole recording with likely flights and estimated jump peaks. Click to seek.");
    video.setAttribute("aria-label", "Recorded jump session");
  }
  const frameTimes = data.video_timing?.frame_times_s || [];
  const syncUnavailable = data.sensor.sync_unavailable_reason || (data.sensor.syncs.length ? ""
    : "Flash matching needs the recorded LED events from this session's events.jsonl. Download or import the sensor recording, then restart the review server.");
  let reviewed = saved.intervals, revision = saved.revision, rows = [], selected = null;
  let labelsRevision = saved.labels_revision, labelContext = saved.label_context;
  let lastTrick = [...reviewed].reverse().find(row => row.trick)?.trick || "";
  let windowStart = 0, audition = null, drag = null, dirty = false, saving = false;
  let motion = {events: [], gaps: []};
  let boardEvents = [], boardListKey = "", selectedBoardEvent = null;
  const colors = {start: "#c9f887", end: "#ffb08a", onset: "#bdacff", ink: "#9ebbb0", faint: "#344942"};
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const format = value => `${Math.floor(value / 60)}:${(value % 60).toFixed(3).padStart(6, "0")}`;
  const span = () => Math.min(duration, Number($("zoom").value));
  function settings() {
    return {threshold: Number($("threshold").value), min_gap: Number($("min-gap").value),
      max_gap: Number($("max-gap").value), offset: Number($("offset").value), scale: Number($("scale").value),
      candidate_mode: $("candidate-mode").value, motion_threshold: Number($("motion-threshold").value),
      filter_threshold: Number($("filter-threshold").value), attempt_threshold: Number($("attempt-threshold").value),
      show_filtered: $("show-filtered").checked};
  }
  function status(message, error = false) { $("status").textContent = message; $("status").dataset.error = String(error); }
  function changed() { dirty = true; status("Unsaved changes. Save label to keep them."); }
  function center(time) { windowStart = clamp(time - span() / 2, 0, Math.max(0, duration - span())); }
  function seek(time) { video.currentTime = clamp(time, 0, duration); center(video.currentTime); draw(); }
  async function play() { try { await video.play(); } catch (error) { status(error.message, true); } }
  function selection() { return rows.find(row => row.id === selected); }
  const windowFor = pair => pair && attemptWindow(pair, duration, labelContext.coverage, reviewed);
  const eventWindow = event => {
    if (!event) return null;
    if (!event.potential_attempt && !event.estimated_peak) return [Math.max(0, event.earliest_s - 1), Math.min(duration, event.latest_s + 2)];
    const index = boardEvents.findIndex(row => row.event_id === event.event_id);
    // Include approach and follow-through without playing into another group.
    return [Math.max(0, event.earliest_s - .5, Math.min(event.earliest_s, boardEvents[index - 1]?.latest_s ?? 0)),
      Math.min(duration, event.latest_s + 1, Math.max(event.latest_s, boardEvents[index + 1]?.earliest_s ?? duration))];
  };
  const isBackground = pair => pair?.outcome === "background";
  const backgroundName = row => row.activity ? `background · ${row.activity}` : "background · unspecified";
  const evidence = row => (row.sources || []).join(" + ");
  const savedDescription = row => `${isBackground(row) ? backgroundName(row) : `${row.trick || "trick"} · ${row.outcome}`} (${format(row.label_start_s)}–${format(row.label_end_s)})`;
  function setEditor() {
    const pair = selection();
    const preview = eventWindow(selectedBoardEvent);
    for (const id of ["start", "end", "mark-start", "mark-end", "audition", "save", "background", "reject", "note", "trick", "outcome", "activity", "label-start", "label-end", "label-mark-start", "label-mark-end"]) $(id).disabled = !pair || saving;
    for (const id of ["previous", "next", "new", "new-background", "open-overlap", "threshold", "min-gap", "max-gap", "candidate-mode", "motion-threshold", "filter-threshold", "attempt-threshold", "show-filtered", "offset", "scale", "pending-only", "save-sync"]) $(id).disabled = saving;
    $("sync-id").disabled = $("save-sync").disabled = saving || Boolean(syncUnavailable);
    $("audition").disabled = saving || (!pair && !preview);
    $("save").disabled ||= !labelContext.available;
    $("background").disabled ||= !labelContext.available;
    $("reject").disabled ||= Boolean(pair?.label_id);
    $("selected-title").textContent = pair ? pair.label_stale ? "Alignment changed · review label" : pair.status === "labeled" ? "Saved label" : isBackground(pair) ? "Label a background range" : pair.status === "rejected" ? "Skipped suggestion" : "Review suggestion" : "Select an interval";
    if (preview) $("selected-title").textContent = selectedBoardEvent.potential_attempt ? "Potential attempt preview" : "Board event preview";
    $("pop-markers").hidden = Boolean(preview) || isBackground(pair);
    $("audition").textContent = preview && !selectedBoardEvent.potential_attempt ? "Play event [Space]"
      : isBackground(pair) ? "Play range [Space]" : "Play attempt [Space]";
    $("start").value = pair ? pair.start_s.toFixed(3) : "";
    $("end").value = pair ? pair.end_s.toFixed(3) : "";
    const window = windowFor(pair);
    $("pair-duration").textContent = preview ? `${(preview[1] - preview[0]).toFixed(3)} s`
      : pair ? `${(isBackground(pair) && window ? window[1] - window[0] : pair.end_s - pair.start_s).toFixed(3)} s` : "";
    $("note").value = pair?.note || "";
    $("trick").value = preview ? "" : pair?.trick ?? lastTrick;
    $("outcome").value = pair?.outcome || "";
    $("activity-field").hidden = !isBackground(pair);
    $("activity").value = pair?.activity || "";
    $("candidate-evidence").textContent = preview ? `${boardEventName(selectedBoardEvent)} · ${format(preview[0])}–${format(preview[1])}.`
      : !isBackground(pair) && pair?.sources?.length
      ? `Suggestion evidence: ${evidence(pair)}. ${pair.filter_reason || "Check the pop and contact in the video."}` : "";
    $("trick").disabled ||= pair?.outcome === "background";
    $("label-start").value = preview ? preview[0].toFixed(3) : pair?.label_start_s ?? (window ? window[0].toFixed(3) : "");
    $("label-end").value = preview ? preview[1].toFixed(3) : pair?.label_end_s ?? (window ? window[1].toFixed(3) : "");
    $("label-availability").textContent = preview ? "To label this event, select a queue suggestion or choose + Missed attempt."
      : labelContext.available
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
    $("window-help").textContent = preview ? "Play previews the selected event window. Any queue draft is kept until you return to it."
      : isBackground(pair)
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
      detail.textContent = row.edited ? "unsaved" : row.label_stale ? "review alignment" : row.outcome === "background" ? backgroundName(row) : row.outcome ? `${row.trick || "trick"} · ${row.outcome}` : row.status === "rejected" ? "skipped" : evidence(row) || "unlabeled";
      if (!row.label_id && row.status !== "rejected") {
        if (row.attempt_score != null) detail.textContent += ` · attempt score ${row.attempt_score.toFixed(2)}`;
        if (row.attempt_duplicate_of) detail.textContent += " · grouped alternative";
        else if (row.motion_filter === "suppressed" || row.attempt_filter === "suppressed") detail.textContent += " · filtered";
        else if (row.attempt_filter === "unchecked") detail.textContent += " · model unchecked";
        if (row.attempt_cluster_count > 1) detail.textContent += ` · ${row.attempt_cluster_count} suggestions grouped`;
      }
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
    stashDraft(); selected = id; selectedBoardEvent = null; audition = null; video.pause();
    const pair = selection(); if (pair) seek(windowFor(pair)?.[0] ?? Math.max(0, pair.start_s - .7));
    setEditor(); list(); draw();
  }
  function regenerate() {
    stashDraft();
    const opts = settings();
    if (!(opts.min_gap >= .1 && opts.max_gap <= 5 && opts.min_gap < opts.max_gap)) {
      status("Use 0.1 ≤ minimum gap < maximum gap ≤ 5 seconds.", true); return;
    }
    motion = motionTimeline(data.sensor.motion, alignmentMatches() ? mapping : null, data.source_pts_origin_s, duration);
    const attempts = attemptFilterTimeline(data.sensor.attempts, alignmentMatches() ? mapping : null, data.source_pts_origin_s, duration);
    boardEvents = $("board-event-mode").value === "raw"
      ? boardEventTimeline(data.sensor.board_motion, alignmentMatches() ? mapping : null, data.source_pts_origin_s, duration)
      : $("board-event-mode").value === "jumps"
      ? jumpEventTimeline(data.sensor.jumps, alignmentMatches() ? mapping : null, data.source_pts_origin_s, duration, $("jump-sensitivity").value)
      : groupAttemptEvents(attemptEventTimeline(data.sensor.attempts, alignmentMatches() ? mapping : null, data.source_pts_origin_s, duration),
        attempts.unavailable_reason ? null : attempts.scored_ranges);
    if (selectedBoardEvent) {
      const refreshed = boardEvents.find(event => event.event_id === selectedBoardEvent.event_id);
      if (!refreshed || refreshed.earliest_s !== selectedBoardEvent.earliest_s || refreshed.latest_s !== selectedBoardEvent.latest_s) {
        audition = null; video.pause();
      }
      selectedBoardEvent = refreshed || null;
    }
    boardListKey = null;
    const modelFilter = opts.candidate_mode === "audio_attempts";
    const filterKey = modelFilter ? "attempt_filter" : "motion_filter";
    const allProposals = proposeCandidates(audio, motion, {...opts, show_filtered: true}, attempts);
    const suppressed = allProposals.filter(pair => pair[filterKey] === "suppressed").length;
    const duplicates = allProposals.filter(pair => pair.attempt_duplicate_of).length;
    const unchecked = allProposals.filter(pair => pair[filterKey] === "unchecked").length;
    const proposals = allProposals.filter(pair => opts.show_filtered || pair[filterKey] !== "suppressed");
    $("filter-controls").hidden = !modelFilter && opts.candidate_mode !== "audio_filtered";
    $("filter-threshold-field").hidden = modelFilter;
    $("attempt-threshold-field").hidden = !modelFilter;
    $("attempt-threshold-value").textContent = opts.attempt_threshold.toFixed(2);
    $("filter-help").textContent = modelFilter
      ? "Keeps audio pairs that overlap a model detection meeting this score, grouping nearby suggestions for the same attempt with up to a 1 s gap. Show filtered suggestions to inspect alternatives. Higher scores can miss real attempts; scores are not confidence percentages. Unscored regions stay visible; hidden suggestions remain unlabeled."
      : "Higher values hide more audio candidates and may hide real attempts. Missing or unreliable IMU coverage keeps a candidate visible. Hidden suggestions remain unlabeled.";
    $("motion-threshold-field").hidden = opts.candidate_mode !== "audio_motion";
    $("filter-threshold-value").textContent = opts.filter_threshold.toFixed(2);
    $("motion-threshold-value").textContent = opts.motion_threshold.toFixed(2);
    $("motion-status").textContent = opts.candidate_mode === "audio" ? "Using audio suggestions only."
      : modelFilter ? (attempts.unavailable_reason ? `${attempts.unavailable_reason} Keeping all audio candidates.`
        : `${suppressed} of ${allProposals.length} audio candidates ${opts.show_filtered ? "marked as filtered" : "hidden by attempt model"}${duplicates ? ` (including ${duplicates} nearby alternatives grouped)` : ""}; ${unchecked} kept without complete model coverage. Minimum score ${opts.attempt_threshold.toFixed(2)}. Saved labels and edits stay visible.`)
      : !alignmentMatches() ? "Match LED flashes to enable motion suggestions. Using audio only."
      : data.sensor.motion?.unavailable_reason || (!data.sensor.motion ? "No motion features available. Using audio only."
      : opts.candidate_mode === "audio_filtered" ? `${suppressed} of ${allProposals.length} audio candidates ${opts.show_filtered ? "marked as filtered" : "hidden by motion filter"}; ${unchecked} kept without enough IMU coverage. Show filtered suggestions to review them. No labels are assigned by the filter.`
      : `${motion.events.length} motion peaks in this clip. Acceleration adds impact candidates; gyro supports nearby events. Suggestions still need review.`);
    rows = mergeReviews(proposals, reviewed);
    rows = mergeReviews(rows, [...drafts.values()]);
    if (jumpReview) rows = [];
    if (!selectedBoardEvent && (!selection() || ($("pending-only").checked && !needsReview(selection())))) selected = visibleRows()[0]?.id || null;
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
    for (const row of jumpReview ? [] : rows) {
      o.fillStyle = row.status === "rejected" ? "#513b32" : row.id === selected ? "#587346" : "#2c4b35";
      const range = row.label_id || isBackground(row) ? windowFor(row) : null;
      const start = range?.[0] ?? row.start_s, end = range?.[1] ?? row.end_s;
      if (row.label_id) o.fillStyle = isBackground(row) ? "#344650" : "#49633d";
      o.fillRect(start / duration * overview.w, 0, Math.max(2, (end - start) / duration * overview.w), overview.h);
    }
    waveform(o, overview.w, 0, duration, 6, overview.h - 12);
    for (const event of boardEvents.filter(row => row.estimated_peak)) {
      o.fillStyle = "#90d8de44";
      o.fillRect(event.earliest_s / duration * overview.w, 0,
        (event.latest_s - event.earliest_s) / duration * overview.w, overview.h);
      marker(o, event.observed_s / duration * overview.w, overview.h, "#90d8de");
    }
    o.strokeStyle = "#8aa68d"; o.strokeRect(windowStart / duration * overview.w, 1, length / duration * overview.w, overview.h - 2);
    marker(o, video.currentTime / duration * overview.w, overview.h, "#ffffff");
    if (jumpReview) {
      if (data.sensor.samples.length) drawImu();
      drawBoardEvents();
      return;
    }
    const {ctx, w, h} = canvas("detail"), x = t => (t - windowStart) / length * w;
    drawSaved(ctx, x, h);
    drawAttempt(ctx, x, h, pair);
    if (pair && !isBackground(pair)) { ctx.fillStyle = "#304432"; ctx.fillRect(x(pair.start_s), 20, x(pair.end_s) - x(pair.start_s), h - 42); }
    waveform(ctx, w, windowStart, length, 26, 108);
    ctx.font = "11px system-ui"; ctx.fillStyle = "#a5b8b3"; ctx.fillText(audio.available === false ? "NO AUDIO · MANUAL REVIEW AND ALIGNED IMU AVAILABLE" : "AUDIO WAVEFORM", 5, 33); ctx.fillText("ONSET STRENGTH", 5, 155);
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
    drawBoardEvents();
  }
  function seekBoardEvent(event) {
    if (saving) return;
    stashDraft(); selected = null; selectedBoardEvent = event;
    video.pause(); audition = null;
    video.currentTime = clamp(event.estimated_peak
      ? nearestFrame(frameTimes, event.observed_s) + 1e-6 : event.earliest_s, 0, duration);
    windowStart = clamp(video.currentTime - span() / 3, 0, Math.max(0, duration - span()));
    setEditor(); list(); draw();
  }
  function drawBoardEvents() {
    const raw = $("board-event-mode").value === "raw";
    const jumps = $("board-event-mode").value === "jumps";
    const context = raw ? data.sensor.board_motion : jumps ? data.sensor.jumps : data.sensor.attempts;
    const matched = alignmentMatches();
    const shown = boardEvents.filter(event => event.earliest_s < windowStart + span() && event.latest_s >= windowStart);
    const available = context?.availability === (raw ? "recorded" : "ready");
    const eventCount = jumps && $("jump-sensitivity").value === "conservative"
      ? (context?.events || []).filter(event => event.detection_method !== "load_pair").length
      : context?.events?.length || 0;
    $("board-event-title").textContent = raw ? "Raw board observations" : jumps ? "Estimated jump peaks" : "Potential trick attempts";
    const noun = raw ? "raw observation(s)" : jumps ? "estimated peak(s)" : "potential attempt(s)";
    const skipped = Object.values(context?.quality?.skipped || {}).reduce((sum, count) => sum + count, 0);
    const modelInfo = raw || jumps || !available ? "" : (matched ? ` Grouped from ${boardEvents.reduce((sum, event) => sum + event.detections.length, 0)} model detections.` : "")
      + ` Learned model: ${context.model_name}; score cutoff ${context.threshold.toFixed(2)}.`
      + (skipped ? ` ${skipped} sensor windows could not be scored.` : "")
      + (context.training_session_overlap ? " This recording was used to train the model." : "");
    $("board-event-status").textContent = (!context ? "Restart the review server to load event detection."
      : context.message || (!matched ? `${eventCount} ${raw || jumps ? noun : "model detection(s)"}. Match LED flashes to place them on the video timeline.`
      : `${boardEvents.length} ${noun} in this clip · ${shown.length} in view.`)) + modelInfo;
    $("board-event-help").textContent = raw
      ? "All sensor interrupts, including everyday board movement. Bands show uncertain motion times. Free-fall describes the board, not a rider fall."
      : jumps ? "More sensitive adds push-off/landing pairs with interrupted unloading; Conservative requires sustained low-g flight. Peaks estimate the midpoint between sensor boundaries. Tucks, rotation and trampoline motion can shift it. Check the video; these are not measured apexes."
      : "Overlapping model windows, or windows up to 1 s apart, form one potential attempt. Bands span the full group; Play attempt adds 0.5 s before and 1 s after. Groups do not cross known sensor gaps. The displayed score is the strongest detection, not a confidence percentage. Confirm the action in the video.";
    $("board-event-help").hidden = !(available || context?.events?.length);
    $("board-events").hidden = !matched || !(available || boardEvents.length);
    $("board-events").setAttribute("aria-label", raw ? "Raw board motion intervals. Click to seek." : jumps ? "Likely flight intervals and estimated peaks. Click a flight to seek its peak." : "Learned potential trick attempt windows. Click to seek.");
    const seekTime = event => jumps ? event.observed_s : event.earliest_s;
    const activeIndex = boardEvents.findIndex(event => event.event_id === selectedBoardEvent?.event_id);
    const previous = activeIndex >= 0 ? boardEvents[activeIndex - 1]
      : [...boardEvents].reverse().find(event => seekTime(event) < video.currentTime - .001);
    const next = activeIndex >= 0 ? boardEvents[activeIndex + 1]
      : boardEvents.find(event => seekTime(event) > video.currentTime + .001);
    $("board-event-previous").textContent = jumps ? "← Previous peak" : "← Previous event";
    $("board-event-next").textContent = jumps ? "Next peak →" : "Next event →";
    $("board-event-previous").disabled = saving || !previous;
    $("board-event-next").disabled = saving || !next;
    $("board-event-previous").onclick = () => previous && seekBoardEvent(previous);
    $("board-event-next").onclick = () => next && seekBoardEvent(next);
    $("jump-navigation").hidden = !jumps;
    $("jump-previous").disabled = $("board-event-previous").disabled;
    $("jump-next").disabled = $("board-event-next").disabled;
    $("jump-previous").onclick = () => previous && seekBoardEvent(previous);
    $("jump-next").onclick = () => next && seekBoardEvent(next);
    $("jump-selected").textContent = selectedBoardEvent?.estimated_peak
      ? `Jump ${activeIndex + 1} of ${boardEvents.length} · ${boardEventName(selectedBoardEvent)} · frame ${nearestFrame(frameTimes, selectedBoardEvent.observed_s).toFixed(3)} s${selectedBoardEvent.detection_method === "load_pair" ? " · Push-off / landing estimate" : ""}`
      : $("board-event-status").textContent;
    // Keep focused event buttons stable while the playhead advances.
    const listKey = `${selectedBoardEvent?.event_id}:${saving}|` + shown.map(event => `${event.event_id}:${event.earliest_s}:${event.latest_s}`).join("|");
    if (listKey !== boardListKey || !$("board-event-list").children.length) {
      boardListKey = listKey;
      $("board-event-list").replaceChildren();
      for (const event of shown) {
        const button = document.createElement("button");
        button.type = "button";
        button.disabled = saving;
        button.setAttribute("aria-pressed", String(event.event_id === selectedBoardEvent?.event_id));
        button.textContent = event.estimated_peak ? boardEventName(event) : `${boardEventName(event)} · ${event.earliest_s.toFixed(3)}–${event.latest_s.toFixed(3)} s`;
        button.title = event.estimated_peak
          ? `Likely flight ${event.earliest_s.toFixed(3)}–${event.latest_s.toFixed(3)} s. Click to pause at the estimated peak.`
          : event.potential_attempt
          ? `${event.detections.length} detection(s); strongest center ${event.observed_s.toFixed(3)} s. Play attempt reviews the complete group with context.`
          : `Observed at ${event.observed_s.toFixed(3)} s. Click to review from the start of its possible interval.`;
        button.onclick = () => seekBoardEvent(event);
        $("board-event-list").append(button);
      }
    }
    if ($("board-events").hidden) return;
    const {ctx, w, h} = canvas("board-events");
    // Share the horizontal time axis with the audio and IMU plots above.
    const x = t => (t - windowStart) / span() * w;
    ctx.font = "11px system-ui";
    for (const [index, [key, name, color]] of (raw ? boardEventKinds : jumps ? jumpEventKinds : attemptEventKinds).entries()) {
      const y = index * 27;
      ctx.fillStyle = color; ctx.fillText(name, 3, y + 11);
      ctx.strokeStyle = colors.faint; ctx.beginPath(); ctx.moveTo(0, y + 25); ctx.lineTo(w, y + 25); ctx.stroke();
      for (const event of shown.filter(row => row[key])) {
        const start = Math.max(0, x(event.earliest_s)), end = Math.min(w, x(event.latest_s));
        ctx.fillStyle = color; ctx.globalAlpha = .35;
        ctx.fillRect(start, y + 14, Math.max(2, end - start), 10);
        ctx.globalAlpha = 1; ctx.strokeStyle = color;
        ctx.strokeRect(start, y + 14, Math.max(2, end - start), 10);
        if (event.estimated_peak) marker(ctx, x(event.observed_s), h - 19, color);
      }
    }
    for (let i = 0; i <= 4; i++) {
      ctx.fillStyle = colors.ink;
      ctx.fillText(`${(windowStart + i / 4 * span()).toFixed(2)}s`, clamp(i / 4 * w - 18, 0, w - 46), h - 4);
    }
    if (video.currentTime >= windowStart && video.currentTime <= windowStart + span()) marker(ctx, x(video.currentTime), h - 19, "#ffffff");
  }
  function drawImu() {
    const {ctx, w, h} = canvas("imu"), opts = settings(), length = span();
    const time = row => (row[0] - opts.offset) / opts.scale - data.source_pts_origin_s;
    const samples = data.sensor.samples.filter(row => time(row) >= windowStart && time(row) <= windowStart + length);
    $("sensor-count").textContent = `${samples.length} samples in view`;
    const pair = selection(), plotHeight = h - 22, half = plotHeight / 2;
    const xAt = t => (t - windowStart) / length * w;
    if (!jumpReview) drawSaved(ctx, xAt, h);
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
    if (["audio_motion", "audio_filtered"].includes(opts.candidate_mode)) for (const event of motion.events) {
      const threshold = opts.candidate_mode === "audio_filtered" ? opts.filter_threshold : opts.motion_threshold;
      if (event.strength < threshold || event.time_s < windowStart || event.time_s > windowStart + length) continue;
      const top = event.kind === "gyro" ? half : 0;
      ctx.fillStyle = event.kind === "gyro" ? "#f4cc83" : "#90d8de";
      ctx.fillRect(xAt(event.time_s) - 1, top + 33, 2, half - 42);
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
      status("Use the saved alignment before labeling. Reset the offset and scale to the saved phone estimate or LED match, or reload.", true); return;
    }
    const clean = {id: pair.id, start_s: pair.start_s, end_s: pair.end_s, note: $("note").value,
      status: decision === "skip" ? "rejected" : "reviewed", outcome: chosenOutcome,
      trick: chosenOutcome === "background" ? "" : $("trick").value.trim(),
      activity: chosenOutcome === "background" ? $("activity").value : "",
      sources: pair.sources || [],
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
  $("sensor-empty").hidden = Boolean(data.sensor.samples.length);
  $("sensor-empty").textContent = data.sensor.samples_message || "No accelerometer or gyroscope samples loaded. This session needs samples.csv; download or import the sensor recording, then restart the review server.";
  $("imu").hidden = $("sensor-help").hidden = !data.sensor.samples.length;
  $("sensor-count").textContent = `${data.sensor.samples.length.toLocaleString()} samples loaded`;
  $("sync-unavailable").hidden = !syncUnavailable;
  $("sync-unavailable").textContent = syncUnavailable;
  $("syncs").textContent = data.sensor.syncs.length ? `Recorded LED rises (session seconds): ${data.sensor.syncs.map(s => `#${s.id} at ${s.session_s.toFixed(3)}`).join(" · ")}` : "No recorded LED flashes.";
  let mapping = labelContext.mapping || data.sensor.mapping;
  data.sensor.mapping = mapping;
  $("offset").value = mapping?.offset_s ?? data.sensor.offset_hint_s;
  $("scale").value = mapping?.scale ?? 1;
  if (saved.settings) for (const [key, id] of [["threshold", "threshold"], ["min_gap", "min-gap"], ["max_gap", "max-gap"], ["offset", "offset"], ["scale", "scale"], ["candidate_mode", "candidate-mode"], ["motion_threshold", "motion-threshold"], ["filter_threshold", "filter-threshold"], ["attempt_threshold", "attempt-threshold"]]) {
    // A fresh measured mapping takes precedence over an older exploratory overlay.
    if (saved.settings[key] != null && (!mapping || !["offset", "scale"].includes(key))) $(id).value = saved.settings[key];
  }
  $("show-filtered").checked = saved.settings?.show_filtered === true;
  $("board-event-mode").onchange = () => regenerate();
  function alignmentMatches() {
    const opts = settings();
    return mapping && Math.abs(opts.offset - mapping.offset_s) < 1e-7 && Math.abs(opts.scale - mapping.scale) < 1e-7;
  }
  function alignmentStatus() {
    const matched = alignmentMatches();
    $("alignment").textContent = alignmentDescription(mapping, matched);
    $("alignment").className = matched && mapping.source !== "phone_clock" ? "help" : "help warning";
    const estimate = data.sensor.clock_estimate;
    $("clock-estimate").hidden = !estimate;
    if (estimate) $("clock-estimate").textContent = `Phone timing evidence retained: ${estimate.probe_count} clock checks. Network uncertainty excludes camera exposure and sensor timing errors; accuracy outside the measured interval is unverified. LED refinements keep the original estimate.`;
  }
  alignmentStatus();
  for (const sync of data.sensor.syncs) {
    const option = document.createElement("option"); option.value = sync.id;
    option.textContent = `#${sync.id} · session ${sync.session_s.toFixed(3)} s`;
    $("sync-id").append(option);
  }
  $("save-sync").disabled = Boolean(syncUnavailable);
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
    finally { setEditor(); }
  };
  $("sensor-panel").hidden = false;
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
  $("jump-sensitivity").onchange = () => { regenerate(); };
  $("seek").oninput = () => { audition = null; seek(Number($("seek").value)); };
  $("zoom").onchange = () => { center(video.currentTime); draw(); };
  for (const id of ["threshold", "min-gap", "max-gap", "candidate-mode", "motion-threshold", "filter-threshold", "attempt-threshold", "show-filtered"]) $(id).oninput = () => { regenerate(); changed(); };
  for (const id of ["offset", "scale"]) $(id).oninput = () => { alignmentStatus(); if (!jumpReview) changed(); regenerate(); };
  $("imu").onpointerdown = event => {
    const box = $("imu").getBoundingClientRect(); audition = null;
    video.currentTime = clamp(windowStart + (event.clientX - box.left) / box.width * span(), 0, duration);
    draw();
  };
  $("board-events").onpointerdown = event => {
    const box = $("board-events").getBoundingClientRect();
    const time = clamp(windowStart + (event.clientX - box.left) / box.width * span(), 0, duration);
    const jump = boardEvents.find(row => row.estimated_peak && row.earliest_s <= time && time <= row.latest_s);
    if (jump) { seekBoardEvent(jump); return; }
    video.pause(); audition = null;
    video.currentTime = time;
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
  $("pending-only").onchange = () => { if (!selectedBoardEvent && !visibleRows().some(row => row.id === selected)) choose(visibleRows()[0]?.id || null); list(); };
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
    if (saving) return;
    const pair = selection(); if (!pair && !selectedBoardEvent) return;
    const window = selectedBoardEvent ? eventWindow(selectedBoardEvent) : windowFor(pair);
    audition = {start: window?.[0] ?? Math.max(0, pair.start_s - 1), end: window?.[1] ?? Math.min(duration, pair.end_s + 2)};
    seek(audition.start); play();
  };
  $("save").onclick = () => savePair("label");
  $("background").onclick = () => savePair("label", "background");
  $("reject").onclick = () => savePair("skip");
  for (const id of ["trick", "outcome", "activity"]) $(id).oninput = () => {
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
    if ($("board-event-mode").value === "jumps") {
      const action = {n: "jump-next", p: "jump-previous", " ": "play", arrowleft: "back", arrowright: "forward"}[key];
      if (action) { event.preventDefault(); $(action).click(); return; }
      if (jumpReview) return;
    }
    const outcomes = {"1": "make", "2": "bail", "3": "fall", "4": "unknown", "0": "background"};
    if (outcomes[key]) { event.preventDefault(); savePair("label", outcomes[key]); return; }
    const action = {" ": video.paused ? "audition" : "play", arrowleft: "back", arrowright: "forward", i: "label-mark-start", o: "label-mark-end", n: "next", p: "previous", s: "save", x: "reject"}[key];
    if (action) { event.preventDefault(); $(action).click(); }
  });
  window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  new ResizeObserver(() => draw()).observe($("overview"));
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
  regenerate(); status(jumpReview ? "Experimental jump review · match the beginning and ending LED flashes, then use Next peak."
    : `Ready. ${reviewed.filter(row => row.label_id).length} saved labels. Watch an attempt, then choose an outcome or Not a trick.`); requestAnimationFrame(tick);
}
