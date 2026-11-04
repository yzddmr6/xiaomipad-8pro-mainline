#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assemble a local release directory from a finished root image build; nothing is uploaded.

Layout of the output directory:
  boot/    the address-free Ubuntu boot set (boot, init_boot, vendor_boot, its
           SHA256SUMS, manifest, boot profile, DTB), boot-ram-check.img and
           boot-ram-maintenance.img (build-persistent-boot.py --maintenance
           --no-serial, same kernel and DTB) with maintenance-manifest.json
  rootfs/  root.sparse.img (for fastboot flash linux_rootfs), its manifest and
           offline verification
  debs/    the piano packages installed in the image (kernel, device support,
           firmware, Mesa, desktop defaults)
  tools/   piano-switch.py and the KernelSU module zip
  RELEASE-NOTES.md, release-manifest.json, SHA256SUMS
The data partition is not shipped; follow docs/INSTALL.en.md to create it.
--changes supplies a bilingual summary, updates and known limitations; a line of
'---' separates Chinese from English. The note template adds installation and
licensing links. Package this directory with package-release-assets.py; it keeps
the archived notes and Release body identical. Package identities remain in
release-manifest.json.
Before writing anything the tool checks that the boot set
carries no unit identity: no serial on its command line, no address properties
in its DTB, the stock-build-only boot profile, and prints what Fastboot writes
for every image (sparse_image.py): it stops if a sparse image carries FILL
beyond the limit or DONT_CARE on used ext4 blocks; the root image is judged by
the ext4 bitmap check recorded when it was encoded.  --forbid-file adds a byte
scan of every output for identifiers of a real unit (serial, Bluetooth or Wi-Fi
address), read from private files that are never copied or recorded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

import sparse_image
from release_notes import render_notes

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN_PROPERTIES = ('local-mac-address', 'local-bd-address', 'mac-address', 'serial-number')
SET_FILES = ('boot.img', 'init_boot.img', 'vendor_boot.img', 'SHA256SUMS', 'manifest.json',
             'profile.json', 'piano-boot.dtb', 'boot-ram-check.img')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def fdt_property_names(blob):
    """Property names used in a flattened devicetree (structure block walk)."""
    magic, _, off_struct, off_strings, _, _, _, _, _, size_struct = struct.unpack_from('>10I', blob)
    if magic != 0xd00dfeed:
        raise ValueError('Not a flattened devicetree')
    names, pos, end = set(), off_struct, off_struct + size_struct
    while pos < end:
        token = struct.unpack_from('>I', blob, pos)[0]
        pos += 4
        if token == 1:  # FDT_BEGIN_NODE: NUL-terminated name, padded
            pos = (blob.index(b'\0', pos) + 4) & ~3
        elif token == 3:  # FDT_PROP: len, nameoff, value
            length, nameoff = struct.unpack_from('>II', blob, pos)
            name_start = off_strings + nameoff
            names.add(blob[name_start:blob.index(b'\0', name_start)].decode())
            pos = (pos + 8 + length + 3) & ~3
        elif token == 9:  # FDT_END
            break
        elif token not in (2, 4):  # FDT_END_NODE, FDT_NOP
            raise ValueError(f'Bad FDT token {token}')
    return names


def identity_patterns(path):
    """Byte patterns of one private identifier file: a serial, an aa:bb:cc:dd:ee:ff address,
    a wlan_mac.bin style 12-digit address, or a boot profile (its serial)."""
    text = path.read_text().strip()
    if text.startswith('{'):
        text = json.loads(text)['serial']
    patterns = set()
    match = re.fullmatch(r'(?:wlan0=)?((?:[0-9A-Fa-f]{2}:?){6})', text)
    if match and len(match[1].replace(':', '')) == 12:
        raw = bytes.fromhex(match[1].replace(':', ''))
        for octets in (raw, raw[::-1]):
            patterns.add(octets)
            for case in (str.lower, str.upper):
                patterns.add(case(octets.hex()).encode())
                patterns.add(case(':'.join(f'{b:02x}' for b in octets)).encode())
    elif re.fullmatch(r'[A-Za-z0-9]{6,}', text):
        patterns.update({text.encode(), text.lower().encode(), text.upper().encode()})
    else:
        raise ValueError(f'{path}: not a serial, address or boot profile')
    return patterns


def scan(path, patterns, chunk=64 << 20):
    longest = max(len(p) for p in patterns)
    with path.open('rb') as f:
        tail = b''
        while block := f.read(chunk):
            data = tail + block
            if any(p in data for p in patterns):
                return True
            tail = data[-(longest - 1):]
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rootfs-build', type=Path, required=True, help='Output directory of build-rootfs-image.py')
    ap.add_argument('--boot-check', type=Path, help='result.json of check-rootfs-boot.py for this image')
    ap.add_argument('--maintenance', type=Path, required=True,
                    help='Output directory of build-persistent-boot.py --maintenance --no-serial for this kernel')
    ap.add_argument('--forbid-file', type=Path, action='append', default=[],
                    help='Private file with an identifier of a real unit that no output may contain')
    ap.add_argument('--max-fill-mib', type=int, default=sparse_image.DEFAULT_MAX_FILL_MIB,
                    help='FILL limit per sparse image (on free blocks for the ext4 root)')
    ap.add_argument('--allow-fill', action='store_true', help='Report FILL beyond the limit without stopping')
    ap.add_argument('--output', type=Path, required=True, help='New release directory')
    ap.add_argument('--changes', type=Path, required=True,
                    help='Bilingual summary, updates and known limitations (UTF-8)')
    a = ap.parse_args()
    changes = a.changes.read_text(encoding='utf-8')
    build = a.rootfs_build.resolve()
    rootfs = json.loads((build / 'manifest.json').read_text())
    verification = json.loads((build / 'verification.json').read_text())
    if rootfs['state'] != 'ROOT_IMAGE_BUILT_OFFLINE_CHECKS_PASS_NOT_BOOTED' or not verification['passed']:
        raise SystemExit('The root image build did not finish with passing offline checks')
    if sha(build / 'root.sparse.img') != rootfs['outputs']['root.sparse.img']['sha256']:
        raise SystemExit('root.sparse.img differs from its build manifest')
    release_name, release = rootfs['release_name'], rootfs['kernel_release']
    head = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(REPO), 'status', '--porcelain', '--untracked-files=no',
                                     '--', 'tools', 'packaging', 'device', 'ubuntu', 'kernel', 'firmware'],
                                    text=True).strip()
    tools_committed = {n: subprocess.run(['git', '-C', str(REPO), 'cat-file', '-e', f'HEAD:tools/{n}'],
                                         capture_output=True).returncode == 0
                       for n in ('build-rootfs-image.py', 'rootfs-image-guest.sh')}
    kernel_source = json.loads((REPO / 'kernel/source.json').read_text())
    kernel_commit = kernel_source['commit']

    # The boot set must not be bound to a unit.
    s = build / 'ubuntu-set'
    manifest = json.loads((s / 'manifest.json').read_text())
    profile = json.loads((s / 'profile.json').read_text())
    boot_header = (s / 'boot.img').read_bytes()[:4096]
    dtb = (s / 'piano-boot.dtb').read_bytes()
    properties = fdt_property_names(dtb)
    checks = {
        'cmdline_root_by_partlabel': manifest['cmdline'].startswith('root=PARTLABEL=linux_rootfs '),
        'cmdline_without_serial': 'androidboot.serialno' not in manifest['cmdline']
                                  and b'androidboot.serialno' not in boot_header,
        'cmdline_in_boot_header': manifest['cmdline'].encode() in boot_header,
        'not_debug': not manifest['debug'] and 'piano.debug' not in manifest['cmdline'],
        'no_address_injected': not manifest['private_bt_address_injected']
                               and not manifest['private_wlan_address_injected'],
        'dtb_without_address_properties': not properties & set(FORBIDDEN_PROPERTIES),
        'dtb_is_the_effective_dtb': sha(s / 'piano-boot.dtb') == manifest['effective_dtb_sha256'],
        'dtb_in_vendor_boot': dtb in (s / 'vendor_boot.img').read_bytes(),
        'profile_without_unit_fields': set(profile) == {'boot', 'init_boot', 'vendor_boot', 'scope'},
        'images_match_manifest': all(sha(s / f'{n}.img') == manifest['outputs'][n]['sha256']
                                     for n in ('boot', 'init_boot', 'vendor_boot')),
    }
    if not all(checks.values()):
        raise SystemExit(f'Boot set is not address-free: {[k for k, v in checks.items() if not v]}')

    # The maintenance image: the set's kernel and address-free DTB, no serial.
    m = a.maintenance.resolve()
    maint = json.loads((m / 'manifest.json').read_text())
    maint_image = m / maint['image']['file']
    maint_checks = {
        'kind': maint.get('kind') == 'piano-maintenance-ram',
        'same_kernel': maint['inputs']['kernel'] == manifest['inputs']['kernel']
                       and maint['kernel_release'] == release,
        'same_address_free_dtb': maint['inputs']['dtb'] == manifest['effective_dtb_sha256'],
        'cmdline_without_serial': not maint['serial_in_cmdline'] and 'androidboot.serialno' not in maint['cmdline']
                                  and b'androidboot.serialno' not in maint_image.read_bytes()[:4096],
        'image_matches_manifest': sha(maint_image) == maint['image']['sha256'],
    }
    if not all(maint_checks.values()):
        raise SystemExit(f'Maintenance image does not fit this release: {[k for k, v in maint_checks.items() if not v]}')

    # What Fastboot writes for each image.  The root's bitmap check needs the raw ext4, so its
    # encoding record stands in for it once it matches the file's chunks.
    images = {f'boot/{n}': s / n for n in SET_FILES if n.endswith('.img')}
    images['boot/boot-ram-maintenance.img'] = maint_image
    images['rootfs/root.sparse.img'] = build / 'root.sparse.img'
    writes = {}
    for rel, path in sorted(images.items()):
        row = sparse_image.summary(path)
        recorded = rootfs.get('sparse') if rel == 'rootfs/root.sparse.img' else None
        if recorded is not None:
            if {k: recorded.get(k) for k in row} != row:
                raise SystemExit('root.sparse.img does not match the sparse summary recorded at its build')
            row = recorded
        writes[rel] = row
        print(f'{rel}: ' + sparse_image.describe(row).split(': ', 1)[1], file=sys.stderr)
    found = [f for r in writes.values()
             for f in sparse_image.problems(r, None if a.allow_fill else a.max_fill_mib << 20)]
    if found:
        raise SystemExit('Sparse image check failed: ' + '; '.join(found))

    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for d in ('boot', 'rootfs', 'debs', 'tools'):
        (out / d).mkdir()
    for name in SET_FILES:
        shutil.copy2(s / name, out / 'boot' / name)
    (out / 'boot/boot-profile.json').write_bytes((build / 'boot-profile.json').read_bytes())
    shutil.copy2(maint_image, out / 'boot/boot-ram-maintenance.img')
    shutil.copy2(m / 'manifest.json', out / 'boot/maintenance-manifest.json')
    # The same file system: a hard link instead of a second 9 GB copy.
    os.link(build / 'root.sparse.img', out / 'rootfs/root.sparse.img')
    for name in ('manifest.json', 'verification.json'):
        shutil.copy2(build / name, out / 'rootfs' / name)
    if a.boot_check:
        check = json.loads(a.boot_check.read_text())
        if check.get('rootfs_sparse_sha256') != rootfs['outputs']['root.sparse.img']['sha256']:
            raise SystemExit('The QEMU boot check belongs to another image')
        shutil.copy2(a.boot_check, out / 'rootfs/qemu-boot-check.json')
    local = json.loads((build / 'inputs-local.json').read_text())
    for entry in rootfs['packages']:
        if entry['source'] != 'piano':
            continue
        path = Path(local[entry['file']])
        if sha(path) != entry['sha256']:
            raise SystemExit(f'{path.name} changed after the image build')
        shutil.copy2(path, out / 'debs' / path.name)
    shutil.copy2(REPO / 'tools/piano-switch.py', out / 'tools/piano-switch.py')
    subprocess.run(['sh', str(REPO / 'device/android/piano-switch-ksu/build.sh'), str(out / 'tools')],
                   check=True, stdout=subprocess.DEVNULL)

    files = sorted(p for p in out.rglob('*') if p.is_file())
    identity = {'files_scanned': len(files), 'identifiers': len(a.forbid_file), 'matches': 0,
                'scope': 'Raw byte scan; content inside compressed files (debs, initrd) is not covered'}
    if a.forbid_file:
        patterns = set()
        for path in a.forbid_file:
            patterns |= identity_patterns(path)
        identity['matches'] = sum(scan(p, patterns) for p in files)
        if identity['matches']:
            raise SystemExit('An output contains an identifier of a real unit; release directory is not usable')

    hashes = {str(p.relative_to(out)): sha(p) for p in files}
    stock = profile['boot']['avb_properties']['com.android.build.boot.fingerprint'].split('/')[4].split(':')[0]
    notes = render_notes(rootfs, stock, head, kernel_source, changes)
    (out / 'RELEASE-NOTES.md').write_text(notes)
    hashes['RELEASE-NOTES.md'] = sha(out / 'RELEASE-NOTES.md')
    release_manifest = {
        'release_name': release_name, 'kernel_release': release, 'kernel_commit': kernel_commit,
        'integration_commit': head, 'integration_tree_dirty': bool(dirty), 'tools_committed': tools_committed,
        'packages': rootfs['packages'],
        'rootfs_manifest_sha256': sha(build / 'manifest.json'), 'boot_set_checks': checks,
        'maintenance_image_checks': maint_checks,
        'dtb_property_count': len(properties), 'identity_scan': identity,
        'data_partition': 'not shipped; create it at install time (docs/RELEASE.md)',
        'qemu_boot_check': 'included' if a.boot_check else 'not run for this build',
        'fastboot_writes': {rel: {k: r[k] for k in ('sparse', 'device_write_bytes', 'raw', 'fill', 'dont_care', 'ext4')
                                  if k in r} for rel, r in writes.items()},
        'files_sha256': hashes,
        'scope': 'Local release directory; not published, not booted on a tablet as a whole',
    }
    (out / 'release-manifest.json').write_text(json.dumps(release_manifest, indent=2) + '\n')
    hashes['release-manifest.json'] = sha(out / 'release-manifest.json')
    (out / 'SHA256SUMS').write_text(''.join(f'{d}  {r}\n' for r, d in sorted(hashes.items())))
    print(json.dumps({'output': str(out), 'files': len(hashes), 'identity_scan': identity,
                      'integration_tree_dirty': bool(dirty)}, indent=2))


if __name__ == '__main__':
    main()
