#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the pinned community camerad with its recorded local fixes and a pinned, prebuilt loopback module.

No DKMS, package installation, service enablement or device access. The kernel
OUT must already contain a completed matching build and Module.symvers.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[1]


def run(*args, **kw):
    return subprocess.run([str(x) for x in args], check=True, **kw)


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-out', type=Path, required=True)
    p.add_argument('--loopback-source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cross-compile', default='aarch64-linux-gnu-')
    p.add_argument('--loopback-only', action='store_true',
                   help='Build only the new kernel module; retain an unchanged camerad deb')
    a = p.parse_args()
    os.umask(0o022)
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    kout = a.kernel_out.resolve()
    source = REPO / 'device/camera'
    lock = json.loads((source / 'source.json').read_text())
    build = json.loads((kout / 'build-result.json').read_text())
    kernel_source = (kout / 'source').resolve()
    if subprocess.check_output(['git', '-C', str(kernel_source), 'rev-parse', 'HEAD'], text=True).strip() != build['kernel_commit']:
        raise ValueError('Kernel source changed since the recorded build')
    if subprocess.check_output(['git', '-C', str(kernel_source), 'status', '--porcelain', '--untracked-files=no']):
        raise ValueError('Kernel source contains uncommitted changes')
    release = (kout / 'include/config/kernel.release').read_text().strip()
    if release != build['kernel_release'] or sha(kout / '.config') != build['files']['.config']:
        raise ValueError('Kernel build receipt mismatch')
    if sha(source / 'piano-camerad.c') != lock['camerad']['sha256']:
        raise ValueError('The camerad differs from its source lock')
    for name, digest in lock['camerad'].get('local_files', {}).items():
        if sha(source / name) != digest:
            raise ValueError('Local camerad source differs from its lock: ' + name)
    if sha(source / '50-piano-camera.conf') != lock['wireplumber']['sha256']:
        raise ValueError('WirePlumber policy differs from its community source lock')
    actual = subprocess.check_output(['git', '-C', str(a.loopback_source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != lock['loopback']['commit']:
        raise ValueError('Wrong v4l2loopback source revision')
    if subprocess.check_output(['git', '-C', str(a.loopback_source), 'status', '--porcelain']):
        raise ValueError('v4l2loopback source must be clean')
    epoch = subprocess.check_output(['git', '-C', str(REPO), 'show', '-s', '--format=%ct', 'HEAD'], text=True).strip()
    os.environ['SOURCE_DATE_EPOCH'] = epoch
    if not a.loopback_only:
        # Permit vector-loop setup/tails for the two-pixel NV12 conversion.
        run(a.cross_compile + 'gcc', '-O2', '-fvect-cost-model=cheap', '-Wall', '-Wextra',
            '-ffile-prefix-map=' + str(REPO) + '=/piano',
            source / 'piano-camerad.c', '-lm', '-o', out / 'piano-camerad')
    archive = out / 'loopback-source.tar'
    run('git', '-C', a.loopback_source, 'archive', '--format=tar', '-o', archive, actual)
    module = out / 'loopback'
    module.mkdir()
    with tarfile.open(archive) as tf:
        tf.extractall(module, filter='data')
    with (kout / '.build.lock').open('rb') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep build-host paths out of the module: __FILE__ strings and debug
        # info otherwise record the kernel tree and module directory.
        prefix_map = ' '.join('-f%s-prefix-map=%s/=' % (kind, path) for kind in ('macro', 'file')
                              for path in (kernel_source, kout, module))
        run('make', '-C', kout, 'M=' + str(module), 'ARCH=arm64',
            'CROSS_COMPILE=' + a.cross_compile, 'CC=' + a.cross_compile + 'gcc',
            'KCPPFLAGS=' + prefix_map,
            'LOCALVERSION=', 'KBUILD_BUILD_USER=yzddmr6', 'KBUILD_BUILD_HOST=xiaomipad-8pro-mainline',
            'KBUILD_BUILD_TIMESTAMP=@' + epoch, 'modules')
    ko = module / 'v4l2loopback.ko'
    run(a.cross_compile + 'strip', '--strip-debug', ko)
    leaked = [s for s in subprocess.check_output(['strings', '-a', str(ko)], text=True).splitlines()
              if str(kernel_source) in s or str(kout) in s or str(module) in s or '/home/' in s]
    if leaked:
        raise ValueError('Loopback module records build-host paths: ' + leaked[0])
    vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(ko)], text=True).strip()
    if vermagic.split()[0] != release:
        raise ValueError('Loopback vermagic does not match the kernel')
    packages = []
    module_name = 'piano-camera-loopback-' + release
    for name, depends, files in [
        ('piano-camerad', 'libc6 (>= 2.38), python3, python3-numpy, v4l-utils, procps, kmod, init-system-helpers, piano-camera-loopback', {
            'usr/lib/piano/piano-camerad': (out / 'piano-camerad', 0o755),
            'usr/lib/systemd/system/piano-camerad.service': (REPO / 'packaging/piano-camerad/usr/lib/systemd/system/piano-camerad.service', 0o644),
            'usr/sbin/piano-camera-test': (REPO / 'tools/piano-camera-test.py', 0o755),
            'usr/share/wireplumber/wireplumber.conf.d/50-piano-camera.conf': (source / '50-piano-camera.conf', 0o644),
            'usr/lib/modprobe.d/piano-camera-loopback.conf': (source / 'v4l2loopback.conf', 0o644),
            'usr/share/doc/piano-camerad/copyright': (source / 'LICENSE', 0o644),
            'usr/share/doc/piano-camerad/source.json': (source / 'source.json', 0o644),
        }),
        (module_name, 'linux-image-' + release + ', kmod', {
            'usr/lib/modules/' + release + '/extra/v4l2loopback.ko': (ko, 0o644),
            'usr/share/doc/' + module_name + '/copyright': (module / 'COPYING', 0o644),
            'usr/share/doc/' + module_name + '/source.json': (source / 'source.json', 0o644),
        }),
    ]:
        if a.loopback_only and name == 'piano-camerad':
            continue
        version = '0.1+20261103.1' if name == 'piano-camerad' else '0.1+20261014.3'
        stage = out / (name + '.stage')
        stage.mkdir()
        for target, (path, mode) in files.items():
            dest = stage / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            dest.chmod(mode)
        control = stage / 'DEBIAN'
        control.mkdir()
        (control / 'control').write_text(
            f'Package: {name}\nVersion: {version}\nArchitecture: arm64\n'
            'Maintainer: yzddmr6 <46088090+yzddmr6@users.noreply.github.com>\n'
            f'Depends: {depends}\nSection: video\nPriority: optional\n'
            'Description: Piano camera offline candidate\n'
            ' Prebuilt camera components; hardware enablement and testing are manual.\n')
        if name == 'piano-camerad':
            scripts = {
                'postinst': '#!/bin/sh\nset -e\nif [ "$1" = configure ]; then\n'
                            ' deb-systemd-helper enable piano-camerad.service >/dev/null\n'
                            ' if [ -d /run/systemd/system ]; then\n'
                            '  systemctl --system daemon-reload\n'
                            '  deb-systemd-invoke start piano-camerad.service >/dev/null || true\n'
                            ' fi\nfi\n',
                'prerm': '#!/bin/sh\nset -e\nif [ "$1" = remove ]; then\n'
                         ' if [ -d /run/systemd/system ]; then\n'
                         '  deb-systemd-invoke stop piano-camerad.service >/dev/null || true\n'
                         ' fi\n'
                         ' deb-systemd-helper disable piano-camerad.service >/dev/null\nfi\n',
                'postrm': '#!/bin/sh\nset -e\nif [ "$1" = purge ]; then\n'
                          ' deb-systemd-helper purge piano-camerad.service >/dev/null\nfi\n'
                          'if [ -d /run/systemd/system ]; then\n'
                          ' systemctl --system daemon-reload\nfi\n',
            }
            for script, contents in scripts.items():
                dest = control / script
                dest.write_text(contents)
                dest.chmod(0o755)
        if name == module_name:
            with (control / 'control').open('a') as f:
                f.write('Provides: piano-camera-loopback\n')
            for script in ['postinst', 'postrm']:
                dest = control / script
                dest.write_text('#!/bin/sh\nset -eu\n'
                                f'if [ -d /lib/modules/{release} ]; then depmod -a {release}; fi\n')
                dest.chmod(0o755)
        deb = out / f'{name}_{version}_arm64.deb'
        run('dpkg-deb', '--root-owner-group', '-Zxz', '-z6', '--build', stage, deb)
        packages.append({'path': deb.name, 'sha256': sha(deb),
                         'files': {n: sha(stage / n) for n in files}})
    result = {'status': 'OFFLINE_ONLY', 'kernel_commit': build['kernel_commit'],
              'integration_commit': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
              'kernel_config_sha256': build['files']['.config'], 'kernel_release': release,
              'module_symvers_sha256': sha(kout / 'Module.symvers'),
              'source': lock, 'source_archive_sha256': sha(archive),
              'compiler': subprocess.check_output([a.cross_compile + 'gcc', '--version'], text=True).splitlines()[0],
              'loopback_vermagic': vermagic, 'loopback_sha256': sha(ko), 'packages': packages}
    (out / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(''.join(x['sha256'] + '  ' + x['path'] + '\n' for x in packages))


if __name__ == '__main__':
    main()
