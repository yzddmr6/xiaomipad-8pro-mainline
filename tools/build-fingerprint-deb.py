#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the piano fingerprint package from fixed, public Qualcomm sources.

The sensor bus of the fingerprint belongs to the secure world, so the client
talks to the factory application through Qualcomm's public MinkIPC transport.
The three upstream projects it needs are pinned in device/fingerprint/source.json
and are fetched at that commit; the client itself is the file of this
repository. The result is a static binary, the four stock firmware files of
firmware/fingerprint/ and a receipt that names every input.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / 'device/fingerprint/source.json'
CLIENT = REPO / 'device/fingerprint/piano-goodix.c'
FIRMWARE = REPO / 'firmware/fingerprint'
PACKAGE = 'piano-fingerprint'
INSTALL = {
    'usr/libexec/piano/piano-fingerprint-ctl': 0o755,
    'usr/lib/firmware/gfpia.elf': 0o644,
    'usr/lib/firmware/piano-goodix-cap-s.bin': 0o644,
    'usr/lib/firmware/piano-goodix-cap-t.bin': 0o644,
    'usr/lib/firmware/piano-goodix-dubai-u4.bin': 0o644,
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*argv, **kwargs):
    return subprocess.run([str(a) for a in argv], check=True, **kwargs)


def copy(source, dest, mode=0o644):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    dest.chmod(mode)


def fetch(name, record, cache):
    """The pinned upstream tree, fetched once and reused."""
    path = cache / name
    if not path.exists():
        run('git', 'clone', '--filter=blob:none', '--no-checkout', record['url'], path)
    if subprocess.run(['git', '-C', str(path), 'cat-file', '-e', record['commit']],
                      capture_output=True).returncode:
        run('git', '-C', str(path), 'fetch', '--depth=1', 'origin', record['commit'])
    run('git', '-C', str(path), 'checkout', '--detach', record['commit'])
    # A tree that was changed after the checkout is not the pinned source.
    if subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain',
                                '--untracked-files=no'], text=True):
        raise ValueError('the fingerprint dependency has local changes: ' + name)
    return path


def build_binary(output, compiler, sources):
    """Compile the upstream objects and the client, and link them statically."""
    work = output / 'build'
    work.mkdir()
    includes = []
    for name, directories in sources['includes'].items():
        for directory in directories:
            includes.append(sources['paths'][name] / directory)
    objects = []
    imported = []
    for name, files in sources['objects'].items():
        for relative in files:
            imported.append(sources['paths'][name] / relative)
    for index, original in enumerate(imported + [CLIENT]):
        obj = work / (str(index) + '.o')
        command = [compiler, '-O2', '-D_GNU_SOURCE', '-DUSE_QCBOR',
                   *['-I' + str(p) for p in includes],
                   '-c', str(original), '-o', str(obj)]
        # The imported upstream sources keep their own warning policy; only the
        # client of this repository is built with the strict flags.
        if original is CLIENT:
            command[1:1] = ['-Wall', '-Wextra', '-Werror']
        run(*command)
        objects.append(obj)
    binary = work / 'piano-fingerprint-ctl'
    run(compiler, '-static', *objects, '-pthread', '-lm', '-o', str(binary))
    return binary, [str(p) for p in imported]


def package(root, output, version, sources, epoch):
    docs = root / 'usr/share/doc' / PACKAGE
    docs.mkdir(parents=True, exist_ok=True)
    copy(REPO / 'NOTICE', docs / 'NOTICE')
    (docs / 'sources.json').write_text(json.dumps(sources, indent=2) + '\n')
    (docs / 'copyright').write_text(
        'Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n'
        'Upstream-Name: Piano fingerprint integration\n'
        'Source: https://github.com/yzddmr6/xiaomipad-8pro-mainline\n\n'
        'Files: usr/libexec/piano/piano-fingerprint-ctl\n'
        'Copyright: 2026 slwyts\n'
        'License: BSD-3-Clause\n\n'
        'Files: usr/lib/firmware/gfpia.elf usr/lib/firmware/piano-goodix-*.bin\n'
        'Copyright: Xiaomi, Goodix and respective vendors\n'
        'License: proprietary-stock\n'
        'Comment: Stock firmware distributed under the same owner decision as firmware.\n'
        ' Static data of the sensor, no per-unit data. See firmware/fingerprint/source.json\n'
        ' in the source repository.\n\n'
        'License: BSD-3-Clause\n'
        ' Redistribution and use in source and binary forms, with or without\n'
        ' modification, are permitted provided that the following conditions are met:\n'
        ' 1. Redistributions of source code must retain the above copyright notice,\n'
        '    this list of conditions and the following disclaimer.\n'
        ' 2. Redistributions in binary form must reproduce the above copyright notice,\n'
        '    this list of conditions and the following disclaimer in the documentation\n'
        '    and/or other materials provided with the distribution.\n'
        ' 3. Neither the name of the copyright holder nor the names of its\n'
        '    contributors may be used to endorse or promote products derived from\n'
        '    this software without specific prior written permission.\n\n'
        ' THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"\n'
        ' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE\n'
        ' IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE\n'
        ' ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE\n'
        ' LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR\n'
        ' CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF\n'
        ' SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS\n'
        ' INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN\n'
        ' CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)\n'
        ' ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE\n'
        ' POSSIBILITY OF SUCH DAMAGE.\n\n'
        'License: proprietary-stock\n'
        ' No redistribution licence has been established.\n'
        ' Distributed by owner decision; remove upon rightsholder request.\n')
    changelog = (f'{PACKAGE} ({version}) unstable; urgency=medium\n\n'
                 '  * Query the Goodix sensor of the pad through the secure world.\n\n'
                 ' -- yzddmr6 <46088090+yzddmr6@users.noreply.github.com>  Tue, 28 Oct 2026 14:30:00 +0800\n')
    (docs / 'changelog.gz').write_bytes(gzip.compress(changelog.encode(), mtime=epoch))
    control = root / 'DEBIAN'
    control.mkdir()
    files = {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*'))
             if p.is_file() and 'DEBIAN' not in p.relative_to(root).parts}
    installed = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    (control / 'control').write_text(
        f'Package: {PACKAGE}\nVersion: {version}\nArchitecture: arm64\nSection: misc\n'
        f'Priority: optional\nMaintainer: yzddmr6 <46088090+yzddmr6@users.noreply.github.com>\n'
        f'Depends: libc6, zlib1g\n'
        f'Installed-Size: {installed // 1024}\n'
        f'Description: Goodix fingerprint sensor of the Xiaomi Pad 8 Pro\n'
        f' The sensor bus of the fingerprint belongs to the secure world. This package\n'
        f' carries the client that queries it and the stock firmware it needs.\n')
    for path in root.rglob('*'):
        if path.is_file():
            os.utime(path, (epoch, epoch), follow_symlinks=False)
    deb = output / f'{PACKAGE}_{version}_arm64.deb'
    run('dpkg-deb', '--root-owner-group', '-Zxz', '-z1', '--build', root, deb)
    return {'file': deb.name, 'sha256': sha(deb), 'files': files}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--compiler', default='aarch64-linux-gnu-gcc')
    p.add_argument('--emulator', default='qemu-aarch64-static',
                   help='runs the cross built binary for a smoke test')
    p.add_argument('--version', default='0.1+20261028.1')
    p.add_argument('--source-cache', type=Path, default=REPO / 'out/fingerprint-sources')
    a = p.parse_args()
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cache = a.source_cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text())
    sources = dict(lock)
    sources['paths'] = {name: fetch(name, record, cache)
                        for name, record in lock['sources'].items()}
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '1792800000'))
    os.environ['SOURCE_DATE_EPOCH'] = str(epoch)
    binary, imported = build_binary(output, a.compiler, sources)
    imported_by_name = {}
    index = 0
    for name, files in lock['objects'].items():
        for relative in files:
            imported_by_name[name] = imported_by_name.get(name, []) + [relative]
            index += 1
    run(a.emulator, str(binary), '--help')
    root = output / 'root'
    copy(binary, root / 'usr/libexec/piano/piano-fingerprint-ctl', INSTALL['usr/libexec/piano/piano-fingerprint-ctl'])
    for name in ('gfpia.elf', 'piano-goodix-cap-s.bin', 'piano-goodix-cap-t.bin',
                 'piano-goodix-dubai-u4.bin'):
        copy(FIRMWARE / name, root / 'usr/lib/firmware' / name, INSTALL['usr/lib/firmware/' + name])
    for name, record in lock['sources'].items():
        license_file = sources['paths'][name] / record['license']
        copy(license_file, root / 'usr/share/licenses/piano-fingerprint' / (name + '.txt'))
    provenance = {
        'client': 'device/fingerprint/piano-goodix.c',
        'client_sha256': sha(CLIENT),
        'sources': lock['sources'],
        'imported_objects': imported_by_name,
        'firmware': {p.name: sha(p) for p in sorted(FIRMWARE.glob('*')) if p.is_file()},
        'operations': ['info', 'initialize', 'prepare-auth', 'watch', 'list'],
        'enrollment_supported': False,
        'authentication_supported': False,
    }
    target = root / 'usr/share/piano-provenance/fingerprint.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(provenance, indent=2) + '\n')
    record = package(root, output, a.version, lock, epoch)
    (output / 'manifest.json').write_text(json.dumps(
        {'scope': 'HOST_BUILD_ONLY; the secure world needs the device',
         'sources': lock['sources'], 'package': record}, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(record['sha256'] + '  ' + record['file'] + '\n')
    print(record['file'])


if __name__ == '__main__':
    main()
