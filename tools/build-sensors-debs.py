#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host-only pinned Meson builds and piano sensor Debian integration packages.

Uses explicit source repositories, ARM64 cross file/sysroot, and stock config
hashes. Never reads a tablet or accepts persist calibration as package input.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from lib.deps import source_repo

REPO = Path(__file__).resolve().parents[1]
LIBDIR = 'usr/lib/aarch64-linux-gnu/piano-sensors'
PROXY_LIBDIR = 'usr/lib/aarch64-linux-gnu/piano-iio-sensor-proxy'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*argv, **kwargs):
    return subprocess.run([str(v) for v in argv], check=True, **kwargs)


def copy(source, dest, mode=0o644):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    dest.chmod(mode)


def unpack_source(repo, lockfile, dest):
    lock = json.loads(lockfile.read_text())
    name = 'iio-sensor-proxy' if lockfile.name == 'sensorproxy.json' else lockfile.parent.name
    repo = source_repo(name, lock, local=repo)
    dest.mkdir()
    data = subprocess.check_output(['git', '-C', str(repo), 'archive', lock['commit']])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(dest, filter='data')
    run('git', 'init', '-q', dest)
    for patch in lock['patches']:
        path = lockfile.parent / patch['path']
        if sha(path) != patch['sha256']:
            raise ValueError('patch identity mismatch: ' + str(path))
        run('git', '-C', dest, 'apply', '--check', '--whitespace=error-all', path)
        run('git', '-C', dest, 'apply', path)
    return lock


def maintscripts(root, unit):
    control = root / 'DEBIAN'
    control.mkdir()
    # Installation arms the next boot. Do not reattach the already used sensor
    # PD or start a second proxy during a package transaction. Removal stops it.
    bodies = {
        'postinst': f'''if [ "$1" = configure ]; then
    deb-systemd-helper unmask {unit} >/dev/null || true
    deb-systemd-helper enable {unit} >/dev/null
    if [ -d /run/systemd/system ]; then systemctl --system daemon-reload; fi
    if command -v udevadm >/dev/null; then udevadm control --reload-rules || true; fi
    echo 'Piano sensors installed: perform a normal reboot before testing.'
fi
''',
        'prerm': f'''if [ "$1" = remove ] || [ "$1" = deconfigure ]; then
    if [ -d /run/systemd/system ]; then deb-systemd-invoke stop {unit} || true; fi
fi
''',
        'postrm': f'''if [ "$1" = purge ]; then
    if [ ! -f /usr/lib/systemd/system/{unit} ] && command -v deb-systemd-helper >/dev/null; then
        deb-systemd-helper purge {unit} >/dev/null
    fi
fi
if [ -d /run/systemd/system ]; then systemctl --system daemon-reload || true; fi
if command -v udevadm >/dev/null; then udevadm control --reload-rules || true; fi
# Keep factory calibration and learned registry even on purge; README gives
# the explicit optional data removal. These are not dpkg-owned package files.
''',
    }
    for name, body in bodies.items():
        (control / name).write_text('#!/bin/sh\nset -e\n' + body)
        (control / name).chmod(0o755)


def package(root, output, name, version, depends, relations, description, epoch, sources):
    docs = root / 'usr/share/doc' / name
    docs.mkdir(parents=True, exist_ok=True)
    copy(REPO / 'NOTICE', docs / 'NOTICE')
    copy(REPO / 'firmware/README.md', docs / 'firmware-policy.md')
    copy(REPO / 'LICENSES/LicenseRef-Proprietary-Firmware.txt', docs / 'proprietary-firmware.txt')
    copy(REPO / 'device/sensors/README.zh-CN.md', docs / 'README.zh-CN.md')
    (docs / 'sources.json').write_text(json.dumps(sources, indent=2) + '\n')
    (docs / 'copyright').write_text(
        'Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n'
        'Upstream-Name: Piano sensor integration\n'
        'Source: https://github.com/yzddmr6/xiaomipad-8pro-mainline\n\n'
        'Files: *\nCopyright: 2026 yzddmr6\nLicense: MIT\n\n'
        'Files: usr/bin/hexagonrpcd usr/share/man/man1/hexagonrpcd.1.gz usr/lib/*/piano-sensors/libhexagonrpc*\n'
        'Copyright: 2023-2026 The Sensor Shell Contributors and hexagonrpc contributors\nLicense: GPL-3+\n\n'
        'Files: usr/lib/*/piano-iio-sensor-proxy/libssc*\nCopyright: 2022-2026 Dylan Van Assche\nLicense: GPL-3+\n\n'
        'Files: usr/libexec/iio-sensor-proxy usr/bin/monitor-sensor usr/lib/udev/rules.d/80-iio-sensor-proxy.rules '
        'usr/lib/systemd/system/iio-sensor-proxy.service usr/share/dbus-1/system.d/* usr/share/polkit-1/*\n'
        'Copyright: Bastien Nocera, Dylan Van Assche, Martin Pitt and iio-sensor-proxy contributors\n'
        'License: GPL-3\n\n'
        'Files: usr/share/piano/sensors/odm/* usr/share/piano/sensors/vendor/*\n'
        'Copyright: Xiaomi, Qualcomm and respective sensor vendors\nLicense: proprietary-stock\n'
        'Comment: Stock configuration only; no persist calibration.\n'
        ' See firmware-policy.md and firmware/sensors/source.json in the source repository.\n\n'
        'License: GPL-3+\n This program may be redistributed under GPL version 3 or later.\n'
        ' On Debian systems see /usr/share/common-licenses/GPL-3.\n\n'
        'License: GPL-3\n On Debian systems see /usr/share/common-licenses/GPL-3.\n\n'
        'License: proprietary-stock\n See proprietary-firmware.txt in this directory.\n\n'
        'License: MIT\n' + ''.join(' ' + line + '\n' if line else ' .\n'
                                  for line in (REPO / 'LICENSE').read_text().split('\n\nFiles carrying')[0].splitlines()))
    changelog = (f'{name} ({version}) unstable; urgency=medium\n\n'
                 '  * hexagonrpcd: implement apps_std frename, so ADSP registry updates persist.\n'
                 '  * Quarantine and reseed a registry that keeps crashing the ADSP.\n\n'
                 ' -- yzddmr6 <46088090+yzddmr6@users.noreply.github.com>  Thu, 22 Oct 2026 16:00:00 +0800\n')
    (docs / 'changelog.gz').write_bytes(gzip.compress(changelog.encode(), mtime=epoch))
    for manual in (root / 'usr/share/man').rglob('*.[1-9]'):
        manual.with_suffix(manual.suffix + '.gz').write_bytes(gzip.compress(manual.read_bytes(), mtime=epoch))
        manual.unlink()
    files = {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*'))
             if p.is_file() and 'DEBIAN' not in p.relative_to(root).parts}
    control = root / 'DEBIAN'
    installed = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    (control / 'control').write_text(
        f'Package: {name}\nVersion: {version}\nArchitecture: arm64\nSection: misc\nPriority: optional\n'
        'Maintainer: yzddmr6 <46088090+yzddmr6@users.noreply.github.com>\n'
        'Homepage: https://github.com/yzddmr6/xiaomipad-8pro-mainline\n'
        f'Installed-Size: {(installed + 1023) // 1024}\nDepends: {depends}\n{relations}'
        f'Description: {description}\n Piano device integration using pinned upstream sensor userspace.\n')
    (control / 'md5sums').write_text(''.join(
        hashlib.md5((root / p).read_bytes()).hexdigest() + '  ' + p + '\n' for p in files))
    for p in [*root.rglob('*'), root]:
        if not p.is_symlink():
            if p.is_dir():
                p.chmod(0o755)
            elif p.stat().st_mode & 0o111:
                p.chmod(0o755)
            elif p.stat().st_mode & 0o200:
                p.chmod(0o644)
        os.utime(p, (epoch, epoch), follow_symlinks=False)
    deb = output / f'{name}_{version}_arm64.deb'
    run('dpkg-deb', '--root-owner-group', '-Zxz', '-z1', '--build', root, deb)
    return {'file': deb.name, 'sha256': sha(deb), 'files': files}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('hexagonrpc-source', 'libssc-source', 'sensorproxy-source'):
        p.add_argument('--' + name, type=Path)
    p.add_argument('--fetch-only', action='store_true')
    for name in ('cross-file', 'sysroot', 'output'):
        p.add_argument('--' + name, type=Path, required=(name == 'output'))
    p.add_argument('--version', default='0.1+20261022.1')
    p.add_argument('--proxy-version', default='3.9+piano20261022.1')
    a = p.parse_args()
    if a.fetch_only:
        for name, local, filename in (('hexagonrpc', a.hexagonrpc_source, 'device/sensors/hexagonrpc/source.json'), ('libssc', a.libssc_source, 'device/sensors/libssc/source.json'), ('iio-sensor-proxy', a.sensorproxy_source, 'device/sensors/libssc/sensorproxy.json')):
            source_repo(name, json.loads((REPO / filename).read_text()), local, a.output)
        return
    if a.cross_file is None or a.sysroot is None:
        p.error('--cross-file and --sysroot are required for compilation')
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    build = output / 'build'; build.mkdir()
    debs = output / 'debs'; debs.mkdir()
    # Install the newly built libssc into an isolated build sysroot. pkg-config
    # prefixes uninstalled absolute include paths with sys_root, so using its
    # uninstalled .pc here would silently select invalid/different headers.
    sysroot = build / 'sysroot'
    run('cp', '-a', '--reflink=auto', a.sysroot.resolve(), sysroot)
    cross = build / 'cross.ini'
    original_cross = a.cross_file.read_text()
    if str(a.sysroot.resolve()) not in original_cross:
        raise ValueError('cross file must explicitly reference --sysroot')
    cross.write_text(original_cross.replace(str(a.sysroot.resolve()), str(sysroot)))
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '1791806400'))
    os.environ['SOURCE_DATE_EPOCH'] = str(epoch)
    locks = {}
    for name, repo, lockfile in (
        ('hexagonrpc', a.hexagonrpc_source, REPO / 'device/sensors/hexagonrpc/source.json'),
        ('libssc', a.libssc_source, REPO / 'device/sensors/libssc/source.json'),
        ('sensorproxy', a.sensorproxy_source, REPO / 'device/sensors/libssc/sensorproxy.json'),
    ):
        source = build / (name + '-source'); dest = build / (name + '-build')
        locks[name] = unpack_source(repo, lockfile, source)
        env = dict(os.environ)
        opts = ['--prefix=/usr', '--libdir=lib/aarch64-linux-gnu', '--libexecdir=libexec',
                '--buildtype=release', '-Db_lto=true', '-Db_pie=true',
                '-Dc_args=-fstack-protector-strong -D_FORTIFY_SOURCE=2',
                f'-Dc_link_args=-L{sysroot}/usr/lib/aarch64-linux-gnu -Wl,-rpath-link,{sysroot}/usr/lib/aarch64-linux-gnu -Wl,-z,relro,-z,now']
        if name == 'sensorproxy':
            opts += ['-Dssc-support=enabled', '-Dtests=false',
                     '-Dudevrulesdir=/usr/lib/udev/rules.d',
                     '-Dsystemdsystemunitdir=/usr/lib/systemd/system']
        with (build / (name + '.log')).open('wb') as log:
            run('meson', 'setup', dest, source, '--cross-file', cross, *opts,
                env=env, stdout=log, stderr=subprocess.STDOUT)
            run('meson', 'compile', '-C', dest, env=env, stdout=log, stderr=subprocess.STDOUT)
            if name == 'hexagonrpc':
                run('meson', 'test', '-C', dest, '--print-errorlogs', env=env, stdout=log, stderr=subprocess.STDOUT)
            if name == 'libssc':
                run('meson', 'install', '-C', dest, '--destdir', sysroot,
                    env=env, stdout=log, stderr=subprocess.STDOUT)
            if name == 'sensorproxy':
                run('meson', 'test', '-C', dest, 'test-shutdown', '--print-errorlogs',
                    env=env, stdout=log, stderr=subprocess.STDOUT)
                run('meson', 'install', '-C', dest, '--destdir', build / 'sensorproxy-install',
                    env=env, stdout=log, stderr=subprocess.STDOUT)
    sensor = output / 'stages/piano-sensors'
    proxy = output / 'stages/piano-iio-sensor-proxy'
    shutil.copytree(REPO / 'packaging/piano-sensors', sensor)
    # polkit's pkg-config directory may include the cross sysroot. Select the
    # target /usr payload and install the upstream policy at its target path;
    # never let a host /home/... prefix escape into the package archive.
    shutil.copytree(build / 'sensorproxy-install/usr', proxy / 'usr')
    copy(build / 'sensorproxy-source/data/net.hadess.SensorProxy.policy',
         proxy / 'usr/share/polkit-1/actions/net.hadess.SensorProxy.policy')
    shutil.copytree(REPO / 'packaging/piano-iio-sensor-proxy', proxy, dirs_exist_ok=True)
    # Tests, headers and development libraries are not a product runtime.
    if (proxy / 'usr/libexec/installed-tests').exists():
        shutil.rmtree(proxy / 'usr/libexec/installed-tests')
    config = json.loads((REPO / 'firmware/sensors/source.json').read_text())
    locks['stock-config'] = config
    for row in config['files']:
        path = REPO / 'firmware/sensors' / row['path']
        if sha(path) != row['sha256']:
            raise ValueError('stock config hash mismatch')
        copy(path, sensor / 'usr/share/piano/sensors' / row['path'], 0o444)
    # Empty immutable seed deliberately carries no persist metadata/fac_cal.
    (sensor / 'usr/share/piano/sensors/registry/registry').mkdir(parents=True)
    copy(REPO / 'device/sensors/qrtr.py', sensor / 'usr/libexec/piano/qrtr.py')
    elf_files = [
        (build / 'hexagonrpc-build/hexagonrpcd/hexagonrpcd', sensor / 'usr/bin/hexagonrpcd'),
        (build / 'hexagonrpc-build/libhexagonrpc/libhexagonrpc.so.0.5', sensor / LIBDIR / 'libhexagonrpc.so.0.5'),
        (build / 'libssc-build/src/libssc.so.2', proxy / PROXY_LIBDIR / 'libssc.so.2'),
    ]
    for src, dest in elf_files:
        copy(src, dest, 0o755 if '/bin/' in str(dest) else 0o644)
    for dest, libdir in [(sensor / 'usr/bin/hexagonrpcd', LIBDIR),
                         (proxy / 'usr/libexec/iio-sensor-proxy', PROXY_LIBDIR)]:
        run('patchelf', '--set-rpath', '/' + libdir, dest)
    for root in (sensor, proxy):
        for path in root.rglob('*'):
            if path.is_file() and not path.is_symlink() and path.read_bytes()[:4] == b'\x7fELF':
                if 'AArch64' not in subprocess.check_output(['readelf', '-h', str(path)], text=True):
                    raise ValueError('non-ARM64 package ELF')
                run('aarch64-linux-gnu-strip', '--strip-unneeded', path)
    (sensor / 'usr/libexec/piano/sensors-prepare').chmod(0o755)
    (proxy / 'usr/bin/piano-sensors-check').chmod(0o755)
    manual = sensor / 'usr/share/man/man1/hexagonrpcd.1.gz'
    manual.parent.mkdir(parents=True)
    manual.write_bytes(gzip.compress((build / 'hexagonrpc-source/hexagonrpcd/hexagonrpcd.1').read_bytes(), mtime=epoch))
    maintscripts(sensor, 'piano-hexagonrpcd-sns.service')
    maintscripts(proxy, 'iio-sensor-proxy.service')
    packages = [
        package(sensor, debs, 'piano-sensors', a.version,
                'libc6 (>= 2.38), python3, systemd, udev, init-system-helpers (>= 1.52)',
                'Conflicts: hexagonrpc, hexagonrpcd\n', 'ADSP sensor service and stock configuration for piano', epoch, locks),
        package(proxy, debs, 'piano-iio-sensor-proxy', a.proxy_version,
                f'piano-sensors (= {a.version}), libc6 (>= 2.38), libglib2.0-0t64 (>= 2.76), '
                'libqmi-glib5 (>= 1.33.4), libprotobuf-c1, libgudev-1.0-0 (>= 237), '
                'libpolkit-gobject-1-0, polkitd, dbus, systemd, udev, init-system-helpers (>= 1.52)',
                'Provides: iio-sensor-proxy (= 3.9)\nConflicts: iio-sensor-proxy\nReplaces: iio-sensor-proxy\n',
                'SensorProxy with libssc for piano rotation and ambient light', epoch, locks),
    ]
    receipt = {'scope': 'HOST_BUILD_ONLY; no device/GNOME validation', 'sources': locks,
               'packages': packages, 'cross_file_sha256': sha(a.cross_file),
               'sysroot_status_sha256': sha(a.sysroot / 'var/lib/dpkg/status')
               if (a.sysroot / 'var/lib/dpkg/status').exists() else None,
               'tools': {tool: subprocess.check_output([tool, '--version'], text=True).splitlines()[0]
                         for tool in ('meson', 'aarch64-linux-gnu-gcc', 'patchelf', 'dpkg-deb', 'qemu-aarch64-static')}}
    (output / 'manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (debs / 'SHA256SUMS').write_text(''.join(r['sha256'] + '  ' + r['file'] + '\n' for r in packages))
    copy(REPO / 'device/sensors/README.zh-CN.md', output / 'README.zh-CN.md')
    copy(REPO / 'packaging/piano-iio-sensor-proxy/usr/bin/piano-sensors-check', output / 'check.sh', 0o755)
    print(output)


if __name__ == '__main__':
    main()
