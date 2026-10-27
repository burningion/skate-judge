#!/usr/bin/env python3
"""Check exported parts, lid rotation, electronics, switch and lid-insert mounts."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from build import inspect_stl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exports", type=Path, default=HERE/"exports")
    parser.add_argument("--openscad")
    parser.add_argument("--lid", type=Path, help="Override lid STL for regression checks")
    parser.add_argument("--check", help="Run one named fit check")
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
    electronics = ('translate([0,0,0.02]) union() { electronics(); led_solder_envelopes(); '
                   'sd_fastener_envelopes(); sd_solder_envelope(); imu_fastener_envelopes(); }')
    sd_sample = ('translate(sd_sample_origin()) '
                 f'import({json.dumps(str((args.exports/"sd-fit-base.stl").resolve()))});')
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
        "sd_to_other_electronics": ('union() { other_electronics(); imu_fastener_envelopes(); }',
                                    'union() { sd_electronics(); sd_solder_envelope(); sd_fastener_envelopes(); }'),
        "sd_heads_to_components": ('sd_electronics();', 'sd_fastener_envelopes();'),
        # Reserve two millimeters above the battery anchor for its strap.
        # The XTSD board now sits beside it, with no vertical stacking.
        "sd_strap_clearance": ('union() { sd_electronics(); sd_solder_envelope(); }',
                              'translate([64,50,8.02]) cube([6,8,1.98]);'),
        "sd_pilots": ('union() { '+base+sd_sample+' }', 'sd_pilot_clearance();'),
        "sd_sample_mount": (sd_sample, 'translate([0,0,0.02]) sd_electronics();'),
        "imu_heads_to_components": ('imu_electronics();', 'imu_fastener_envelopes();'),
        "imu_pilots": (base, 'imu_pilot_clearance();'),
        "imu_to_led_wiring": ('union() { imu_electronics(); imu_fastener_envelopes(); }',
                              'wired_led_insertion();'),
    }
    # Independent default-size fixtures: 13.5 x 8.4 mm on X=0, centered at
    # Y=50, Z=11.5. A 0.01 mm tolerance avoids coincident faces. Check both
    # the clear through-hole and surrounding material, so an oversized opening
    # or an open-to-rim notch cannot pass merely by containing the clear probe.
    switch_opening = 'translate([-0.02,43.26,7.31]) cube([3.04,13.48,8.38]);'
    switch_frame = ('difference() { translate([0.05,43.05,7.10]) cube([2.9,13.9,8.8]); '
                    'translate([0,43.24,7.29]) cube([3,13.52,8.42]); }')
    for variant, filename in [("base", "base.stl"), ("velcro", "base-velcro.stl")]:
        shell = f'import({json.dumps(str((args.exports/filename).resolve()))});'
        checks[f"switch_opening_{variant}"] = ('union() { '+shell+lid+' }', switch_opening)
        checks[f"switch_frame_{variant}"] = (f'difference() {{ {switch_frame} {shell} }}', switch_frame)

    # Independent R8 fixtures, not calls to the CAD's cutting module. Check the
    # stepped void AND required solid material, catching undersized/oversized
    # holes, wrong depth, a broken floor, and any surviving side nut channels.
    # The manufactured insert intentionally displaces plastic, so testing its
    # 5 mm OD against an unheated 4.6 mm bore would be a false collision.
    def insert_fixtures(positions):
        bores, bosses = [], []
        for x, y, diameter in positions:
            bores.append(f'''translate([{x},{y},0]) union() {{
                translate([0,0,15.02]) cylinder(d={diameter-0.04},h=5);
                translate([0,0,10.02]) cylinder(d=3.36,h=5.02);
                translate([0,0,19.8]) cylinder(d1={diameter-0.04},d2={diameter+0.36},h=0.2);
            }}''')
            bosses.append(f'''translate([{x},{y},0]) difference() {{
                translate([0,0,0.02]) cylinder(r=5.8,h=19.96);
                translate([0,0,9.98]) cylinder(d=3.44,h=10.04);
                translate([0,0,14.98]) cylinder(d={diameter+0.04},h=5.04);
                translate([0,0,19.78]) cylinder(d1={diameter+0.04},d2={diameter+0.52},h=0.24);
            }}''')
        return ('union() { '+''.join(bores)+' }', 'union() { '+''.join(bosses)+' }')

    insert_bores, insert_bosses = insert_fixtures(
        [(x, y, 4.6) for x in [7, 91] for y in [7, 68]])
    # A 12 mm screw seated 0.2 mm below flush ends at Z=10.8. This also checks
    # shaft alignment through the physically rotated lid at every corner.
    screw_path = ('for(x=[7,91],y=[7,68]) '
                  'translate([x,y,10.8]) cylinder(d=3,h=12.4,$fn=48);')
    for variant, filename in [("base", "base.stl"), ("velcro", "base-velcro.stl")]:
        shell = f'import({json.dumps(str((args.exports/filename).resolve()))});'
        checks[f"insert_bores_{variant}"] = (shell, insert_bores)
        checks[f"insert_bosses_{variant}"] = (f'difference() {{ {insert_bosses} {shell} }}', insert_bosses)
        checks[f"lid_screw_path_{variant}"] = ('union() { '+shell+lid+' }', screw_path)
    coupon = f'import({json.dumps(str((args.exports/"fit-coupon.stl").resolve()))});'
    coupon_bores, coupon_bosses = insert_fixtures([(8, 9, 4.4), (23, 9, 4.6), (38, 9, 4.8)])
    checks["insert_coupon_bores"] = (coupon, coupon_bores)
    checks["insert_coupon_bosses"] = (f'difference() {{ {coupon_bosses} {coupon} }}', coupon_bosses)

    # Independent R9 material fixtures: all four 5.6 mm bodies, 8 mm tapered
    # roots, original 4 mm seating height and 0.4 mm blind pilot bottoms.
    # A collision-only check would also pass with missing or old thin posts.
    feather_centers = '[[10.54,15.34],[10.54,33.12],[56.26,14.6415],[56.26,33.755]]'
    feather_posts = f'''for(p={feather_centers}) translate([p[0],p[1],0]) difference() {{
        union() {{
            translate([0,0,3.02]) cylinder(d=5.5,h=3.96);
            translate([0,0,3.02]) cylinder(d1=7.9,d2=5.5,h=1.96);
        }}
        translate([0,0,3.38]) cylinder(d=1.76,h=3.7);
    }}'''
    feather_pilots = (f'for(p={feather_centers}) translate([p[0],p[1],3.42]) '
                      'cylinder(d=1.66,h=3.6);')
    # Nearest header pads from the Adafruit 8MB/no-PSRAM Eagle file. The
    # solder envelope (2 mm diameter, 1.5 mm below PCB) is an assumption,
    # not a measurement of the installed wires; the narrowest gap is 0.05 mm.
    feather_solder = ('for(p=[[14.35,14.07],[52.45,14.07],[52.45,34.39]]) '
                      'translate([p[0],p[1],5.5]) cylinder(d=2,h=1.5);')
    for variant, filename in [("base", "base.stl"), ("velcro", "base-velcro.stl")]:
        shell = f'import({json.dumps(str((args.exports/filename).resolve()))});'
        checks[f"feather_posts_{variant}"] = (f'difference() {{ {feather_posts} {shell} }}', feather_posts)
        checks[f"feather_pilots_{variant}"] = (shell, feather_pilots)
        checks[f"feather_solder_{variant}"] = (shell, feather_solder)

    # Independently enforce the new two-hole 16.51 mm mount, not just absence
    # of collisions (which could also pass with missing posts).
    sd_centers = '[[73.54,42.54],[90.05,42.54]]'
    sd_posts = f'''for(p={sd_centers}) translate([p[0],p[1],0]) difference() {{
        translate([0,0,3.02]) cylinder(d=5.5,h=3.96);
        translate([0,0,3.38]) cylinder(d=1.76,h=3.7);
    }}'''
    checks["xtsd_posts"] = (f'difference() {{ {sd_posts} {base} }}', sd_posts)
    checks["xtsd_independent_pilots"] = (base,
        f'for(p={sd_centers}) translate([p[0],p[1],3.42]) cylinder(d=1.66,h=3.6);')
    # Ensure the exported carrier also fits after a physical 90 degree rotation.
    carrier = ('translate([32.3,5.5,3.52]) rotate([90,0,0]) '
               f'import({json.dumps(str((args.exports/"led-carrier.stl").resolve()))});')
    checks["exported_carrier_fit"] = ('union() { '+base+lid+' }', carrier)
    # Conservative assumed left-port plug plus turn toward the battery-side
    # cable corridor. The exact cable must still be checked physically.
    imu_cable = 'translate([59.5,20.3,8.7]) cube([7.48,17.7,4]);'
    checks["imu_cable_route"] = ('union() { '+base+lid+
        ' electronics(); sd_solder_envelope(); sd_fastener_envelopes(); }', imu_cable)
    report = {"source_sha256": manifest["source_sha256"], "revision": 10,
              "lid_transform": "rotate X 180 degrees, then translate [0, 75, 23]",
              "assumptions": [
                  "36 x 29 x 5 mm wrapped battery; 0.8 mm pad; 2 mm strap envelope at right anchor",
                  "WS2812B strip: two LEDs; estimated 12.7 mm pitch; assumed 25.4 x 10 mm cut segment; 0.7 mm flex+adhesive; 2 mm LEDs",
                  "Adafruit 4899 official Eagle outline and holes; simplified component envelopes; direct solder, no plug-in headers",
                  "Switch body opening only: bezel, terminals, insertion depth and leads must be physically checked",
                  "Existing M3x4x5 inserts and M2x5 screws; heat-set retention, printability and impact strength unverified",
                  "IMU left-port plug and cable turn reserved at X=59.5..67, Y=20.3..38, Z=8.7..12.7; actual cable dimensions unverified",
                  "Feather solder <=2 mm diameter and <=1.5 mm below PCB; R9 reinforced post shape retained"
              ], "contact_face_offset_mm": 0.02, "checks": {}}
    with tempfile.TemporaryDirectory(prefix="skate-enclosure-fit-") as directory:
        directory = Path(directory)
        if args.check and args.check not in checks:
            parser.error(f"Unknown check: {args.check}")
        def run_check(name, fixtures):
            first, second = fixtures
            wrapper, output = directory/f"{name}.scad", directory/f"{name}.stl"
            wrapper.write_text(f'use <{source}>\n$fn=48;\ndimensions=enclosure_size();\n'
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
            return name
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = [pool.submit(run_check, name, fixtures)
                       for name, fixtures in checks.items() if not args.check or name == args.check]
            failures = []
            for future in as_completed(pending):
                try:
                    name = future.result()
                except Exception as exc:
                    failures.append(str(exc))
                    print(f"FAIL: {exc}", flush=True)
                else:
                    report["checks"][name] = "no interference with stated fixtures"
                    print(f"{name}: PASS", flush=True)
            if failures:
                raise RuntimeError("Fit checks failed:\n" + "\n".join(failures))
        report["checks"] = dict(sorted(report["checks"].items()))
    if not args.lid and not args.check:
        (args.exports/"fit-validation.json").write_text(json.dumps(report,indent=2)+"\n")


if __name__ == "__main__":
    main()
