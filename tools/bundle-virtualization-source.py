#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bundle corresponding sources without build logs, local configuration or credentials."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build', type=Path, required=True)
p.add_argument('--inputs', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
repo = Path(__file__).resolve().parents[1]
lock = json.loads((repo / 'tools/crosvm.lock.json').read_text())
a.output.parent.mkdir(parents=True, exist_ok=True)
os.setpriority(os.PRIO_PROCESS, 0, max(10, os.getpriority(os.PRIO_PROCESS, 0)))
files = [
    'LICENSE', 'NOTICE', 'device/virtualization', 'packaging/piano-vm',
    'packaging/piano-crosvm', 'docs/VIRTUALIZATION.md', 'docs/VIRTUALIZATION.zh-CN.md',
    'docs/BUILD-VIRTUALIZATION.md', 'deps', 'tools/lib/deps.py',
    'tools/build-device-debs.py', 'tools/build-virtualization-debs.py',
    'tools/build-crosvm.py', 'tools/fetch-crosvm-inputs.py',
    'tools/fetch-cargo-inputs.py', 'tools/fetch-deps.py',
    'tools/bundle-virtualization-source.py', 'tools/crosvm.lock.json',
    'tools/windows.lock.json', 'tools/build-windows-assets.py',
    'tools/fetch-windows-inputs.py',
]
cmd = ['tar', '--sort=name', '--mtime=@' + str(lock['source_date_epoch']),
       '--owner=0', '--group=0', '--numeric-owner', '--mode=u+rwX,go+rX,go-w',
       '--exclude=__pycache__', '-I', 'zstd -T0 -3', '-cf', str(a.output.resolve()),
       '-C', str(repo), *files]
config = a.build / 'cargo-home/config.toml'
if config.is_file():
    # Read the local path but never put the host-specific config into the bundle.
    source = tomllib.loads(config.read_text())['source']['preserved']['directory']
    vendor = Path(source).resolve(strict=True)
    if not vendor.is_dir():
        raise ValueError('Cargo directory source is missing')
    cmd += [r'--transform=flags=rh;s,^\./,cargo-vendor/,;s,^\.$,cargo-vendor,',
            '-C', str(vendor), '.']
else:
    # Earlier locked builds used a registry cache; keep those source bundles valid.
    cmd += ['--transform=s,^cargo-home/,cargo-cache/,', '-C', str(a.build.resolve()),
            'cargo-home/registry/cache', 'cargo-home/registry/index']
for name in ('crosvm-96e84c3.tar.gz', 'minijail-7845d899.tar.gz'):
    cmd += ['--transform=s,^' + name + '$,inputs/' + name + ',',
            '-C', str(a.inputs.resolve()), name]
subprocess.run(cmd, check=True)
