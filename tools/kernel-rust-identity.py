#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate the explicitly selected Rust tools before configuring a kernel."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

lock = json.loads((Path(__file__).resolve().parents[1] / 'kernel/rust-toolchain.json').read_text())
for selector, expected in lock['files'].items():
    variable, _, suffix = selector.partition('/')
    if not os.environ.get(variable):
        raise SystemExit(f'Set {variable} from kernel/rust-toolchain.json before building')
    path = Path(os.environ[variable]) / suffix if suffix else Path(os.environ[variable])
    with path.open('rb') as f:
        actual = hashlib.file_digest(f, 'sha256').hexdigest()
    if actual != expected:
        raise SystemExit(f'Pinned kernel Rust input differs: {selector}')
for variable, key in [('RUSTC', 'rustc_version'), ('BINDGEN', 'bindgen_version')]:
    actual = subprocess.check_output([os.environ[variable], '--version'], text=True).strip()
    if actual != lock[key]:
        raise SystemExit(f'Pinned {variable} version differs: {actual}')
print(json.dumps(lock, sort_keys=True, separators=(',', ':')))
