#!/usr/bin/env python3
"""Build the initial editable Fritzing layout from pinned parts. Does not run Fritzing.

Use a NEW --output directory when rebuilding: never overwrite a hand-edited .fzz.
The normal build/validation uses only Python's standard library. --preview also
requires PyQt6 (Qt's SVG renderer), and is a preview, not a Fritzing application test.
"""

import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent
NAME = "skate-judge-direct-lipo"
ADAFRUIT_COMMIT = "7d905c3e982de3712c3033da4596cc71ea32de1c"
CORE_COMMIT = "e64ffe973e92176b989ab390ab668638a85ee305"
SOURCE_PARTS = {
    "feather": "Adafruit ESP32-S3 Feather.fzpz",
    "imu": "Adafruit LSM6DSO32.fzpz",
    "stick": "Adafruit NeoPixel Stick.fzpz",
}
COLORS = {
    "battery": "#cf202f",
    "ground": "#333333",
    "data": "#20964c",
    "sda": "#246bd1",
    "scl": "#d1ac00",
}


def element(parent, tag, text=None, **attributes):
    node = ET.SubElement(parent, tag, {k: str(v) for k, v in attributes.items()})
    node.text = text
    return node


def xml_bytes(root):
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def archive(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, (2026, 9, 7, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, data)


def import_sources(directory):
    """One-time vendoring from the verified official downloads, no network access."""
    source = HERE / "sources"
    source.mkdir(exist_ok=True)
    for key, name in SOURCE_PARTS.items():
        shutil.copyfile(directory / name, source / f"{key}.fzpz")
    resistor = ET.parse(directory / "resistor.fzp").getroot()
    members = {"part.resistor.fzp": (directory / "resistor.fzp").read_bytes()}
    for layer in resistor.findall("./views/*/layers"):
        relative = layer.get("image")
        members["svg." + relative.replace("/", ".", 1)] = (
            directory / ("core-svg_core_" + relative.replace("/", "_"))
        ).read_bytes()
    archive(source / "resistor.fzpz", members)
    shutil.copyfile(directory / "Adafruit-LICENSE", source / "CC-BY-SA-3.0.txt")
    shutil.copyfile(directory / "core-LICENSE.txt", source / "Fritzing-LICENSE.txt")
    provenance = {
        key: {
            "upstream": f"https://github.com/adafruit/Fritzing-Library/blob/{ADAFRUIT_COMMIT}/parts/{name}",
            "sha256": hashlib.sha256((source / f"{key}.fzpz").read_bytes()).hexdigest(),
        }
        for key, name in SOURCE_PARTS.items()
    }
    provenance["resistor"] = {
        "upstream": f"https://github.com/fritzing/fritzing-parts/tree/{CORE_COMMIT}",
        "sha256": hashlib.sha256((source / "resistor.fzpz").read_bytes()).hexdigest(),
    }
    (source / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


def multiply(a, b):
    return tuple(
        sum(a[row * 3 + k] * b[k * 3 + col] for k in range(3))
        for row in range(3)
        for col in range(3)
    )


IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)


def transform(text):
    result = IDENTITY
    for name, body in re.findall(r"(\w+)\s*\(([^)]*)\)", text or ""):
        v = list(
            map(
                float, re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", body)
            )
        )
        if name == "matrix":
            a, b, c, d, e, f = v
            current = (a, c, e, b, d, f, 0, 0, 1)
        elif name == "translate":
            current = (1, 0, v[0], 0, 1, v[1] if len(v) > 1 else 0, 0, 0, 1)
        elif name == "scale":
            current = (v[0], 0, 0, 0, v[-1], 0, 0, 0, 1)
        elif name == "rotate":
            angle = math.radians(v[0])
            c, s = math.cos(angle), math.sin(angle)
            current = (c, -s, 0, s, c, 0, 0, 0, 1)
            if len(v) == 3:
                current = multiply(
                    transform(f"translate({v[1]},{v[2]})"),
                    multiply(current, transform(f"translate({-v[1]},{-v[2]})")),
                )
        else:
            raise ValueError(f"Unsupported SVG transform: {name}")
        result = multiply(result, current)
    return result


def svg_size(svg):
    def units(value):
        amount, unit = re.fullmatch(r"([\d.]+)(in|mm|cm|px|pt)?", value).groups()
        return (
            float(amount)
            * {
                "in": 90,
                "mm": 90 / 25.4,
                "cm": 90 / 2.54,
                "pt": 1.25,
                "px": 1,
                None: 1,
            }[unit]
        )

    return units(svg.get("width")), units(svg.get("height"))


def svg_point(svg, svg_id, line_start=False):
    """Include ancestor transforms; support zero-width schematic terminals."""
    parents = {child: parent for parent in svg.iter() for child in parent}
    node = next(n for n in svg.iter() if n.get("id") == svg_id)
    tag = node.tag.split("}")[-1]

    def n(key):
        return float(node.get(key, "0"))

    if tag in ("circle", "ellipse"):
        x, y = n("cx"), n("cy")
    elif tag == "rect":
        x, y = n("x") + n("width") / 2, n("y") + n("height") / 2
    elif tag == "line":
        x, y = (
            (n("x1"), n("y1"))
            if line_start
            else ((n("x1") + n("x2")) / 2, (n("y1") + n("y2")) / 2)
        )
    elif tag in ("polygon", "polyline"):
        values = list(map(float, re.findall(r"[-+\d.eE]+", node.get("points"))))
        x, y = (
            (min(values[::2]) + max(values[::2])) / 2,
            (min(values[1::2]) + max(values[1::2])) / 2,
        )
    else:
        raise ValueError(f"Unsupported connector geometry: {svg_id} ({tag})")
    chain = [node]
    while chain[-1] in parents:
        chain.append(parents[chain[-1]])
    matrix = IDENTITY
    for ancestor in reversed(chain):
        matrix = multiply(matrix, transform(ancestor.get("transform")))
    x, y = (
        matrix[0] * x + matrix[1] * y + matrix[2],
        matrix[3] * x + matrix[4] * y + matrix[5],
    )
    vx, vy, vw, vh = map(float, svg.get("viewBox").split())
    w, h = svg_size(svg)
    return (x - vx) * w / vw, (y - vy) * h / vh


class Part:
    def __init__(self, name, index, title, members, positions):
        self.name, self.index, self.title, self.members, self.positions = (
            name,
            index,
            title,
            members,
            positions,
        )
        self.file = next(
            key for key in members if key.startswith("part.") and key.endswith(".fzp")
        )
        self.definition = ET.fromstring(members[self.file])
        self.module = self.definition.get("moduleId")
        self.views, self.connectors = {}, {}

    def svg_bytes(self, view):
        image = self.definition.find(f"./views/{view}View/layers").get("image")
        return self.members["svg." + image.replace("/", ".", 1)]

    def point(self, connector, view="breadboard"):
        p = self.definition.find(
            f"./connectors/connector[@id='{connector}']/views/{view}View/p"
        )
        point = svg_point(
            ET.fromstring(self.svg_bytes(view)),
            p.get("legId", p.get("terminalId", p.get("svgId"))),
            line_start=bool(p.get("legId")),
        )
        return tuple(a + b for a, b in zip(self.positions[view], point))

    def label_position(self, view):
        x, y = self.positions[view]
        if view == "breadboard" and self.name == "feather":
            return 400, 337
        if view == "breadboard" and self.name == "resistor":
            return 462, 130
        return x, y - 12


def parts():
    specs = [
        (
            "feather",
            1,
            "U1 · Feather ESP32-S3",
            {"breadboard": (310, 245), "schematic": (250, 230), "pcb": (100, 100)},
        ),
        (
            "imu",
            2,
            "U2 · LSM6DSO32",
            {"breadboard": (70, 265), "schematic": (510, 300), "pcb": (330, 100)},
        ),
        (
            "stick",
            3,
            "LED1 · 8-pixel RGB stick",
            {"breadboard": (550, 135), "schematic": (510, 100), "pcb": (100, 250)},
        ),
        (
            "resistor",
            4,
            "R1 · 330 Ω",
            {
                "breadboard": (502, 143.9599),
                "schematic": (435, 133.83),
                "pcb": (330, 250),
            },
        ),
    ]
    result = {}
    for key, index, title, positions in specs:
        with zipfile.ZipFile(HERE / "sources" / f"{key}.fzpz") as z:
            result[key] = Part(
                key,
                index,
                title,
                {name: z.read(name) for name in z.namelist()},
                positions,
            )
    members = {
        "part.skate-judge-lipo.fzp": (HERE / "parts/lipo-500mah.fzp").read_bytes()
    }
    for view in ("breadboard", "schematic", "pcb"):
        members[f"svg.{view}.skate-judge-lipo.svg"] = (
            HERE / "parts" / f"lipo-{view}.svg"
        ).read_bytes()
    result["battery"] = Part(
        "battery",
        5,
        "BAT1 · 1S LiPo",
        members,
        {"breadboard": (190, 105), "schematic": (100, 250), "pcb": (100, 350)},
    )
    return result


def connection(parent, connector, layer):
    connectors = parent.find("connectors")
    if connectors is None:
        connectors = element(parent, "connectors")
    node = connectors.find(f"connector[@connectorId='{connector}']")
    if node is None:
        node = element(connectors, "connector", connectorId=connector, layer=layer)
        element(node, "geometry", x=0, y=0)
        element(node, "connects")
    return node.find("connects")


def build(output, replace_generated=False):
    output.mkdir(parents=True, exist_ok=True)
    targets = [output / (NAME + suffix) for suffix in (".fz", ".fzz", ".svg")]
    if not replace_generated and any(target.exists() for target in targets):
        raise FileExistsError(
            "Use a new output directory; existing editable projects are never overwritten."
        )
    ps = parts()
    root = ET.Element("module", fritzingVersion="1.0.5")
    element(root, "title", "Skate Judge — direct-LiPo first prototype")
    views = element(root, "views")
    for view in ("breadboard", "schematic", "pcb"):
        element(
            views,
            "view",
            name=view + "View",
            backgroundColor="#ffffff",
            gridSize="0.1in",
            showGrid="0",
            alignToGrid="0",
            viewFromBelow="0",
        )
    instances = element(root, "instances")
    for p in ps.values():
        instance = element(
            instances,
            "instance",
            moduleIdRef=p.module,
            modelIndex=p.index,
            path=p.file.removeprefix("part."),
        )
        element(instance, "title", p.title)
        if p.name == "resistor":
            for key, value in {
                "resistance": "330",
                "tolerance": "±5%",
                "power": "0.25",
                "pin spacing": "400 mil",
            }.items():
                element(instance, "property", name=key, value=value)
        iviews = element(instance, "views")
        for view, (x, y) in p.positions.items():
            v = element(
                iviews, view + "View", layer="copper0" if view == "pcb" else view
            )
            element(v, "geometry", x=x, y=y, z=2)
            lx, ly = p.label_position(view)
            label = element(
                v,
                "titleGeometry",
                visible="true",
                x=lx,
                y=ly,
                z=8,
                xOffset=lx - x,
                yOffset=ly - y,
                textColor="#202020",
                fontSize=7,
            )
            element(label, "displayKey", key="")
            p.views[view] = v

    # Every route is an electrical connection, not a line drawn over an image.
    # The JST pins below are the cable/socket contacts, not similarly named header pads.
    routes = [
        (
            "LiPo positive → Feather battery JST +",
            "battery",
            "connector0",
            "feather",
            "connector218",
            "battery",
            [(358.065, 127)],
        ),
        (
            "LiPo negative → Feather battery JST −",
            "battery",
            "connector1",
            "feather",
            "connector217",
            "ground",
            [(347.435, 150)],
        ),
        (
            "BAT → stick +5V input (battery voltage)",
            "feather",
            "connector64",
            "stick",
            "connector24",
            "battery",
            [(372.55, 215), (535, 215), (535, 157.5)],
        ),
        (
            "Common ground → stick GND",
            "feather",
            "connector60",
            "stick",
            "connector26",
            "ground",
            [(363.55, 350), (765, 350), (765, 110), (535, 110), (535, 139.5)],
        ),
        (
            "GPIO5 → 330 Ω resistor",
            "feather",
            "connector73",
            "resistor",
            "connector0",
            "data",
            [(453.55, 193), (480, 193), (480, 148.5)],
        ),
        (
            "330 Ω → stick DIN",
            "resistor",
            "connector1",
            "stick",
            "connector25",
            "data",
            [],
        ),
        (
            "STEMMA QT switched 3.3 V → IMU VIN",
            "imu",
            "connector12",
            "feather",
            "connector30",
            "battery",
            [(220, 299.1281), (280, 288.0669)],
        ),
        (
            "STEMMA QT ground",
            "imu",
            "connector11",
            "feather",
            "connector29",
            "ground",
            [(220, 304.7973), (280, 293.3074)],
        ),
        (
            "STEMMA QT SDA / GPIO3",
            "imu",
            "connector13",
            "feather",
            "connector31",
            "sda",
            [(220, 293.4587), (280, 282.7919)],
        ),
        (
            "STEMMA QT SCL / GPIO4",
            "imu",
            "connector14",
            "feather",
            "connector32",
            "scl",
            [(220, 287.7895), (280, 277.5168)],
        ),
    ]
    wire_id = 100
    preview_wires = []
    for route_name, aname, ac, bname, bc, color, bends in routes:
        a, b = ps[aname], ps[bname]
        # Breadboard wiring is intentionally the authored view. Other views
        # retain component placement; Fritzing can derive unrouted connections.
        view, layer = "breadboard", "breadboardWire"
        points = [a.point(ac)] + bends + [b.point(bc)]
        ids = list(range(wire_id, wire_id + len(points) - 1))
        wire_id += len(ids)
        for i, (start, end) in enumerate(zip(points, points[1:])):
            w = element(
                instances,
                "instance",
                moduleIdRef="WireModuleID",
                modelIndex=ids[i],
                path="wire.fzp",
            )
            element(w, "title", route_name)
            v = element(element(w, "views"), view + "View", layer=layer)
            element(
                v,
                "geometry",
                x=start[0],
                y=start[1],
                x1=0,
                y1=0,
                x2=end[0] - start[0],
                y2=end[1] - start[1],
                z=4,
                wireFlags=64,
            )
            element(
                v, "wireExtras", mils=22.2222, color=COLORS[color], opacity=1, banded=0
            )
            for cid, other_id, other_c, other_layer in (
                (
                    "connector0",
                    a.index if i == 0 else ids[i - 1],
                    ac if i == 0 else "connector1",
                    view if i == 0 else layer,
                ),
                (
                    "connector1",
                    b.index if i == len(ids) - 1 else ids[i + 1],
                    bc if i == len(ids) - 1 else "connector0",
                    view if i == len(ids) - 1 else layer,
                ),
            ):
                element(
                    connection(v, cid, layer),
                    "connect",
                    connectorId=other_c,
                    modelIndex=other_id,
                    layer=other_layer,
                )
        element(
            connection(a.views[view], ac, view),
            "connect",
            connectorId="connector0",
            modelIndex=ids[0],
            layer=layer,
        )
        element(
            connection(b.views[view], bc, view),
            "connect",
            connectorId="connector1",
            modelIndex=ids[-1],
            layer=layer,
        )
        preview_wires.append((route_name, COLORS[color], points))

    notes = [
        (
            40,
            25,
            720,
            50,
            "SKATE JUDGE — DIRECT-LiPo FIRST PROTOTYPE",
            "Editable wiring layout · power disconnected while wiring · not a production PCB design",
        ),
        (
            55,
            345,
            270,
            60,
            "ONE STEMMA QT CABLE",
            "Red: switched 3.3 V · black: GND · blue: SDA / GPIO3 · yellow: SCL / GPIO4. GPIO7 enables this rail; no extra wire.",
        ),
        (
            550,
            205,
            195,
            115,
            "LED STICK",
            "GPIO5 (pad marked 5) is DATA, not 5 V. R1: recommended 330 Ω, near DIN. DOUT is unconnected. BAT supplies battery voltage, not regulated 5 V.",
        ),
        (
            55,
            415,
            690,
            75,
            "CHECK BEFORE SKATING",
            "Direct LiPo is a bench-test option: this RGB stick is specified for 4–7 V, so operation below 4 V is not guaranteed. Test fully charged and partly discharged. Verify JST polarity. Insulate and protect the battery. Crossings without a junction are not connections.",
        ),
    ]
    for index, (x, y, width, height, heading, text) in enumerate(notes, 900):
        note = element(
            instances,
            "instance",
            moduleIdRef="NoteModuleID",
            modelIndex=index,
            path="note.fzp",
        )
        element(note, "title", heading)
        element(
            note,
            "text",
            f'<html><body style="font-family:Arial;font-size:9pt;"><b>{html.escape(heading)}</b><br/>{html.escape(text)}</body></html>',
        )
        v = element(element(note, "views"), "breadboardView", layer="breadboardNote")
        element(v, "geometry", x=x, y=y, width=width, height=height, z=6)

    data = xml_bytes(root)
    targets[0].write_bytes(data)
    members = {NAME + ".fz": data}
    for p in ps.values():
        members.update(p.members)
    members["LICENSE-CC-BY-SA-3.0.txt"] = (
        HERE / "sources/CC-BY-SA-3.0.txt"
    ).read_bytes()
    members["ATTRIBUTION.txt"] = (
        b"Board artwork: Adafruit. Resistor artwork: Fritzing / Brendan Howell. Battery illustration and arrangement: skate-judge contributors. CC BY-SA 3.0.\n"
    )
    archive(targets[1], members)
    preview(targets[2], ps, preview_wires, notes)
    return targets[1]


def preview(path, ps, wires, notes):
    """A portable SVG preview from the same coordinates, not an app export."""
    svg = ET.Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        viewBox="0 0 810 515",
        width="1620",
        height="1030",
    )
    element(svg, "title", "Skate Judge direct-LiPo editable wiring project preview")
    element(svg, "rect", width=810, height=515, fill="white")
    for p in ps.values():
        node = ET.fromstring(p.svg_bytes("breadboard"))
        x, y = p.positions["breadboard"]
        w, h = svg_size(node)
        vx, vy, vw, vh = map(float, node.get("viewBox").split())
        node.tag = "{http://www.w3.org/2000/svg}g"
        node.attrib.clear()
        node.set(
            "transform",
            f"translate({x},{y}) scale({w / vw},{h / vh}) translate({-vx},{-vy})",
        )
        # Prefix IDs so nested drawings cannot collide (e.g. connector0pin).
        for child in node.iter():
            if child.get("id"):
                child.set("id", p.name + "_" + child.get("id"))
        if p.name == "resistor":
            for child in node.iter():
                if child.get("id") in ("resistor_band_1_st", "resistor_band_2_nd"):
                    child.set("fill", "#f28b00")
        svg.append(node)
        lx, ly = p.label_position("breadboard")
        element(
            svg,
            "text",
            p.title,
            x=lx,
            y=ly,
            fill="#202020",
            **{"font-family": "Arial", "font-size": 11},
        )
    for title, color, points in wires:
        points_text = " ".join(f"{x},{y}" for x, y in points)
        element(
            svg,
            "polyline",
            points=points_text,
            fill="none",
            stroke="white",
            **{"stroke-width": 4.5, "stroke-linejoin": "round"},
        )
        line = element(
            svg,
            "polyline",
            points=points_text,
            fill="none",
            stroke=color,
            **{
                "stroke-width": 2,
                "stroke-linejoin": "round",
                "stroke-linecap": "round",
            },
        )
        element(line, "title", title)
    import textwrap

    for x, y, width, height, heading, text in notes:
        element(
            svg,
            "rect",
            x=x,
            y=y,
            width=width,
            height=height,
            rx=3,
            fill="#fff9db",
            stroke="#e1d8af",
        )
        element(
            svg,
            "text",
            heading,
            x=x + 9,
            y=y + 15,
            fill="#272722",
            **{"font-family": "Arial", "font-size": 10, "font-weight": "bold"},
        )
        for i, line in enumerate(textwrap.wrap(text, width=int((width - 18) / 4.7))):
            element(
                svg,
                "text",
                line,
                x=x + 9,
                y=y + 29 + i * 12,
                fill="#34342e",
                **{"font-family": "Arial", "font-size": 9},
            )
    path.write_bytes(xml_bytes(svg))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--import-from",
        type=Path,
        help="One-time vendoring of verified upstream downloads",
    )
    parser.add_argument("--output", type=Path, default=HERE)
    parser.add_argument(
        "--replace-generated",
        action="store_true",
        help="Deliberately replace generated artifacts; destroys hand edits to those files",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Also render a PNG with PyQt6; no Fritzing required",
    )
    args = parser.parse_args()
    if args.import_from:
        import_sources(args.import_from)
    result = build(args.output, args.replace_generated)
    if args.preview:
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtWidgets import QApplication

        app = QApplication([])
        renderer = QSvgRenderer(str(result.with_suffix(".svg")))
        image = QImage(1620, 1030, QImage.Format.Format_ARGB32)
        image.fill(0xFFFFFFFF)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, 1620, 1030))
        painter.end()
        if not image.save(str(result.with_suffix(".png"))):
            raise RuntimeError("Could not save preview")
        app.quit()
    print(result)


if __name__ == "__main__":
    main()
