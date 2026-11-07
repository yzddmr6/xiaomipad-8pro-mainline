#!/bin/sh
# SPDX-License-Identifier: MIT
# Fetch only the locked upstream script, preserving its copyright and licence.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python3 - "$repo" <<'PY'
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo / "tools"))
from lib.deps import fetch, inventory
pin = json.loads((repo / 'tools/aosp-avb.json').read_text())
target = repo / 'tools/local/aosp-avb/avbtool.py'
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    data = target.read_bytes()
else:
    url = f'{pin["url"]}/+/{pin["commit"]}/avbtool.py?format=TEXT'
    entry = next(r for r in inventory()['files'] if r['name'] == 'aosp-avb')
    raw = fetch(url, target.parent / 'avbtool.py.base64', entry['sha256'])
    data = base64.b64decode(raw.read_bytes(), validate=True)
if hashlib.sha256(data).hexdigest() != pin['avbtool_sha256']:
    raise SystemExit('avbtool.py differs from tools/aosp-avb.json; refusing to use or overwrite it')
if not target.exists():
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        temporary.chmod(0o644)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
version = subprocess.check_output([sys.executable, str(target), 'version'], text=True).strip()
if version != pin['version']:
    raise SystemExit(f'Unexpected avbtool version: {version}')
print(f'{target}: OK ({version}, {pin["commit"]})')
PY
