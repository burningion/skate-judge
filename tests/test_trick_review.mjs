import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile} from "node:fs/promises";
import {attemptWindow, init, mergeReviews, needsReview, nextPending, proposePairs, requireCurrentServer, stepFrame, validInterval} from "../viz/trick_review.mjs";

const opts = {threshold: .65, min_gap: .18, max_gap: .9};
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
async function controller(t, {legacyServer = false} = {}) {
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
    if (match[1] === "select") {
      const block = html.slice(match.index, html.indexOf("</select>", match.index));
      const options = [...block.matchAll(/<option\b[^>]*>/g)].map(row => row[0]);
      node.value = (options.find(option => option.includes("selected")) || options[0] || "").match(/value="([^"]*)"/)?.[1] ?? "";
    }
    nodes.set(match[2], node);
  }
  const get = id => { assert.ok(nodes.has(id), `missing control ${id}`); return nodes.get(id); };
  const mapping = {scale: 1, offset_s: 0, points: 2};
  const state = {revision: 0, labels_revision: "0", intervals: [],
    settings: {...opts, offset: 999, scale: .99},
    label_context: {available: true, mapping, coverage: [0, 20]}};
  const data = {api_version: 2, video: "test.mp4", duration_s: 20, source_pts_origin_s: 0, media_url: "/media.mp4",
    video_timing: {fps: 120000 / 1001, frame_times_s: [0, 1001 / 120000, 2002 / 120000]},
    audio: {onsets: [2, 2.5, 8, 8.5].map(time_s => ({time_s, strength: 1})),
      strength: Array(200).fill(0), waveform_min: [], waveform_max: [], step_s: .1},
    sensor: {mapping, samples: [], warnings: [], syncs: [], offset_hint_s: 0}};
  if (legacyServer) { delete data.api_version; delete state.label_context; delete state.labels_revision; }
  const calls = [];
  let fail = false;
  const globals = {document: {getElementById: get, createElement: element,
      addEventListener: (name, handler) => listeners.set(name, handler)},
    window: {devicePixelRatio: 1, addEventListener() {}}, requestAnimationFrame() {},
    ResizeObserver: class { observe() {} },
    fetch: async (url, options) => {
      if (!options) return {ok: true, json: async () => structuredClone(url === "/api/data" ? data : state)};
      const body = JSON.parse(options.body); calls.push(body);
      assert.equal(url, "/api/label");
      if (fail) return {ok: false, json: async () => ({error: "disk full"})};
      const row = {...body.interval, status: body.decision === "skip" ? "rejected" : "labeled"};
      if (body.decision === "label") row.label_id = row.id;
      state.intervals = [...state.intervals.filter(old => old.id !== row.id), row];
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
  return {get, calls, state, fail: () => { fail = true; }, key: key => {
    listeners.get("keydown")({key, target: {closest: () => null}, preventDefault() {}});
  }};
}

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
  assert.throws(() => requireCurrentServer({api_version: 2}, {intervals: []}), /Restart the review server/);
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
