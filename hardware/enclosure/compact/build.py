#!/usr/bin/env python3
"""Export the compact XTSD enclosure with the shared topology checker."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from build import main

if __name__ == "__main__":
    main(directory=HERE, extra_targets=[("led-carrier", "led_carrier", False)])
