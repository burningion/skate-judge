import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile} from "node:fs/promises";
import {alignmentDescription, attemptEventTimeline, attemptFilterTimeline, attemptWindow, backgroundWindow, boardEventTimeline, groupAttemptEvents, init, jumpEventTimeline, mergeReviews, motionTimeline, nearestFrame, needsReview, nextPending, overlappingLabels, proposeCandidates, proposePairs, requireCurrentServer, screenAttemptCandidates, screenAudioCandidates, stepFrame, validInterval} from "../viz/trick_review.mjs";
import {evaluateClips} from "../viz/evaluate_candidates.mjs";

const opts = {threshold: .65, min_gap: .18, max_gap: .9};
const combined = {...opts, candidate_mode: "audio_motion", motion_threshold: .65};

function attemptFixture() {
  return {availability: "ready", threshold: .5, model_name: "trained", model_sha256: "model",
    scored_center_ranges_s: [[1, 19]], events: [
      {time_s: 2.3, window_start_s: 1.3, window_end_s: 3.3, score: .8}]};
}

test("attempt-model filter retains matching audio pairs and supports a stricter score cutoff", () => {
  const audio = audioFixture([2, 2.5, 8, 8.5]);
  const context = attemptFixture(), before = structuredClone(context);
  const attempts = attemptFilterTimeline(context, {scale: 1, offset_s: 0}, 0, 20);
  const settings = {...opts, candidate_mode: "audio_attempts", attempt_threshold: .5};
  const baseline = proposePairs(audio.onsets, [], .01, opts);
  const kept = proposeCandidates(audio, {}, settings, attempts);
  assert.deepEqual(kept.map(row => row.id), [baseline[0].id]);
  assert.deepEqual(kept.map(row => [row.start_s, row.end_s]), [[2, 2.5]]);
  assert.equal(kept[0].attempt_score, .8);
  const all = proposeCandidates(audio, {}, {...settings, show_filtered: true}, attempts);
  assert.deepEqual(all.map(row => row.attempt_filter), ["supported", "suppressed"]);
  assert.deepEqual(proposeCandidates(audio, {}, {...settings, attempt_threshold: .85}, attempts), []);
  assert.ok(all.every(row => row.outcome === undefined && row.status === "suggested"));
  assert.deepEqual(context, before);
});

test("attempt filter keeps unscored regions and unavailable or unaligned data visible", () => {
  const pairs = proposePairs(audioFixture([2, 2.5, 8, 8.5]).onsets, [], .01, opts);
  const context = {...attemptFixture(), events: [], scored_center_ranges_s: [[1, 5], [9, 19]]};
  const timeline = attemptFilterTimeline(context, {scale: 1, offset_s: 0}, 0, 20);
  assert.deepEqual(screenAttemptCandidates(pairs, timeline).map(row => row.attempt_filter), ["suppressed", "unchecked"]);
  for (const [source, mapping] of [[context, null], [undefined, {scale: 1, offset_s: 0}],
    [{...context, availability: "unknown"}, {scale: 1, offset_s: 0}]]) {
    const result = screenAttemptCandidates(pairs, attemptFilterTimeline(source, mapping, 0, 20));
    assert.ok(result.every(row => row.attempt_filter === "unchecked"));
  }
  const mapped = attemptFilterTimeline(context, {scale: 1.01, offset_s: 10}, .5, 20);
  assert.equal(mapped.scored_ranges[1][1], (19 - 10) / 1.01 - .5);
  assert.equal(mapped.scored_ranges[1][0], (9 - 10) / 1.01 - .5);
});

test("learned filtering keeps one original audio pair for adjacent suggestions and restores every alternative", () => {
  const audio = audioFixture([2, 2.2, 2.4, 2.6, 2.8, 3, 8, 8.5]);
  const attempts = attemptFilterTimeline(attemptFixture(), {scale: 1, offset_s: 0}, 0, 20);
  const before = structuredClone({audio, attempts});
  const settings = {...opts, candidate_mode: "audio_attempts", attempt_threshold: .5};
  const baseline = proposePairs(audio.onsets, [], .01, opts);
  assert.equal(baseline.length, 4);
  const kept = proposeCandidates(audio, {}, settings, attempts);
  assert.equal(kept.length, 1);
  assert.equal(kept[0].id, baseline[0].id);
  assert.deepEqual([kept[0].start_s, kept[0].end_s], [2, 2.2]);
  assert.equal(kept[0].attempt_cluster_count, 3);
  assert.deepEqual(attemptWindow(kept[0], 20, [0, 20]), [1, 5]);
  assert.deepEqual(attemptWindow({...kept[0], label_start_s: 1.5, label_end_s: 4}, 20, [0, 20]), [1.5, 4]);
  const audit = proposeCandidates(audio, {}, {...settings, show_filtered: true}, attempts);
  assert.deepEqual(audit.map(row => row.id), baseline.map(row => row.id));
  assert.deepEqual(audit.map(row => row.attempt_duplicate_of), [undefined, kept[0].id, kept[0].id, undefined]);
  assert.ok(audit.every(row => row.outcome === undefined && row.status === "suggested"));
  assert.deepEqual(proposeCandidates(audio, {}, {...settings, candidate_mode: "audio"}, attempts), baseline);
  assert.deepEqual({audio, attempts}, before);
});

test("nearby candidate grouping keeps the strongest audio pair and covers earlier alternatives in playback", () => {
  const audio = audioFixture([2, 2.2, 2.4, 2.6]);
  audio.onsets[2].strength = audio.onsets[3].strength = 2;
  const attempts = attemptFilterTimeline(attemptFixture(), {scale: 1, offset_s: 0}, 0, 20);
  const [kept] = proposeCandidates(audio, {}, {...opts, candidate_mode: "audio_attempts"}, attempts);
  assert.equal(kept.id, "auto-2.4000-2.6000");
  assert.deepEqual([kept.start_s, kept.end_s], [2.4, 2.6]);
  assert.deepEqual(attemptWindow(kept, 20, [0, 20]), [1, 4.6]);
});

test("candidate grouping allows a one-second gap but keeps more distant suggestions separate", () => {
  const audio = audioFixture([2, 2.2, 3.2, 3.4, 4.4001, 4.6]);
  const context = {...attemptFixture(), events: [
    {time_s: 2.5, window_start_s: 1.5, window_end_s: 3.5, score: .8},
    {time_s: 4, window_start_s: 3, window_end_s: 5, score: .8}]};
  const attempts = attemptFilterTimeline(context, {scale: 1, offset_s: 0}, 0, 20);
  const kept = proposeCandidates(audio, {}, {...opts, candidate_mode: "audio_attempts"}, attempts);
  assert.deepEqual(kept.map(row => row.start_s), [2, 4.4001]);
  assert.equal(kept[0].attempt_cluster_count, 2);
});

test("candidate grouping never hides pairs across unscored regions or extends stronger model scores", () => {
  const audio = audioFixture([2, 2.2, 2.4, 2.6]);
  const settings = {...opts, candidate_mode: "audio_attempts"};
  for (const ranges of [[[1, 2.2], [2.4, 19]], [[1, 2.2]]]) {
    const attempts = attemptFilterTimeline({...attemptFixture(), scored_center_ranges_s: ranges}, {scale: 1, offset_s: 0}, 0, 20);
    assert.equal(proposeCandidates(audio, {}, settings, attempts).length, 2);
  }
  const context = {...attemptFixture(), events: [
    {...attemptFixture().events[0], score: .95},
    {time_s: 3.5, window_start_s: 2.5, window_end_s: 4.5, score: .55}]};
  const attempts = attemptFilterTimeline(context, {scale: 1, offset_s: 0}, 0, 20);
  const audit = proposeCandidates(audioFixture([2.2, 2.4, 3.4, 3.6]), {},
    {...settings, attempt_threshold: .9, show_filtered: true}, attempts);
  assert.deepEqual(audit.map(row => row.attempt_filter), ["supported", "suppressed"]);
  assert.ok(audit.every(row => !row.attempt_duplicate_of && !row.attempt_cluster_count));
});

test("learned events require model scores and map input windows through drift and media origin", () => {
  const context = {availability: "ready", threshold: .5, model_sha256: "model", events: [
    {time_s: 13.02, window_start_s: 12.01, window_end_s: 14.03, score: .8},
    {time_s: 15, window_start_s: 14, window_end_s: 16, score: .49}]};
  const before = structuredClone(context), mapping = {scale: 1.01, offset_s: 10};
  const mapped = attemptEventTimeline(context, mapping, 1, 20);
  assert.equal(mapped.length, 1);
  assert.ok(Math.abs(mapped[0].earliest_s - (2.01 / 1.01 - 1)) < 1e-9);
  assert.ok(Math.abs(mapped[0].observed_s - (3.02 / 1.01 - 1)) < 1e-9);
  assert.ok(Math.abs(mapped[0].latest_s - (4.03 / 1.01 - 1)) < 1e-9);
  assert.equal(mapped[0].potential_attempt, true);
  assert.equal(mapped[0].score, .8);
  assert.deepEqual(attemptEventTimeline(context, null, 0, 20), []);
  assert.deepEqual(attemptEventTimeline({...context, availability: "unknown"}, mapping, 0, 20), []);
  assert.deepEqual(context, before);
});

test("overlapping detections become one complete attempt with the strongest score and stable identity", () => {
  const events = [3, 4, 5, 10].map((time, index) => ({event_id: String(index), potential_attempt: true,
    earliest_s: time - 1, observed_s: time, latest_s: time + 1, score: [.6, .95, .7, .8][index]}));
  const before = structuredClone(events);
  const grouped = groupAttemptEvents([events[2], events[0], events[3], events[1]], [[1, 19]]);
  assert.equal(grouped.length, 2);
  assert.deepEqual(grouped.map(row => [row.earliest_s, row.latest_s]), [[2, 6], [9, 11]]);
  assert.equal(grouped[0].score, .95);
  assert.equal(grouped[0].observed_s, 4);
  assert.equal(grouped[0].event_id, events[0].event_id);
  assert.equal(grouped[0].detections.length, 3);
  assert.deepEqual(events, before, "grouping must preserve original detector results");
  assert.deepEqual(groupAttemptEvents([]), []);
});

test("attempt groups require at most a one-second gap and never bridge known unscored regions", () => {
  const events = [2, 5, 8.01].map((time, index) => ({event_id: String(index), potential_attempt: true,
    earliest_s: time - 1, observed_s: time, latest_s: time + 1, score: .8}));
  const grouped = groupAttemptEvents(events, [[1, 19]]);
  assert.deepEqual(grouped.map(row => row.detections.length), [2, 1]);
  assert.equal(groupAttemptEvents(events, [[1, 2], [5, 19]]).length, 3);
});

test("grouping for playback never promotes a weak neighbor or a quiet gap in the queue score filter", () => {
  const context = {...attemptFixture(), events: [
    {time_s: 3, window_start_s: 2, window_end_s: 4, score: .95},
    {time_s: 5.5, window_start_s: 4.5, window_end_s: 6.5, score: .55}]};
  const timeline = attemptFilterTimeline(context, {scale: 1, offset_s: 0}, 0, 20);
  const group = groupAttemptEvents(timeline.events, timeline.scored_ranges);
  assert.equal(group.length, 1);
  assert.equal(group[0].score, .95);
  const screened = screenAttemptCandidates([
    {start_s: 2.5, end_s: 3}, {start_s: 4.1, end_s: 4.3}, {start_s: 5, end_s: 5.4}], timeline, .9);
  assert.deepEqual(screened.map(row => row.attempt_filter), ["supported", "suppressed", "suppressed"]);
});

test("recorded event intervals follow drift and PTS without clipping bounds or mutating observations", () => {
  const context = {events: [{event_id: "first", earliest_s: 12.01, observed_s: 12.515, latest_s: 13.02},
    {event_id: "late", earliest_s: 30, observed_s: 31, latest_s: 32}]};
  const before = structuredClone(context);
  const events = boardEventTimeline(context, {scale: 1.01, offset_s: 10}, 2, 5);
  assert.equal(events.length, 1);
  assert.ok(Math.abs(events[0].earliest_s + .00990099) < 1e-6);
  assert.ok(Math.abs(events[0].latest_s - .99009901) < 1e-6);
  assert.deepEqual(context, before);
  assert.deepEqual(boardEventTimeline(context, null, 2, 5), []);
  assert.deepEqual(boardEventTimeline(context, {scale: 0, offset_s: 0}, 0, 5), []);
});
const audioFixture = times => ({onsets: times.map(time_s => ({time_s, strength: 1})), strength: [], step_s: .01});
const motionFixture = () => ({events: [
  {time_s: 2.03, kind: "accel", strength: 1}, {time_s: 2.1, kind: "gyro", strength: 1},
  {time_s: 2.51, kind: "accel", strength: 1},
  {time_s: 5, kind: "accel", strength: 1}, {time_s: 5.1, kind: "gyro", strength: 1},
  {time_s: 5.4, kind: "accel", strength: 1}], gaps: [], coverage: [0, 20]});

test("audio screening keeps acceleration OR rotation near pop, not motion only at landing", () => {
  const audio = audioFixture([2, 2.5, 5, 5.4, 8, 8.5, 11, 11.5]);
  const motion = {coverage: [0, 20], gaps: [], events: [
    {time_s: 2.02, kind: "accel", strength: .5},
    {time_s: 5.1, kind: "gyro", strength: .5},
    {time_s: 8.5, kind: "accel", strength: 2}]};
  const settings = {...opts, candidate_mode: "audio_filtered", filter_threshold: .4};
  const baseline = proposePairs(audio.onsets, [], .01, opts);
  const kept = proposeCandidates(audio, motion, settings);
  assert.deepEqual(kept.map(row => row.id), baseline.slice(0, 2).map(row => row.id));
  const all = proposeCandidates(audio, motion, {...settings, show_filtered: true});
  assert.deepEqual(all.map(row => row.motion_filter), ["supported", "supported", "suppressed", "suppressed"]);
  assert.deepEqual(all.map(row => [row.start_s, row.end_s]), baseline.map(row => [row.start_s, row.end_s]));
  assert.ok(all.every(row => !row.outcome && row.status === "suggested"));
});

test("filter fails open for missing, interrupted, or out-of-range IMU coverage", () => {
  const pairs = proposePairs(audioFixture([2, 2.5]).onsets, [], .01, opts);
  for (const motion of [{events: [], gaps: [], coverage: null},
    {events: [], gaps: [], coverage: [1.9, 20]},
    {events: [], gaps: [], coverage: [0, 2.1]},
    {events: [], gaps: [[1.9, 2]], coverage: [0, 20]}]) {
    const screened = screenAudioCandidates(pairs, motion);
    assert.equal(screened.length, 1);
    assert.equal(screened[0].motion_filter, "unchecked");
  }
  assert.equal(screenAudioCandidates(pairs, {events: [], gaps: [], coverage: [0, 20]})[0].motion_filter, "suppressed",
    "a quiet, fully covered window differs from missing data");
});

test("evaluation distinguishes reviewed background from unclassified suggestions and records lost attempts", () => {
  const clip = {session: "fixture", video: "clip.mp4", origin: .5, duration: 20,
    audio: audioFixture([2, 2.5, 5, 5.4, 8, 8.5]), mapping: {scale: 1, offset_s: 10},
    motion: {coverage: [10, 31], gaps: [], events: [{time_s: 12.6, kind: "accel", strength: .5}]},
    labels: [{id: "trick", outcome: "make", review_interval: {start_s: 2}},
      {id: "background", outcome: "background", video_start_s: 5.5, video_end_s: 6}]};
  const audit = evaluateClips([clip], [{...opts, candidate_mode: "audio"},
    {...opts, candidate_mode: "audio_filtered", filter_threshold: .4},
    {...opts, candidate_mode: "audio_filtered", filter_threshold: .65}]);
  assert.equal(audit.results[0].totals.background_candidates, 1);
  assert.equal(audit.results[0].totals.unclassified_candidates, 1);
  assert.equal(audit.results[1].totals.matched_attempts, 1);
  assert.equal(audit.results[1].totals.background_candidates, 0);
  assert.deepEqual(audit.results[2].sessions[0].lost_attempt_ids, ["trick"]);
});

test("motion corroborates audio without moving it and recovers an inaudible pair", () => {
  const audio = audioFixture([2, 2.5]), motion = motionFixture();
  const pairs = proposeCandidates(audio, motion, combined);
  assert.deepEqual(pairs.map(p => [p.start_s, p.end_s]), [[2, 2.5], [5, 5.4]]);
  assert.deepEqual(new Set(pairs[0].sources), new Set(["audio", "accel", "gyro"]));
  assert.deepEqual(new Set(pairs[1].sources), new Set(["accel", "gyro"]));
  assert.equal(pairs[0].id, proposePairs(audio.onsets, [], .01, opts)[0].id);
  assert.deepEqual(proposeCandidates(audio, motion, {...combined, candidate_mode: "audio"}), proposePairs(audio.onsets, [], .01, opts));
  assert.deepEqual(motion, motionFixture(), "detection must not mutate cached signals");
});

test("rotation alone cannot invent a trick; no motion pairs cross acquisition gaps", () => {
  const motion = motionFixture();
  assert.deepEqual(proposeCandidates(audioFixture([]), {events: motion.events.filter(e => e.kind === "gyro"), gaps: []}, combined), []);
  const pairs = proposeCandidates(audioFixture([]), {...motion, gaps: [[2.2, 2.4], [5.1, 5.3]]}, combined);
  assert.deepEqual(pairs, []);
});

test("a louder IMU rebound cannot replace the existing audio pop", () => {
  const audio = audioFixture([2, 2.5, 2.8]);
  const motion = motionFixture();
  motion.events.push({time_s: 2.78, kind: "accel", strength: 10}, {time_s: 2.7, kind: "gyro", strength: 4});
  const baseline = proposePairs(audio.onsets, [], .01, opts);
  const candidates = proposeCandidates(audio, motion, combined);
  for (const pair of baseline) assert.ok(candidates.some(p => p.id === pair.id && p.start_s === pair.start_s && p.end_s === pair.end_s));
});

test("motion mapping respects drift, original PTS, coverage, and unavailable alignment", () => {
  const motion = {events: [{time_s: 12.02, kind: "accel", strength: 1}, {time_s: 30, kind: "gyro", strength: 1}], gaps: [[12.02, 13.03]]};
  const mapping = {offset_s: 10, scale: 1.01};
  const aligned = motionTimeline(motion, mapping, .5, 4);
  assert.equal(aligned.events.length, 1);
  assert.ok(Math.abs(aligned.events[0].time_s - 1.5) < 1e-8);
  assert.ok(Math.abs(aligned.gaps[0][1] - 2.5) < 1e-8);
  assert.deepEqual(motionTimeline(motion, null, 0, 4).events, []);
  assert.deepEqual(motionTimeline({...motion, unavailable_reason: "bad sensor"}, mapping, 0, 4).events, []);
});
test("pairs impacts across a plausible gap and leaves isolated sounds unpaired", () => {
  const onsets = [1, 1.4, 5, 9, 9.5].map(time_s => ({time_s, strength: 1}));
  const pairs = proposePairs(onsets, Array(1000).fill(0), .01, opts);
  assert.deepEqual(pairs.map(p => [p.start_s, p.end_s]), [[1, 1.4], [9, 9.5]]);
});
test("threshold, separation, and non-overlap prevent forced pairs", () => {
  assert.deepEqual(proposePairs([{time_s: 1, strength: .2}, {time_s: 1.4, strength: 1}], [], .01, opts), []);
  assert.deepEqual(proposePairs([{time_s: 1, strength: 1}, {time_s: 1.1, strength: 1}], [], .01, opts), []);
  const pairs = proposePairs([1, 1.3, 1.5, 1.8].map(time_s => ({time_s, strength: 1})), [], .01, opts);
  for (let i = 1; i < pairs.length; i++) assert.ok(pairs[i].start_s > pairs[i - 1].end_s);
});
test("human corrections and rejected pairs survive new proposals and do not mutate saved state", () => {
  const reviewed = [{id: "a", start_s: 1.01, end_s: 1.6, status: "reviewed"},
    {id: "b", start_s: 3, end_s: 3.5, status: "rejected"}];
  const merged = mergeReviews([{id: "a", start_s: 1, end_s: 1.5}], reviewed);
  assert.equal(merged.length, 2); assert.equal(merged[0].start_s, 1.01);
  merged[0].start_s = 1.1;
  assert.equal(reviewed[0].start_s, 1.01);
  assert.equal(mergeReviews([], reviewed).length, 2);
});
test("editing validates finite ordered media timestamps", () => {
  assert.equal(validInterval(0, 1, 1), true);
  for (const [start, end] of [[-1, 1], [1, 1], [2, 1], [0, 2], [NaN, 1]]) assert.equal(validInterval(start, end, 1), false);
});

test("attempt windows include context, respect sensor coverage, and preserve edited bounds", () => {
  const pair = {start_s: 20, end_s: 20.5};
  assert.deepEqual(attemptWindow(pair, 100, [15.5724, 90.0001]), [19, 22.5]);
  assert.deepEqual(attemptWindow({start_s: 16, end_s: 16.5}, 100, [15.5724, 18.1234]), [15.573, 18.123]);
  assert.deepEqual(attemptWindow({...pair, label_start_s: 18, label_end_s: 24}, 100, [15, 90]), [18, 24]);
  assert.equal(attemptWindow({start_s: 1, end_s: 1.5}, 100, [15, 90]), null);
});

test("adjacent suggestions trim context at saved bounds without moving the action or human edits", () => {
  const saved = [{id: "ollie", label_id: "one", label_start_s: 38.01, label_end_s: 38.871826},
    {id: "next", label_id: "two", label_start_s: 40.2, label_end_s: 42}];
  const pair = {id: "suggestion", start_s: 38.97, end_s: 39.33};
  const window = attemptWindow(pair, 100, [15.5724, 90], saved);
  assert.deepEqual(window, [38.872, 40.2]);
  assert.deepEqual(overlappingLabels(pair, window, saved), []);
  const edited = {...pair, label_start_s: 37.97, label_end_s: 41.33};
  assert.deepEqual(attemptWindow(edited, 100, [15, 90], saved), [37.97, 41.33]);
  assert.deepEqual(overlappingLabels(edited, [37.97, 41.33], saved).map(row => row.id), ["ollie", "next"]);
  assert.deepEqual(attemptWindow({...saved[0], start_s: 38.188, end_s: 38.505}, 100, [15, 90], saved), [38.01, 38.871826]);
});

test("partially overlapping actions remain visible for review, including stale saved labels", () => {
  const saved = [{id: "ollie", label_id: "one", status: "labeled", start_s: 2, end_s: 2.5,
    label_start_s: 1, label_end_s: 4}];
  const pair = {id: "partial", start_s: 3.8, end_s: 4.3};
  assert.equal(mergeReviews([pair], saved).length, 2);
  for (const stale of [false, true]) {
    const labels = [{...saved[0], label_stale: stale}];
    assert.equal(overlappingLabels(pair, attemptWindow(pair, 20, [0, 20], labels), labels).length, 1);
  }
});

test("background ranges start at the playhead and stop at saved labels or coverage", () => {
  const saved = [{label_id: "one", label_start_s: 5.1234, label_end_s: 9}];
  assert.deepEqual(backgroundWindow(3.0012, 20, [1, 19], saved), [3.002, 5.123]);
  assert.equal(backgroundWindow(6, 20, [1, 19], saved), null);
  assert.equal(backgroundWindow(.5, 20, [1, 19], saved), null);
  assert.equal(backgroundWindow(19, 20, [1, 19], saved), null);
  assert.deepEqual(backgroundWindow(9, 20, [1, 19], saved), [9, 12]);
  assert.deepEqual(backgroundWindow(18, 20, [1, 19], saved), [18, 19]);
});

test("queue advances through unlabeled and stale rows without inferring background", () => {
  const rows = [{id: "a", status: "labeled"}, {id: "b", status: "rejected"},
    {id: "c", status: "reviewed"}, {id: "d", status: "labeled", label_stale: true}];
  assert.equal(nextPending(rows, "a"), "c");
  assert.equal(nextPending(rows, "d"), "c");
  assert.equal(nextPending(rows.slice(0, 2), "a"), null);
  assert.equal(needsReview({...rows[0], edited: true}), true);
});

test("new proposals fully covered by a human label do not return as duplicate work", () => {
  const saved = [{id: "a", status: "labeled", label_start_s: 1, label_end_s: 4, start_s: 2, end_s: 2.5}];
  const proposals = [{id: "b", start_s: 2.1, end_s: 2.6}, {id: "c", start_s: 5, end_s: 5.5}];
  assert.deepEqual(mergeReviews(proposals, saved).map(row => row.id), ["a", "c"]);
  assert.equal(mergeReviews(proposals, [{...saved[0], label_stale: true}]).length, 3);
});

// Exercise the real controller with a small DOM/media fixture. This verifies
// user actions and requests without depending on a graphical browser or codec.
async function controller(t, {legacyServer = false, intervals = [], onsets = [2, 2.5, 8, 8.5], sensor = {},
    reviewMode = "attempts", frameTimes = [0, 1001 / 120000, 2002 / 120000]} = {}) {
  const html = await readFile(new URL("../viz/trick_review.html", import.meta.url), "utf8");
  const nodes = new Map(), listeners = new Map();
  function element() {
    return {value: "", checked: false, disabled: false, dataset: {}, children: [], currentTime: 0, paused: true,
      setAttribute() {}, addEventListener() {}, getBoundingClientRect: () => ({width: 200, height: 100, left: 0}),
      getContext: () => new Proxy({}, {get: (obj, key) => obj[key] ?? (() => {})}),
      append(...children) { this.children.push(...children); }, replaceChildren() { this.children = []; },
      pause() { this.paused = true; }, async play() { this.paused = false; },
      click() { if (!this.disabled) return this.onclick?.(); }};
  }
  for (const match of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    assert.ok(!nodes.has(match[2]), `duplicate id ${match[2]}`);
    const node = element(), tag = match[0];
    node.value = tag.match(/\bvalue="([^"]*)"/)?.[1] ?? "";
    node.checked = /\bchecked\b/.test(tag); node.disabled = /\bdisabled\b/.test(tag);
    node.hidden = /\bhidden\b/.test(tag); node.open = /\bopen\b/.test(tag);
    if (match[1] === "select") {
      const block = html.slice(match.index, html.indexOf("</select>", match.index));
      const options = [...block.matchAll(/<option\b[^>]*>/g)].map(row => row[0]);
      node.value = (options.find(option => option.includes("selected")) || options[0] || "").match(/value="([^"]*)"/)?.[1] ?? "";
    }
    nodes.set(match[2], node);
  }
  const get = id => { assert.ok(nodes.has(id), `missing control ${id}`); return nodes.get(id); };
  const mapping = sensor.mapping || {scale: 1, offset_s: 0, points: 2};
  const state = {revision: 0, labels_revision: "0", intervals,
    settings: {...opts, offset: 999, scale: .99},
    label_context: {available: true, mapping, coverage: [0, 20]}};
  const data = {api_version: 9, video: "test.mp4", duration_s: 20, source_pts_origin_s: 0, media_url: "/media.mp4", review_mode: reviewMode,
    video_timing: {fps: 120000 / 1001, frame_times_s: frameTimes},
    audio: {onsets: onsets.map(time_s => ({time_s, strength: 1})),
      strength: Array(200).fill(0), waveform_min: [], waveform_max: [], step_s: .1},
    sensor: {mapping, samples: [], warnings: [], syncs: [], offset_hint_s: 0, ...sensor}};
  if (legacyServer) { delete data.api_version; delete state.label_context; delete state.labels_revision; }
  const calls = [];
  let fail = false;
  let nextFrame = null;
  const globals = {document: {getElementById: get, createElement: element,
      addEventListener: (name, handler) => listeners.set(name, handler)},
    window: {devicePixelRatio: 1, addEventListener() {}}, requestAnimationFrame(callback) { nextFrame = callback; },
    ResizeObserver: class { observe() {} },
    fetch: async (url, options) => {
      if (!options) return {ok: true, json: async () => structuredClone(url === "/api/data" ? data : state)};
      const body = JSON.parse(options.body); calls.push(body);
      assert.equal(url, "/api/label");
      if (fail) return {ok: false, json: async () => ({error: "disk full"})};
      const row = {...body.interval, status: body.decision === "skip" ? "rejected" : "labeled"};
      if (body.decision === "label") row.label_id = row.id;
      state.intervals = [...state.intervals.filter(old => old.id !== row.id), row];
      state.settings = body.settings;
      state.revision++; state.labels_revision = String(state.revision);
      return {ok: true, json: async () => structuredClone(state)};
    }};
  const before = Object.fromEntries(Object.keys(globals).map(key => [key, globalThis[key]]));
  Object.assign(globalThis, globals);
  t.after(() => { for (const [key, value] of Object.entries(before)) {
    if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
  }});
  if (legacyServer) {
    await assert.rejects(init(), /Restart the review server.*Ctrl\+C/);
    return {get, calls};
  }
  await init();
  assert.doesNotMatch(get("status").textContent, /Could not open/);
  assert.equal(Number(get("offset").value), 0, "current alignment supersedes saved exploratory settings");
  assert.equal(Number(get("scale").value), 1);
  return {get, calls, state, frame: () => nextFrame?.(), fail: () => { fail = true; }, key: key => {
    listeners.get("keydown")({key, target: {closest: () => null}, preventDefault() {}});
  }};
}

test("missing sensor data keeps flash controls visible and explains recovery", async t => {
  const {get, calls} = await controller(t, {sensor: {
    samples_message: "samples.csv is missing. Download or import the recording.",
    sync_unavailable_reason: "events.jsonl is missing.", warnings: ["storage_flush_failed"]}});
  assert.equal(get("sensor-panel").hidden, false);
  assert.equal(get("sync-controls").open, true);
  assert.equal(get("sensor-empty").hidden, false);
  assert.match(get("sensor-empty").textContent, /samples.csv.*Download/);
  assert.equal(get("imu").hidden, true);
  assert.equal(get("sensor-count").textContent, "0 samples loaded");
  assert.match(get("sensor-quality").textContent, /storage_flush_failed/);
  assert.equal(get("sync-unavailable").hidden, false);
  assert.match(get("sync-unavailable").textContent, /events.jsonl/);
  assert.equal(get("sync-id").disabled, true);
  await get("save-sync").click();
  assert.equal(calls.length, 0);
});

test("phone timing is visibly estimated while optional LED matching remains available", async t => {
  const mapping = {scale: 1, offset_s: 0, points: 0, source: "phone_clock", drift_estimated: true,
    network_uncertainty_s: .012, probe_count: 12};
  const {get, calls} = await controller(t, {sensor: {mapping, clock_estimate: mapping,
    syncs: [{id: 1, session_s: 2}], sync_unavailable_reason: ""}});
  assert.match(get("alignment").textContent, /ESTIMATED phone timing.*±12.0 ms/);
  assert.doesNotMatch(get("alignment").textContent, /0 saved LED/);
  assert.equal(get("clock-estimate").hidden, false);
  assert.match(get("clock-estimate").textContent, /12 clock checks/);
  assert.equal(get("save-sync").disabled, false);
  assert.equal(calls.length, 0, "loading an estimate must not create LED matches or labels");
  assert.match(alignmentDescription({source: "led_phone_clock", points: 1}), /1 saved LED.*estimated drift/);
  assert.match(alignmentDescription({source: "led", points: 2}), /Offset and drift come from LED/);
  assert.match(alignmentDescription(mapping, false), /EXPLORATORY/);
});

function jumpFixture() {
  return {availability: "ready", events: [
    {event_id: "jump-1", takeoff_s: 2, peak_s: 2.317, landing_s: 2.634},
    {event_id: "jump-2", takeoff_s: 10, peak_s: 10.407, landing_s: 10.814},
  ]};
}

test("jump peaks follow clock drift and media origin, excluding offscreen apexes", () => {
  const context = jumpFixture(), original = structuredClone(context);
  const mapped = jumpEventTimeline(context, {scale: 1.01, offset_s: .1}, 1, 20);
  assert.ok(Math.abs(mapped[0].observed_s - ((2.317 - .1) / 1.01 - 1)) < 1e-10);
  assert.ok(Math.abs(mapped[0].earliest_s - ((2 - .1) / 1.01 - 1)) < 1e-10);
  assert.equal(mapped[0].estimated_peak, true);
  assert.equal(jumpEventTimeline(context, {scale: 1, offset_s: 0}, 2.4, 20).length, 1);
  for (const mapping of [null, {scale: 0, offset_s: 0}, {scale: 1, offset_s: NaN}]) {
    assert.deepEqual(jumpEventTimeline(context, mapping, 0, 20), []);
  }
  assert.deepEqual(jumpEventTimeline({...context, availability: "unavailable"}, {scale: 1, offset_s: 0}, 0, 20), []);
  assert.deepEqual(context, original);
});

test("peak frame selection uses actual timestamps, including irregular intervals and edges", () => {
  const frames = [.01, .02, .05, .1];
  assert.equal(nearestFrame(frames, .034), .02);
  assert.equal(nearestFrame(frames, .04), .05);
  assert.equal(nearestFrame(frames, .05), .05);
  assert.equal(nearestFrame(frames, 0), .01);
  assert.equal(nearestFrame(frames, 1), .1);
  assert.equal(nearestFrame([], 2.3), 2.3);
});

test("conservative jump review filters load pairs without changing timing or legacy events", () => {
  const context = jumpFixture();
  context.events[1].detection_method = "load_pair";
  const mapping = {scale: 1, offset_s: 0};
  assert.equal(jumpEventTimeline(context, mapping, 0, 20).length, 2);
  const strict = jumpEventTimeline(context, mapping, 0, 20, "conservative");
  assert.equal(strict.length, 1);
  assert.equal(strict[0].observed_s, 2.317);
  assert.equal(context.events.length, 2);
});

test("jump sensitivity updates navigation and clears a filtered selection without saving labels", async t => {
  const jumps = jumpFixture();
  jumps.events[1].detection_method = "load_pair";
  const {get, calls, key} = await controller(t, {reviewMode: "jumps", sensor: {jumps},
    frameTimes: Array.from({length: 1200}, (_, i) => i / 60)});
  key("n"); key("n");
  assert.match(get("jump-selected").textContent, /Jump 2 of 2.*Push-off \/ landing estimate/);
  get("jump-sensitivity").value = "conservative"; get("jump-sensitivity").onchange();
  assert.match(get("jump-selected").textContent, /1 estimated peak/);
  assert.equal(get("jump-next").disabled, true);
  key("p");
  assert.match(get("jump-selected").textContent, /Jump 1 of 1/);
  get("jump-sensitivity").value = "sensitive"; get("jump-sensitivity").onchange();
  assert.equal(get("jump-next").disabled, false);
  get("offset").value = .5; get("offset").oninput();
  get("jump-sensitivity").value = "conservative"; get("jump-sensitivity").onchange();
  assert.match(get("jump-selected").textContent, /1 estimated peak.*Match LED flashes/);
  assert.equal(get("jump-next").disabled, true);
  assert.deepEqual(calls, []);
});

test("jump mode navigates distinct peaks, pauses on indexed frames, and never saves labels", async t => {
  const frames = Array.from({length: 1200}, (_, i) => i / 60);
  const {get, calls, key} = await controller(t, {reviewMode: "jumps", frameTimes: frames,
    sensor: {jumps: jumpFixture()}});
  assert.equal(get("board-event-mode").value, "jumps");
  assert.equal(get("review-queue").hidden, true);
  assert.equal(get("jump-navigation").hidden, false);
  assert.equal(get("jump-previous").disabled, true);
  assert.match(get("jump-selected").textContent, /2 estimated peak/);
  await get("video").play();
  key("n");
  assert.equal(get("video").paused, true);
  assert.ok(Math.abs(get("video").currentTime - (139 / 60 + 1e-6)) < 1e-10);
  assert.match(get("jump-selected").textContent, /Jump 1 of 2.*Estimated peak 2.317/);
  key("n");
  assert.ok(Math.abs(get("video").currentTime - (624 / 60 + 1e-6)) < 1e-10);
  assert.equal(get("jump-next").disabled, true);
  key("p");
  assert.ok(Math.abs(get("video").currentTime - (139 / 60 + 1e-6)) < 1e-10);
  key(" ");
  assert.equal(get("video").paused, false);
  key(" ");
  assert.equal(get("video").paused, true);
  key("ArrowRight");
  assert.ok(Math.abs(get("video").currentTime - (140 / 60 + 1e-6)) < 1e-10);
  for (const keyName of ["1", "0", "s", "x"]) key(keyName);
  assert.deepEqual(calls, []);
});

test("jump navigation is gated by verified alignment and distinguishes unavailable from no detections", async t => {
  const {get} = await controller(t, {reviewMode: "jumps", sensor: {jumps: jumpFixture()}});
  get("offset").value = .5; get("offset").oninput();
  assert.equal(get("board-events").hidden, true);
  assert.equal(get("jump-next").disabled, true);
  assert.equal(get("jump-previous").disabled, true);
  assert.match(get("jump-selected").textContent, /Match LED flashes/);
  get("offset").value = 0; get("offset").oninput();
  assert.equal(get("jump-next").disabled, false);
});

test("unavailable jump scans explain the issue and cannot navigate", async t => {
  const {get} = await controller(t, {reviewMode: "jumps", sensor: {jumps: {
    availability: "unavailable", events: [], message: "Stop recording and finish importing."}}});
  assert.match(get("jump-selected").textContent, /Stop recording/);
  assert.equal(get("jump-next").disabled, true);
  assert.equal(get("board-events").hidden, true);
});

test("quiet jump scans show zero peaks and never substitute skateboard detections", async t => {
  const {get} = await controller(t, {reviewMode: "jumps", sensor: {
    jumps: {availability: "ready", events: []}, attempts: attemptFixture()}});
  assert.match(get("jump-selected").textContent, /0 estimated peak/);
  assert.equal(get("jump-next").disabled, true);
  assert.equal(get("board-event-list").children.length, 0);
});

test("board events seek within their uncertainty bands and never create labels or proposals", async t => {
  const {get, calls, state} = await controller(t, {sensor: {board_motion: {
    availability: "recorded", message: "", events: [
      {event_id: "one", earliest_s: 1.75, observed_s: 1.99, latest_s: 2, orientation_changed: true},
      {event_id: "two", earliest_s: 10, observed_s: 10.24, latest_s: 10.25, wake_up: true}]}}});
  get("board-event-mode").value = "raw";
  get("board-event-mode").onchange();
  const candidates = get("candidates").children.length;
  assert.equal(get("board-events").hidden, false);
  assert.match(get("board-event-status").textContent, /2 raw observation/);
  assert.match(get("board-event-list").children[0].textContent, /Orientation.*1.750–2.000/);
  await get("board-event-list").children[0].click();
  assert.equal(get("video").currentTime, 1.75);
  await get("board-event-next").click();
  assert.equal(get("video").currentTime, 10);
  assert.match(get("board-event-list").children[0].textContent, /Wake-up/);
  get("imu").onpointerdown({clientX: 100});
  const imuTime = get("video").currentTime;
  get("board-events").onpointerdown({clientX: 100});
  assert.equal(get("video").currentTime, imuTime, "event and IMU plots share the horizontal time axis");
  assert.equal(get("candidates").children.length, candidates);
  assert.equal(state.intervals.length, 0);
  assert.equal(calls.length, 0);
  get("offset").value = 2;
  get("offset").oninput();
  assert.equal(get("board-events").hidden, true);
  assert.match(get("board-event-status").textContent, /Match LED flashes/);
  assert.equal(get("board-event-list").children.length, 0);
  assert.equal(get("board-event-next").disabled, true);
});

test("old recordings explain unavailable event capture instead of claiming zero motion", async t => {
  const {get} = await controller(t, {sensor: {board_motion: {availability: "not_recorded", events: [],
    message: "Board-event recording was not enabled for this session."}}});
  get("board-event-mode").value = "raw";
  get("board-event-mode").onchange();
  assert.equal(get("board-events").hidden, true);
  assert.match(get("board-event-status").textContent, /not enabled/);
  assert.equal(get("board-event-next").disabled, true);
});

test("event panel defaults to learned attempts and keeps raw interrupts separate", async t => {
  const {get, calls, state} = await controller(t, {sensor: {
    attempts: {availability: "ready", threshold: .5, model_name: "trained", model_sha256: "model",
      quality: {skipped: {}}, events: [
        {time_s: 3, window_start_s: 2, window_end_s: 4, score: .8},
        {time_s: 11, window_start_s: 10, window_end_s: 12, score: .6}]},
    board_motion: {availability: "recorded", events: [
      {event_id: "raw", earliest_s: 1, observed_s: 1.2, latest_s: 1.25, wake_up: true}]}}});
  assert.equal(get("board-event-mode").value, "attempts");
  assert.equal(get("board-event-title").textContent, "Potential trick attempts");
  assert.match(get("board-event-status").textContent, /2 potential attempt.*cutoff 0.50/);
  assert.match(get("board-event-list").children[0].textContent, /Potential attempt.*score 0.80.*2.000–4.000/);
  assert.doesNotMatch(get("board-event-list").children[0].textContent, /Wake-up/);
  await get("board-event-list").children[0].click();
  assert.equal(get("video").currentTime, 2);
  await get("board-event-next").click();
  assert.equal(get("video").currentTime, 10);
  await get("board-event-previous").click();
  assert.equal(get("video").currentTime, 2);
  get("board-event-mode").value = "raw";
  get("board-event-mode").onchange();
  assert.match(get("board-event-status").textContent, /1 raw observation/);
  assert.match(get("board-event-list").children[0].textContent, /Wake-up/);
  get("board-event-mode").value = "attempts";
  get("board-event-mode").onchange();
  get("offset").value = 2;
  get("offset").oninput();
  assert.equal(get("board-events").hidden, true);
  assert.match(get("board-event-status").textContent, /Match LED flashes/);
  assert.equal(calls.length, 0);
  assert.equal(state.intervals.length, 0);
});

test("event navigation makes Play attempt use that event instead of the previous queue selection", async t => {
  const {get, calls, frame} = await controller(t, {sensor: {attempts: {
    ...attemptFixture(), events: [{time_s: 11, window_start_s: 10, window_end_s: 12, score: .8}]}}});
  get("note").value = "keep my queue draft"; get("note").oninput();
  await get("board-event-next").click();
  assert.equal(get("video").currentTime, 10);
  assert.equal(get("audition").disabled, false);
  await get("audition").click();
  assert.equal(get("video").currentTime, 9.5, "playback includes lead-in to this event, not the old audio suggestion");
  assert.equal(get("video").paused, false);
  get("video").currentTime = 13; frame();
  assert.equal(get("video").paused, true, "preview stops after the event follow-through");
  assert.equal(get("save").disabled, true, "previewing an event cannot save an unrelated queue label");
  await get("save").click();
  assert.equal(calls.length, 0);
  await get("candidates").children[0].click();
  assert.equal(get("note").value, "keep my queue draft");
  await get("audition").click();
  assert.equal(get("video").currentTime, 1, "selecting a queue row restores its own playback window");
});

test("grouped event playback covers all three detections before stopping or looping, then advances to the next attempt", async t => {
  const {get, key, frame, calls} = await controller(t, {onsets: [], sensor: {attempts: {
    ...attemptFixture(), events: [
      {time_s: 3, window_start_s: 2, window_end_s: 4, score: .8},
      {time_s: 4, window_start_s: 3, window_end_s: 5, score: .9},
      {time_s: 5, window_start_s: 4, window_end_s: 6, score: .7},
      {time_s: 11, window_start_s: 10, window_end_s: 12, score: .6}]}}});
  assert.equal(get("audition").disabled, true);
  assert.match(get("board-event-status").textContent, /2 potential attempt.*Grouped from 4 model detections/);
  assert.match(get("board-event-list").children[0].textContent, /3 detections grouped.*2.000–6.000/);
  await get("board-event-next").click();
  assert.equal(get("audition").disabled, false);
  key(" ");
  assert.equal(get("video").currentTime, 1.5);
  assert.equal(get("video").paused, false);
  for (const time of [4, 5, 6]) {
    get("video").currentTime = time; frame();
    assert.equal(get("video").paused, false, "internal detection boundaries do not stop playback");
  }
  get("loop").checked = true;
  get("video").currentTime = 7; frame();
  assert.equal(get("video").currentTime, 1.5);
  assert.equal(get("video").paused, false);
  get("loop").checked = false;
  get("video").currentTime = 7; frame();
  assert.equal(get("video").paused, true);
  await get("board-event-next").click();
  assert.equal(get("video").currentTime, 10, "next event skips the duplicate detections inside the first group");
  await get("audition").click();
  assert.equal(get("video").currentTime, 9.5);
  await get("board-event-previous").click();
  await get("audition").click();
  assert.equal(get("video").currentTime, 1.5);
  assert.equal(calls.length, 0);
});

test("event preview survives filtering but clears when its alignment or event view is changed", async t => {
  const {get} = await controller(t, {onsets: [], sensor: {attempts: attemptFixture()}});
  await get("board-event-list").children[0].click();
  get("candidate-mode").value = "audio_attempts"; get("candidate-mode").oninput();
  get("pending-only").checked = true; get("pending-only").onchange();
  await get("audition").click();
  assert.equal(get("video").paused, false);
  assert.equal(get("video").currentTime, .8);
  get("offset").value = 2; get("offset").oninput();
  assert.equal(get("video").paused, true);
  assert.equal(get("audition").disabled, true);
  get("offset").value = 0; get("offset").oninput();
  await get("board-event-list").children[0].click();
  await get("audition").click();
  get("board-event-mode").value = "raw"; get("board-event-mode").onchange();
  assert.equal(get("video").paused, true);
  assert.equal(get("audition").disabled, true);
});

test("raw event playback includes context and clips its bounds to the video", async t => {
  const {get, frame} = await controller(t, {onsets: [], sensor: {board_motion: {
    availability: "recorded", events: [
      {event_id: "start", earliest_s: -.1, observed_s: 0, latest_s: .1, wake_up: true},
      {event_id: "end", earliest_s: 19.9, observed_s: 20, latest_s: 20.1, wake_up: true}]}}});
  get("board-event-mode").value = "raw"; get("board-event-mode").onchange();
  await get("board-event-list").children[0].click();
  assert.equal(get("audition").disabled, false);
  await get("audition").click();
  assert.equal(get("video").currentTime, 0);
  get("video").currentTime = 2.1; frame();
  assert.equal(get("video").paused, true);
  await get("board-event-next").click();
  await get("audition").click();
  assert.equal(get("video").currentTime, 18.9);
  get("video").currentTime = 20; frame();
  assert.equal(get("video").paused, true);
});

test("unavailable detection never presents raw interrupts as potential attempts", async t => {
  const {get} = await controller(t, {sensor: {
    attempts: {availability: "unavailable", events: [], message: "Attempt model is missing."},
    board_motion: {availability: "recorded", events: [
      {event_id: "raw", earliest_s: 1, observed_s: 1.2, latest_s: 1.25, wake_up: true}]}}});
  assert.match(get("board-event-status").textContent, /model is missing/);
  assert.equal(get("board-events").hidden, true);
  assert.equal(get("board-event-list").children.length, 0);
  assert.equal(get("board-event-next").disabled, true);
});

test("successful quiet scans report zero attempts even with recorded board motion", async t => {
  const {get} = await controller(t, {sensor: {
    attempts: {availability: "ready", threshold: .5, model_name: "trained", events: [], quality: {skipped: {}}},
    board_motion: {availability: "recorded", events: [
      {event_id: "raw", earliest_s: 1, observed_s: 1.2, latest_s: 1.25, wake_up: true}]}}});
  assert.match(get("board-event-status").textContent, /0 potential attempt/);
  assert.equal(get("board-events").hidden, false);
  assert.equal(get("board-event-list").children.length, 0);
});

test("recovered data shows motion plots and enables recorded flash matching", async t => {
  const {get} = await controller(t, {sensor: {samples: [[0, 9.81, 0], [.005, 9.82, 1]],
    syncs: [{id: 1, session_s: 1}], warnings: ["missing_footer, storage_flush_failed"]}});
  assert.equal(get("sensor-panel").hidden, false);
  assert.equal(get("sensor-empty").hidden, true);
  assert.equal(get("imu").hidden, false);
  assert.match(get("sensor-count").textContent, /2 samples in view/);
  assert.equal(get("sync-controls").open, true);
  assert.equal(get("sync-unavailable").hidden, true);
  assert.equal(get("save-sync").disabled, false);
  assert.equal(get("sync-id").disabled, false);
  assert.match(get("sync-id").children[0].textContent, /#1/);
});

test("recorded flashes stay accessible without samples but open recordings explain disabled matching", async t => {
  const {get} = await controller(t, {sensor: {syncs: [{id: 1, session_s: 1}]}});
  assert.equal(get("sensor-panel").hidden, false);
  assert.equal(get("save-sync").disabled, false);
  assert.equal(get("sensor-empty").hidden, false);
});

test("an unfinished recording explains why recorded flashes cannot be matched yet", async t => {
  const {get} = await controller(t, {sensor: {syncs: [{id: 1, session_s: 1}],
    sync_unavailable_reason: "Stop recording and finish importing the sensor data before matching flashes."}});
  assert.equal(get("save-sync").disabled, true);
  assert.match(get("sync-unavailable").textContent, /Stop recording/);
});

test("UI saves background in one click, advances, and never labels untouched suggestions", async t => {
  const {get, calls, state} = await controller(t);
  assert.equal(calls.length, 0);
  assert.equal(get("outcome").value, "");
  await get("save").click();
  assert.equal(calls.length, 0);
  get("trick").value = "ollie"; get("trick").oninput();
  await get("background").click();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].interval.outcome, "background");
  assert.equal(calls[0].interval.trick, "");
  assert.deepEqual([calls[0].interval.label_start_s, calls[0].interval.label_end_s], [1, 4.5]);
  assert.equal(get("start").value, "8.000");
  assert.equal(get("outcome").value, "");
  assert.equal(state.intervals.length, 1);
  await get("reject").click();
  assert.equal(calls[1].decision, "skip");
  assert.equal(state.intervals.filter(row => row.label_id).length, 1);
  assert.match(get("status").textContent, /Queue complete/);
});

test("UI saves and restores a chosen activity without carrying it into the next attempt", async t => {
  const {get, calls, state} = await controller(t);
  await get("candidates").children[0].click();
  get("outcome").value = "background"; get("outcome").oninput();
  assert.equal(get("activity-field").hidden, false);
  get("activity").value = "pushing"; get("activity").oninput();
  await get("save").click();
  assert.equal(calls[0].interval.activity, "pushing");
  assert.equal(calls[0].interval.outcome, "background");
  assert.equal(state.intervals[0].activity, "pushing");
  assert.equal(get("activity").value, "");
  assert.equal(get("activity-field").hidden, true);
  await get("candidates").children[0].click();
  assert.equal(get("activity").value, "pushing");
});

test("UI enables motion only at saved alignment and keeps activity drafts across threshold changes", async t => {
  const {get, calls} = await controller(t, {sensor: {motion: motionFixture()}});
  assert.equal(get("candidate-mode").value, "audio");
  assert.match(get("motion-status").textContent, /audio suggestions only/);
  get("candidate-mode").value = "audio_motion"; get("candidate-mode").oninput();
  assert.match(get("motion-status").textContent, /motion peaks/);
  assert.ok(get("candidates").children.some(button => button.children[1].textContent.includes("gyro")));
  await get("candidates").children[0].click();
  get("outcome").value = "background"; get("outcome").oninput();
  get("activity").value = "carrying"; get("activity").oninput();
  get("motion-threshold").value = "1"; get("motion-threshold").oninput();
  assert.equal(get("activity").value, "carrying");
  get("offset").value = "1"; get("offset").oninput();
  assert.match(get("motion-status").textContent, /Match LED flashes/);
  assert.equal(calls.length, 0);
});

test("UI filter is reversible, keeps saved labels and drafts, and never saves rejected outcomes automatically", async t => {
  const {get, calls} = await controller(t, {sensor: {motion: motionFixture()}});
  assert.equal(get("candidates").children.length, 2);
  get("candidate-mode").value = "audio_filtered"; get("candidate-mode").oninput();
  assert.equal(get("candidates").children.length, 1);
  assert.match(get("motion-status").textContent, /1 of 2 audio candidates hidden/);
  get("show-filtered").checked = true; get("show-filtered").oninput();
  assert.equal(get("candidates").children.length, 2);
  assert.match(get("candidates").children[1].children[1].textContent, /filtered/);
  await get("candidates").children[1].click();
  get("note").value = "check this quiet attempt"; get("note").oninput();
  get("show-filtered").checked = false; get("show-filtered").oninput();
  assert.equal(get("candidates").children.length, 2, "a human draft stays visible despite the filter");
  assert.equal(get("note").value, "check this quiet attempt");
  assert.equal(calls.length, 0, "hiding a suggestion is not a label decision");
  get("trick").value = "ollie"; get("trick").oninput();
  get("outcome").value = "make"; get("outcome").oninput();
  await get("save").click();
  assert.equal(calls[0].interval.outcome, "make");
  get("filter-threshold").value = "2"; get("filter-threshold").oninput();
  assert.equal(get("candidates").children.length, 1, "a saved label survives even when every suggestion is filtered");
});

test("UI learned filtering reviews a nearby cluster once, plays it fully, and retains edited alternatives", async t => {
  const {get, calls, frame} = await controller(t, {onsets: [2, 2.2, 2.4, 2.6, 2.8, 3, 8, 8.5],
    sensor: {attempts: {...attemptFixture(), events: [...attemptFixture().events,
      {time_s: 8.2, window_start_s: 7.2, window_end_s: 9.2, score: .8}]}}});
  get("candidate-mode").value = "audio_attempts"; get("candidate-mode").oninput();
  assert.equal(get("candidates").children.length, 2);
  assert.match(get("motion-status").textContent, /2 nearby alternatives grouped/);
  assert.match(get("candidates").children[0].children[1].textContent, /3 suggestions grouped/);
  await get("audition").click();
  assert.equal(get("video").currentTime, 1);
  get("video").currentTime = 4.5; frame();
  assert.equal(get("video").paused, false, "playback includes the later contacts and follow-through");
  get("video").currentTime = 5; frame();
  assert.equal(get("video").paused, true);
  await get("next").click();
  assert.equal(Number(get("start").value), 8, "Next skips alternatives within the previous cluster");
  get("show-filtered").checked = true; get("show-filtered").oninput();
  assert.equal(get("candidates").children.length, 4);
  assert.match(get("candidates").children[1].children[1].textContent, /grouped alternative/);
  await get("candidates").children[1].click();
  get("note").value = "keep this alternate pop"; get("note").oninput();
  get("show-filtered").checked = false; get("show-filtered").oninput();
  assert.equal(get("candidates").children.length, 3, "a human-edited alternative stays visible");
  assert.equal(get("note").value, "keep this alternate pop");
  assert.equal(calls.length, 0);
  get("trick").value = "ollie"; get("trick").oninput();
  get("outcome").value = "make"; get("outcome").oninput();
  await get("save").click();
  assert.equal(calls.length, 1);
  assert.deepEqual([calls[0].interval.start_s, calls[0].interval.end_s], [2.4, 2.6]);
  await init();
  assert.equal(get("candidates").children.length, 2, "saved alternate and the later attempt survive reload");
});

test("UI learned filter preserves drafts and labels, saves settings, and restores them on reload", async t => {
  const {get, calls} = await controller(t, {sensor: {attempts: attemptFixture()}});
  get("candidate-mode").value = "audio_attempts"; get("candidate-mode").oninput();
  assert.equal(get("candidates").children.length, 1);
  assert.equal(get("attempt-threshold-field").hidden, false);
  assert.equal(get("filter-threshold-field").hidden, true);
  assert.match(get("motion-status").textContent, /1 of 2 audio candidates hidden by attempt model/);
  assert.match(get("candidate-evidence").textContent, /learned attempt window.*0.80/);
  get("show-filtered").checked = true; get("show-filtered").oninput();
  assert.equal(get("candidates").children.length, 2);
  assert.match(get("candidates").children[1].children[1].textContent, /filtered/);
  await get("candidates").children[1].click();
  get("note").value = "review a possible missed attempt"; get("note").oninput();
  get("show-filtered").checked = false; get("show-filtered").oninput();
  assert.equal(get("candidates").children.length, 2);
  assert.equal(get("note").value, "review a possible missed attempt");
  get("attempt-threshold").value = "0.85"; get("attempt-threshold").oninput();
  assert.equal(get("candidates").children.length, 1, "only the human draft survives the stricter filter");
  assert.equal(calls.length, 0);
  get("trick").value = "ollie"; get("trick").oninput();
  get("outcome").value = "make"; get("outcome").oninput();
  await get("save").click();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].settings.candidate_mode, "audio_attempts");
  assert.equal(calls[0].settings.attempt_threshold, .85);
  await init();
  assert.equal(get("candidate-mode").value, "audio_attempts");
  assert.equal(Number(get("attempt-threshold").value), .85);
  assert.equal(get("candidates").children.length, 1, "the saved label stays visible on reload");
  get("candidate-mode").value = "audio"; get("candidate-mode").oninput();
  assert.equal(get("candidates").children.length, 2);
});

test("UI learned filter explains missing alignment and model without emptying the queue", async t => {
  const {get, calls} = await controller(t, {sensor: {attempts: attemptFixture()}});
  get("candidate-mode").value = "audio_attempts"; get("candidate-mode").oninput();
  get("offset").value = "2"; get("offset").oninput();
  assert.equal(get("candidates").children.length, 2);
  assert.match(get("motion-status").textContent, /Match LED flashes.*Keeping all audio candidates/);
  assert.equal(calls.length, 0);
});

test("UI learned filter reports unavailable model and keeps suggestions", async t => {
  const {get} = await controller(t, {sensor: {attempts: {
    availability: "unavailable", message: "Attempt model is missing.", events: []}}});
  get("candidate-mode").value = "audio_attempts"; get("candidate-mode").oninput();
  assert.equal(get("candidates").children.length, 2);
  assert.match(get("motion-status").textContent, /model is missing.*Keeping all audio candidates/);
});

test("UI saves one watched background range covering multiple suggestions and restores it", async t => {
  const {get, calls, state} = await controller(t, {onsets: [2, 2.5, 4, 4.5, 8, 8.5]});
  get("video").currentTime = 1;
  get("new-background").click();
  assert.equal(get("outcome").value, "background");
  assert.equal(get("pop-markers").hidden, true);
  assert.equal(get("trick").disabled, true);
  assert.equal(calls.length, 0, "creating a range requires explicit save");
  get("video").currentTime = 6;
  get("label-mark-end").click();
  await get("save").click();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].interval.outcome, "background");
  assert.equal(calls[0].interval.trick, "");
  assert.deepEqual([calls[0].interval.start_s, calls[0].interval.end_s], [1, 6]);
  assert.deepEqual([state.intervals[0].label_start_s, state.intervals[0].label_end_s], [1, 6]);
  assert.equal(get("count").textContent, "1 remaining");
  assert.equal(get("start").value, "8.000");
  assert.equal(get("candidates").children.length, 2);
  get("threshold").oninput();
  assert.equal(get("count").textContent, "1 remaining");
  await init();
  assert.equal(get("candidates").children.length, 2);
  assert.equal(get("outcome").value, "background");
  assert.equal(Number(get("label-end").value), 6);
});

test("UI trims padding before saving a separate adjacent attempt", async t => {
  const {get, calls} = await controller(t, {onsets: [2, 2.5, 4, 4.5]});
  get("trick").value = "ollie"; get("trick").oninput();
  get("outcome").value = "make"; get("outcome").oninput();
  get("label-end").value = 3.871826; get("label-end").onchange();
  await get("save").click();
  assert.equal(get("start").value, "4.000");
  assert.equal(Number(get("label-start").value), 3.872);
  assert.equal(get("window-conflict").hidden, true);
  get("outcome").value = "bail"; get("outcome").oninput();
  await get("save").click();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].interval.label_start_s, 3.872);
  assert.equal(calls[1].interval.start_s, 4);
});

test("UI identifies a partial overlap before save, opens the saved label, and preserves drafts", async t => {
  const intervals = [{id: "saved", label_id: "one", status: "labeled", start_s: 2, end_s: 2.5,
    label_start_s: 1, label_end_s: 4, trick: "ollie", outcome: "make"}];
  const {get, calls, key} = await controller(t, {intervals, onsets: [3.8, 4.3]});
  get("pending-only").checked = true; get("pending-only").onchange();
  assert.equal(get("window-conflict").hidden, false);
  assert.match(get("window-conflict-text").textContent, /ollie.*0:01.000–0:04.000/);
  assert.equal(get("save").disabled, true);
  key("0");
  assert.equal(calls.length, 0, "keyboard actions also respect overlaps");
  get("note").value = "check this contact"; get("note").oninput();
  get("open-overlap").click();
  assert.equal(get("selected-title").textContent, "Saved label");
  assert.equal(get("pending-only").checked, false);
  get("next").click();
  assert.equal(get("note").value, "check this contact");
  await get("reject").click();
  assert.equal(calls[0].decision, "skip");
  assert.equal(intervals[0].outcome, "make");
});

test("UI background range refuses saved time and keeps an overlapping edited range unsaved", async t => {
  const intervals = [{id: "saved", label_id: "one", status: "labeled", start_s: 8, end_s: 8.5,
    label_start_s: 7, label_end_s: 10, trick: "ollie", outcome: "make"}];
  const {get, calls, key} = await controller(t, {intervals});
  get("video").currentTime = 8; get("new-background").click();
  assert.match(get("status").textContent, /inside a saved label/);
  get("video").currentTime = 5; get("new-background").click();
  assert.equal(Number(get("label-end").value), 7);
  get("video").currentTime = 9; get("label-mark-end").click();
  assert.equal(get("window-conflict").hidden, false);
  key("s"); key("0");
  assert.equal(calls.length, 0);
  assert.equal(Number(get("label-end").value), 9, "never silently shrink an explicit range");
});

test("UI preserves drafts on failed saves and outcome shortcuts submit the selected trick", async t => {
  const ui = await controller(t);
  ui.get("trick").value = "kickflip"; ui.get("trick").oninput();
  ui.get("note").value = "front foot missed"; ui.get("note").oninput();
  ui.fail(); ui.key("2");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.calls[0].interval.outcome, "bail");
  assert.equal(ui.calls[0].interval.trick, "kickflip");
  assert.match(ui.get("status").textContent, /disk full/);
  assert.equal(ui.get("outcome").value, "bail");
  assert.equal(ui.get("note").value, "front foot missed");
  assert.equal(ui.get("start").value, "2.000");
  assert.equal(ui.state.intervals.length, 0);
});

test("an old running server gets restart instructions before accessing missing label context", async t => {
  const {calls} = await controller(t, {legacyServer: true});
  assert.equal(calls.length, 0);
  assert.throws(() => requireCurrentServer({api_version: 4}, {intervals: []}), /Restart the review server/);
  assert.throws(() => requireCurrentServer({api_version: 7}, {label_context: {}, labels_revision: "saved"}), /Restart the review server/);
});

test("frame stepping follows 119.88 fps timestamps and irregular presentation intervals", () => {
  const times = Array.from({length: 5}, (_, i) => .008 + i * 1001 / 120000);
  assert.equal(stepFrame(times, times[1], 1), times[2]);
  assert.equal(stepFrame(times, times[1], -1), times[0]);
  assert.equal(stepFrame(times, times[1] + .002, -1), times[0]);
  assert.equal(stepFrame(times, 0, 1), times[0]);
  assert.equal(stepFrame(times, 0, -1), times[0]);
  assert.equal(stepFrame(times, times.at(-1), 1), times.at(-1));
  assert.equal(stepFrame([0, .01, .037, .052], .01, 1), .037);
  assert.equal(stepFrame([0, .01, .037, .052], .04, -1), .01);
});

test("UI frame buttons step through native video frames independently of playback speed", async t => {
  const {get} = await controller(t);
  get("speed").value = "0.25"; get("speed").onchange();
  get("video").currentTime = 0;
  get("forward").click();
  assert.ok(Math.abs(get("video").currentTime - 1001 / 120000) < .00001);
  get("forward").click();
  assert.ok(Math.abs(get("video").currentTime - 2002 / 120000) < .00001);
  get("back").click();
  assert.ok(Math.abs(get("video").currentTime - 1001 / 120000) < .00001);
  assert.match(get("video-rate").textContent, /119\.88 fps/);
});
