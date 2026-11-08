#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fetch pinned firmware source, host tools and the original signed driver ZIP."""
import argparse
import json
import os
from pathlib import Path
from lib.deps import fetch, source_repo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if args.offline:
        os.environ['DEPS_OFFLINE'] = '1'
    repo = Path(__file__).resolve().parents[1]
    lock = json.loads((repo / 'tools/windows.lock.json').read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    for item in [*lock['host_debs'], lock['drivers']]:
        fetch(item.get('asset_url', item['url']), args.output / item['filename'], item['sha256'])
    source_repo('edk2-gunyah', lock['firmware_preserved'], cache=args.output / 'firmware')


if __name__ == '__main__':
    main()
