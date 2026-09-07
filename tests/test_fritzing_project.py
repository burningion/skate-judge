import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from hardware.fritzing.build_project import HERE, NAME, archive, build, parts
from hardware.fritzing.validate_project import validate


class FritzingProjectTests(unittest.TestCase):
    def setUp(self):
        self.project = HERE / (NAME + ".fzz")

    def test_bundle_contains_native_parts_and_checked_electrical_nets(self):
        result = validate(self.project)
        self.assertEqual(result["hardware_parts"], 5)
        self.assertEqual(result["electrical_nets"], 7)
        self.assertEqual(result["editable_wire_segments"], 31)

    def test_vendored_archives_match_pinned_hashes(self):
        sources = json.loads((HERE / "sources/provenance.json").read_text())
        for key, source in sources.items():
            self.assertEqual(
                hashlib.sha256(
                    (HERE / "sources" / f"{key}.fzpz").read_bytes()
                ).hexdigest(),
                source["sha256"],
            )

    def test_socket_and_header_positions_match_independent_qt_svg_measurements(self):
        # Measurements from Qt's SVG renderer; the builder uses its own XML geometry.
        ps = parts()
        for key, connector, expected in [
            ("feather", "connector64", (372.55, 249.5)),
            ("feather", "connector73", (453.55, 249.5)),
            ("feather", "connector218", (358.065, 242.71525)),
            ("feather", "connector30", (390.8811, 288.0669)),
            ("imu", "connector12", (164.9548, 299.1281)),
            ("stick", "connector25", (551.7711, 148.5004)),
        ]:
            actual = ps[key].point(connector)
            for a, e in zip(actual, expected):
                self.assertAlmostEqual(a, e, places=3)

    def test_rebuild_is_deterministic_and_refuses_to_overwrite_hand_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = build(Path(folder))
            self.assertEqual(path.read_bytes(), self.project.read_bytes())
            with self.assertRaises(FileExistsError):
                build(Path(folder))

    def mutate(self, operation):
        with zipfile.ZipFile(self.project) as z:
            members = {name: z.read(name) for name in z.namelist()}
        filename = NAME + ".fz"
        root = ET.fromstring(members[filename])
        operation(root)
        members[filename] = ET.tostring(root)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.fzz"
            archive(path, members)
            with self.assertRaises(AssertionError):
                validate(path)

    def test_wrong_resistor_value_is_rejected(self):
        self.mutate(
            lambda root: root.find(
                "./instances/instance[@modelIndex='4']/property[@name='resistance']"
            ).set("value", "0")
        )

    def test_missing_reciprocal_wire_connection_is_rejected(self):
        def remove(root):
            parent = root.find(
                "./instances/instance[@modelIndex='1']/views/breadboardView/connectors/connector/connects"
            )
            parent.remove(parent[0])

        self.mutate(remove)

    def test_power_to_ground_short_is_rejected(self):
        def short(root):
            view = root.find(
                "./instances/instance[@modelIndex='1']/views/breadboardView/connectors"
            )
            for a, b in (
                ("connector64", "connector60"),
                ("connector60", "connector64"),
            ):
                parent = view.find(f"connector[@connectorId='{a}']/connects")
                ET.SubElement(
                    parent, "connect", connectorId=b, modelIndex="1", layer="breadboard"
                )

        self.mutate(short)


if __name__ == "__main__":
    unittest.main()
