#!/usr/bin/env python3
"""Check exported parts, lid rotation, electronics, switch and lid-insert mounts."""
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
    parser.add_argument("--check", choices=["lid_to_base", "electronics_to_base", "electronics_to_lid", "wired_led_insertion", "sample_lid_to_base", "sd_to_other_electronics", "sd_heads_to_components", "sd_card_removal", "sd_pilots", "sd_sample_mount", "imu_heads_to_components", "imu_pilots", "imu_to_led_wiring", "switch_opening_base", "switch_frame_base", "switch_opening_velcro", "switch_frame_velcro", "insert_bores_base", "insert_bosses_base", "lid_screw_path_base", "insert_bores_velcro", "insert_bosses_velcro", "lid_screw_path_velcro", "insert_coupon_bores", "insert_coupon_bosses"])
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
        # The lid is removed for card access; the SD socket intentionally contains
        # the card, so exclude it while checking the battery, posts and screw heads.
        "sd_card_removal": ('union() { '+base+' other_electronics(); sd_fastener_envelopes(); }',
                            'sd_card_access();'),
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
        [(x, y, 4.6) for x in [7, 101] for y in [7, 73]])
    # A 12 mm screw seated 0.2 mm below flush ends at Z=10.8. This also checks
    # shaft alignment through the physically rotated lid at every corner.
    screw_path = ('for(x=[7,101],y=[7,73]) '
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

    report = {"source_sha256": manifest["source_sha256"], "revision": 8,
              "lid_transform": "rotate X 180 degrees, then translate [0, case_width, body_height + lid_thickness]",
              "imu_assumptions": "PCB extends from screw row toward SD; components face lid; 4 mm posts; M2 heads <=4 mm diameter and <=2 mm tall; holes shifted 12.7 mm toward LEDs from R5",
              "sd_assumptions": "Adafruit 254; 1.6 mm PCB; M2 heads <=4 mm diameter and <=2 mm tall; direct-soldered wires; card removed with lid off",
              "switch_assumptions": "13.5 x 8.4 mm cutout on X=0, centered Y=50/Z=11.5, through 3 mm wall; measured body 13.27 x 8.18 mm; bezel, clips, insertion depth, terminals and wiring are not modeled",
              "insert_assumptions": "Jouth M3x4x5 confirmed by user as M3 thread, 4 mm long, 5 mm OD; vendor bore specification unavailable; flush in 4.6 mm bore, 5 mm deep; 0.2 mm entry bevel; 3.4 mm screw relief to 10 mm depth; coupon bores 4.4/4.6/4.8 mm; physical heat-set fit and retention unverified",
              "contact_face_offset_mm": 0.02, "checks": {}}
    with tempfile.TemporaryDirectory(prefix="skate-enclosure-fit-") as directory:
        directory = Path(directory)
        for name, (first, second) in checks.items():
            if args.check and name != args.check:
                continue
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
            report["checks"][name] = "no interference with stated fixtures"
            print(f"{name}: PASS", flush=True)
    if not args.lid and not args.check:
        (args.exports/"fit-validation.json").write_text(json.dumps(report,indent=2)+"\n")


if __name__ == "__main__":
    main()
