#!/usr/bin/env python3
"""Check real exported parts, a physical lid flip, and assumed wired-LED travel."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from build import HERE, inspect_stl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exports", type=Path, default=HERE/"exports")
    parser.add_argument("--openscad")
    parser.add_argument("--lid", type=Path, help="Override lid STL for regression checks")
    parser.add_argument("--check", choices=["lid_to_base", "electronics_to_base", "electronics_to_lid", "wired_led_insertion", "sample_lid_to_base"])
    args = parser.parse_args()
    executable = args.openscad or shutil.which("openscad") or "/Applications/OpenSCAD-2021.01.app/Contents/MacOS/OpenSCAD"
    source = HERE/"skate-judge.scad"
    manifest = json.loads((args.exports/"validation.json").read_text())
    if manifest["source_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
        parser.error("Exports were generated from a different source; rebuild first.")
    for name, part in manifest["parts"].items():
        if part["sha256"] != hashlib.sha256((args.exports/name).read_bytes()).hexdigest():
            parser.error(f"Export changed since its mesh validation: {name}")
    base = f'import({json.dumps(str((args.exports/"base.stl").resolve()))});'
    lid_path = args.lid or args.exports/"lid.stl"
    # Deliberately independent of assembled_lid(): this must remain a physical
    # rigid rotation of the EXPORTED part. A reflected preview hid the R2 fault.
    lid = ('translate([0,dimensions[1],dimensions[2]]) rotate([180,0,0]) '
           f'import({json.dumps(str(lid_path.resolve()))});')
    electronics = 'translate([0,0,0.02]) union() { electronics(); led_solder_envelopes(); }'
    sample_depth = manifest["parts"]["led-fit-base.stl"]["size_mm"][1]
    sample_base = f'import({json.dumps(str((args.exports/"led-fit-base.stl").resolve()))});'
    sample_lid = (f'translate([0,{sample_depth},dimensions[2]+0.02]) rotate([180,0,0]) '
                  f'import({json.dumps(str((args.exports/"led-fit-lid.stl").resolve()))});')
    checks = {
        "lid_to_base": (base, 'translate([0,0,0.02]) '+lid),
        "electronics_to_base": (base, electronics),
        "electronics_to_lid": (lid, electronics),
        "wired_led_insertion": (base, 'translate([0,0,0.02]) wired_led_insertion();'),
        "sample_lid_to_base": (sample_base, sample_lid),
    }
    report = {"source_sha256": manifest["source_sha256"], "revision": 3,
              "lid_transform": "rotate X 180 degrees, then translate [0, case_width, body_height + lid_thickness]",
              "contact_face_offset_mm": 0.02, "checks": {}}
    with tempfile.TemporaryDirectory(prefix="skate-enclosure-fit-") as directory:
        directory = Path(directory)
        for name, (first, second) in checks.items():
            if args.check and name != args.check:
                continue
            wrapper, output = directory/f"{name}.scad", directory/f"{name}.stl"
            wrapper.write_text(f'use <{source}>\ndimensions=enclosure_size();\n'
                               'union() {\n translate([-20,-20,0]) cube(1);\n'
                               f' intersection() {{ {first}\n {second}\n }}\n}}\n')
            run = subprocess.run([executable,"--export-format","asciistl","-o",str(output),str(wrapper)], capture_output=True, text=True)
            if run.returncode or "ERROR:" in run.stderr or "WARNING:" in run.stderr:
                raise RuntimeError(f"{name}: CGAL failed or found contact/interference:\n{run.stderr}")
            mesh = inspect_stl(output)
            # Empty intersection leaves only a known 1 mm witness cube. This
            # rejects volume collisions and stray contact surfaces alike.
            if mesh["bounds_mm"] != [[-20.0,-20.0,0.0],[-19.0,-19.0,1.0]]:
                raise RuntimeError(f"{name}: unexpected intersection: {mesh}")
            report["checks"][name] = "no interference with stated fixtures"
            print(f"{name}: PASS", flush=True)
    if not args.lid and not args.check:
        (args.exports/"fit-validation.json").write_text(json.dumps(report,indent=2)+"\n")


if __name__ == "__main__":
    main()
