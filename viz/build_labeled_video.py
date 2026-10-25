"""Prepare six-axis graph assets and provenance for an editable Resolve video.

Run with the project's NumPy/Pillow Python environment. Source labels are read-only.
The Resolve assembly is in resolve_labeled_video.py; graphics stay separate from video.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from capture.session import video_mapping
from viz.overlay_motion import G, MAX_GAP, latest_labels, load_samples

DEFAULT = ROOT / "sessions/edits/labeled-imu-five"
WIDTH, HEIGHT = 2160, 3840
FPS = 60000 / 1001
SOURCE_FPS = 120000 / 1001
FRAMES = 180
DOMAIN = (-1.05, 2.05)
PANEL = (1000, 470)
PLOT = (135, 100, 970, 385)
COLORS = ("#FF9677", "#58E1CB", "#BCABFF")
BG = "#0B1420"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def conform_graphics(output):
    """Use an explicit media clock; Resolve imports PNG stills with a 24 fps clock."""
    manifest=json.loads((output/"manifest.json").read_text())
    base=Path(manifest["base_manifest"]).parent if manifest.get("base_manifest") else None
    def encode(path):
        dest=path.with_suffix(".mov")
        if dest.exists() and dest.stat().st_mtime_ns >= path.stat().st_mtime_ns:
            return
        original=base/"assets"/path.name if base else None
        if original and original.exists() and original.with_suffix(".mov").exists() and digest(path)==digest(original):
            shutil.copyfile(original.with_suffix(".mov"),dest)
            print("REUSED",path.name,flush=True)
            return
        alpha=path.stem=="transparent"
        subprocess.run(["ffmpeg","-v","error","-nostdin","-y","-loop","1","-framerate","60000/1001","-i",str(path),
                        "-frames:v",str(FRAMES),"-an","-c:v","prores_ks","-profile:v","4" if alpha else "3",
                        "-pix_fmt","yuva444p10le" if alpha else "yuv422p10le","-threads","2",str(dest)],check=True)
        print("CONFORMED",path.name,flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(encode,sorted((output/"assets").glob("*.png"))))


def graph(times, values, axis, limit, output, overlaid=False):
    """Full-resolution signed raw traces; preserve every sample and break gaps."""
    panel_size = (2040, 690) if overlaid else PANEL
    plot_box = (170, 110, 1980, 585) if overlaid else PLOT
    im = Image.new("RGB", panel_size, "#122130")
    d = ImageDraw.Draw(im)
    font = lambda size: ImageFont.truetype(FONT, size)
    left, top, right, bottom = plot_box
    tick_size = 52 if overlaid else 37
    x = lambda t: left + (t - DOMAIN[0]) / (DOMAIN[1] - DOMAIN[0]) * (right - left)
    y = lambda v: (top + bottom) / 2 - v / limit * (bottom - top) / 2
    for v in [-limit, 0, limit]:
        d.line((left, y(v), right, y(v)), fill="#536577" if v == 0 else "#304353", width=2)
        d.text((left-18, y(v)), f"{v:+g}" if v else "0", font=font(tick_size), fill="#DCE7F0", anchor="rm")
    for t in [-1, -.5, 0, .5, 1, 1.5, 2]:
        d.line((x(t), top, x(t), bottom), fill="#29404F", width=2)
        d.text((x(t), bottom+25), f"{t:g}", font=font(tick_size), fill="#DCE7F0", anchor="mt")
    d.line((x(0), top, x(0), bottom), fill="#A9C27E", width=3)
    d.text((x(0), top-14), "POP", font=font(42 if overlaid else 31), fill="#D5EDB0", anchor="mb")
    if overlaid:
        for i, label in enumerate("XYZ"):
            lx = 1320+i*220
            d.line((lx, 50, lx+75, 50), fill=COLORS[i], width=8)
            d.text((lx+95, 50), label, font=font(58), fill=COLORS[i], anchor="lm")
    else:
        d.text((24, 20), axis, font=font(62), fill=COLORS["XYZ".index(axis)])
    active = (times >= DOMAIN[0]) & (times <= DOMAIN[1])
    t, v = times[active], values[active]
    assert len(t) > 100 and np.max(np.abs(v)) < limit
    cuts = np.r_[0, np.flatnonzero(np.diff(t) > MAX_GAP) + 1, len(t)]
    traces = [(v[:, i], COLORS[i]) for i in range(3)] if overlaid else [(v, COLORS["XYZ".index(axis)])]
    for trace, color in traces:
        for a, b in zip(cuts[:-1], cuts[1:]):
            pts = [(x(float(tt)), y(float(vv))) for tt, vv in zip(t[a:b], trace[a:b])]
            if len(pts) > 1:
                d.line(pts, fill=color, width=6 if overlaid else 5, joint="curve")
    im.save(output)


def prepare(output, layout="separate", selection=None):
    for name in ["assets", "data", "template"]:
        (output/name).mkdir(parents=True, exist_ok=True)
    makes_path = ROOT / "sessions/edits/kickflips-16-plus1s-assets/alignment.json"
    misses_path = ROOT / "sessions/edits/falls-imu-sync/alignment-manifest.json"
    makes = json.loads(makes_path.read_text())
    misses = json.loads(misses_path.read_text())
    config=json.loads(selection.read_text()) if selection else {}
    ids = config.get("label_ids",["66fa0a5b", "3153f62c", "e0ac78db", "0e7ee429", "6e996c79"])
    candidates = makes + misses
    selected = [next(r for r in candidates if r["id"].startswith(id_)) for id_ in ids]
    assert len({r['id'] for r in selected})==len(selected), "Attempts must be unique."
    cache, clips, arrays = {}, [], []
    for i, row in enumerate(selected, 1):
        session = ROOT / "sessions" / row["session"]
        label = next(r for r in latest_labels(session, row["video"]) if r["id"] == row["id"])
        assert (label["trick"], label["outcome"]) == (row["trick"], row["outcome"])
        scale, offset, points = video_mapping(session, row["video"])
        assert math.isclose(scale, row["mapping"]["scale"], abs_tol=1e-12)
        assert math.isclose(offset, row["mapping"]["offset_s"], abs_tol=1e-9)
        if row["session"] not in cache:
            cache[row["session"]] = load_samples(session / "samples.csv")
        samples = cache[row["session"]]
        raw_video = (samples[:, 0] - offset) / scale
        correction = row.get("clock_correction_model") or {"slope": 0, "intercept": row["alignment_correction_s"]}
        camera = raw_video * (1 + correction["slope"]) + correction["intercept"]
        pop = row["alignment_video_s"]
        expected_pop = (row["imu_pop_sensor_s"]-offset)/scale
        expected_pop = expected_pop*(1+correction["slope"])+correction["intercept"]
        assert abs(expected_pop-pop) < 1e-8
        first = round((pop - 1) * FPS) * 2
        start = first/SOURCE_FPS
        end = start + FRAMES/FPS
        m = (camera >= pop+DOMAIN[0]-.02) & (camera <= pop+DOMAIN[1]+.02)
        local, times = samples[m], camera[m]-pop
        assert camera[0] < start and camera[-1] > end
        max_gap = float(np.max(np.diff(local[:, 0])))
        assert max_gap < MAX_GAP, (row["id"], max_gap)
        readings = np.column_stack((local[:, 1:4]/G, local[:, 4:7]))
        arrays.append((times, readings))
        source = session / row["video"]
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", str(source)]))["streams"][0]
        assert probe["r_frame_rate"] == "120000/1001" and (probe["width"], probe["height"]) == (1920,1080)
        csv_path = output/"data"/f"{i:02d}-{row['id'][:8]}.csv"
        with csv_path.open("w") as f:
            writer = csv.writer(f)
            writer.writerow(["sensor_s", "camera_s", "seconds_from_pop", "ax_g", "ay_g", "az_g", "gx_rad_s", "gy_rad_s", "gz_rad_s"])
            writer.writerows(np.column_stack((local[:,0],camera[m],times,readings)))
        clips.append(dict(number=i, label=label, session=row["session"], source=str(source), source_first_frame=first,
                          source_end_frame_exclusive=first+FRAMES*2, camera_start_s=start, camera_end_s=end,
                          pop_camera_s=pop, timeline_frames=FRAMES, record_frame=(i-1)*FRAMES,
                          mapping=dict(scale=scale,offset_s=offset,points=points), correction=correction,
                          csv=str(csv_path), max_sample_gap_ms=max_gap*1000,
                          source_bytes=source.stat().st_size, source_mtime_ns=source.stat().st_mtime_ns,
                          provenance={name:digest(session/name) for name in ["labels.jsonl","video_sync.jsonl","samples.csv"]}))
        if config.get("presentation") == "clean-impact":
            impact = config["impact_markers"][label["id"]]
            saved_contact = label["review_interval"]["end_s"] + label.get("source_pts_origin_s", 0)
            assert math.isclose(impact["camera_s"], saved_contact, abs_tol=1e-9)
            assert math.isclose(impact["seconds_from_pop"], saved_contact-pop, abs_tol=1e-9)
            assert DOMAIN[0] < impact["seconds_from_pop"] < DOMAIN[1]
            clips[-1]["board_impact"] = impact
    maxima = np.max([np.max(np.abs(a),axis=0) for t,a in arrays],axis=0)
    accel_limit = math.ceil((max(maxima[:3])+1)/5)*5
    gyro_limit = math.ceil((max(maxima[3:])+1)/5)*5
    panels = []
    for sensor in range(2):
        if layout=="overlaid":
            panels.append(dict(channel=sensor, channels=list(range(sensor*3,sensor*3+3)), axis="XYZ",
                               sensor=["acceleration","gyro"][sensor], unit=["g","rad/s"][sensor],
                               limit=[accel_limit,gyro_limit][sensor], x=60, y=[1930,2820][sensor],
                               width=2040, height=690, heading_x=1080, heading_y=[1830,2720][sensor]))
        else:
            for axis in range(3):
                panels.append(dict(channel=sensor*3+axis, axis="XYZ"[axis], sensor=["acceleration","gyro"][sensor],
                                   unit=["g","rad/s"][sensor], limit=[accel_limit,gyro_limit][sensor],
                                   x=[60,1100][sensor], y=1990+axis*505, width=PANEL[0], height=PANEL[1]))
    for clip,(times,readings) in zip(clips,arrays):
        clip["panels"] = []
        for panel in panels:
            dest = output/"assets"/f"{clip['number']:02d}-{panel['sensor']}-{panel['axis']}.png"
            channels=panel["channels"] if layout=="overlaid" else panel["channel"]
            graph(times,readings[:,channels],panel["axis"],panel["limit"],dest,overlaid=layout=="overlaid")
            clip["panels"].append(panel | dict(file=str(dest)))
    Image.new("RGB", (WIDTH,HEIGHT),BG).save(output/"assets/background.png")
    Image.new("RGBA", (WIDTH,HEIGHT),(0,0,0,0)).save(output/"assets/transparent.png")
    manifest = dict(width=WIDTH,height=HEIGHT,fps=FPS,fps_setting="59.94",source_fps=SOURCE_FPS,total_frames=FRAMES*len(clips),
                    graph_domain=DOMAIN,plot_box=(170,110,1980,585) if layout=="overlaid" else PLOT,
                    layout=layout,axis_colors=dict(zip("XYZ",COLORS)),
                    graph_units="Acceleration in g (gravity retained); gyro in rad/s; raw sensor axes.",
                    source_labels_modified=False,comparison_scales=dict(acceleration_g=accel_limit,gyro_rad_s=gyro_limit),
                    selection_counts=dict(Counter(c["label"]["outcome"] for c in clips)),clips=clips)
    for key in ["base_manifest","base_timeline","random_selection","presentation","title_font_px","impact_markers"]:
        if key in config:
            manifest[key]=config[key]
    if config.get("base_manifest"):
        base=json.loads(Path(config["base_manifest"]).read_text())
        assert base["comparison_scales"]==manifest["comparison_scales"], "Existing graph scales must cover the new attempts."
        for old,new in zip(base["clips"],clips):
            for key in ["label","source_first_frame","source_end_frame_exclusive","camera_start_s","camera_end_s","pop_camera_s","mapping","correction","provenance"]:
                assert old[key]==new[key], (key,old['number'])
    save_json(output/"manifest.json",manifest)
    print(json.dumps(dict(output=str(output),clips=len(clips),frames=manifest["total_frames"],limits=manifest["comparison_scales"])),flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=DEFAULT)
    parser.add_argument("--conform-only",action="store_true")
    parser.add_argument("--layout",choices=["separate","overlaid"],default="separate")
    parser.add_argument("--selection",type=Path,help="JSON containing ordered label_ids and optional selection provenance")
    args=parser.parse_args()
    if args.conform_only:
        conform_graphics(args.output.resolve())
    else:
        prepare(args.output.resolve(),args.layout,args.selection)
        conform_graphics(args.output.resolve())
