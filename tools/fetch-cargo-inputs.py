#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Materialize every registry package in the locked Cargo graph, without an index."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import tomllib
from lib.deps import fetch, check_hash, REPO


def prepare(lockfile, cache, vendor):
    lock = tomllib.loads(Path(lockfile).read_text())
    vendor = Path(vendor)
    vendor.mkdir(parents=True, exist_ok=True)
    count = 0
    for package in lock['package']:
        if not package.get('source'):
            continue
        if package['source'] != 'registry+https://github.com/rust-lang/crates.io-index':
            raise ValueError('Unpreserved Cargo source: ' + package['source'])
        name, version, expected = package['name'], package['version'], package['checksum']
        filename = f'{name}-{version}.crate'
        url = f'https://static.crates.io/crates/{name}/{filename}'
        archive = fetch(url, Path(cache) / filename, expected)
        target = vendor / f'{name}-{version}'
        # Always rebuild from the checked original, so edited extracted files
        # never turn a valid archive hash into trust of different source bytes.
        with tempfile.TemporaryDirectory(dir=vendor) as temp:
            with tarfile.open(archive) as source:
                source.extractall(temp, filter='data')
            tree = Path(temp) / target.name
            if not tree.is_dir() or len(list(Path(temp).iterdir())) != 1:
                raise ValueError('Unexpected crate layout: ' + filename)
            files = {}
            for path in sorted(tree.rglob('*')):
                if path.is_file():
                    with path.open('rb') as stream:
                        files[str(path.relative_to(tree))] = hashlib.file_digest(stream, 'sha256').hexdigest()
            (tree / '.cargo-checksum.json').write_text(json.dumps({'files': files, 'package': expected}) + '\n')
            if target.exists():shutil.rmtree(target)
            tree.rename(target)
        count += 1
    return count


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lock', type=Path, default=REPO / 'device/virtualization/Cargo.lock')
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--vendor', type=Path, required=True)
    p.add_argument('--offline', action='store_true')
    a = p.parse_args()
    if a.offline:os.environ['DEPS_OFFLINE'] = '1'
    print('Registry packages:', prepare(a.lock, a.cache, a.vendor))


if __name__ == '__main__':
    main()
