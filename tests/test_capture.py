"""Data integrity checks for recordings used as training evidence."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from capture.session import (
    BoardRestart,
    Session,
    Timeline,
    label,
    read_jsonl,
    sync_point,
    video_mapping,
)


def sample(stamp, sequence=0, boot="abcdef01"):
    return f"D,{stamp},1,2,9.8,0.1,0.2,0.3,23,{sequence},{boot}"


class TimelineTests(unittest.TestCase):
    def test_loss_duplicates_and_reordering_do_not_change_clock(self):
        timeline = Timeline()
        first, missing, _ = timeline.sample(sample(1_000_000, 10))
        self.assertEqual((first["t_s"], missing), (0, 0))
        last, missing, gap = timeline.sample(sample(1_030_000, 13))
        self.assertEqual((last["t_s"], missing, gap), (0.03, 2, 0.03))
        self.assertIsNone(timeline.sample(sample(1_020_000, 12)))
        self.assertIsNone(timeline.sample(sample(1_030_000, 13)))
        self.assertEqual(timeline.sample(sample(1_040_000, 14))[0]["t_s"], 0.04)

    def test_restart_cannot_merge_two_boots_into_one_training_session(self):
        timeline = Timeline()
        timeline.sample(sample(1_000_000, 10))
        with self.assertRaises(BoardRestart):
            timeline.sample(sample(20_000, 0, "abcdef02"))

    def test_legacy_timestamp_wrap_and_reset(self):
        timeline = Timeline()
        timeline.sample(f"D,{2**32 - 5000},0,0,9.8,0,0,0,22")
        self.assertEqual(timeline.sample("D,5000,0,0,9.8,0,0,0,22")[0]["t_s"], 0.01)
        with self.assertRaises(BoardRestart):
            timeline.sample("D,1000,0,0,9.8,0,0,0,22")

    def test_sequence_wrap(self):
        timeline = Timeline()
        timeline.sample(sample(1000, 2**32 - 1))
        self.assertEqual(timeline.sample(sample(11000, 0))[1], 0)

    def test_malformed_or_nonfinite_data_cannot_poison_clock(self):
        for bad in (
            "D,1,2",
            sample(1).replace("9.8", "nan"),
            sample(-1),
            sample(1, -1),
            sample(1, boot="bad"),
        ):
            timeline = Timeline()
            with self.assertRaises(ValueError):
                timeline.sample(bad)
            self.assertIsNone(timeline.origin)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "session"
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.temporary.cleanup()

    def recorded(self):
        session = Session(self.path, {"synthetic": False})
        session.ingest(sample(1_000_000, 0))
        session.ingest("S,2000000,1,1,1,abcdef01")
        session.ingest(sample(21_000_000, 2000))
        session.ingest("S,21000000,2,1,1,abcdef01")
        session.close()

    def label_args(self, **changes):
        values = dict(
            session=self.path,
            start=1.0,
            end=3.0,
            outcome="bail",
            video=None,
            trick="ollie",
            note="",
            replace=None,
        )
        values.update(changes)
        return argparse.Namespace(**values)

    def test_raw_data_and_quality_counters_survive_invalid_input(self):
        session = Session(self.path, {})
        session.ingest(sample(1_000_000, 10))
        session.ingest("D,nonsense")
        session.ingest(sample(1_100_000, 20))
        session.ingest(sample(1_100_000, 20))
        session.close()
        meta = json.loads((self.path / "metadata.json").read_text())
        self.assertEqual(
            (
                meta["samples"],
                meta["malformed"],
                meta["stale"],
                meta["missing_sequences"],
            ),
            (2, 1, 1, 9),
        )
        self.assertEqual(len(read_jsonl(self.path / "raw.jsonl")), 4)
        self.assertEqual(meta["gaps_over_30ms"], 1)

    def test_stale_stream_cannot_accept_human_label(self):
        session = Session(self.path, {})
        try:
            session.ingest(
                sample(1_000_000), host_ns=time.monotonic_ns() - 1_000_000_000
            )
            with self.assertRaisesRegex(ValueError, "fresh"):
                session.mark("start ollie")
            session.ingest(sample(1_010_000, 1))
            session.mark("start ollie")
            session.ingest(sample(1_020_000, 2))
            session.mark("fall")
            rows = read_jsonl(self.path / "labels.jsonl")
            self.assertEqual((rows[0]["outcome"], rows[0]["trick"]), ("fall", "ollie"))
        finally:
            session.close()

    def test_unfinished_attempt_is_preserved_without_inventing_outcome(self):
        session = Session(self.path, {})
        session.ingest(sample(1_000_000))
        session.mark("start kickflip")
        session.close()
        self.assertFalse((self.path / "labels.jsonl").exists())
        self.assertEqual(
            read_jsonl(self.path / "events.jsonl")[-1]["kind"], "unfinished_attempt"
        )

    def test_session_never_overwrites_an_existing_recording(self):
        self.recorded()
        with self.assertRaises(FileExistsError):
            Session(self.path, {})

    def test_offline_label_bounds_overlap_and_append_only_corrections(self):
        self.recorded()
        label(self.label_args())
        with self.assertRaisesRegex(ValueError, "overlaps"):
            label(self.label_args())
        original = read_jsonl(self.path / "labels.jsonl")[0]
        label(self.label_args(replace=original["id"], outcome="make"))
        rows = read_jsonl(self.path / "labels.jsonl")
        self.assertEqual([row["outcome"] for row in rows], ["bail", "make"])
        self.assertEqual(rows[0]["id"], rows[1]["id"])
        for changes in (
            {"end": 30},
            {"start": -1},
            {"end": float("inf")},
            {"start": 3, "end": 2},
        ):
            with self.assertRaises(ValueError):
                label(self.label_args(**changes))

    def test_video_alignment_corrects_offset_and_drift(self):
        self.recorded()
        sync_point(
            argparse.Namespace(
                session=self.path, sync_id=1, video="phone.mov", video_seconds=5.0
            )
        )
        scale, offset, count = video_mapping(self.path, "phone.mov")
        self.assertEqual((scale, offset, count), (1.0, -4.0, 1))
        sync_point(
            argparse.Namespace(
                session=self.path, sync_id=2, video="phone.mov", video_seconds=24.01
            )
        )
        scale, offset, count = video_mapping(self.path, "phone.mov")
        self.assertAlmostEqual(scale * 5 + offset, 1)
        self.assertAlmostEqual(scale * 24.01 + offset, 20)
        self.assertEqual(count, 2)
        label(self.label_args(video="phone.mov", start=6, end=8))
        row = read_jsonl(self.path / "labels.jsonl")[0]
        self.assertEqual(row["source"], "video_human")
        self.assertAlmostEqual(row["start_s"], scale * 6 + offset)

    def test_sync_summary_counts_distinct_flashes_and_invalid_fit_does_not_append(self):
        self.recorded()
        def match(identity, video_s):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                sync_point(argparse.Namespace(session=self.path, sync_id=identity,
                                               video="phone.mov", video_seconds=video_s))
            return output.getvalue()
        self.assertIn("Add a second flash", match(1, 5))
        self.assertIn("1 flash matched", match(1, 5.01))
        second = match(2, 24.01)
        self.assertIn("Using 2 flash matches; drift correction is active", second)
        self.assertNotIn("Add a second flash", second)
        self.assertIn("Using 2 flash matches", match(1, 5))
        original = (self.path / "video_sync.jsonl").read_bytes()
        with self.assertRaises(ValueError):
            match(2, 6)
        self.assertEqual((self.path / "video_sync.jsonl").read_bytes(), original)

    def test_missing_or_disabled_led_cannot_claim_video_sync(self):
        session = Session(self.path, {})
        session.ingest(sample(1_000_000))
        session.ingest("S,2000000,1,1,0,abcdef01")
        session.close()
        for sync_id in (1, 99):
            with self.assertRaises(ValueError):
                sync_point(
                    argparse.Namespace(
                        session=self.path,
                        sync_id=sync_id,
                        video="phone.mov",
                        video_seconds=5,
                    )
                )


if __name__ == "__main__":
    unittest.main()
