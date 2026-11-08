#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Adapted for piano, 2026-10-11; see mesa/snap/provenance.json.
"""Package a fresh core24 Mesa build as a gpu-2404 content provider.

Overlay the verified Mesa output on the complete, pinned stock mesa-2404 snap.
Non-Mesa payload, wrapper and component hooks remain byte-for-byte upstream.
Packaging needs no emulation; compilation still uses the pinned core24 SDK.
This does not install, connect or publish a snap.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

REPO = Path(__file__).resolve().parents[1]
LIB = Path('usr/lib/aarch64-linux-gnu')
def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root):
    files = {}
    for path in sorted(root.rglob('*')):
        name = str(path.relative_to(root))
        if path.is_symlink():
            files[name] = {'symlink': os.readlink(path)}
        elif path.is_file():
            files[name] = {'sha256': sha(path), 'mode': path.stat().st_mode & 0o7777}
    return files


def mesa_payload(name):
    path = Path(name)
    return (path.is_relative_to(LIB) and '.so' in path.name
            or name.startswith(('usr/share/glvnd/egl_vendor.d/',
                                'usr/share/vulkan/icd.d/', 'usr/share/drirc.d/')))


def destination(name):
    return name.replace('usr/share/drirc.d/', 'drirc.d/', 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--build-output', type=Path, help='Completed core24 SDK build with its stage')
    inputs.add_argument('--reuse-mesa-build', type=Path,
                        help='Previous provider build; reuse only hash-verified Mesa files, not its runtime')
    parser.add_argument('--base-snap', type=Path, required=True, help='Locked stock mesa-2404 arm64 snap')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base_lock = json.loads((REPO / 'mesa/snap/mesa-2404.json').read_text())
    if sha(args.base_snap) != base_lock['sha256']:
        parser.error('stock mesa-2404 snap does not match its lock')
    build = (args.build_output or args.reuse_mesa_build).resolve()
    result = json.loads((build / 'build-result.json').read_text())
    identity = json.loads((build / 'input.json').read_text())
    source = json.loads((REPO / 'mesa/source.json').read_text())
    if (result['profile'] != 'gpu-2404' or result['prefix'] != '/usr'
            or result['input_sha256'] != sha(build / 'input.json') or identity['source'] != source
            or identity['recipe_sha256'] != sha(REPO / 'tools/build-mesa.py')):
        parser.error('incompatible or unverified Mesa build')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    recipe = REPO / 'packaging/mesa-snap'
    with tempfile.TemporaryDirectory(prefix='prime-', dir=output) as temp:
        root = Path(temp) / 'prime'
        subprocess.run(['unsquashfs', '-no-progress', '-d', str(root), str(args.base_snap)], check=True)
        original = inventory(root)
        metadata = yaml.safe_load((root / 'meta/snap.yaml').read_text())
        if (metadata['name'] != base_lock['name'] or metadata['version'] != base_lock['version']
                or metadata['architectures'] != ['arm64'] or metadata['base'] != 'core24'):
            parser.error('stock snap metadata differs from lock')
        reused = None
        if args.reuse_mesa_build:
            previous = json.loads((build / 'manifest.json').read_text())
            snap = build / previous['package']['file']
            if (snap.name != previous['package']['file'] or sha(snap) != previous['package']['sha256']
                    or previous['source'] != source or previous['build'] != result):
                parser.error('previous Mesa provider differs from its build receipt')
            stage = Path(temp) / 'mesa'
            subprocess.run(['unsquashfs', '-no-progress', '-d', str(stage), str(snap)], check=True)
            reused = previous['package']
            mesa_license = stage / 'usr/share/doc/piano-mesa-2404/mesa-license.rst'
            epoch = str(int(snap.stat().st_mtime))
        else:
            stage = build / 'stage'
            mesa_license = build / ('mesa-' + source['version']) / 'docs/license.rst'
            epoch = (build / 'source-date-epoch').read_text().strip()
        overlay = {}
        for name in sorted(set(result['files_sha256']) | set(result['symlinks'])):
            if not mesa_payload(name):
                continue
            target = destination(name)
            src = stage / (target if reused else name)
            dst = root / target
            dst.parent.mkdir(parents=True, exist_ok=True)
            if name in result['symlinks']:
                if not src.is_symlink() or os.readlink(src) != result['symlinks'][name]:
                    parser.error('changed Mesa build symlink: ' + name)
                if dst.exists() or dst.is_symlink():
                    dst.unlink()
                dst.symlink_to(result['symlinks'][name])
            else:
                data = src.read_bytes()
                if name.startswith(('usr/share/glvnd/', 'usr/share/vulkan/')):
                    # Old provider builds already normalized the manifest's absolute library path.
                    raw = json.loads(data)
                    library = Path(raw['ICD']['library_path']).name
                    if not reused and sha(src) != result['files_sha256'][name]:
                        parser.error('changed Mesa manifest: ' + name)
                    # For reuse, these normalized bytes are authenticated by the entire
                    # previous snap hash, not by the pre-normalization stage hash.
                    raw['ICD']['library_path'] = library
                    data = (json.dumps(raw, indent=2) + '\n').encode()
                elif sha(src) != result['files_sha256'][name]:
                    parser.error('changed Mesa build file: ' + name)
                if dst.is_symlink():
                    dst.unlink()
                dst.write_bytes(data)
                dst.chmod(src.stat().st_mode & 0o777)
            overlay[target] = name
        docs = root / 'usr/share/doc/piano-mesa-2404'
        docs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mesa_license, docs / 'mesa-license.rst')
        shutil.copy2(REPO / 'LICENSES/oneplus-mesa-tools-GPL-2.0-only.txt', docs / 'packaging-license')
        provenance = {'source': source, 'build': result, 'stock_provider': base_lock,
                      'mesa_overlay': overlay, 'reused_mesa_package': reused,
                      'recipe_sha256': {str(p.relative_to(REPO)): sha(p) for p in
                                        [Path(__file__), recipe / 'VERSION', REPO / 'mesa/snap/mesa-2404.json']}}
        fingerprint = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()[:8]
        version = source['version'] + '+piano' + (recipe / 'VERSION').read_text().strip() + '.' + fingerprint
        # Preserve stock wrapper, hooks, component declarations and slot layout. Its
        # install hook disables the inherited monitor when no kernel component is connected.
        metadata['name'] = 'piano-mesa-2404'
        metadata['version'] = version
        # Piano uses the system kernel, not an optional kernel content snap.
        # Export one directory so gpu-2404-2 cannot be created by our own slot.
        metadata['slots']['gpu-2404']['read'] = ['$SNAP']
        (root / 'meta/snap.yaml').write_text(json.dumps(metadata, indent=2) + '\n')
        after = inventory(root)
        missing = sorted(set(original) - set(after))
        changed = sorted(name for name in original if name in after and original[name] != after[name])
        unexpected = sorted(set(changed) - set(overlay) - {'meta/snap.yaml'})
        if missing or unexpected:
            raise ValueError(f'stock content contract broken: missing={missing}, changed={unexpected}')
        provenance['stock_content_diff'] = {'stock_files': len(original), 'missing': missing,
                                            'changed': changed, 'added': sorted(set(after) - set(original))}
        (docs / 'build.json').write_text(json.dumps(provenance, indent=2) + '\n')
        snap = output / ('piano-mesa-2404_' + version + '_arm64.snap')
        subprocess.run(['mksquashfs', str(root), str(snap), '-noappend', '-all-root', '-no-xattrs',
                        '-comp', 'xz', '-processors', str(os.cpu_count() or 1), '-mkfs-time', epoch],
                       check=True, env={k: v for k, v in os.environ.items() if k != 'SOURCE_DATE_EPOCH'})
        provenance.update(version=version, package={'file': snap.name, 'sha256': sha(snap)})
        (output / 'manifest.json').write_text(json.dumps(provenance, indent=2) + '\n')
        for name in ('build-result.json', 'input.json', 'build-root.json'):
            if (build / name).is_file():
                shutil.copy2(build / name, output / name)
        print(json.dumps(provenance['package']))


if __name__ == '__main__':
    main()
