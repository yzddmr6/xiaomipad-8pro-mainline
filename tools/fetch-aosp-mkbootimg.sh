#!/bin/sh
# SPDX-License-Identifier: MIT
# Adapted from the liuqin mainline project; see NOTICE.

set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
extract_dir="$project_root/tools/local/aosp-mkbootimg"
commit=954bc3ead5e679005fddf3484d247f2557b3c2c9
url="https://android.googlesource.com/platform/system/tools/mkbootimg"
mkbootimg_sha256=37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c
unpack_sha256=06b54dd9a07c5281778e29e234e76f6e3faee8bf0c904a5ef88fdee30eeed12e

python3 - "$project_root" "$extract_dir" <<'PYCODE'
import sys, tarfile
from pathlib import Path
repo, target = map(Path, sys.argv[1:])
sys.path.insert(0, str(repo / 'tools'))
from lib.deps import fetch, inventory
entry = next(r for r in inventory()['files'] if r['name'] == 'aosp-mkbootimg')
archive = fetch(entry['upstream'][0], target.parent / entry['filename'], entry['sha256'])
# Never overwrite user edits in an existing tool tree.
if not target.exists():
    target.mkdir(parents=True)
    with tarfile.open(archive) as source:
        source.extractall(target, filter='data')
PYCODE
echo "$mkbootimg_sha256  $extract_dir/mkbootimg.py" | sha256sum --check
echo "$unpack_sha256  $extract_dir/unpack_bootimg.py" | sha256sum --check

printf 'AOSP tag: android-16.0.0_r4\ncommit: %s\n' "$commit"
python3 "$extract_dir/mkbootimg.py" --help >/dev/null
python3 "$extract_dir/unpack_bootimg.py" --help >/dev/null
