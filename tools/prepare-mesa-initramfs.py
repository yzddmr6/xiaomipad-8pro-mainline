#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Stage the isolated Mesa build for RAM diagnostics only (no device I/O).

Host packages use build-host-mesa.py and package-mesa.py. This diagnostic prefix
check never constrains their standard multiarch installation paths."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mesa-build', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    lock = json.loads((repo / 'mesa/source.json').read_text())
    receipt_path = args.mesa_build / 'manifest.json'
    receipt = json.loads(receipt_path.read_text())
    for key in ('url', 'tag', 'commit', 'patch', 'prefix'):
        if receipt[key] != lock[key]:
            raise ValueError(f'Mesa build does not match source lock: {key}')
    if receipt['patch_sha256'] != sha(repo / lock['patch']):
        raise ValueError('Mesa patch differs from the recorded build')
    if receipt.get('additional_patches', []) != lock.get('additional_patches', []):
        raise ValueError('Mesa additional patches differ from the source lock')
    for patch in lock.get('additional_patches', []):
        if sha(repo / patch['path']) != patch['sha256']:
            raise ValueError('Mesa additional patch differs from the recorded build')
    prefix = lock['prefix'].lstrip('/')
    if not prefix.startswith('opt/piano/') or '..' in Path(prefix).parts:
        raise ValueError('RAM diagnostic Mesa must use its isolated /opt/piano prefix')
    source = args.mesa_build / 'stage'
    tree = source / prefix
    files, links = {}, {}
    for path in sorted(tree.rglob('*')):
        name = str(path.relative_to(source))
        if not re.fullmatch(r'[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*', name):
            raise ValueError('Unsafe Mesa path')
        if path.is_symlink():
            target = os.readlink(path)
            resolved = path.resolve(strict=True)
            if Path(target).is_absolute() or not resolved.is_relative_to(tree.resolve()):
                raise ValueError('Mesa symlink escapes the isolated prefix')
            if str(resolved.relative_to(source.resolve())) not in receipt['files']:
                raise ValueError('Mesa symlink target is not a recorded regular file')
            links[name] = target
        elif path.is_file():
            digest = sha(path)
            if receipt['files'].get(name) != digest:
                raise ValueError(f'Mesa file changed: {name}')
            files[name] = digest
        elif not path.is_dir():
            raise ValueError('Mesa tree contains a special file')
    if files != receipt['files']:
        raise ValueError('Mesa files missing from the build')
    stage = args.stage.resolve()
    stage.mkdir(parents=True, exist_ok=False)
    shutil.copytree(tree, stage / prefix, symlinks=True)
    (stage / 'etc').mkdir()
    metadata = dict(source=lock, build_manifest_sha256=sha(receipt_path), files=files, symlinks=links)
    (stage / 'etc/piano-mesa.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (stage / 'etc/piano-mesa-prefix').write_text(lock['prefix'] + '\n')
    (stage / 'etc/piano-mesa.sha256').write_text(''.join(f'{digest}  /{name}\n' for name, digest in files.items()))
    for path in sorted(stage.rglob('*')):
        name = '/' + str(path.relative_to(stage))
        if path.is_symlink():
            print(f'slink {name} {os.readlink(path)} 0777 0 0')
        elif path.is_dir() and name != '/etc':
            print(f'dir {name} 0755 0 0')
        elif path.is_file():
            print(f'file {name} {path} 0644 0 0')


if __name__ == '__main__':
    main()
