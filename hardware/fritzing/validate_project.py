#!/usr/bin/env python3
"""Check bundle integrity and the electrical nets independently of drawing colors.

This is structural validation, not a replacement for opening the file in Fritzing.
"""

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


def validate(path):
    with zipfile.ZipFile(path) as bundle:
        assert bundle.testzip() is None, "Corrupt ZIP member"
        names = bundle.namelist()
        assert len(names) == len(set(names)), "Duplicate archive member"
        assert all(
            "/" not in name and "\\" not in name for name in names
        ), "Non-flat bundle path"
        sketches = [name for name in names if name.endswith(".fz")]
        assert len(sketches) == 1, "Expected exactly one sketch"
        project = ET.fromstring(bundle.read(sketches[0]))
        definitions = {}
        for name in names:
            if not name.startswith("part.") or not name.endswith(".fzp"):
                continue
            part = ET.fromstring(bundle.read(name))
            definitions[part.get("moduleId")] = part
            for view in part.findall("./views/*"):
                image = "svg." + view.find("layers").get("image").replace("/", ".", 1)
                assert image in names, f"Missing {image}"
                svg = ET.fromstring(bundle.read(image))
                ids = {node.get("id") for node in svg.iter()}
                for ref in part.findall(f"./connectors/connector/views/{view.tag}/p"):
                    for attribute in ("svgId", "terminalId", "legId"):
                        if ref.get(attribute):
                            assert (
                                ref.get(attribute) in ids
                            ), f"Missing {attribute}: {ref.attrib}"

    instances = project.findall("./instances/instance")
    lookup = {node.get("modelIndex"): node for node in instances}
    assert len(lookup) == len(instances), "Duplicate instance IDs"
    edges = set()
    parents = {}

    def find(node):
        parents.setdefault(node, node)
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    def union(a, b):
        parents[find(a)] = find(b)

    def exists(index, connector):
        instance = lookup[index]
        module = instance.get("moduleIdRef")
        if module == "WireModuleID":
            return connector in ("connector0", "connector1")
        return (
            definitions[module].find(f"./connectors/connector[@id='{connector}']")
            is not None
        )

    hardware = wires = 0
    for instance in instances:
        index, module = instance.get("modelIndex"), instance.get("moduleIdRef")
        if module == "WireModuleID":
            wires += 1
            union((index, "connector0"), (index, "connector1"))
            assert (
                instance.find("./views/breadboardView/geometry").get("wireFlags")
                == "64"
            )
        elif module != "NoteModuleID":
            hardware += 1
            assert module in definitions, f"Part is not bundled: {module}"
            for bus in definitions[module].findall("./buses/bus"):
                members = [node.get("connectorId") for node in bus]
                for member in members[1:]:
                    union((index, members[0]), (index, member))
        for connector in instance.findall(
            "./views/breadboardView/connectors/connector"
        ):
            a = (index, connector.get("connectorId"))
            assert exists(*a), f"Unknown connector {a}"
            for ref in connector.findall("./connects/connect"):
                b = (ref.get("modelIndex"), ref.get("connectorId"))
                assert exists(*b), f"Unknown target connector {b}"
                edges.add((a, b))
                union(a, b)
    for a, b in edges:
        assert (b, a) in edges, f"One-way connection {a} → {b}"

    nets = {
        "VBAT": [(5, 0), (1, 218), (1, 64), (3, 24), (3, 21)],
        "GND": [(5, 1), (1, 217), (1, 60), (1, 29), (2, 11), (3, 26)],
        "GPIO5_before_resistor": [(1, 73), (4, 0)],
        "DIN_after_resistor": [(4, 1), (3, 25)],
        "QT_switched_3V3": [(1, 30), (2, 12)],
        "SDA_GPIO3": [(1, 31), (2, 13)],
        "SCL_GPIO4": [(1, 32), (2, 14)],
    }
    roots = []
    for name, members in nets.items():
        mapped = {(str(index), f"connector{connector}") for index, connector in members}
        components = {find(node) for node in mapped}
        assert len(components) == 1, f"Broken {name} net"
        roots.append(next(iter(components)))
    assert len(set(roots)) == len(roots), "Two distinct electrical nets are shorted"
    for index, connector in [(3, 20), (1, 66), (1, 61)]:
        assert (
            find((str(index), f"connector{connector}")) not in roots
        ), "DOUT/USB/3V must not join the LED circuit"
    resistor = lookup["4"].find("property[@name='resistance']")
    assert resistor.get("value") == "330", "Wrong data resistor value"
    assert hardware == 5 and wires > 10
    return dict(
        hardware_parts=hardware,
        editable_wire_segments=wires,
        electrical_nets=len(nets),
        bundled_parts=len(definitions),
        reciprocal_connections=len(edges),
        native_application_tested=False,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.project), indent=2))
