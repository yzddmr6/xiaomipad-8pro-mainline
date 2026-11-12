# SPDX-License-Identifier: MIT
"""Validate and stage the pinned firmware files without a package-manager dependency.

The locks are firmware/piano-*.json; the repository's firmware/ tree carries the
files they select. Callers own a fresh staging directory and its cleanup. This
module does not download firmware, install packages, enable services or access a device.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat

REPO_FIRMWARE = Path(__file__).resolve().parents[1] / 'firmware'
FIRMWARE_ROOT = PurePosixPath('usr/lib/firmware')
# Remoteproc images required by the board DT; service maps are userspace data.
KERNEL_DSP_IMAGES = ('adsp.mdt', 'adsp_dtb.mdt', 'cdsp.mdt', 'cdsp_dtb.mdt')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_lock(path):
    lock = json.loads(Path(path).read_text())
    if lock.get('schema') != 1 or not lock.get('files'):
        raise ValueError('Expected a nonempty version-1 DSP firmware lock')
    names = [row['name'] for row in lock['files']]
    if len(names) != len(set(names)) or any(
            not re.fullmatch(r'[A-Za-z0-9_+.-]+', name) or name in ('.', '..') for name in names):
        raise ValueError('Firmware lock must contain unique flat filenames')
    for row in lock['files']:
        if (type(row['bytes']) is not int or row['bytes'] < 0 or
                not re.fullmatch(r'[0-9a-f]{64}', row['sha256'])):
            raise ValueError(f'Invalid firmware identity: {row["name"]}')
    directory = lock['firmware_directory']
    if PurePosixPath(directory).is_absolute() or any(p in ('', '.', '..') for p in directory.split('/')):
        raise ValueError('Firmware directory must be a safe relative payload path')
    images = lock['images']
    if len(images) != len(set(images)) or any(n not in names or not n.endswith('.mdt') for n in images):
        raise ValueError('Firmware images must name distinct locked MDT headers')
    return lock


def select_files(lock, images=None):
    """All locked inputs, or only named MDT images and their split segments.

    Selecting images excludes service-map JSNs. A full package retains all
    locked files, including those maps, just as the original Debian builder did.
    """
    if images is None:
        return lock['files']
    if not images or len(images) != len(set(images)) or any(n not in lock['images'] for n in images):
        raise ValueError('Select distinct MDT image names from the firmware lock')
    patterns = [re.compile(re.escape(n[:-4]) + r'\.(?:mdt|b[0-9]+)') for n in images]
    return [r for r in lock['files'] if any(p.fullmatch(r['name']) for p in patterns)]


def stage_payload(lock, firmware_dir, stage, images=None):
    """Copy selected regular files to an owned stage and verify the copied bytes."""
    source_dir, stage = Path(firmware_dir), Path(stage)
    rows = select_files(lock, images)
    for row in rows:
        source = source_dir / row['name']
        status = source.lstat()
        if not stat.S_ISREG(status.st_mode) or status.st_size != row['bytes']:
            raise ValueError(f'Expected matching regular firmware file: {row["name"]}')
    payload = stage / lock['firmware_directory']
    payload.mkdir(parents=True, exist_ok=False)
    for row in rows:
        target = payload / row['name']
        shutil.copyfile(source_dir / row['name'], target)
        target.chmod(0o644)
        if target.stat().st_size != row['bytes'] or sha(target) != row['sha256']:
            raise ValueError(f'Firmware hash mismatch: {row["name"]}')
    return rows


def _identity(path, row):
    parts = path.split('/')
    if any(not re.fullmatch(r'[A-Za-z0-9_+.-]+', p) or p in ('.', '..') for p in parts):
        raise ValueError(f'Firmware lock path must be a safe relative path: {path}')
    if type(row['bytes']) is not int or row['bytes'] < 0 or not re.fullmatch(r'[0-9a-f]{64}', row['sha256']):
        raise ValueError(f'Invalid firmware identity: {path}')
    return {'path': path, 'bytes': row['bytes'], 'sha256': row['sha256']}


def load_tree(lock_dir=REPO_FIRMWARE):
    """Rows {path, bytes, sha256} below /usr/lib/firmware for every packaged file.

    The DSP lock (a file list in firmware_directory) contributes the kernel's
    images; path-keyed locks contribute all files, below install_dir if given.
    """
    rows = []
    for lock_path in sorted(Path(lock_dir).glob('piano-*.json')):
        lock = json.loads(lock_path.read_text())
        if isinstance(lock.get('files'), list):
            lock = load_lock(lock_path)
            prefix = PurePosixPath(lock['firmware_directory']).relative_to(FIRMWARE_ROOT)
            rows += [_identity(f'{prefix}/{r["name"]}', r) for r in select_files(lock, KERNEL_DSP_IMAGES)]
            continue
        if lock.get('schema') != 1 or not lock.get('files'):
            raise ValueError(f'Expected a nonempty version-1 firmware lock: {lock_path.name}')
        prefix = PurePosixPath(lock.get('install_dir', '/' + str(FIRMWARE_ROOT))).relative_to('/' / FIRMWARE_ROOT)
        rows += [_identity(str(prefix / name), row) for name, row in lock['files'].items()]
    paths = [r['path'] for r in rows]
    if not rows or len(paths) != len(set(paths)):
        raise ValueError('Firmware locks must select a nonempty set of distinct paths')
    return sorted(rows, key=lambda r: r['path'])


def stage_tree(rows, source, firmware_root):
    """Copy the locked regular files from a tree with the kernel's layout and verify the copies."""
    source, firmware_root = Path(source), Path(firmware_root)
    for row in rows:
        path = source / row['path']
        status = path.lstat()
        if (not stat.S_ISREG(status.st_mode) or status.st_size != row['bytes'] or
                not path.resolve().is_relative_to(source.resolve())):
            raise ValueError(f'Expected matching regular firmware file: {row["path"]}')
    firmware_root.mkdir(parents=True, exist_ok=False)
    for row in rows:
        target = firmware_root / row['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / row['path'], target)
        target.chmod(0o644)
        if target.stat().st_size != row['bytes'] or sha(target) != row['sha256']:
            raise ValueError(f'Firmware hash mismatch: {row["path"]}')
    return rows
