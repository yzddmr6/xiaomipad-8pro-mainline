#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile the board topology using the pinned, vendored AudioReach templates."""
import argparse
from pathlib import Path
import subprocess
from lib.deps import REPO, export_vendor

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
source = a.output / 'source'
export_vendor('audioreach-topology', source)
config = a.output / 'Xiaomi-Pad-8-Pro.conf'
with config.open('wb') as stream:
    subprocess.run(['m4', '-I', str(REPO / 'device/audio'), '-I', str(source),
                    str(REPO / 'device/audio/Xiaomi-Pad-8-Pro.m4')], stdout=stream, check=True)
subprocess.run(['alsatplg', '-c', str(config), '-o', str(a.output / 'Xiaomi-Pad-8-Pro-tplg.bin')], check=True)
