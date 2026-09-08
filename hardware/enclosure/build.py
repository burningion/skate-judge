#!/usr/bin/env python3
"""Export the default OpenSCAD parts and check their STL topology. Stdlib only."""
import argparse
from collections import Counter, defaultdict
import json
import hashlib
import math
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def inspect_stl(path):
    """Check closed, consistently oriented, connected triangle surfaces."""
    vertices = [tuple(float(v) for v in match) for match in re.findall(
        r"vertex\s+([-+\deE.]+)\s+([-+\deE.]+)\s+([-+\deE.]+)", path.read_text()
    )]
    if not vertices or len(vertices) % 3 or not all(math.isfinite(v) for p in vertices for v in p):
        raise ValueError(f"Invalid ASCII STL: {path}")
    edges, directed = Counter(), Counter()
    adjacency = defaultdict(set)
    volume = 0.0
    for i in range(0, len(vertices), 3):
        a, b, c = vertices[i:i+3]
        if len({a, b, c}) != 3:
            raise ValueError(f"Degenerate triangle in {path}")
        volume += (a[0]*(b[1]*c[2]-b[2]*c[1]) + a[1]*(b[2]*c[0]-b[0]*c[2]) + a[2]*(b[0]*c[1]-b[1]*c[0]))/6
        for u, v in [(a,b), (b,c), (c,a)]:
            edges[tuple(sorted((u,v)))] += 1
            directed[(u,v)] += 1
            adjacency[u].add(v)
            adjacency[v].add(u)
    if any(n != 2 for n in edges.values()) or any(directed[(u,v)] != directed[(v,u)] for u,v in directed):
        raise ValueError(f"STL has open/nonmanifold/inconsistently oriented edges: {path}")
    remaining, components = set(adjacency), 0
    while remaining:
        components += 1
        pending = [remaining.pop()]
        while pending:
            new = adjacency[pending.pop()] & remaining
            remaining -= new
            pending.extend(new)
    if components != 1 or volume <= 0:
        raise ValueError(f"Expected one positive-volume connected part: {path} ({components=}, {volume=})")
    low = [min(p[i] for p in vertices) for i in range(3)]
    high = [max(p[i] for p in vertices) for i in range(3)]
    if abs(low[2]) > 0.001:
        raise ValueError(f"Part does not sit on z=0: {path}")
    return {"triangles": len(vertices)//3, "closed": True, "components": components,
            "bounds_mm": [low, high], "size_mm": [round(b-a,3) for a,b in zip(low,high)],
            "solid_volume_cm3": round(volume/1000,3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openscad", help="Path to OpenSCAD 2021.01 or newer")
    parser.add_argument("--output", type=Path, default=HERE/"exports")
    parser.add_argument("--replace", action="store_true", help="Replace generated output files")
    args = parser.parse_args()
    executable = args.openscad or shutil.which("openscad")
    if not executable:
        for candidate in ["/Applications/OpenSCAD-2021.01.app/Contents/MacOS/OpenSCAD", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"]:
            if Path(candidate).exists():
                executable = candidate
                break
    if not executable:
        parser.error("OpenSCAD not found; pass --openscad /path/to/openscad")
    version = subprocess.run([executable,"--version"], text=True, capture_output=True, check=True)
    version_text = (version.stdout+version.stderr).strip()
    match = re.search(r"version (\d{4})",version_text)
    if not match or int(match[1]) < 2021:
        parser.error(f"Requires OpenSCAD 2021.01 or newer; found {version_text}")
    targets = [("base", "base", True), ("base-velcro", "base", False),
               ("lid", "lid", True), ("fit-coupon", "fit_coupon", True),
               ("led-fit-base", "led_fit_base", False), ("led-fit-lid", "led_fit_lid", False)]
    outputs = [args.output/f"{name}.stl" for name,_,_ in targets] + [args.output/"validation.json"]
    if not args.replace and any(p.exists() for p in outputs):
        parser.error("Output exists. Choose --output /new/directory, or --replace for generated files.")
    args.output.mkdir(parents=True, exist_ok=True)
    # Fit checks apply to a particular export set and must be rerun after a build.
    (args.output/"fit-validation.json").unlink(missing_ok=True)
    report = {"openscad":version_text, "source":"../skate-judge.scad",
              "source_sha256":hashlib.sha256((HERE/"skate-judge.scad").read_bytes()).hexdigest(), "parts":{}}
    for name,part,ears in targets:
        destination = args.output/f"{name}.stl"
        run = subprocess.run([executable,"--export-format","asciistl","-o",str(destination),
                              "-D",f'part="{part}"',"-D",f'mount_ears={str(ears).lower()}',
                              str(HERE/"skate-judge.scad")], text=True, capture_output=True)
        if run.returncode or "ERROR:" in run.stderr or "WARNING:" in run.stderr:
            raise RuntimeError(f"OpenSCAD failed for {name}:\n{run.stdout}\n{run.stderr}")
        report["parts"][destination.name] = inspect_stl(destination)
        report["parts"][destination.name]["sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
        print(f"{destination.name}: closed, single connected part, {report['parts'][destination.name]['size_mm']} mm", flush=True)
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n")


if __name__ == "__main__":
    main()
