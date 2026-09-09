import assert from "node:assert/strict";
import {test} from "node:test";
import {mergeReviews, proposePairs, validInterval} from "../viz/trick_review.mjs";

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
