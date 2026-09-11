#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26,<3", "Pillow>=10,<13"]
# ///
"""Render saved attempts as transparent ProRes 4444 overlays, entirely offline.

    uv run viz/render_overlay.py sessions/a7s-001 --video C0642.MP4
    uv run viz/render_overlay.py sessions/a7s-001 --video C0642.MP4 --speed .25

The skateboard rotates from recorded IMU data; its center stays fixed. Output
includes alpha MOVs, PNG previews, and a timing manifest. Use --reel for a compilation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from capture.session import video_mapping  # noqa: E402
from viz.overlay_motion import (  # noqa: E402
    MAX_GAP,
    check_alignment,
    estimate_attitude,
    infer_mount,
    interpolate,
    latest_labels,
    load_samples,
    mount_matrix,
)

WHITE = (240, 249, 246, 255)
MUTED = (171, 198, 190, 255)
LIME = (202, 249, 128, 255)
PURPLE = (192, 170, 255, 255)
CYAN = (119, 225, 232, 255)
GOLD = (255, 204, 123, 255)
CORAL = (255, 165, 130, 255)


def unit(v):
    return np.asarray(v) / np.linalg.norm(v)


def skateboard_mesh():
    """A real 3D deck, maple plies, trucks, bushings, and four urethane wheels."""
    faces = []

    def face(points, color):
        faces.append((np.array(points, float), np.array(color[:3], float)))

    def box(center, size, color):
        c, s = np.array(center), np.array(size) / 2
        v = np.array(
            [
                c + s * [x, y, z]
                for x, y, z in [
                    (-1, -1, -1),
                    (1, -1, -1),
                    (1, 1, -1),
                    (-1, 1, -1),
                    (-1, -1, 1),
                    (1, -1, 1),
                    (1, 1, 1),
                    (-1, 1, 1),
                ]
            ]
        )
        for ix in [
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        ]:
            face(v[list(ix)], color)

    def cylinder(center, radius, length, axis, color, n=20):
        c = np.array(center)
        u = np.eye(3)[(axis + 1) % 3]
        v = np.eye(3)[(axis + 2) % 3]
        a = np.eye(3)[axis] * length / 2
        rings = [
            [
                c + sign * a + radius * (u * math.cos(t) + v * math.sin(t))
                for t in np.linspace(0, 2 * math.pi, n, endpoint=False)
            ]
            for sign in [-1, 1]
        ]
        face(rings[0][::-1], color)
        face(rings[1], color)
        for i in range(n):
            j = (i + 1) % n
            face([rings[0][i], rings[0][j], rings[1][j], rings[1][i]], color)

    xs = np.linspace(-0.395, 0.395, 35)

    def point(x, fraction, layer=0):
        width = 0.105 * math.sqrt(max(0.001, 1 - (max(abs(x) - 0.285, 0) / 0.112) ** 2))
        return [
            x,
            width * fraction,
            0.042 * (max(abs(x) - 0.255, 0) / 0.14) ** 2
            + 0.004 * fraction * fraction
            + layer,
        ]

    for a, b in zip(xs[:-1], xs[1:]):
        for u, v in zip(np.linspace(-1, 1, 7)[:-1], np.linspace(-1, 1, 7)[1:]):
            face([point(a, u), point(b, u), point(b, v), point(a, v)], (46, 65, 65))
            # Bright underside makes the kickflip's full turn easy to read.
            color = (186, 233, 114) if not -0.055 < a < 0.025 else (32, 66, 60)
            face(
                [
                    point(a, u, -0.011),
                    point(a, v, -0.011),
                    point(b, v, -0.011),
                    point(b, u, -0.011),
                ],
                color,
            )
        for side in [-1, 1]:
            for k in range(5):
                z1, z2 = -0.0022 * k, -0.0022 * (k + 1)
                color = (211, 165, 111) if k % 2 else (245, 207, 153)
                face(
                    [
                        point(a, side, z1),
                        point(a, side, z2),
                        point(b, side, z2),
                        point(b, side, z1),
                    ],
                    color,
                )
    # A narrow nose stripe and mounting bolts provide a visible orientation cue.
    for a, b in [(0.297, 0.307), (0.315, 0.319)]:
        face(
            [
                point(a, -0.9, 0.0007),
                point(b, -0.9, 0.0007),
                point(b, 0.9, 0.0007),
                point(a, 0.9, 0.0007),
            ],
            (198, 240, 128),
        )
    for x in [-0.245, 0.245]:
        box([x, 0, -0.016], [0.055, 0.05, 0.012], (141, 168, 176))
        cylinder([x, 0, -0.04], 0.014, 0.035, 2, (202, 156, 83), 12)
        cylinder([x, 0, -0.063], 0.01, 0.215, 1, (179, 200, 207), 12)
        box([x, 0, -0.054], [0.027, 0.139, 0.02], (173, 196, 199))
        for y in [-0.098, 0.098]:
            cylinder([x, y, -0.064], 0.03, 0.027, 1, (235, 220, 179), 22)
            outer = y + math.copysign(0.014, y)
            cylinder([x, outer, -0.064], 0.013, 0.001, 1, (174, 126, 76), 16)
            cylinder(
                [x, outer + math.copysign(0.001, y), -0.064],
                0.006,
                0.003,
                1,
                (115, 139, 148),
                12,
            )
        for dx in [-0.016, 0.016]:
            for y in [-0.023, 0.023]:
                cylinder([x + dx, y, 0.001], 0.0035, 0.001, 2, (166, 183, 182), 8)
    return faces


def find_font(bold=False, override=None):
    if override:
        return str(override)
    candidates = (
        [
            "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        if bold
        else [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for path in candidates:
        if Path(path).exists():
            return path
    raise ValueError("No suitable font found. Pass --font /path/to/font.ttf.")


def clock(t):
    return f"{int(t//60)}:{t%60:06.3f}"


class Renderer:
    def __init__(self, samples, attitudes, audio, mapping, mount, args):
        self.samples, self.attitudes, self.audio = samples, attitudes, audio
        self.scale, self.offset, self.origin = mapping
        self.mount, self.args = mount, args
        self.mesh = skateboard_mesh()
        self.camera = np.array([0.95, -2.7, 1.65])
        forward = unit(-self.camera)
        right = unit(np.cross(forward, [0, 0, 1]))
        up = np.cross(right, forward)
        self.view = np.array([right, up, forward])
        self.light = unit([-0.3, -0.5, 1.0])
        self.fonts = {}
        self.aa = args.antialias
        self.regular, self.bold = (
            find_font(override=args.font),
            find_font(True, args.font),
        )
        self.sensor_video = (samples[:, 0] - self.offset) / self.scale - self.origin
        self.accel = np.linalg.norm(samples[:, 1:4], axis=1)
        self.gyro = np.linalg.norm(samples[:, 4:7], axis=1)
        # Shared scales across clips make attempts comparable. Include all data
        # in the selected export interval; never silently clip an impact.
        self.limits = [
            max(1.0, math.ceil(float(np.max(audio["strength"])) * 2) / 2),
            max(25, math.ceil(max(self.accel) / 25) * 25),
            max(1, math.ceil(max(self.gyro) / 5) * 5),
        ]

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(
                self.bold if bold else self.regular, int(size * self.aa)
            )
        return self.fonts[key]

    def text(self, pos, value, size=20, color=WHITE, bold=False, anchor=None):
        self.draw.text(
            tuple(v * self.aa for v in pos),
            value,
            font=self.font(size, bold),
            fill=color,
            anchor=anchor,
            stroke_width=self.aa,
            stroke_fill=(10, 22, 24, 210),
        )

    def line(self, points, color, width=1):
        if len(points) > 1:
            self.draw.line(
                [(x * self.aa, y * self.aa) for x, y in points],
                fill=color,
                width=max(1, int(width * self.aa)),
                joint="curve",
            )

    def rect(self, box, color, radius=0, outline=None):
        self.draw.rounded_rectangle(
            tuple(v * self.aa for v in box),
            radius=radius * self.aa,
            fill=color,
            outline=outline,
            width=self.aa,
        )

    def board(self, attitude):
        polygons = []
        portrait = self.args.height > self.args.width
        center = np.array([540, 340] if portrait else [405, 322])
        bounds = (60, 154, 1020, 526) if portrait else (90, 172, 720, 460)
        focal = 1800 if portrait else 2050
        for points, color in self.mesh:
            world = points @ attitude.T
            camera = (world - self.camera) @ self.view.T
            xy = camera[:, :2] / camera[:, 2, None]
            screen = np.column_stack([xy[:, 0], -xy[:, 1]])
            normal = np.cross(world[1] - world[0], world[2] - world[0])
            norm = np.linalg.norm(normal)
            shade = (
                0.48 + 0.52 * abs(np.dot(normal / norm, self.light))
                if norm > 1e-10
                else 0.7
            )
            rgb = tuple(int(v) for v in np.clip(color * shade, 0, 255)) + (255,)
            polygons.append((np.mean(camera[:, 2]), screen, rgb))
        # Keep every pose clear of captions. Presentation zoom may pull back
        # for a tall pose; the measured orientation and fixed center are intact.
        vertices = np.concatenate([row[1] for row in polygons])
        low, high = vertices.min(axis=0), vertices.max(axis=0)
        for axis in range(2):
            if low[axis] < 0:
                focal = min(focal, (center[axis] - bounds[axis]) / -low[axis])
            if high[axis] > 0:
                focal = min(focal, (bounds[axis + 2] - center[axis]) / high[axis])
        for _, points, color in sorted(polygons, key=lambda item: -item[0]):
            self.draw.polygon(
                [tuple((p * focal + center) * self.aa) for p in points], fill=color
            )

    def trace(self, times, values, lo, hi, bounds, limit, color, now, gap=None):
        left, top, right, bottom = bounds
        ix = np.flatnonzero((times >= lo) & (times <= hi))
        if not len(ix):
            return
        # Preserve min/max transients in each display column instead of sampling
        # every Nth input value (which could discard a landing impulse).
        x = left + (times[ix] - lo) / (hi - lo) * (right - left)
        columns = np.floor(x).astype(int)
        chunks = np.split(np.arange(len(ix)), np.flatnonzero(np.diff(columns)) + 1)
        kept = []
        for chunk in chunks:
            indexes = ix[chunk]
            kept.extend(
                sorted(
                    {
                        int(indexes[0]),
                        int(indexes[-1]),
                        int(indexes[np.argmin(values[indexes])]),
                        int(indexes[np.argmax(values[indexes])]),
                    }
                )
            )
        path = []
        previous = None
        for i in kept:
            t = times[i]
            if (
                previous is not None
                and gap
                and np.any(np.diff(times[previous : i + 1]) > gap)
            ):
                self.line(path, color, 2)
                path = []
            px = left + (t - lo) / (hi - lo) * (right - left)
            py = bottom - values[i] / limit * (bottom - top)
            path.append((px, py))
            previous = i
        self.line(path, (*color[:3], 110), 2)
        past = [
            p for p in path if p[0] <= left + (now - lo) / (hi - lo) * (right - left)
        ]
        # Redraw past data with full opacity. Keep gaps when the input is sparse.
        if not gap or not np.any(np.diff(times[ix]) > gap):
            self.line(past, color, 2.2)

    def render(self, video_time, segment, heading):
        portrait = self.args.height > self.args.width
        canvas_width, canvas_height = (1080, 1080) if portrait else (1920, 540)
        self.layer = Image.new(
            "RGBA", (canvas_width * self.aa, canvas_height * self.aa), (0, 0, 0, 0)
        )
        self.draw = ImageDraw.Draw(self.layer)
        label = segment.get("label")
        title = label.get("trick", "ATTEMPT").upper() if label else "SKATE JUDGE"
        outcome = label.get("outcome", "").upper() if label else "SESSION REPLAY"
        number = segment.get("number", 1)
        self.text(
            (60, 12) if portrait else (90, 38),
            "SKATE JUDGE   /   MOTION STUDY",
            19 if portrait else 17,
            MUTED,
            True,
        )
        self.text(
            (58, 45) if portrait else (88, 70),
            title,
            56 if portrait else 52,
            WHITE,
            True,
        )
        self.text(
            (62, 116) if portrait else (92, 136),
            f"{number:02d}   {outcome}   ·   VIDEO {clock(video_time+self.origin)}",
            22 if portrait else 19,
            LIME if outcome == "MAKE" else CORAL,
        )
        sensor_time = self.scale * (video_time + self.origin) + self.offset
        sample = interpolate(self.samples, self.attitudes, sensor_time)
        if sample:
            readings, attitude = sample
            self.board(heading @ attitude @ self.mount)
        else:
            readings = np.full(6, np.nan)
            self.text(
                (360, 320) if portrait else (220, 300),
                "NO SENSOR DATA",
                26,
                CORAL,
                True,
            )
        if portrait:
            self.text((60, 550), "IMU ROTATION", 18, MUTED, True)
            self.text((305, 550), "Estimated attitude · fixed position", 18, MUTED)
            self.text((1016, 550), f"{self.args.speed:g}×", 22, LIME, True, anchor="ra")
        else:
            self.text((92, 472), "IMU ROTATION", 17, MUTED, True)
            self.text((92, 502), "Estimated attitude · fixed position", 18, MUTED)
            self.text((704, 502), f"{self.args.speed:g}×", 20, LIME, True, anchor="ra")
        lo, hi = segment["start"], segment["end"]
        if segment.get("session"):
            lo = max(segment["start"], min(video_time - 2, segment["end"] - 6))
            hi = min(segment["end"], lo + 6)
        step = self.audio["step_s"]
        strength = np.asarray(self.audio["strength"])
        audio_times = np.arange(len(strength)) * step
        current_strength = float(
            np.interp(video_time, audio_times, strength, left=0, right=0)
        )
        values = [
            current_strength,
            np.linalg.norm(readings[:3]),
            np.linalg.norm(readings[3:]),
        ]
        for n, (name, color, unit_text) in enumerate(
            [
                ("AUDIO ONSETS", PURPLE, "relative strength"),
                ("ACCELEROMETER", CYAN, "m/s² · magnitude"),
                ("GYROSCOPE", GOLD, "rad/s · magnitude"),
            ]
        ):
            y = 590 + n * 148 if portrait else 35 + n * 156
            panel_left, panel_right = (40, 1040) if portrait else (780, 1830)
            text_left, value_right = panel_left + 20, panel_right - 22
            graph_right = panel_right - 220
            self.rect(
                (panel_left, y, panel_right, y + 140),
                (12, 28, 31, 190),
                16,
                (85, 117, 112, 120),
            )
            self.text((text_left, y + 13), name, 19 if portrait else 17, color, True)
            if n and np.isfinite(readings).all():
                xyz = readings[:3] if n == 1 else readings[3:]
                self.text(
                    (text_left + 310, y + 15),
                    "   ".join(
                        f"{axis} {value:+.1f}" for axis, value in zip("XYZ", xyz)
                    ),
                    15 if portrait else 14,
                    MUTED,
                )
            self.text(
                (graph_right + 30, y + 13),
                f"0–{self.limits[n]:g}",
                15,
                MUTED,
                anchor="ra",
            )
            self.text(
                (value_right, y + 51),
                f"{values[n]:.2f}" if np.isfinite(values[n]) else "—",
                35,
                color,
                True,
                anchor="ra",
            )
            self.text((value_right, y + 100), unit_text, 15, MUTED, anchor="ra")
            left, top, right, bottom = text_left, y + 50, graph_right, y + 112
            self.line([(left, bottom), (right, bottom)], (128, 159, 152, 80))
            self.line(
                [(left, (top + bottom) / 2), (right, (top + bottom) / 2)],
                (128, 159, 152, 40),
            )
            if n == 0:
                self.trace(
                    audio_times,
                    strength,
                    lo,
                    hi,
                    (left, top, right, bottom),
                    self.limits[n],
                    color,
                    video_time,
                )
                for onset in self.audio["onsets"]:
                    t = onset["time_s"]
                    if lo <= t <= hi and onset["strength"] >= self.args.onset_threshold:
                        px = left + (t - lo) / (hi - lo) * (right - left)
                        self.line(
                            [
                                (px, bottom),
                                (
                                    px,
                                    bottom
                                    - min(onset["strength"] / self.limits[0], 1)
                                    * (bottom - top),
                                ),
                            ],
                            color,
                            2,
                        )
            else:
                self.trace(
                    self.sensor_video,
                    self.accel if n == 1 else self.gyro,
                    lo,
                    hi,
                    (left, top, right, bottom),
                    self.limits[n],
                    color,
                    video_time,
                    MAX_GAP / self.scale,
                )
            review = (label or {}).get("review_interval", {})
            for key, marker_color in [("start_s", LIME), ("end_s", CORAL)]:
                t = review.get(key)
                if t is not None and lo <= t <= hi:
                    px = left + (t - lo) / (hi - lo) * (right - left)
                    self.line([(px, top - 5), (px, bottom)], (*marker_color[:3], 130))
            px = left + (video_time - lo) / (hi - lo) * (right - left)
            if left <= px <= right:
                self.line([(px, top - 5), (px, bottom + 3)], WHITE, 2)
            self.text((left, y + 119), clock(lo + self.origin), 12, MUTED)
            self.text((right, y + 119), clock(hi + self.origin), 12, MUTED, anchor="ra")
        footer_x, footer_y = (60, 1053) if portrait else (798, 516)
        self.text((footer_x, footer_y), "SAVED MARKERS", 13, MUTED)
        self.text((footer_x + 147, footer_y), "POP", 13, LIME, True)
        self.text((footer_x + 202, footer_y), "CONTACT", 13, CORAL, True)
        self.text(
            (1020 if portrait else 1828, footer_y),
            "SENSOR + AUDIO  /  LED-SYNCHRONIZED",
            13,
            MUTED,
            anchor="ra",
        )
        ratio = min(
            self.args.width / canvas_width,
            self.args.height / (1920 if portrait else 1080),
        )
        content = self.layer.resize(
            (round(canvas_width * ratio), round(canvas_height * ratio)),
            Image.Resampling.LANCZOS,
        )
        frame = Image.new("RGBA", (self.args.width, self.args.height), (0, 0, 0, 0))
        frame.paste(
            content,
            ((self.args.width - content.width) // 2, self.args.height - content.height),
        )
        return frame


def encode_frames(path, renderer, segment, heading, args):
    count = math.ceil((segment["end"] - segment["start"]) / args.speed * args.fps)
    temporary = path.with_name(path.stem + ".partial.mov")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgba",
        "-video_size",
        f"{args.width}x{args.height}",
        "-framerate",
        str(args.fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-pix_fmt",
        "yuva444p10le",
        "-alpha_bits",
        "16",
        "-qscale:v",
        "4",
        "-threads",
        "4",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    start = time.monotonic()
    with subprocess.Popen(
        command, stdin=subprocess.PIPE, stderr=subprocess.PIPE
    ) as encoder:
        try:
            for i in range(count):
                t = min(segment["end"], segment["start"] + i / args.fps * args.speed)
                active = segment
                if segment.get("session"):
                    label = next(
                        (
                            r
                            for r in segment["labels"]
                            if r["video_start_s"] - renderer.origin
                            <= t
                            < r["video_end_s"] - renderer.origin
                        ),
                        None,
                    )
                    active = {**segment, "label": label}
                encoder.stdin.write(renderer.render(t, active, heading).tobytes())
                if i % max(1, args.fps * 2) == 0:
                    print(
                        f"  {path.name}: {i}/{count} frames ({time.monotonic()-start:.1f}s)",
                        flush=True,
                    )
            encoder.stdin.close()
            errors = encoder.stderr.read().decode()
            if encoder.wait():
                raise ValueError(f"FFmpeg failed: {errors[-2000:]}")
        except BaseException:
            encoder.terminate()
            encoder.wait()
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)
    return count


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--video", help="Video filename used by the saved labels")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=["attempts", "session"], default="attempts")
    parser.add_argument(
        "--label", help="Render only this saved label ID (or a unique prefix)"
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1,
        help="Playback speed; .25 exports four-times-slower motion",
    )
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument(
        "--nose-axis", default="auto", choices=["auto", "x", "y", "z", "-x", "-y", "-z"]
    )
    parser.add_argument(
        "--up-axis", default="auto", choices=["auto", "x", "y", "z", "-x", "-y", "-z"]
    )
    parser.add_argument("--onset-threshold", type=float, default=0.65)
    parser.add_argument("--font", type=Path)
    parser.add_argument("--antialias", type=int, default=2, choices=[1, 2, 3])
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument(
        "--reel",
        action="store_true",
        help="Also concatenate the individual attempts into one MOV",
    )
    args = parser.parse_args(argv)
    if (
        not 0 < args.speed <= 4
        or not 1 <= args.fps <= 120
        or min(args.width, args.height) < 320
        or args.width % 2
        or args.height % 2
    ):
        parser.error(
            "Use speed > 0 and ≤ 4, fps 1–120, and even dimensions of at least 320 pixels."
        )
    if not shutil.which("ffmpeg"):
        parser.error("Install ffmpeg before rendering.")
    session = args.session.resolve()
    if not args.video:
        from viz.overlay_motion import read_jsonl

        videos = {
            r.get("video")
            for r in read_jsonl(session / "labels.jsonl")
            if r.get("video")
        }
        if len(videos) != 1:
            parser.error(
                "Choose --video: this session does not have exactly one labeled video."
            )
        args.video = videos.pop()
    if Path(args.video).name != args.video:
        parser.error("--video must be a filename inside the session.")
    output = args.output or session / "overlays" / Path(args.video).stem
    output.mkdir(parents=True, exist_ok=True)
    analysis_path = session / "review" / Path(args.video).stem / "analysis.json"
    analysis = json.loads(analysis_path.read_text())
    labels = latest_labels(session, args.video)
    source = session / args.video
    if source.exists():
        stat = source.stat()
        if analysis.get("source") != {
            "name": source.name,
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }:
            parser.error(
                "Audio analysis refers to different media. Rebuild the trick review before rendering."
            )
    if any(
        row.get("review_source", analysis["source"]) != analysis["source"]
        for row in labels
    ):
        parser.error(
            "Saved labels refer to different media. Review them before rendering."
        )
    attempts = [r for r in labels if r["outcome"] != "background"]
    if args.label:
        attempts = [r for r in attempts if r["id"].startswith(args.label)]
        if len(attempts) != 1:
            parser.error("--label must uniquely identify one saved attempt.")
    if not attempts:
        parser.error("No saved attempts for this video.")
    samples = load_samples(session / "samples.csv")
    scale, offset, points = video_mapping(session, args.video)
    check_alignment(labels, scale, offset, (samples[0, 0], samples[-1, 0]))
    origin = analysis["source_pts_origin_s"]
    attitudes, calibration = estimate_attitude(samples)
    auto_nose, auto_up, method = infer_mount(samples, labels)
    nose = auto_nose if args.nose_axis == "auto" else args.nose_axis
    up = auto_up if args.up_axis == "auto" else args.up_axis
    mount_method = method if args.nose_axis == "auto" else "explicit nose axis"
    mount = mount_matrix(nose, up)
    print(
        f"{len(attempts)} saved attempts · {points} flash matches · nose {nose}, deck up {up}",
        flush=True,
    )
    print(
        f"Mounting: {mount_method}. Override with --nose-axis / --up-axis.", flush=True
    )
    renderer = Renderer(
        samples, attitudes, analysis["audio"], (scale, offset, origin), mount, args
    )
    segments = [
        dict(
            start=r["video_start_s"] - origin,
            end=r["video_end_s"] - origin,
            label=r,
            number=i + 1,
        )
        for i, r in enumerate(attempts)
    ]
    if args.mode == "session":
        segments = [
            dict(
                start=max(0, (samples[0, 0] - offset) / scale - origin),
                end=min(
                    analysis["duration_s"], (samples[-1, 0] - offset) / scale - origin
                ),
                labels=attempts,
                session=True,
                number=1,
            )
        ]
    manifest = {
        "schema": 1,
        "video": args.video,
        "fps": args.fps,
        "width": args.width,
        "height": args.height,
        "speed": args.speed,
        "codec": "ProRes 4444",
        "alpha": "straight",
        "mode": args.mode,
        "layout": "portrait" if args.height > args.width else "landscape",
        "source_pts_origin_s": origin,
        "mapping": {"scale": scale, "offset_s": offset, "points": points},
        "mount": {
            "nose_sensor_axis": nose,
            "up_sensor_axis": up,
            "method": mount_method,
        },
        "attitude": {
            "method": "gyro integration with gated gravity correction",
            **calibration,
        },
        "limitations": [
            "Estimated rotation only; no measured translation or jump height.",
            "Yaw has no magnetometer correction; heading is reset for each clip.",
            "Automatic mounting cannot distinguish the nose from the tail.",
        ],
        "graph_limits": renderer.limits,
        "source_sha256": {
            p.name: digest(p)
            for p in [
                session / "samples.csv",
                session / "labels.jsonl",
                session / "video_sync.jsonl",
                analysis_path,
            ]
        },
        "clips": [],
    }
    for segment in segments:
        label = segment.get("label")
        slug = (
            re.sub(r"[^a-z0-9]+", "-", label.get("trick", "attempt").lower()).strip("-")
            if label
            else "session"
        )
        label_token = re.sub(r"[^a-zA-Z0-9]", "", label["id"])[:8] if label else ""
        name = (
            f"{segment['number']:02d}-{slug}-{label['outcome']}-{label_token}"
            if label
            else "session-overlay"
        )
        start_sample = interpolate(
            samples, attitudes, scale * (segment["start"] + origin) + offset
        )
        attitude = start_sample[1] @ mount if start_sample else np.eye(3)
        yaw = math.atan2(attitude[1, 0], attitude[0, 0])
        c, s = math.cos(-yaw), math.sin(-yaw)
        heading = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        marker = (label or {}).get("review_interval", {})
        midpoint = (
            marker.get("start_s", segment["start"])
            + marker.get("end_s", segment["end"])
        ) / 2
        midpoint = np.clip(midpoint, segment["start"], segment["end"])
        preview = renderer.render(float(midpoint), segment, heading)
        preview.save(output / f"{name}.png")
        checker = Image.new("RGBA", preview.size, (38, 43, 49, 255))
        draw = ImageDraw.Draw(checker)
        for y in range(0, args.height, 32):
            for x in range(0, args.width, 32):
                if (x // 32 + y // 32) % 2:
                    draw.rectangle((x, y, x + 31, y + 31), fill=(50, 56, 63, 255))
        Image.alpha_composite(checker, preview).convert("RGB").save(
            output / f"{name}-preview.jpg", quality=92
        )
        print(f"Preview: {output/name}.png", flush=True)
        frames = (
            0
            if args.preview_only
            else encode_frames(output / f"{name}.mov", renderer, segment, heading, args)
        )
        manifest["clips"].append(
            {
                "file": None if args.preview_only else name + ".mov",
                "preview": name + ".png",
                "label_id": label["id"] if label else None,
                "frames": frames,
                "output_duration_s": frames / args.fps,
                "review_start_s": segment["start"],
                "review_end_s": segment["end"],
                "heading_reset_rad": yaw,
                "label": label,
            }
        )
    if args.reel and len(segments) > 1 and not args.preview_only:
        # Controlled basenames contain only ASCII letters, numbers, and dashes.
        concat = output / "reel-inputs.txt"
        concat.write_text(
            "".join(f"file '{clip['file']}'\n" for clip in manifest["clips"])
        )
        reel = output / "attempts-reel.mov"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(concat),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(reel),
            ],
            check=True,
        )
        manifest["reel"] = reel.name
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n"
    )
    print(f"Saved overlay assets to {output}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
