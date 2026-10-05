// Shared candidate code is evaluated against snapshot labels, never used to fit
// those labels. Incomplete annotation means unclassified candidates stay unknown.
import {readFileSync} from "node:fs";
import {pathToFileURL} from "node:url";
import {motionTimeline, proposeCandidates} from "./trick_review.mjs";

export function evaluateClips(clips, settings) {
  const tolerance = .15;
  const matches = (pair, label) => Math.abs(pair.start_s - label.review_interval.start_s) <= tolerance;
  const results = settings.map(options => {
    const sessions = clips.map(clip => {
      const motion = motionTimeline(clip.motion, clip.mapping, clip.origin, clip.duration);
      const pairs = proposeCandidates(clip.audio, motion, options);
      const baseline = proposeCandidates(clip.audio, motion, {...options, candidate_mode: "audio"});
      const attempts = clip.labels.filter(label => ["make", "bail", "fall"].includes(label.outcome));
      const marked = attempts.filter(label => Number.isFinite(label.review_interval?.start_s));
      const backgrounds = clip.labels.filter(label => label.outcome === "background");
      const hit = (rows, label) => rows.some(pair => matches(pair, label));
      const counts = {candidates: pairs.length, known_attempts: attempts.length, attempts_with_pop: marked.length,
        matched_attempts: marked.filter(label => hit(pairs, label)).length,
        background_candidates: 0, unclassified_candidates: 0,
        unchecked_candidates: pairs.filter(pair => pair.motion_filter === "unchecked").length};
      for (const pair of pairs) {
        if (backgrounds.some(label => pair.start_s >= label.video_start_s - clip.origin &&
            pair.end_s <= label.video_end_s - clip.origin)) counts.background_candidates++;
        else if (!marked.some(label => matches(pair, label))) counts.unclassified_candidates++;
      }
      return {session: clip.session, video: clip.video, ...counts,
        outcomes: Object.fromEntries(["make", "bail", "fall"].map(outcome => [outcome, {
          with_pop: marked.filter(label => label.outcome === outcome).length,
          matched: marked.filter(label => label.outcome === outcome && hit(pairs, label)).length} ])),
        lost_attempt_ids: marked.filter(label => hit(baseline, label) && !hit(pairs, label)).map(label => label.id)};
    });
    const totals = {};
    for (const key of ["candidates", "known_attempts", "attempts_with_pop", "matched_attempts",
      "background_candidates", "unclassified_candidates", "unchecked_candidates"]) {
      totals[key] = sessions.reduce((sum, row) => sum + row[key], 0);
    }
    return {settings: options, totals, sessions};
  });
  return {evaluation: "retrospective development comparison; not held-out validation",
    pop_tolerance_s: tolerance,
    background_rule: "Entire proposed pop/contact interval is inside an eligible human background label.",
    unknown_rule: "Other unmatched candidates are unclassified, not assumed false positives.", results};
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const {clips, settings} = JSON.parse(readFileSync(0, "utf8"));
  console.log(JSON.stringify(evaluateClips(clips, settings), null, 2));
}
