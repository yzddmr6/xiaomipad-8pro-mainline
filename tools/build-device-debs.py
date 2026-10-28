#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assemble the existing device userspace and Mesa into local Debian packages.

The firmware package, piano-local-firmware, is built by build-firmware-deb.py.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

PROJECT = '2026 The piano mainline project contributors (adapted sources: NOTICE in this directory)'
# Licences whose full text Debian ships in /usr/share/common-licenses; the others are included.
COMMON_LICENSES = {'GPL-2.0-only': 'GPL-2', 'GPL-2.0-or-later': 'GPL-2', 'Apache-2.0': 'Apache-2.0'}


def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def spdx(path):
    """SPDX expression of a repository file; files without one are MIT (LICENSE)."""
    m = re.search(rb'SPDX-License-Identifier:\s*(.+?)\s*(?:\*/|-->)?\s*$', path.read_bytes()[:4096], re.M)
    return m.group(1).decode() if m else 'MIT'


def copyright_file(repo, paragraphs):
    """machine-readable debian/copyright: Files paragraphs, then one text per licence used."""
    def text(body):
        return ''.join(f' {line}\n' if line.strip() else ' .\n' for line in body.strip('\n').splitlines())
    mit = (repo / 'LICENSE').read_text().split('Permission is hereby granted', 1)[1].split('\n\nFiles carrying', 1)[0]
    bsd = (repo / 'LICENSES/alsa-ucm-conf-BSD-3-Clause.txt').read_text().split('All rights reserved.', 1)[1]
    full = {'MIT': 'Permission is hereby granted' + mit, 'BSD-3-Clause': bsd}
    # A licence text in LICENSES/ whose name is the SPDX identifier itself is the
    # text of that identifier. The other names there belong to a single import.
    for path in sorted((repo / 'LICENSES').glob('*.txt')):
        if re.fullmatch(r'[A-Za-z0-9.+-]+', path.stem) and path.stem not in full:
            full[path.stem] = path.read_text()
    out = 'Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n'
    used = []
    for files, holders, licence, comment in paragraphs:
        out += f'\nFiles: {" ".join(sorted(files))}\nCopyright: {holders}\nLicense: {licence}\n'
        if comment:
            out += 'Comment:\n' + text(comment)
        used += [i for i in re.split(r'\s+(?:AND|OR)\s+|[()]', licence) if i and i not in used]
    for licence in used:
        if licence in COMMON_LICENSES:
            body = f'On Debian systems the full text is in /usr/share/common-licenses/{COMMON_LICENSES[licence]}.'
            if licence.endswith('-or-later'):
                body = ('This program is free software; you can redistribute it and/or modify it under\n'
                        'the terms of the GNU General Public License as published by the Free Software\n'
                        'Foundation; either version 2 of the License, or (at your option) any later version.\n\n'
                        + body)
        elif licence in full:
            body = full[licence]
        else:
            raise ValueError(f'No licence text for {licence}')
        out += f'\nLicense: {licence}\n' + text(body)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    for name in ('thp-live', 'mesa-build', 'busybox', 'avbtool'):
        ap.add_argument('--' + name, type=Path)
    ap.add_argument('--snap-graphics-build', type=Path, help='Verified build-mesa-root.py output')
    ap.add_argument('--version', required=True)
    select = ap.add_mutually_exclusive_group()
    select.add_argument('--support-only', action='store_true', help='Rebuild only the small device integration package')
    select.add_argument('--desktop-only', action='store_true', help='Build desktop policy without rebuilding locked support/Mesa inputs')
    select.add_argument('--mesa-only', action='store_true', help='Build only the verified desktop Mesa package')
    select.add_argument('--snap-graphics-only', action='store_true', help='Build only piano-snap-graphics')
    select.add_argument('--mipps-auth-only', action='store_true', help='Build only the pinned MiPPS authentication package')
    a = ap.parse_args()
    if a.snap_graphics_only and not a.snap_graphics_build:
        ap.error('--snap-graphics-only requires --snap-graphics-build')
    if a.mesa_only and not a.mesa_build:
        ap.error('--mesa-only requires --mesa-build')
    if not (a.desktop_only or a.snap_graphics_only or a.mipps_auth_only or a.mesa_only):
        # The support package carries the device integration only; the desktop
        # package needs the verified Mesa build, so --support-only does not.
        required = ('thp_live', 'busybox', 'avbtool')
        if not a.support_only:
            required += ('mesa_build',)
        for name in required:
            if getattr(a, name) is None:
                ap.error('--' + name.replace('_', '-') + ' is required unless --desktop-only, '
                         '--snap-graphics-only or --mipps-auth-only')
    repo = Path(__file__).resolve().parents[1]
    a.output.mkdir(parents=True, exist_ok=False)
    stages = a.output / 'stages'
    stages.mkdir()
    receipts = {}
    licences = {}  # stage root -> {installed path: SPDX expression} of files taken from this repository

    def copy(src, root, dest, mode=None):
        target = root / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        target.chmod(mode if mode is not None else 0o644)
        if Path(src).resolve().is_relative_to(repo) and not Path(src).resolve().is_relative_to(repo / 'tools/local'):
            licences.setdefault(root, {})[dest] = spdx(Path(src))

    # Bytecode the tests leave next to the packaged scripts (git-ignored) is not part of a package.
    untracked = shutil.ignore_patterns('__pycache__')

    def tree(src, root, dest):
        for p in sorted(src.rglob('*')):
            if p.is_file() and not p.is_symlink() and '__pycache__' not in p.relative_to(src).parts:
                licences.setdefault(root, {})[str(Path(dest) / p.relative_to(src))] = spdx(p)

    def package(name, root, depends, description, postinst=None, recommends=None, third_party=(),
                prerm=None, postrm=None, conffiles=()):
        """third_party: (files, copyright, licence, comment) paragraphs for files not from this repository;
        every other regular file is attributed to this repository under its SPDX header or MIT."""
        docs = root / 'usr/share/doc' / name
        docs.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / 'NOTICE', docs / 'NOTICE')
        claimed = {f for paragraph in third_party for f in paragraph[0]}
        own = {}
        for p in root.rglob('*'):
            rel = str(p.relative_to(root))
            if p.is_file() and not p.is_symlink() and not any(rel == c or rel.startswith(c.rstrip('*'))
                                                              for c in claimed) and rel != f'usr/share/doc/{name}/NOTICE':
                own.setdefault(licences.get(root, {}).get(rel, 'MIT'), []).append(rel)
        paragraphs = [(files, PROJECT, licence, None) for licence, files in sorted(own.items())]
        paragraphs += [((f'usr/share/doc/{name}/NOTICE',), PROJECT, 'MIT', None), *third_party]
        (docs / 'copyright').write_text(copyright_file(repo, paragraphs))
        control = root / 'DEBIAN'
        control.mkdir()
        (control / 'control').write_text(
            f'Package: {name}\nVersion: {a.version}\nArchitecture: arm64\n'
            'Maintainer: Piano local build <piano@localhost>\n'
            f'Depends: {depends}\n' + (f'Recommends: {recommends}\n' if recommends else '') +
            f'Description: {description}\n')
        if conffiles:
            for path in conffiles:
                if not (root / path.lstrip('/')).is_file():
                    raise ValueError(f'Missing conffile: {path}')
            (control / 'conffiles').write_text(''.join(path + '\n' for path in conffiles))
        for name_script, body in (('postinst', postinst), ('prerm', prerm), ('postrm', postrm)):
            if body:
                (control / name_script).write_text(body)
                (control / name_script).chmod(0o755)
        owned = {str(p.relative_to(root)): sha(p) for p in root.rglob('*')
                 if p.is_file() and not p.is_symlink() and not p.is_relative_to(control)}
        deb = a.output / f'{name}_{a.version}_arm64.deb'
        subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '-z1', '--build', str(root), str(deb)], check=True)
        receipts[name] = {'file': deb.name, 'sha256': sha(deb), 'files': owned}

    if a.snap_graphics_build:
        build = a.snap_graphics_build
        manifest = json.loads((build / 'manifest.json').read_text())
        source = json.loads((repo / 'mesa/source.json').read_text())
        if manifest['source'] != source or manifest['build']['profile'] != 'gpu-2404':
            raise ValueError('snap provider does not match the piano Mesa source lock/profile')
        snap = build / manifest['package']['file']
        if snap.name != manifest['package']['file'] or sha(snap) != manifest['package']['sha256']:
            raise ValueError('snap provider hash/path mismatch')
        metadata = json.loads(subprocess.check_output(['unsquashfs', '-cat', str(snap), 'meta/snap.yaml']))
        if (metadata['name'] != 'piano-mesa-2404' or metadata['base'] != 'core24'
                or metadata['architectures'] != ['arm64'] or metadata['version'] != manifest['version']
                or metadata['slots'] != {'gpu-2404': {'interface': 'content', 'read': ['$SNAP']}}):
            raise ValueError('incompatible snap provider metadata')
        graphics = stages / 'piano-snap-graphics'
        shutil.copytree(repo / 'packaging/piano-snap-graphics', graphics, ignore=untracked)
        tree(repo / 'packaging/piano-snap-graphics', graphics, '.')
        payload = 'usr/share/piano/snaps/'
        copy(snap, graphics, payload + 'piano-mesa-2404.snap')
        (graphics / payload / 'provider.json').write_text(json.dumps(
            {'name': metadata['name'], 'version': metadata['version'], 'sha256': sha(snap)}, indent=2) + '\n')
        copy(build / 'manifest.json', graphics, 'usr/share/doc/piano-snap-graphics/provider-build.json')
        package('piano-snap-graphics', graphics, 'python3, snapd, init-system-helpers',
                'Adreno 830 Mesa content provider for core24 gpu-2404 snaps',
                '#!/bin/sh\nset -e\nif [ "$1" = configure ]; then\n'
                ' rm -f /var/lib/piano/snap-graphics-configured\n'
                ' deb-systemd-helper enable piano-snap-graphics.service >/dev/null\nfi\n',
                third_party=[((payload + 'piano-mesa-2404.snap',),
                              'Mesa, GLVND, Vulkan, Ubuntu runtime and provider-tool contributors',
                              'GPL-2.0-only', 'This is an aggregate, not a relicensing of Mesa/runtime libraries.\n'
                              'The stock mesa-2404 payload retains its MIT metadata and per-library licences.\n'
                              'Full upstream notices and source/build provenance are inside the snap at\n'
                              '/usr/share/doc/piano-mesa-2404/; see provider-build.json beside this file.')],
                prerm='#!/bin/sh\nset -e\nif [ "$1" = remove ]; then\n'
                      ' deb-systemd-invoke stop piano-snap-graphics.service >/dev/null || true\n'
                      ' deb-systemd-helper disable piano-snap-graphics.service >/dev/null\nfi\n',
                postrm='#!/bin/sh\nset -e\nif [ "$1" = purge ]; then\n'
                       ' deb-systemd-helper purge piano-snap-graphics.service >/dev/null\n'
                       ' rm -f /var/lib/piano/snap-graphics-configured\nfi\n'
                       'if [ -d /run/systemd/system ]; then\n'
                       ' systemctl --system daemon-reload >/dev/null || true\nfi\n')
        if a.snap_graphics_only:
            (a.output / 'manifest.json').write_text(json.dumps(receipts, indent=2) + '\n')
            return

    if not (a.desktop_only or a.mesa_only):
        auth = stages / 'piano-mipps-auth'
        shutil.copytree(repo / 'packaging/piano-mipps-auth', auth, ignore=untracked)
        tree(repo / 'packaging/piano-mipps-auth', auth, '.')
        source = repo / 'device/power/mipps-auth'
        lock = json.loads((source / 'source.json').read_text())
        upstream = source / lock['file']
        if sha(upstream) != lock['sha256']:
            raise ValueError('MiPPS source hash mismatch')
        target = auth / 'usr/libexec/xiaomi-mipps-auth'
        copy(upstream, auth, 'usr/libexec/xiaomi-mipps-auth', 0o755)
        for patch in lock['patches']:
            path = source / patch['file']
            if sha(path) != patch['sha256']:
                raise ValueError('MiPPS patch hash mismatch')
            subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', '-p1', '-i', str(path.resolve())],
                           cwd=target.parent, check=True)
        if sha(target) != lock['patched_sha256']:
            raise ValueError('MiPPS patched output hash mismatch')
        package('piano-mipps-auth', auth, 'python3, util-linux, systemd',
                'Xiaomi MiPPS charger authentication through the kernel MCA relay',
                third_party=[(('usr/libexec/xiaomi-mipps-auth',), 'ianchb; bluseliu50',
                              'GPL-2.0-only', lock['url'] + ' at ' + lock['commit'] +
                              '; piano relay path and device-only integration patch in device/power/mipps-auth.')])
        if a.mipps_auth_only:
            (a.output / 'manifest.json').write_text(json.dumps(receipts, indent=2) + '\n')
            return

    if not (a.desktop_only or a.mesa_only):
        support = stages / 'piano-device-support'
        shutil.copytree(repo / 'packaging/piano-device-support', support, ignore=untracked)
        tree(repo / 'packaging/piano-device-support', support, '.')
        for p in support.rglob('*'):
            if p.is_file() and p.read_bytes().startswith(b'#!'):
                p.chmod(0o755)
        copy(repo / 'device/boot/piano-boot-switch', support, 'usr/sbin/piano-boot-switch', 0o755)
        copy(repo / 'device/boot/piano-slot-success', support, 'usr/sbin/piano-slot-success', 0o755)
        copy(repo / 'device/boot/piano_bootslot.py', support, 'usr/lib/piano/boot/piano_bootslot.py', 0o644)
        copy(a.thp_live, support, 'usr/libexec/piano/piano-thp-live', 0o755)
        copy(a.busybox, support, 'usr/libexec/piano/busybox', 0o755)
        copy(repo / 'device/platform/piano-physical-platform', support, 'usr/libexec/piano/piano-physical-platform', 0o755)
        copy(repo / 'device/bluetooth/bluetooth-address.py', support, 'usr/libexec/piano/bluetooth-address', 0o755)
        copy(repo / 'device/bluetooth/piano-pen-radio.py', support, 'usr/libexec/piano/piano-pen-radio', 0o755)
        copy(repo / 'device/bluetooth/p81c-radio.json', support, 'usr/share/piano/pen/p81c-radio.json', 0o644)
        copy(repo / 'initramfs/usb-diag', support, 'usr/libexec/piano/usb-diag', 0o755)
        network = json.loads((repo / 'initramfs/network.json').read_text())
        (support / 'etc/piano-network').write_text(''.join(f'PIANO_{k.upper()}={v}\n' for k, v in network.items()))
        # build-persistent-boot.py imports initramfs_archive from its own directory.
        for source, name in [(repo / 'tools/build-persistent-boot.py', 'build-persistent-boot.py'),
                             (repo / 'tools/initramfs_archive.py', 'initramfs_archive.py'),
                             (repo / 'tools/local/aosp-mkbootimg/mkbootimg.py', 'mkbootimg.py'),
                             (a.avbtool, 'avbtool.py')]:
            copy(source, support, 'usr/lib/piano/boot/' + name, 0o755)
        shutil.copytree(repo / 'tools/local/aosp-mkbootimg/gki', support / 'usr/lib/piano/boot/gki',
                        ignore=shutil.ignore_patterns('__pycache__', '*test*'))
        # piano-boot-update runs the packaged copy: it must import from the package alone.
        subprocess.run(['python3', '-E', '-s', '-B', str((support / 'usr/lib/piano/boot/build-persistent-boot.py').resolve()), '--help'],
                       check=True, stdout=subprocess.DEVNULL, cwd='/')
        busybox = re.search(r'^package=busybox-static_(\S+)_arm64\.deb$',
                            (repo / 'tools/fetch-busybox-arm64.sh').read_text(), re.M).group(1)
        support_third_party = [
            (('usr/libexec/piano/piano-pen-radio',), 'slwyts and piano mainline contributors',
             'BSD-2-Clause-Patent',
             'Adapted from slwyts/Project-SunUEFI 6a8763d010adb00b18911d6d12e6dd02869f98b9;\n'
             'linux/userspace/pen/piano-pen-bluetooth.py (see NOTICE).'),
            (('usr/libexec/piano/bluetooth-address',), 'slwyts and piano mainline contributors', 'MIT',
             'Adapted from slwyts/Project-SunUEFI 6a8763d010adb00b18911d6d12e6dd02869f98b9;\n'
             'linux/bsp/common/usr/lib/piano/bluetooth-address.py (see NOTICE).'),
            (('usr/lib/piano/boot/mkbootimg.py', 'usr/lib/piano/boot/gki/*'), 'The Android Open Source Project',
             'Apache-2.0', 'AOSP platform/system/tools/mkbootimg, the checkout pinned by tools/fetch-aosp-mkbootimg.sh;\n'
             'unmodified, tests excluded.'),
            (('usr/lib/piano/boot/avbtool.py',), 'The Android Open Source Project', 'MIT',
             'AOSP platform/external/avb avbtool.py, unmodified; its MIT notice is in the file header.'),
            (('usr/libexec/piano/busybox',),
             'Erik Andersen, Rob Landley, Denys Vlasenko and other BusyBox contributors', 'GPL-2.0-only',
             f'The busybox-static {busybox} arm64 binary from Ubuntu, unmodified (pinned by\n'
             'tools/fetch-busybox-arm64.sh). Its corresponding source is the Ubuntu source package\n'
             f'busybox {busybox}: https://launchpad.net/ubuntu/+source/busybox/{busybox}'),
            (('usr/libexec/piano/piano-thp-live',), 'ianchb; Niels Lohmann; ' + PROJECT,
             'Apache-2.0 AND MIT', 'Built from device/piano-thp: files adapted from ianchb/xiaomi-sheng-thp are\n'
             'Apache-2.0, the rest MIT (see NOTICE); it includes the MIT nlohmann JSON headers.'),
        ]
        package('piano-device-support', support,
                'python3, openssl, device-tree-compiler, dracut, kmod, init-system-helpers, piano-mipps-auth, '
                'piano-local-firmware (>= 0.6+os3.0.303.0)',
                'Piano platform, touchscreen and persistent boot integration',
                '#!/bin/sh\nset -e\nif [ "$1" = configure ]; then\n'
                # piano-thp.service is udev-activated only; remove the multi-user.target link
                # and deb-systemd-helper state left by versions that enabled it.
                ' deb-systemd-helper purge piano-thp.service >/dev/null\n'
                ' deb-systemd-helper enable piano-slot-success.service piano-debug.service '
                'piano-debug-timeout.service >/dev/null\n'
                # Shipped unit files change between versions (restart policy, dependencies);
                # the running manager must read them before the next activation.
                ' if [ -d /run/systemd/system ]; then systemctl --system daemon-reload >/dev/null || true; fi\n'
                'fi\n',
                recommends='openssh-server',  # developer mode only; piano-devmode refuses without sshd
                third_party=support_third_party)
        if a.support_only:
            (a.output / 'manifest.json').write_text(json.dumps(receipts, indent=2) + '\n')
            return

    if not a.desktop_only:
        mesa = stages / 'piano-mesa'
        subprocess.run(['python3', str(repo / 'tools/prepare-mesa-initramfs.py'), '--mesa-build', str(a.mesa_build),
                        '--stage', str(mesa)], stdout=subprocess.DEVNULL, check=True)
        mesa_source = json.loads((repo / 'mesa/source.json').read_text())
        package('piano-mesa', mesa, 'libc6, libdrm2, libexpat1, libzstd1, zlib1g',
                'Isolated Mesa 26.2 with piano A830 input lock',
                third_party=[((mesa_source['prefix'].lstrip('/') + '/*',), 'Mesa contributors (per-file notices)',
                              'MIT', f'Mesa {mesa_source["tag"]} ({mesa_source["url"]}, commit {mesa_source["commit"]})\n'
                              f'with {mesa_source["patch"]} from this repository. Most of Mesa is MIT; some files\n'
                              'carry other permissive licences, listed in docs/license.rst of that source.')])
        if a.mesa_only:
            (a.output / 'manifest.json').write_text(json.dumps(receipts, indent=2) + '\n')
            return

    desktop = stages / 'piano-ubuntu-desktop'
    shutil.copytree(repo / 'packaging/piano-ubuntu-desktop', desktop, ignore=untracked)
    tree(repo / 'packaging/piano-ubuntu-desktop', desktop, '.')
    copy(repo / 'device/keyboard/README.md', desktop,
         'usr/share/doc/piano-ubuntu-desktop/keyboard.md')
    # Distribution-specific configuration consumes the board profiles once.
    tuned = repo / 'device/power/tuned'
    for profile in sorted((tuned / 'profiles').glob('piano-*/tuned.conf')):
        copy(profile, desktop, 'usr/lib/tuned/profiles/' + str(profile.relative_to(tuned / 'profiles')))
    # Ubuntu tuned-ppd owns ppd.conf; a service mount supplies our separate conffile.
    copy(tuned / 'ppd.conf', desktop, 'etc/tuned/piano-ppd.conf')
    copy(tuned / 'README.md', desktop, 'usr/share/doc/piano-ubuntu-desktop/power-profiles.md')
    # ALSA UCM for the internal-microphone card (device/audio/ucm2, MIT/BSD-3-Clause, see NOTICE);
    # its conf.d entry is a relative symlink, kept as one.
    shutil.copytree(repo / 'device/audio/ucm2', desktop / 'usr/share/alsa/ucm2', symlinks=True)
    tree(repo / 'device/audio/ucm2', desktop, 'usr/share/alsa/ucm2')
    env = desktop / 'etc/environment.d/50-piano-mesa.conf'
    env.parent.mkdir(parents=True)
    prefix = json.loads((repo / 'mesa/source.json').read_text())['prefix']
    env.write_text(f'LD_LIBRARY_PATH={prefix}/lib\nLIBGL_DRIVERS_PATH={prefix}/lib/dri\n')
    dropin = desktop / 'etc/systemd/system/gdm.service.d/50-piano-mesa.conf'
    dropin.parent.mkdir(parents=True)
    dropin.write_text('[Service]\n' + ''.join('Environment=' + l + '\n' for l in env.read_text().splitlines()))
    # The speaker up-mix is a stream property: the same file for PulseAudio and native PipeWire clients.
    upmix = 'usr/share/pipewire/pipewire-pulse.conf.d/50-piano-speaker-upmix.conf'
    copy(repo / 'packaging/piano-ubuntu-desktop' / upmix, desktop,
         'usr/share/pipewire/client.conf.d/50-piano-speaker-upmix.conf')
    # fake-hwclock: the RTC cannot be set; it restores the time saved at shutdown or hourly.
    package('piano-ubuntu-desktop', desktop, 'resources (= 1.10.2-0ubuntu3.1+piano2), gnome-settings-daemon (= 50.0-1ubuntu1+piano1), gnome-settings-daemon-common (= 50.0-1ubuntu1+piano1), gnome-control-center (= 1:50.0-0ubuntu6+piano1), gnome-control-center-data (= 1:50.0-0ubuntu6+piano1), piano-mesa, piano-iris-vaapi (>= 0.1.0+git.f587b14-0piano4), gdm3, gnome-shell (>= 50), colord, colord-data, python3-gi, fake-hwclock, python3, console-setup, snapd, init-system-helpers, tuned-ppd (>= 2.25.1), showtime, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-plugins-bad, gstreamer1.0-libav',
            'Piano Ubuntu desktop defaults: graphics, clock without RTC, speaker up-mix and UCM, '
            'GNOME Shell 50 on-screen keyboard guard',
            '#!/bin/sh\nset -e\nif [ "$1" = configure ]; then\n'
            ' if ! grep -q "^CHASSIS=" /etc/machine-info 2>/dev/null; then\n'
            '  if [ ! -e /etc/machine-info ]; then\n'
            '   install -m 0644 /dev/null /etc/machine-info\n'
            '  fi\n'
            '  sed -i -e \'$a\\\' /etc/machine-info\n'
            '  printf "CHASSIS=convertible\\n" >> /etc/machine-info\n'
            ' fi\n'
            ' deb-systemd-helper enable piano-firefox-snap-remove.service >/dev/null\n'
            ' deb-systemd-helper --user enable piano-panel-srgb.service >/dev/null\nfi\n',
            prerm='#!/bin/sh\nset -e\nif [ "$1" = remove ]; then\n'
                  ' deb-systemd-helper --user disable piano-panel-srgb.service >/dev/null\nfi\n',
            postrm='#!/bin/sh\nset -e\nif [ "$1" = purge ]; then\n'
                   ' if [ -f /etc/machine-info ]; then\n'
                   '  sed -i \'/^CHASSIS=convertible$/d\' /etc/machine-info\n'
                   '  if [ ! -s /etc/machine-info ]; then rm -f /etc/machine-info; fi\n'
                   ' fi\n'
                   ' deb-systemd-helper --user purge piano-panel-srgb.service >/dev/null\nfi\n',
            conffiles=['/etc/apt/preferences.d/piano-resources', '/etc/apt/preferences.d/piano-power-button-lock', '/etc/tuned/piano-ppd.conf',
                       '/etc/xdg/mimeapps.list'],
            third_party=[(('usr/share/alsa/ucm2/*',),
                          '2026 bluseliu50 and piano mainline contributors; '
                          '2019 Advanced Linux Sound Architecture (ALSA) project', 'MIT AND BSD-3-Clause',
                          'device/audio/ucm2, adapted from blu-sharky/debian-piano and alsa-ucm-conf (see NOTICE).')])
    (a.output / 'manifest.json').write_text(json.dumps(receipts, indent=2) + '\n')
    print('Local packages created; no packages installed or device accessed')


if __name__ == '__main__':
    main()
