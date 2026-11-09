#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Repack an explicitly hash-pinned piano dracut initrd for one bounded RAM hardware test.

MODULES_DIR (or --modules-dir) must be stage-kernel-modules.py --all output.
No package, root image or device is changed. --ram-debug explicitly selects
the separate RAM console assets; a pre-pivot hook carries matching modules
and the ephemeral console across switch-root.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import firmware_payload
from initramfs_archive import read_newc, regular_file
from kernel_modules import sha, verified_subset, verified_modules

REPO = Path(__file__).resolve().parents[1]
SUPPORT = REPO / 'packaging/piano-device-support'
RAM_DEBUG = REPO / 'packaging/piano-ram-debug/usr/share/piano-ram-debug'
DEBUG_FILES = ('usr/libexec/piano/debug-console', 'usr/libexec/piano/debug-shell',
               'usr/lib/systemd/system/piano-debug.service')
RETURN_FILES = ('usr/libexec/piano/debug-timeout',
                'usr/lib/systemd/system/piano-debug-timeout.service')


def stage_return(stage, units):
    """Use the current helper before and after pivot, even with an old base/root."""
    for name in RETURN_FILES:
        dest = stage / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SUPPORT / name, dest)
        dest.chmod(0o755 if '/libexec/' in name else 0o644)
    for name in RETURN_FILES[1:]:
        dest = units / Path(name).name
        # /run survives pivot; the installed root is deliberately untouched.
        dest.write_text((SUPPORT / name).read_text().replace(
            '/usr/libexec/piano/debug-timeout', '/run/piano-native-display/debug-timeout'))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ram-debug', action='store_true', required=True, help='Explicitly include the RAM-only console; never use this initrd for persistent installation')
    ap.add_argument('--purpose', choices=('native-display', 'keyboard', 'iris'), default='native-display')
    ap.add_argument('--base-initrd', type=Path, required=True)
    ap.add_argument('--base-sha256', required=True)
    ap.add_argument('--kernel-out', type=Path, required=True)
    ap.add_argument('--modules-dir', type=Path, default=os.environ.get('MODULES_DIR'))
    ap.add_argument('--gen-init-cpio', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    if sha(a.base_initrd) != a.base_sha256:
        ap.error('Base initrd hash differs from the explicit input')
    if a.modules_dir is None:
        ap.error('MODULES_DIR or --modules-dir is required')
    modules = verified_subset(a.modules_dir)
    kout = a.kernel_out.resolve()
    build = json.loads((kout / 'build-result.json').read_text())
    release = build['kernel_release']
    full = verified_modules(kout, build, release)
    if (modules.get('selection') != 'all' or modules['kernel_release'] != release or
            modules['build_result_sha256'] != sha(kout / 'build-result.json') or
            modules['module_count'] != build['modules']['count']):
        ap.error('Require the complete matching module staging, not a subset')
    for name in ('arch/arm64/boot/Image', '.config'):
        if sha(kout / name) != build['files'][name]:
            ap.error(f'Changed kernel artifact: {name}')
    if 'CONFIG_RD_XZ=y' not in (kout / '.config').read_text().splitlines():
        ap.error('The native display RAM initrd requires built-in RD_XZ')
    for name, path in full.items():
        if '.ko' in name and sha(a.modules_dir / f'lib/modules/{release}' / name) != sha(path):
            ap.error(f'Changed or missing staged module: {name}')
    a.output.mkdir(parents=True, exist_ok=False)
    out = a.output.resolve()
    stage = out / 'root'
    stage.mkdir()
    # These are the five device nodes in the pinned dracut input. gen_init_cpio
    # restores them without requiring host root or mknod privileges.
    nodes = {'console': (5, 1), 'kmsg': (1, 11), 'null': (1, 3),
             'random': (1, 8), 'urandom': (1, 9)}
    raw = subprocess.check_output(['zstd', '-dc', str(a.base_initrd)])
    subprocess.run(['cpio', '-idm', '--quiet', '--no-absolute-filenames', '--no-preserve-owner',
                    '--nonmatching', *[f'dev/{n}' for n in nodes]], input=raw, cwd=stage, check=True)
    old_files = {str(p.relative_to(stage)): sha(p) for p in stage.rglob('*')
                 if p.is_file() and not p.is_symlink()}
    shutil.rmtree(stage / 'usr/lib/modules')
    shutil.copytree(a.modules_dir / 'lib/modules', stage / 'usr/lib/modules')
    # The kernel can open the GPU before the root exists during native KMS
    # takeover. Use the same locked local firmware that the package carries.
    rows = firmware_payload.load_tree()
    fwstage = out / 'firmware'
    firmware_payload.stage_tree(rows, REPO / 'firmware', fwstage)
    shutil.copytree(fwstage, stage / 'usr/lib/firmware', dirs_exist_ok=True)
    target = stage / 'etc/piano-native-display'
    target.mkdir()
    (target / 'release').write_text(release + '\n')
    shutil.copyfile(kout / '.config', target / 'kernel.config')
    (target / 'modules.sha256').write_text(''.join(
        f'{digest}  {name.removeprefix("lib/modules/")}\n'
        for name, digest in sorted(modules['files'].items())))
    if a.purpose == 'iris':
        iris = [r for r in rows if r['path'] == 'qcom/sm8750/vpu35_4v.mbn']
        if len(iris) != 1:
            raise ValueError('Iris RAM check requires the locked VPU35 firmware')
        (target / 'iris-firmware.sha256').write_text(
            f'{iris[0]["sha256"]}  {iris[0]["path"]}\n')
    units = target / 'systemd'
    units.mkdir()
    stage_return(stage, units)
    for name in DEBUG_FILES:
        dest = stage / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RAM_DEBUG / Path(name).name, dest)
        dest.chmod(0o755 if '/libexec/' in name else 0o644)
    debug_unit = (RAM_DEBUG / 'piano-debug.service').read_text().replace(
        '/usr/libexec/piano/debug-console', '/run/piano-native-display/debug-console').replace(
        '[Service]', '[Service]\nEnvironment=PIANO_DEBUG_SHELL=/run/piano-native-display/debug-shell')
    (units / 'piano-debug.service').write_text(debug_unit)
    for root in (stage / 'usr/lib/systemd/system', units):
        link = root / 'sysinit.target.wants/piano-debug.service'
        link.parent.mkdir(parents=True, exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to('../piano-debug.service')
    # Reuse the developer SSH listener/authentication, with ownership assigned
    # to the existing debug service for this boot. No persistent enablement.
    for name in ('piano-devmode-ssh.socket', 'piano-devmode-ssh@.service'):
        text = (SUPPORT / 'usr/lib/systemd/system' / name).read_text()
        dependency = 'BindsTo' if name.endswith('.socket') else 'PartOf'
        old = f'{dependency}=piano-devmode.service'
        if text.count(old) != 1:
            raise ValueError(f'Unexpected SSH unit dependency: {name}')
        # Dependency directives cannot be cleared by an empty drop-in. These
        # complete /run unit copies override the installed unit for this boot.
        (units / name).write_text(text.replace(old, 'PartOf=piano-debug.service'))
    for name, body in {
        'piano-debug.service.d/native-display.conf': '[Unit]\nWants=piano-devmode-ssh.socket\n',
    }.items():
        p = units / name
        p.parent.mkdir(parents=True)
        p.write_text(body)
    hook = stage / 'var/lib/dracut/hooks/pre-pivot/50-piano-native-display.sh'
    shutil.copyfile(REPO / 'device/native-display/pre-pivot.sh', hook)
    hook.chmod(0o755)
    receipt = {'state': 'OFFLINE_' + a.purpose.upper().replace('-', '_') + '_INITRD_NOT_DEVICE_TESTED',
               'purpose': a.purpose,
               'base_initrd_sha256': a.base_sha256, 'kernel_commit': build['kernel_commit'],
               'kernel_release': release, 'kernel_sha256': build['files']['arch/arm64/boot/Image'],
               'config_sha256': build['files']['.config'], 'modules': modules, 'firmware': rows,
               'pre_pivot_sha256': sha(hook),
               'return_files': {name: sha(stage / name) for name in RETURN_FILES},
               'runtime_units': {str(p.relative_to(units)): sha(p)
                                 for p in sorted(units.rglob('*')) if p.is_file()}}
    (target / 'manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    lines = ['dir / 0755 0 0']
    for p in sorted(stage.rglob('*')):
        name = '/' + str(p.relative_to(stage))
        if any(c.isspace() for c in str(p)):
            raise ValueError('gen_init_cpio input path contains whitespace')
        mode = stat.S_IMODE(p.lstat().st_mode)
        if p.is_symlink():
            lines.append(f'slink {name} {os.readlink(p)} {mode:04o} 0 0')
        elif p.is_dir():
            lines.append(f'dir {name} {mode:04o} 0 0')
        elif p.is_file():
            lines.append(f'file {name} {p} {mode:04o} 0 0')
        else:
            raise ValueError(f'Unexpected special file: {p}')
    lines += [f'nod /dev/{n} 0644 0 0 c {major} {minor}' for n, (major, minor) in nodes.items()]
    spec = out / 'initrd.list'
    spec.write_text('\n'.join(lines) + '\n')
    archive = subprocess.check_output([str(a.gen_init_cpio.resolve()), '-t', '0', str(spec)])
    entries = read_newc(archive)
    for name, digest in modules['files'].items():
        if hashlib.sha256(regular_file(entries, 'usr/' + name)).hexdigest() != digest:
            raise ValueError(f'Packed module identity differs: {name}')
    # Preserve all original non-module bytes, except the explicitly locked
    # firmware update and the current bounded-return helper/units.
    changes = [n for n, h in old_files.items()
               if n not in entries or hashlib.sha256(entries[n][1]).hexdigest() != h]
    if any(n not in RETURN_FILES + DEBUG_FILES and not n.startswith(('usr/lib/modules/', 'usr/lib/firmware/'))
           for n in changes):
        raise ValueError(f'Unexpected base initrd changes: {changes}')
    # Keep the complete module/firmware payload, but compress it enough for the
    # v2 RAM image to stay below the boot partition size. CRC32 is supported by
    # the kernel XZ decoder; one thread produces one stream with a 64 MiB dict.
    image = out / 'initrd.cpio.xz'
    with image.open('wb') as stream:
        subprocess.run(['xz', '-9e', '--check=crc32', '-T1', '-c'],
                       input=archive, stdout=stream, check=True)
    if subprocess.check_output(['xz', '-dc', str(image)]) != archive:
        raise ValueError('Compressed initrd does not round-trip')
    receipt.update(initrd_sha256=sha(image), initrd_bytes=image.stat().st_size,
                   changed_base_files=changes, entries=len(entries),
                   compression='xz -9e --check=crc32 -T1', uncompressed_bytes=len(archive))
    (out / 'manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: receipt[k] for k in ('state', 'kernel_release', 'initrd_sha256', 'initrd_bytes')}))


if __name__ == '__main__':
    main()
