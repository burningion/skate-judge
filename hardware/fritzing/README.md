# Editable skateboard wiring project

Open **[skate-judge-direct-lipo.fzz](skate-judge-direct-lipo.fzz)** in Fritzing.
It contains native components, electrical connections, movable wire bendpoints,
labels, and editable notes—not a flattened drawing. All six part definitions
and their artwork are bundled, including the four official Adafruit boards.
No separate part-library download is required to open the bundle.

![Preview of the direct-LiPo wiring](skate-judge-direct-lipo.png)

The circuit now uses the **Adafruit 4899 / XTSD 512 MB soldered SD board**.
The LED artwork still depicts the previous eight-pixel stick; use the actual
flexible strip's DIN, GND and positive-input labels and configured pixel count.

### SD soldering reference

![Adafruit 4899 wiring and old-wire mapping](sd-4899-wiring.png)

The [SD close-up SVG](sd-4899-wiring.svg) and [full circuit SVG](skate-judge-direct-lipo.svg)
can be enlarged without losing detail. Connect by labels, not the old SD board's pin order.

## Open and edit

1. Install [Fritzing for macOS](https://fritzing.org/download/) if needed. The
   official prebuilt download requires a purchase; no purchase or application
   installation was performed as part of creating this project.
2. In Fritzing, use **File → Open** and select `skate-judge-direct-lipo.fzz`.
3. Select **Breadboard** view and zoom to fit. This is a direct-wired assembly;
   there is no physical breadboard in the drawing.
4. Move components, drag wire bendpoints, or edit the notes. The wires reference
   real part connectors, including the battery JST and STEMMA QT contacts.
5. Keep saving as **`.fzz`** so custom parts travel with the project. Use Save As
   for a new circuit variant before changing the power arrangement.

The `.fz` file is the initial uncompressed sketch for inspecting changes in Git.
The `.svg` and `.png` are generated previews, **not** the editable Fritzing source
and **not** exports from a running Fritzing application. After editing in Fritzing,
the saved `.fzz` is authoritative; these initial snapshots will not update themselves.

Only the breadboard wiring view is authored and reviewed. Schematic/PCB component
positions are provided, but those views are not routed or reviewed. This is not
a fabrication-ready PCB, a simulation, or a dimensional mounting template. The
battery graphic is a generic 1S 500 mAh illustration, not a model of your exact cell.

## Circuit represented

| Connection | Destination |
| --- | --- |
| 3.7 V / 500 mAh LiPo | Feather battery JST, correct polarity |
| Feather `BAT` | NeoPixel stick `5V` / `+` input; actual supply is battery voltage |
| Feather `GND` | NeoPixel stick `GND` |
| Feather pad **5**, GPIO5 | R1, **330 Ω**, then stick `DIN` |
| Stick `DOUT` | Unconnected |
| Feather STEMMA QT | IMU STEMMA QT: GND, switched 3.3 V, SDA/GPIO3, SCL/GPIO4 |
| Feather `3.3V` | Adafruit **4899** `VIN` |
| Feather `GND` | 4899 `GND` |
| Feather `SCK` / GPIO36 | 4899 `SCK` |
| Feather `MO` / MOSI / GPIO35 | 4899 `MOSI` |
| Feather `MI` / MISO / GPIO37 | 4899 `MISO` |
| Feather **10** / GPIO10 | 4899 `CS` |
| 4899 `3V3` regulator output | Unconnected |

The four IMU wires represent **one STEMMA QT cable**. GPIO7 enables the Feather's
QT supply in firmware; it is not a fifth wire. The red QT wire is **not** the same
power net as red `BAT`. The 330 Ω resistor is the recommended data-input protection,
not an LED current-limiting resistor. Crossing lines are not connected unless they
meet at an electrical connector/junction.

The [Adafruit 4899](https://www.adafruit.com/product/4899) provides 512 MB of
soldered SD-protocol storage over SPI. Its **VIN is the power input** and
**3V3 is a regulator output**, per [Adafruit's pinout](https://learn.adafruit.com/adafruit-spi-flash-sd-card/pinouts).
This uses the Feather's main 3.3 V rail, separate from the switched QT supply.
Do not tap the IMU's `3Vo` or `VIN` for SD power: sensor initialization/rechecks
cycle the QT supply. IMU `GND` is a valid alternative ground connection, but the
drawing uses Feather `GND`. The IMU's signal contacts cannot replace these SPI wires.

Reuse the old 254 board's six wires if still connected to the Feather:
`3V → VIN`, `CLK → SCK`, `DI → MOSI`, `DO → MISO`, `GND → GND`, `CS → CS`.
Keep SPI wires short. There is no removable card; copy files over USB or Wi-Fi.
Verify `storage=sd`, record a short test and download it before relying on the
new board. Stop/save before removing power. See [SD recording and verification](../../docs/onboard-recording.md#sd-card-and-internal-flash)
for storage selection, firmware settings, and the required physical bench test.

This is the documented **direct-LiPo first test**, not the regulated 5 V alternative.
The stick's listed supply range is 4–7 V, so operation as a single LiPo discharges
below 4 V is not guaranteed. Test both fully charged and partly discharged before
relying on the sync flashes. Verify battery polarity and protect it from impacts.
See the [hardware reference](../../docs/recording.md#direct-lipo-wiring-for-the-first-test)
for the wiring caveats, manufacturer sources, and regulated-power alternative.

## Verification and reproducibility

The bundle was checked for valid XML, ZIP integrity, complete part/SVG references,
reciprocal wire connections, and the expected isolated electrical nets. In particular,
the checker rejects a bypassed/wrong data resistor, connections to LED DOUT,
Feather USB or the SD 3V3 output, swapped SPI signals, the wrong SD part, and shorts
between the twelve expected nets. Both previews were rendered with librsvg and visually reviewed.

**Native Fritzing open/save testing has not been performed** because the application
was not installed in this environment. Check that it opens without missing-part
warnings and that wires stay attached when moving a board before relying on it as
your working project. Physical hardware operation is also not established by a diagram.

From the repository root:

```bash
python3 hardware/fritzing/validate_project.py hardware/fritzing/skate-judge-direct-lipo.fzz
python3 -m unittest discover -s tests -p test_fritzing_project.py -v
```

`build_project.py` recreates the **initial layout** from the checked-in parts using
Python's standard library. It refuses to overwrite an existing project by default:

```bash
python3 hardware/fritzing/build_project.py --output /path/to/new-preview-directory
```

The optional `--preview` additionally needs PyQt6 and produces both PNGs. Alternatively,
render the generated SVGs with librsvg:

```bash
rsvg-convert hardware/fritzing/skate-judge-direct-lipo.svg -o hardware/fritzing/skate-judge-direct-lipo.png
rsvg-convert hardware/fritzing/sd-4899-wiring.svg -o hardware/fritzing/sd-4899-wiring.png
```

The internal `--replace-generated` option deliberately overwrites the initial outputs and will
lose any hand edits to them; do not use it on your working Fritzing file.

## Parts and attribution

- Feather ESP32-S3, LSM6DSO32, RGB NeoPixel Stick, and Adafruit 4899 artwork/definitions:
  [Adafruit Fritzing Library](https://github.com/adafruit/Fritzing-Library), pinned
  at `7d905c3e982de3712c3033da4596cc71ea32de1c`. The Feather asset identifies the
  8 MB revision; the RGB stick asset is not the WWA or RGBW part.
- Standard resistor artwork/definition: Fritzing / Brendan Howell,
  [Fritzing parts](https://github.com/fritzing/fritzing-parts), pinned at
  `e64ffe973e92176b989ab390ab668638a85ee305`. R1's native resistance property is 330 Ω.
- Generic battery illustration and arrangement: skate-judge contributors.
- Upstream URLs and archive hashes: [sources/provenance.json](sources/provenance.json).

The diagram, bundled artwork, and generic battery part are distributed under
**Creative Commons Attribution–ShareAlike 3.0 Unported**. Attribution is also inside
the `.fzz`; see [the license](sources/CC-BY-SA-3.0.txt) and
[Fritzing's graphics notice](sources/Fritzing-LICENSE.txt). This notice does not
relicense the firmware or the rest of the repository.
