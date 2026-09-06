#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pygame>=2.5",
#     "PyOpenGL>=3.1.7",
#     "pyserial>=3.5",
#     "numpy>=1.24",
# ]
# ///
"""3D orientation viewer for an Adafruit LSM6DSO32 streamed over USB serial.

Pairs with firmware/imu_stream (ESP32-S3), which prints lines like
    D,<micros>,<ax>,<ay>,<az>,<gx>,<gy>,<gz>,<temp_C>
with accel in m/s^2 and gyro in rad/s.  Samples are fused with a Mahony
filter (6-DOF: gravity pins roll/pitch, the gyro integrates yaw) and the
breakout board is drawn in OpenGL.

    uv run viz/imu_viz.py                     auto-detect the serial port
    uv run viz/imu_viz.py -p /dev/cu.usbmodem101
    uv run viz/imu_viz.py --demo              synthetic data, no hardware
    uv run viz/imu_viz.py --list              list serial ports

Keys:  C  re-calibrate gyro bias (keep the board still for 2 s)
       Z  zero the heading (yaw)          M  toggle fused / tilt-only
       R  reset the filter and peaks      S  save a screenshot
       mouse drag orbits, wheel zooms, Esc quits
"""
from __future__ import annotations

import argparse
import collections
import math
import sys
import threading
import time

import numpy as np
import pygame
import serial
import serial.tools.list_ports
from OpenGL.GL import *   # noqa: F401,F403
from OpenGL.GLU import *  # noqa: F401,F403

G0 = 9.80665
IDENTITY = np.array([1.0, 0.0, 0.0, 0.0])
# Where the int16 readings saturate at the firmware's +/-32 g and +/-2000 dps settings;
# the firmware reports its own values in an "R," line, these are only the fallback.
DEFAULT_SAT_A = 32767 * 0.976e-3 * G0
DEFAULT_SAT_G = math.radians(32767 * 0.070)
CLIP_FRACTION = 0.98
RED, GREEN, BLUE = (0.92, 0.30, 0.28), (0.35, 0.82, 0.38), (0.35, 0.55, 0.98)

# --------------------------------------------------------------------------- quaternions
# q = (w, x, y, z) rotates vectors from the SENSOR frame into the EARTH frame (Z up).


def q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ])


def q_conj(q):
    return np.array([q[0], -q[1], -q[2], -q[3]])


def q_norm(q):
    n = np.linalg.norm(q)
    return q / n if n > 1e-12 else IDENTITY.copy()


def q_to_mat(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z),     2 * (x * y - w * z),     2 * (x * z + w * y)],
        [    2 * (x * y + w * z), 1 - 2 * (x * x + z * z),     2 * (y * z - w * x)],
        [    2 * (x * z - w * y),     2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def q_to_euler_deg(q):
    """Roll about X, pitch about Y, yaw about Z (ZYX convention), in degrees."""
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def q_axis_angle(axis, angle):
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    return np.array([math.cos(angle / 2), *(axis * math.sin(angle / 2))])


def q_from_gravity(a):
    """Tilt-only orientation: the shortest rotation taking the measured 'up' onto earth Z."""
    a = np.asarray(a, float)
    n = np.linalg.norm(a)
    if n < 1e-6:
        return IDENTITY.copy()
    a = a / n
    up = np.array([0.0, 0.0, 1.0])
    c = float(np.dot(a, up))
    axis = np.cross(a, up)
    s = float(np.linalg.norm(axis))
    if s < 1e-8:
        return IDENTITY.copy() if c > 0 else q_axis_angle([1, 0, 0], math.pi)
    return q_axis_angle(axis / s, math.atan2(s, c))


def q_angle_deg(a, b):
    return math.degrees(2 * math.acos(min(1.0, abs(float(np.dot(a, b))))))


# --------------------------------------------------------------------------- sensor fusion
class Mahony:
    """Mahony complementary filter, 6-DOF variant (no magnetometer)."""

    def __init__(self, kp=1.0, ki=0.02):
        self.kp, self.ki = kp, ki
        self.q = IDENTITY.copy()
        self.integral = np.zeros(3)

    def reset(self, q=None):
        self.q = IDENTITY.copy() if q is None else q_norm(np.asarray(q, float))
        self.integral[:] = 0.0

    def update(self, gyro, accel, dt):
        g = np.asarray(gyro, float).copy()
        a = np.asarray(accel, float)
        n = float(np.linalg.norm(a))
        if 0.6 * G0 < n < 1.4 * G0:            # only trust accel when it's mostly gravity
            a = a / n
            w, x, y, z = self.q
            v = np.array([2 * (x * z - w * y), 2 * (y * z + w * x), w * w - x * x - y * y + z * z])
            e = np.cross(a, v)                 # error between measured and estimated 'up'
            if self.ki > 0:
                self.integral += self.ki * e * dt
                g += self.integral
            g += self.kp * e
        qdot = 0.5 * q_mul(self.q, np.array([0.0, *g]))
        self.q = q_norm(self.q + qdot * dt)


# --------------------------------------------------------------------------- data sources
Sample = collections.namedtuple("Sample", "t ax ay az gx gy gz temp")
KNOWN_VIDS = {0x303A, 0x239A, 0x10C4, 0x1A86, 0x0403}  # Espressif, Adafruit, CP210x, CH34x, FTDI


def list_ports():
    return list(serial.tools.list_ports.comports())


def find_port():
    ports = list_ports()
    for p in ports:
        if p.vid in KNOWN_VIDS:
            return p.device
    for p in ports:
        if any(k in p.device for k in ("usbmodem", "usbserial", "ttyACM", "ttyUSB")):
            return p.device
    return None


class SerialSource(threading.Thread):
    def __init__(self, port=None, baud=115200):
        super().__init__(daemon=True)
        self.port_arg, self.baud = port, baud
        self.queue = collections.deque()
        self.status = "searching for a serial device"
        self.info = []
        self.lines = self.bad = 0
        self.truth = None
        self.sat = None
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            port = self.port_arg or find_port()
            if not port:
                self.status = "no serial device found - is the board plugged in?"
                time.sleep(0.5)
                continue
            try:
                with serial.Serial(port, self.baud, timeout=0.2) as ser:
                    self.status = f"connected: {port}"
                    ser.reset_input_buffer()
                    ser.write(b"i\n")
                    while not self._stop.is_set():
                        raw = ser.readline()
                        if raw:
                            self._parse(raw)
            except (serial.SerialException, OSError) as e:
                self.status = f"serial error: {e}"
                time.sleep(1.0)

    def _parse(self, raw):
        line = raw.decode("ascii", "replace").strip()
        if line.startswith("D,"):
            parts = line.split(",")
            try:
                vals = [float(p) for p in parts[1:9]]
            except ValueError:
                self.bad += 1
                return
            if len(vals) < 7:
                self.bad += 1
                return
            temp = vals[7] if len(vals) > 7 else float("nan")
            self.queue.append(Sample(vals[0] * 1e-6, *vals[1:7], temp))
            self.lines += 1
        elif line.startswith("R,"):
            try:
                a_sat, g_sat = (float(v) for v in line.split(",")[1:3])
                self.sat = (a_sat, g_sat)
            except ValueError:
                self.bad += 1
        elif line.startswith(("I,", "E,")):
            self.info.append(line[2:])
            del self.info[:-6]


class DemoSource(threading.Thread):
    """Synthetic IMU: still for a moment (so calibration works), then a slow tumble."""

    def __init__(self, rate=100.0, still_seconds=2.5):
        super().__init__(daemon=True)
        self.queue = collections.deque()
        self.status = "demo: synthetic IMU data"
        self.info = ["no hardware - synthetic data with a 0.8 deg/s gyro bias"]
        self.lines = self.bad = 0
        self.sat = (DEFAULT_SAT_A, DEFAULT_SAT_G)
        self.truth = IDENTITY.copy()
        self.rate, self.still = rate, still_seconds
        self.bias = np.radians([0.8, -0.5, 0.3])
        self.rng = np.random.default_rng(1)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        dt = 1.0 / self.rate
        k = 0
        t0 = time.monotonic()
        while not self._stop.is_set():
            t = k * dt
            if t < self.still:
                omega = np.zeros(3)
            else:
                u = t - self.still
                omega = np.array([0.9 * math.sin(0.35 * u), 0.7 * math.sin(0.23 * u + 1.0), 0.5 * math.sin(0.17 * u + 2.0)])
            self.truth = q_norm(self.truth + 0.5 * q_mul(self.truth, np.array([0.0, *omega])) * dt)
            a = q_to_mat(self.truth).T @ np.array([0.0, 0.0, G0]) + self.rng.normal(0, 0.04, 3)
            g = omega + self.bias + self.rng.normal(0, 0.004, 3)
            if t >= self.still and int((t - self.still) * self.rate) % int(4 * self.rate) < 2:
                a[0] += 34 * G0     # a fake landing impact every 4 s, to exercise the clipping warning
            self.queue.append(Sample(t, *a, *g, 25.0))
            self.lines += 1
            k += 1
            wait = t0 + k * dt - time.monotonic()
            if wait > 0:
                time.sleep(wait)


# --------------------------------------------------------------------------- drawing helpers
EARTH_TO_GL = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], float)  # Z-up -> GL Y-up
BOARD = (2.54, 1.78, 0.16)  # 25.4 x 17.8 x 1.6 mm at 1 unit = 10 mm


def gl_mult(m4):
    glMultMatrixf(np.ascontiguousarray(np.asarray(m4, dtype=np.float32).T))


def mat4_from_q(q):
    m = np.eye(4)
    m[:3, :3] = q_to_mat(q)
    return m


def draw_box(size, center=(0, 0, 0), top=(0.6, 0.6, 0.6), bottom=None, side=None):
    hx, hy, hz = size[0] / 2, size[1] / 2, size[2] / 2
    cx, cy, cz = center
    bottom = bottom or top
    side = side or top
    faces = (
        (top,    (0, 0, 1),  ((-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz))),
        (bottom, (0, 0, -1), ((-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz), (hx, -hy, -hz))),
        (side,   (1, 0, 0),  ((hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz))),
        (side,   (-1, 0, 0), ((-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz))),
        (side,   (0, 1, 0),  ((-hx, hy, -hz), (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz))),
        (side,   (0, -1, 0), ((-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz))),
    )
    glBegin(GL_QUADS)
    for color, normal, verts in faces:
        glColor3f(*color)
        glNormal3f(*normal)
        for vx, vy, vz in verts:
            glVertex3f(cx + vx, cy + vy, cz + vz)
    glEnd()


def draw_arrow(quad, axis, length, color, radius=0.045):
    glColor3f(*color)
    glPushMatrix()
    if axis == "x":
        glRotatef(90, 0, 1, 0)
    elif axis == "y":
        glRotatef(-90, 1, 0, 0)
    head = 0.32
    gluCylinder(quad, radius, radius, length - head, 14, 1)
    glTranslatef(0, 0, length - head)
    gluCylinder(quad, radius * 2.8, 0.0, head, 14, 1)
    glPopMatrix()


def draw_grid(extent=4.0, step=0.5, z=-1.5):
    glDisable(GL_LIGHTING)
    glLineWidth(1.0)
    glBegin(GL_LINES)
    n = int(extent / step)
    for i in range(-n, n + 1):
        v = i * step
        c = 0.30 if i == 0 else 0.18
        glColor3f(c, c + 0.02, c + 0.05)
        glVertex3f(v, -extent, z); glVertex3f(v, extent, z)
        glVertex3f(-extent, v, z); glVertex3f(extent, v, z)
    glEnd()
    glEnable(GL_LIGHTING)


def draw_board():
    # PCB: blue top so the component side is obvious, dark underside.
    draw_box(BOARD, top=(0.13, 0.36, 0.64), bottom=(0.09, 0.10, 0.13), side=(0.16, 0.18, 0.22))
    # the LSM6DSO32 package (exaggerated so it's visible) with a pin-1 dot
    draw_box((0.55, 0.45, 0.10), center=(0, 0, 0.13), top=(0.12, 0.12, 0.13), side=(0.22, 0.22, 0.24))
    draw_box((0.08, 0.08, 0.02), center=(-0.19, 0.14, 0.19), top=(0.92, 0.92, 0.92))
    # STEMMA QT / Qwiic connectors on the short ends
    for x in (-1.06, 1.06):
        draw_box((0.42, 0.62, 0.30), center=(x, 0, 0.23), top=(0.06, 0.06, 0.06), side=(0.10, 0.10, 0.11))
    # header pads along one long edge
    for i in range(8):
        draw_box((0.12, 0.12, 0.02), center=(-1.05 + 0.30 * i, -0.72, 0.09), top=(0.85, 0.72, 0.30))


# --------------------------------------------------------------------------- fonts
MONO_FONT_FILES = (
    "/System/Library/Fonts/Menlo.ttc",                       # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",   # Debian/Ubuntu
    "C:/Windows/Fonts/consola.ttf",                          # Windows
)


def load_mono_font(size, bold=False):
    """Load a monospace font by path; pygame's SysFont can stall for seconds on macOS."""
    for path in MONO_FONT_FILES:
        try:
            f = pygame.font.Font(path, size)
            f.set_bold(bold)
            return f
        except (FileNotFoundError, OSError, pygame.error):
            continue
    f = pygame.font.Font(None, size)
    f.set_bold(bold)
    return f


# --------------------------------------------------------------------------- viewer
class Viewer:
    def __init__(self, source, args):
        self.source, self.args = source, args
        pygame.init()
        pygame.display.set_caption("LSM6DSO32 orientation")
        self.win_w, self.win_h = args.width, args.height
        flags = pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE
        self.msaa = True
        try:
            pygame.display.gl_set_attribute(pygame.GL_MULTISAMPLEBUFFERS, 1)
            pygame.display.gl_set_attribute(pygame.GL_MULTISAMPLESAMPLES, 4)
            pygame.display.set_mode((self.win_w, self.win_h), flags)
        except pygame.error:
            self.msaa = False
            pygame.display.gl_set_attribute(pygame.GL_MULTISAMPLEBUFFERS, 0)
            pygame.display.set_mode((self.win_w, self.win_h), flags)
        vp = glGetIntegerv(GL_VIEWPORT)
        self.scale = (vp[2] / self.win_w) if (self.win_w and vp[2]) else 1.0   # HiDPI framebuffer scale
        self.font = load_mono_font(int(13 * self.scale))
        self.font_big = load_mono_font(int(24 * self.scale), bold=True)
        self.quad = gluNewQuadric()
        gluQuadricNormals(self.quad, GLU_SMOOTH)

        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glLightfv(GL_LIGHT0, GL_DIFFUSE, (0.95, 0.95, 0.95, 1.0))
        glLightfv(GL_LIGHT0, GL_AMBIENT, (0.25, 0.25, 0.25, 1.0))
        glLightModelfv(GL_LIGHT_MODEL_AMBIENT, (0.22, 0.22, 0.22, 1.0))
        glEnable(GL_NORMALIZE)
        glShadeModel(GL_SMOOTH)
        if self.msaa:
            glEnable(GL_MULTISAMPLE)

        self.filter = Mahony(args.kp, args.ki)
        self.mode = "fused"
        self.q_ref = IDENTITY.copy()
        self.q_display = IDENTITY.copy()
        self.bias = np.zeros(3)
        self.calib = None
        self.calib_t0 = None
        self.calib_msg = ""
        self.last_t = None
        self.latest = None
        self.a_lp = None
        self.stamps = collections.deque()
        self.cam = {"az": 38.0, "el": 27.0, "dist": 8.5}
        self.dragging = False
        self.frames = 0
        self.labels = []
        self.sat_a, self.sat_g = DEFAULT_SAT_A, DEFAULT_SAT_G
        self.reset_peaks()
        self.start_calibration()

    def reset_peaks(self):
        self.peak_a = self.peak_g = 0.0
        self.clip_a = self.clip_g = 0
        self.last_clip = None
        self.last_clip_kind = ""

    # ---- data -------------------------------------------------------------
    def start_calibration(self):
        self.calib, self.calib_t0 = [], None
        self.calib_msg = "calibrating gyro bias: keep the board still..."

    def feed_calibration(self, t, g, a):
        if self.calib_t0 is None:
            self.calib_t0 = t
        self.calib.append(np.concatenate([g, a]))
        if t - self.calib_t0 < self.args.calib_seconds and len(self.calib) < 400:
            return
        arr = np.array(self.calib)
        self.calib = None
        gyro, acc = arr[:, :3], arr[:, 3:]
        self.bias = gyro.mean(0)
        spread = float(np.degrees(gyro.std(0).max()))
        self.filter.reset(q_from_gravity(acc.mean(0)))
        self.q_ref = IDENTITY.copy()
        self.last_t = None
        b = np.degrees(self.bias)
        if spread > 2.0:
            self.calib_msg = f"calibration looked noisy (gyro std {spread:.1f} deg/s) - was the board moving? press C to redo"
        else:
            self.calib_msg = f"gyro bias removed: {b[0]:+.2f} {b[1]:+.2f} {b[2]:+.2f} deg/s"

    def process(self):
        q = self.source.queue
        while q:
            s = q.popleft()
            self.latest = s
            self.stamps.append(time.monotonic())
            g = np.array([s.gx, s.gy, s.gz])
            a = np.array([s.ax, s.ay, s.az])
            self.a_lp = a if self.a_lp is None else 0.9 * self.a_lp + 0.1 * a
            if self.source.sat:
                self.sat_a, self.sat_g = self.source.sat
            pa, pg = float(np.max(np.abs(a))), float(np.max(np.abs(g)))
            self.peak_a, self.peak_g = max(self.peak_a, pa), max(self.peak_g, pg)
            kinds = []
            if pa >= CLIP_FRACTION * self.sat_a:
                self.clip_a += 1
                kinds.append("accel")
            if pg >= CLIP_FRACTION * self.sat_g:
                self.clip_g += 1
                kinds.append("gyro")
            if kinds:
                self.last_clip = time.monotonic()
                self.last_clip_kind = " + ".join(kinds)
            if self.calib is not None:
                self.feed_calibration(s.t, g, a)
                continue
            dt = 0.0 if self.last_t is None else s.t - self.last_t
            self.last_t = s.t
            if not (0.0 < dt < 0.1):          # first sample, reconnect, or micros() wrap
                dt = 0.01
            self.filter.update(g - self.bias, a, dt)
        now = time.monotonic()
        while self.stamps and now - self.stamps[0] > 1.0:
            self.stamps.popleft()

    def zero_heading(self):
        _, _, yaw = q_to_euler_deg(self.filter.q)
        self.q_ref = q_axis_angle([0, 0, 1], math.radians(yaw))

    def compute_display(self):
        if self.mode == "tilt":
            self.q_display = q_from_gravity(self.a_lp) if self.a_lp is not None else IDENTITY.copy()
        else:
            self.q_display = q_norm(q_mul(q_conj(self.q_ref), self.filter.q))

    # ---- rendering --------------------------------------------------------
    def draw_scene(self, w, h):
        glViewport(0, 0, w, h)
        glClearColor(0.075, 0.085, 0.105, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(40.0, w / max(1, h), 0.1, 100.0)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        az, el, d = math.radians(self.cam["az"]), math.radians(self.cam["el"]), self.cam["dist"]
        eye = (d * math.cos(el) * math.sin(az), d * math.sin(el), d * math.cos(el) * math.cos(az))
        gluLookAt(*eye, 0, 0, 0, 0, 1, 0)
        glLightfv(GL_LIGHT0, GL_POSITION, (2.0, 6.0, 3.0, 0.0))

        glPushMatrix()
        gl_mult(EARTH_TO_GL)
        draw_grid()
        rot = mat4_from_q(self.q_display)

        # ground shadow: the board squashed flat onto the grid
        glDisable(GL_LIGHTING)
        glPushMatrix()
        glTranslatef(0, 0, -1.49)
        glScalef(1, 1, 0.0)
        gl_mult(rot)
        draw_box(BOARD, top=(0.04, 0.045, 0.06))
        glPopMatrix()
        glEnable(GL_LIGHTING)

        glPushMatrix()
        gl_mult(rot)
        draw_board()
        self.labels = []
        for axis, color in (("x", RED), ("y", GREEN), ("z", BLUE)):
            draw_arrow(self.quad, axis, 2.2, color)
            tip = {"x": (2.45, 0, 0), "y": (0, 2.45, 0), "z": (0, 0, 2.45)}[axis]
            sx, sy, _ = gluProject(*tip)
            self.labels.append((axis.upper(), color, sx, sy))
        glPopMatrix()
        glPopMatrix()

    def text(self, s, x, y, color=(225, 228, 232), font=None, top_origin=True, h=None, right=False):
        font = font or self.font
        surf = font.render(s, True, color)
        if right:
            x = x - surf.get_width()
        data = pygame.image.tobytes(surf, "RGBA", True) if hasattr(pygame.image, "tobytes") else pygame.image.tostring(surf, "RGBA", True)
        if top_origin:
            y = h - y - surf.get_height()
        if x < 0 or y < 0:
            return surf.get_height()
        glWindowPos2i(int(x), int(y))
        glDrawPixels(surf.get_width(), surf.get_height(), GL_RGBA, GL_UNSIGNED_BYTE, data)
        return surf.get_height()

    def draw_hud(self, w, h):
        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        sc = self.scale
        pad, lh = int(14 * sc), int(18 * sc)
        y = pad

        for name, color, sx, sy in self.labels:
            c = tuple(int(255 * v) for v in color)
            self.text(name, sx + 6 * sc, sy - 8 * sc, c, self.font, top_origin=False)

        rate = len(self.stamps)
        y += self.text(f"{self.source.status}   |   {rate:3d} samples/s   |   mode: {self.mode}", pad, y, (160, 200, 240), h=h) + 4
        y += self.text(self.calib_msg, pad, y, (235, 200, 120), h=h) + 8

        roll, pitch, yaw = q_to_euler_deg(self.q_display)
        y += self.text(f"roll {roll:+7.1f}   pitch {pitch:+7.1f}   yaw {yaw:+7.1f}", pad, y, (245, 245, 245), self.font_big, h=h) + 6

        s = self.latest
        if s is not None:
            an = math.sqrt(s.ax ** 2 + s.ay ** 2 + s.az ** 2) / G0
            gx, gy, gz = (math.degrees(v) for v in (s.gx, s.gy, s.gz))
            y += self.text(f"accel  x {s.ax:+6.2f}  y {s.ay:+6.2f}  z {s.az:+6.2f}  m/s2   |a| {an:4.2f} g", pad, y, h=h) + 2
            y += self.text(f"gyro   x {gx:+6.1f}  y {gy:+6.1f}  z {gz:+6.1f}  deg/s   temp {s.temp:4.1f} C", pad, y, h=h) + 2
            y += self.text(f"peak   accel {self.peak_a / G0:5.1f} g of {self.sat_a / G0:.0f}     gyro {math.degrees(self.peak_g):5.0f} of {math.degrees(self.sat_g):.0f} deg/s"
                           f"     clipped samples: accel {self.clip_a}  gyro {self.clip_g}", pad, y, (200, 205, 215), h=h) + 2
            if self.last_clip is not None and time.monotonic() - self.last_clip < 1.5:
                self.text(f"CLIPPING: {self.last_clip_kind} at full scale", w - pad, pad, (255, 70, 60), self.font_big, h=h, right=True)
        else:
            y += self.text("waiting for data...", pad, y, (200, 200, 200), h=h) + 2
        if self.source.truth is not None:
            err = q_angle_deg(self.source.truth, self.filter.q)
            y += self.text(f"demo: estimate vs truth {err:5.2f} deg", pad, y, (180, 240, 180), h=h) + 2
        y += 6
        for line in self.source.info:
            y += self.text(line, pad, y, (140, 145, 155), h=h) + 1

        help1 = "C calibrate (hold still)   Z zero heading   M fused/tilt   R reset   S screenshot   drag orbit   wheel zoom   Esc quit"
        self.text(help1, pad, h - pad - lh, (150, 155, 165), h=h)

        glDisable(GL_BLEND)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)

    def screenshot(self, path, w, h):
        glPixelStorei(GL_PACK_ALIGNMENT, 1)
        data = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
        make = pygame.image.frombytes if hasattr(pygame.image, "frombytes") else pygame.image.fromstring
        pygame.image.save(make(bytes(data), (w, h), "RGBA", True), path)
        print(f"saved {path}")

    # ---- main loop --------------------------------------------------------
    def run(self):
        clock = pygame.time.Clock()
        args = self.args
        auto_shot_frame = (args.frames - 1 if args.frames else 3) if args.screenshot else None
        pending_shot = None
        running = True
        while running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif ev.key == pygame.K_c:
                        self.start_calibration()
                    elif ev.key == pygame.K_z:
                        self.zero_heading()
                    elif ev.key == pygame.K_m:
                        self.mode = "tilt" if self.mode == "fused" else "fused"
                    elif ev.key == pygame.K_r:
                        self.filter.reset()
                        self.q_ref = IDENTITY.copy()
                        self.last_t = None
                        self.reset_peaks()
                    elif ev.key == pygame.K_s:
                        pending_shot = time.strftime("imu_viz_%Y%m%d_%H%M%S.png")
                elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    self.dragging = True
                elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                    self.dragging = False
                elif ev.type == pygame.MOUSEMOTION and self.dragging:
                    self.cam["az"] -= ev.rel[0] * 0.4
                    self.cam["el"] = max(-85.0, min(85.0, self.cam["el"] + ev.rel[1] * 0.4))
                elif ev.type == pygame.MOUSEWHEEL:
                    self.cam["dist"] = max(3.0, min(25.0, self.cam["dist"] - ev.y * 0.5))
                elif ev.type == pygame.VIDEORESIZE:
                    self.win_w, self.win_h = ev.w, ev.h

            self.process()
            self.compute_display()
            w, h = int(self.win_w * self.scale), int(self.win_h * self.scale)
            self.draw_scene(w, h)
            self.draw_hud(w, h)

            if self.frames == auto_shot_frame:
                self.screenshot(args.screenshot, w, h)
                if not args.frames:
                    running = False
            if pending_shot:
                self.screenshot(pending_shot, w, h)
                pending_shot = None

            pygame.display.flip()
            self.frames += 1
            if args.frames and self.frames >= args.frames:
                running = False
            clock.tick(60)

        if self.source.truth is not None:
            print(f"final estimate-vs-truth error: {q_angle_deg(self.source.truth, self.filter.q):.2f} deg")
        self.source.stop()
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--port", help="serial port (default: auto-detect)")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("--demo", action="store_true", help="synthetic data instead of a serial device")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("--width", type=int, default=1100)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--kp", type=float, default=1.0, help="Mahony proportional gain")
    ap.add_argument("--ki", type=float, default=0.02, help="Mahony integral gain")
    ap.add_argument("--calib-seconds", type=float, default=2.0)
    ap.add_argument("--frames", type=int, default=0, help="exit after this many frames (for testing)")
    ap.add_argument("--screenshot", help="save a PNG: on the last frame with --frames, otherwise right away, then exit")
    args = ap.parse_args()

    if args.list:
        for p in list_ports():
            vid = f"{p.vid:04x}:{p.pid:04x}" if p.vid else "----:----"
            print(f"{p.device:32s} {vid}  {p.description}")
        return

    source = DemoSource() if args.demo else SerialSource(args.port, args.baud)
    source.start()
    Viewer(source, args).run()


if __name__ == "__main__":
    main()
