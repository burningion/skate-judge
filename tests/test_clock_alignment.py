import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest

from capture.clock_alignment import clock_mapping, fit_clock_evidence
from capture.session import build_label, video_mapping, video_mapping_details


def clock_fixture(video="phone.mov", *, scale=1.0002, offset=-1.5, times=(2, 10, 20, 40, 60), rtt=.02):
    meta = dict(onboard_id="recording", boot_id="boot", device_origin_us=5_000_000,
                closed_utc="2026-10-30", duration_s=100)
    evidence = dict(schema=1, method="avcapture_start_pts", filename=video,
                    sessionID=meta["onboard_id"], bootID=meta["boot_id"],
                    anchor=dict(hostTimeS=1000, sessionTimeS=800), probes=[
                        dict(sendHostS=1000 + t - rtt / 2, receiveHostS=1000 + t + rtt / 2,
                             sendVideoS=t - rtt / 2, receiveVideoS=t + rtt / 2,
                             deviceUS=meta["device_origin_us"] + (scale * t + offset) * 1e6) for t in times])
    return meta, evidence


class ClockFitTests(unittest.TestCase):
    def test_offset_and_drift_use_monotonic_clocks_with_network_uncertainty(self):
        meta, evidence = clock_fixture()
        result = fit_clock_evidence(evidence, meta, "phone.mov")
        self.assertAlmostEqual(result["scale"], 1.0002)
        self.assertAlmostEqual(result["offset_s"], -1.5)
        self.assertAlmostEqual(result["network_uncertainty_s"], .010002)
        self.assertTrue(result["drift_estimated"])
        self.assertEqual(result["points"], 0)
        self.assertEqual(result["source"], "phone_clock")
        self.assertEqual(result["measured_video_range_s"], [2, 60])

    def test_fastest_exchange_in_each_window_wins_and_slow_stalls_are_ignored(self):
        meta, evidence = clock_fixture()
        evidence["probes"].insert(1, dict(sendHostS=1002.1, receiveHostS=1002.5, sendVideoS=2.1, receiveVideoS=2.5, deviceUS=5_820_000))
        evidence["probes"].append(dict(sendHostS=1061, receiveHostS=1063, sendVideoS=61, receiveVideoS=63, deviceUS=65_000_000))
        result = fit_clock_evidence(evidence, meta, "phone.mov")
        self.assertAlmostEqual(result["offset_s"], -1.5)
        self.assertEqual(result["anchor_count"], 5)
        self.assertEqual(result["probe_count"], 7)

    def test_short_or_sparse_recording_does_not_infer_drift(self):
        for times in [(2,), (2, 6, 12), (2, 60)]:
            meta, evidence = clock_fixture(scale=1, times=times)
            result = fit_clock_evidence(evidence, meta, "phone.mov")
            self.assertEqual(result["scale"], 1)
            self.assertAlmostEqual(result["offset_s"], -1.5)
            self.assertFalse(result["drift_estimated"])

    def test_capture_clock_rate_conversion_is_used_instead_of_assuming_host_seconds(self):
        meta, evidence = clock_fixture()
        for probe in evidence["probes"]:
            probe["sendHostS"] = 1000 + probe["sendVideoS"] / 1.0005
            probe["receiveHostS"] = 1000 + probe["receiveVideoS"] / 1.0005
        result = fit_clock_evidence(evidence, meta, "phone.mov")
        self.assertAlmostEqual(result["scale"], 1.0002)
        self.assertAlmostEqual(result["offset_s"], -1.5)

    def test_invalid_identity_unimported_data_or_nonmonotonic_clocks_cannot_align(self):
        meta, evidence = clock_fixture()
        broken = []
        for key, value in [("schema", 2), ("method", "button_time"), ("filename", "other.mov"),
                           ("sessionID", "other"), ("bootID", "reboot")]:
            broken.append((meta, dict(evidence, **{key: value})))
        broken += [(dict(meta, device_origin_us=None), evidence), (dict(meta, closed_utc=None), evidence)]
        for key, value in [("sendHostS", 999), ("receiveHostS", float("nan")), ("deviceUS", -1)]:
            changed = copy.deepcopy(evidence)
            changed["probes"][1][key] = value
            broken.append((meta, changed))
        for metadata, changed in broken:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                fit_clock_evidence(changed, metadata, "phone.mov")

    def test_long_stalls_and_implausible_drift_fail_without_fabricating_alignment(self):
        for kwargs in [dict(rtt=.6), dict(scale=1.03), dict(times=())]:
            meta, evidence = clock_fixture(**kwargs)
            with self.assertRaises(ValueError):
                fit_clock_evidence(evidence, meta, "phone.mov")


class ClockRefinementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.meta, self.evidence = clock_fixture()
        (self.path / "metadata.json").write_text(json.dumps(self.meta))
        self.sidecar = self.path / "phone.json"
        self.sidecar.write_text(json.dumps(dict(clock_alignment=self.evidence)))

    def points(self, rows):
        (self.path / "video_sync.jsonl").write_text("\n".join(json.dumps(dict(
            video="phone.mov", sync_id=i, video_s=x, t_s=y)) for i, x, y in rows))

    def test_phone_estimate_one_led_and_two_leds_have_explicit_precedence(self):
        original = self.sidecar.read_bytes()
        self.assertEqual(video_mapping_details(self.path, "phone.mov")["points"], 0)
        self.assertFalse((self.path / "video_sync.jsonl").exists())
        self.points([(1, 4, 3)])
        result = video_mapping_details(self.path, "phone.mov")
        self.assertEqual(result["source"], "led_phone_clock")
        self.assertAlmostEqual(result["scale"], 1.0002)
        self.assertAlmostEqual(result["scale"] * 4 + result["offset_s"], 3)
        self.points([(1, 4, 3), (2, 64, 63.06)])
        result = video_mapping_details(self.path, "phone.mov")
        self.assertEqual(result["source"], "led")
        self.assertAlmostEqual(result["scale"], 1.001)
        self.assertAlmostEqual(result["offset_s"], -1.004)
        # Editing a flash ID replaces its correspondence, without altering evidence.
        with (self.path / "video_sync.jsonl").open("a") as stream:
            stream.write('\n' + json.dumps(dict(video="phone.mov", sync_id=2, video_s=64, t_s=63)))
        self.assertEqual(video_mapping(self.path, "phone.mov"), (1, -1, 2))
        self.assertEqual(self.sidecar.read_bytes(), original)

    def test_bad_optional_timing_cannot_disable_valid_led_alignment(self):
        self.sidecar.write_text('{"clock_alignment": {}}')
        with self.assertRaisesRegex(ValueError, "Phone clock estimate unavailable"):
            video_mapping_details(self.path, "phone.mov")
        self.points([(1, 4, 3)])
        self.assertEqual(video_mapping_details(self.path, "phone.mov"), dict(scale=1, offset_s=-1, points=1, source="led"))

    def test_legacy_external_video_stays_unaligned_without_leds(self):
        self.sidecar.write_text('{}')
        self.assertIsNone(clock_mapping(self.path, "phone.mov"))
        with self.assertRaisesRegex(ValueError, "No video alignment"):
            video_mapping(self.path, "phone.mov")
        self.assertIsNone(clock_mapping(self.path, "external.mp4"))

    def test_labels_save_clock_provenance_and_do_not_change_when_alignment_is_refined(self):
        label = build_label(argparse.Namespace(session=self.path, video="phone.mov", start=3, end=4,
            outcome="make", trick="ollie", note="", replace=None))
        self.assertAlmostEqual(label["start_s"], 1.5006)
        self.assertEqual(label["video_mapping"]["source"], "phone_clock")
        labels = self.path / "labels.jsonl"
        labels.write_text(json.dumps(label) + '\n')
        original = labels.read_bytes()
        self.points([(1, 4, 3)])
        video_mapping_details(self.path, "phone.mov")
        self.assertEqual(labels.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
