#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Package a release for upload, or restore and verify it entirely on the host.

  package-release-assets.py --release RELEASE --output ASSETS
  package-release-assets.py verify --assets ASSETS --output RESTORED

Requires Python 3.11+, zstd, sha256sum and (for verify) unzip. Output must not
exist and must be outside the input tree. No device or network is accessed.
The six attachment categories are boot/tools/packages ZIPs, rootfs zstd parts,
SHA256SUMS.assets and assets-manifest.json. Boot ZIP also carries all release
metadata, including the original SHA256SUMS, notes and rootfs metadata.

The manifest inventories payload attachments; SHA256SUMS.assets covers those
and the manifest. Integrity metadata cannot contain its own checksum. The
output RELEASE-NOTES.md is the Release body, NOT an upload attachment; the
archived and standalone notes are identical and use the bilingual release format.
Inputs are checked before construction; publication is a separate action. Failed runs remove their temporary output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

from release_notes import asset_prefix, restore_commands, validate_notes

PART_BYTES = 1_900_000_000
ATTACHMENT_LIMIT = 2 << 30
ROOT_IMAGE = 'rootfs/root.sparse.img'
CHUNK = 8 << 20


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def safe_path(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '..' in path.parts or str(path) != name
            or any(c in name for c in '\\\r\n\0')):
        raise ValueError(f'Unsafe relative path: {name!r}')
    return name


def file_tree(directory):
    files = {}
    for path in sorted(directory.rglob('*')):
        mode = path.lstat().st_mode
        name = safe_path(path.relative_to(directory).as_posix())
        if stat.S_ISREG(mode):
            files[name] = path
        elif not stat.S_ISDIR(mode):
            raise ValueError(f'Only regular files and directories are supported: {name}')
    return files


def read_sums(path):
    sums = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64}) [ *](.+)', line)
        if not match:
            raise ValueError(f'Malformed checksum line in {path.name}')
        name = safe_path(match[2])
        if name in sums:
            raise ValueError(f'Duplicate checksum: {name}')
        sums[name] = match[1]
    if not sums:
        raise ValueError(f'Empty checksum file: {path.name}')
    return sums


def check_sums(directory, filename, expected):
    sums = read_sums(directory / filename)
    if set(sums) != set(expected):
        raise ValueError(f'{filename}: file inventory differs (missing or unexpected files)')
    for name in sums:
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing regular file: {name}')
    subprocess.run(['sha256sum', '--strict', '-c', filename], cwd=directory, check=True)
    return sums


def output_path(source, output):
    source, output = source.resolve(strict=True), output.resolve()
    if not source.is_dir():
        raise ValueError('Input must be a directory')
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError('Input and output directories must be disjoint')
    if output.exists():
        raise ValueError(f'Output already exists: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    return source, output


def report(label, started):
    seconds = round(time.monotonic() - started, 3)
    print(f'{label}: {seconds:.3f} s', file=sys.stderr, flush=True)
    return seconds


def make_zip(source, target, names):
    started = time.monotonic()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(names):
            archive.write(source / name, name)
    if target.stat().st_size >= ATTACHMENT_LIMIT:
        raise ValueError(f'{target.name} exceeds the single-attachment limit')
    digest = sha(target)
    return {'name': target.name, 'bytes': target.stat().st_size, 'sha256': digest,
            'contents': sorted(names), 'restore_command': f'unzip {target.name}',
            'seconds': report(target.name, started)}


def split_stream(stream, output, prefix, part_bytes):
    """Stream bounded parts; hash during writing and never emit an empty tail."""
    if not 0 < part_bytes < ATTACHMENT_LIMIT:
        raise ValueError('Invalid part size')
    parts = []
    while True:
        started = time.monotonic()
        block = stream.read(min(CHUNK, part_bytes))
        if not block:
            return parts
        name = f'{prefix}-rootfs.img.zst.part{len(parts)}'
        digest, size = hashlib.sha256(), 0
        with (output / name).open('xb') as target:
            while block:
                target.write(block)
                digest.update(block)
                size += len(block)
                if size == part_bytes:
                    break
                block = stream.read(min(CHUNK, part_bytes - size))
        parts.append({'name': name, 'bytes': size, 'sha256': digest.hexdigest(),
                      'contents': [ROOT_IMAGE], 'part_index': len(parts),
                      'seconds': report(name, started)})


def compress_root(source, output, prefix, part_bytes):
    command = ['zstd', '-T0', '-10', '--long=27', '-c', str(source / ROOT_IMAGE)]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        try:
            parts = split_stream(process.stdout, output, prefix, part_bytes)
        except BaseException:
            process.kill()
            raise
        finally:
            process.stdout.close()
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, command)
    if not parts:
        raise ValueError('zstd produced no output')
    restore = restore_commands(prefix.removeprefix('piano-ubuntu-'), [p['name'] for p in parts])
    command = next(line for line in restore.splitlines() if line.startswith('cat '))
    for part in parts:
        part['restore_command'] = command
    return parts


def package(release, output, *, part_bytes=PART_BYTES):
    started = time.monotonic()
    release, output = output_path(release, output)
    files = file_tree(release)
    for required in ('SHA256SUMS', 'release-manifest.json', 'RELEASE-NOTES.md', ROOT_IMAGE):
        if required not in files:
            raise ValueError(f'Missing release file: {required}')
    for directory in ('boot', 'tools', 'debs', 'rootfs'):
        if not (release / directory).is_dir():
            raise ValueError(f'Missing release directory: {directory}')
    version = json.loads(files['release-manifest.json'].read_text())['release_name']
    prefix = asset_prefix(version)
    notes = files['RELEASE-NOTES.md'].read_text(encoding='utf-8')
    validate_notes(notes, version)
    check_sums(release, 'SHA256SUMS', set(files) - {'SHA256SUMS'})
    validation_seconds = report('Input verification', started)
    # Construct beside the destination; a failed compression/check never leaves
    # an apparently complete attachment directory behind.
    with tempfile.TemporaryDirectory(prefix=f'.{output.name}-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'assets'
        stage.mkdir()
        assets = []
        groups = {'tools': [n for n in files if n.startswith('tools/')],
                  'packages': [n for n in files if n.startswith(('debs/', 'optional/virtualization/', 'repositories/'))]}
        groups['boot'] = sorted(set(files) - set(groups['tools']) - set(groups['packages']) - {ROOT_IMAGE})
        for kind in ('boot', 'tools', 'packages'):
            assets.append(make_zip(release, stage / f'{prefix}-{kind}.zip', groups[kind]))
        compression_started = time.monotonic()
        parts = compress_root(release, stage, prefix, part_bytes)
        compression_seconds = report('Rootfs compression and splitting', compression_started)
        assets.extend(parts)
        manifest = {
            'schema': 1, 'release_name': version, 'part_bytes': part_bytes,
            'compression': ['zstd', '-T0', '-10', '--long=27'],
            'assets': assets, 'release_files': sorted(files),
            'rootfs_parts': [p['name'] for p in parts],
            'restore_commands': restore_commands(version, [p['name'] for p in parts]).splitlines(),
            'integrity': 'SHA256SUMS.assets covers every payload attachment and this manifest; '
                         'checksum metadata has no self-checksum.',
            'release_body': 'RELEASE-NOTES.md is for the Release body, not an upload attachment. '
                            'The original notes are preserved inside boot.zip.',
            'timings_seconds': {'input_verification': validation_seconds,
                                'rootfs_compression': compression_seconds},
        }
        (stage / 'assets-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        sums = {a['name']: a['sha256'] for a in assets}
        sums['assets-manifest.json'] = sha(stage / 'assets-manifest.json')
        (stage / 'SHA256SUMS.assets').write_text(''.join(f'{d}  {n}\n' for n, d in sorted(sums.items())))
        sizes = {name: (stage / name).stat().st_size for name in [*sums, 'SHA256SUMS.assets']}
        (stage / 'RELEASE-NOTES.md').write_text(notes, encoding='utf-8')
        os.rename(stage, output)
    return {'output': str(output), 'seconds': report('Packaging total', started), 'sizes': sizes}


def zip_inventory(path):
    with zipfile.ZipFile(path) as archive:
        names = []
        for entry in archive.infolist():
            name = safe_path(entry.filename)
            mode = entry.external_attr >> 16
            if entry.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ValueError(f'Unsupported ZIP member: {name}')
            if name in names:
                raise ValueError(f'Duplicate ZIP member: {name}')
            names.append(name)
        return names


def verify(assets, output):
    started = time.monotonic()
    assets, output = output_path(assets, output)
    actual_files = file_tree(assets)
    # Verify checksums BEFORE trusting the manifest or extracting archives.
    checksummed = set(actual_files) - {'SHA256SUMS.assets', 'RELEASE-NOTES.md'}
    sums = check_sums(assets, 'SHA256SUMS.assets', checksummed)
    manifest = json.loads((assets / 'assets-manifest.json').read_text())
    if manifest['schema'] != 1:
        raise ValueError('Unsupported assets manifest schema')
    prefix = asset_prefix(manifest['release_name'])
    notes = (assets / 'RELEASE-NOTES.md').read_text(encoding='utf-8')
    validate_notes(notes, manifest['release_name'])
    names = [entry['name'] for entry in manifest['assets']]
    zips = [f'{prefix}-{kind}.zip' for kind in ('boot', 'tools', 'packages')]
    parts = manifest['rootfs_parts']
    expected_parts = [f'{prefix}-rootfs.img.zst.part{i}' for i in range(len(parts))]
    if not parts or parts != expected_parts or names != zips + parts:
        raise ValueError('Missing, duplicate or out-of-order attachments/parts')
    if set(sums) != set(names) | {'assets-manifest.json'}:
        raise ValueError('Attachment checksum inventory differs from manifest')
    part_bytes = manifest['part_bytes']
    if not isinstance(part_bytes, int) or not 0 < part_bytes < ATTACHMENT_LIMIT:
        raise ValueError('Invalid part size in manifest')
    for entry in manifest['assets']:
        size = (assets / entry['name']).stat().st_size
        if size != entry['bytes'] or entry['sha256'] != sums[entry['name']]:
            raise ValueError('Attachment size/hash differs from manifest')
        if size >= ATTACHMENT_LIMIT:
            raise ValueError('Attachment exceeds upload limit')
        if entry['name'] in parts and (not 0 < size <= part_bytes or
                                       entry['name'] != parts[-1] and size != part_bytes):
            raise ValueError('Incorrect rootfs part size')
    inventory = {ROOT_IMAGE}
    for entry in manifest['assets'][:3]:
        contents = zip_inventory(assets / entry['name'])
        if sorted(contents) != entry['contents'] or inventory.intersection(contents):
            raise ValueError('Overlapping or incorrect archive inventory')
        inventory.update(contents)
    if sorted(inventory) != manifest['release_files']:
        raise ValueError('Release file inventory differs from manifest')
    # A file may not also be a parent directory for another member.
    for name in inventory:
        if any(str(parent) in inventory for parent in PurePosixPath(name).parents):
            raise ValueError(f'Conflicting archive paths: {name}')
    with tempfile.TemporaryDirectory(prefix=f'.{output.name}-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'release'
        stage.mkdir()
        for name in zips:
            subprocess.run(['unzip', '-q', str(assets / name), '-d', str(stage)], check=True)
        archived_notes = (stage / 'RELEASE-NOTES.md').read_text(encoding='utf-8')
        validate_notes(archived_notes, manifest['release_name'])
        if archived_notes != notes:
            raise ValueError('Release body differs from the archived notes')
        (stage / 'rootfs').mkdir(exist_ok=True)
        with (stage / ROOT_IMAGE).open('xb') as target:
            command = ['zstd', '-d', '-c']
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=target) as process:
                try:
                    for name in parts:
                        with (assets / name).open('rb') as source:
                            shutil.copyfileobj(source, process.stdin, CHUNK)
                    process.stdin.close()
                except BaseException:
                    process.kill()
                    raise
                if process.wait():
                    raise subprocess.CalledProcessError(process.returncode, command)
        restored = file_tree(stage)
        if set(restored) != inventory:
            raise ValueError('Restored directory differs from inventory')
        check_sums(stage, 'SHA256SUMS', set(restored) - {'SHA256SUMS'})
        os.rename(stage, output)
    return {'output': str(output), 'files_verified': len(inventory) - 1,
            'seconds': report('Verification total', started)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    pack = sub.add_parser('pack', help='Package a local release')
    pack.add_argument('--release', type=Path, required=True)
    pack.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('verify', help='Restore attachments and check all original files')
    check.add_argument('--assets', type=Path, required=True)
    check.add_argument('--output', type=Path, required=True)
    args = sys.argv[1:]
    if args and args[0].startswith('--') and args[0] not in ('--help', '-h'):
        args = ['pack', *args]
    a = parser.parse_args(args)
    try:
        result = verify(a.assets, a.output) if a.command == 'verify' else package(a.release, a.output)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        parser.exit(1, f'error: {error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
