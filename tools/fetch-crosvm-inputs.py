#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fetch immutable crosvm inputs from cache, owned archive, then upstream."""
import argparse
import json
import os
from pathlib import Path
from lib.deps import fetch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--offline', action='store_true')
args = parser.parse_args()
if args.offline:
    os.environ['DEPS_OFFLINE'] = '1'
lock = json.loads((Path(__file__).resolve().parent / 'crosvm.lock.json').read_text())
for rel, expected in lock['inputs'].items():
    fetch(lock['input_urls'][rel], args.output / rel, expected)
    print(rel)
