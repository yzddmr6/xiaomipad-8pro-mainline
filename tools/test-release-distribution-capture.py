#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Run with an install.py argument inside a disposable, network-isolated APT test root.

Requires dpkg, APT and GnuPG. The chroot runner supplies a boot-switch stub.
The fixture signs its own distribution repository and removes it before rollback.
"""
import gzip
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from email.utils import formatdate


def run(*args):
    subprocess.run(args, check=True)


def build_deb(work, pool, name, source, version):
    tree = work / (name + '_' + version)
    control = tree / 'DEBIAN/control'
    control.parent.mkdir(parents=True)
    control.write_text(f'Package: {name}\nSource: {source}\nVersion: {version}\nArchitecture: all\n'
                       'Maintainer: Fixture <fixture@example.invalid>\nDescription: APT capture fixture\n')
    payload = tree / 'usr/share' / name / 'build'
    payload.parent.mkdir(parents=True)
    payload.write_text(version)
    archive = pool / (tree.name + '_all.deb')
    run('dpkg-deb', '--build', tree, archive)
    return archive


def sign_repository(work, repo, installer, archives):
    index = '\n'.join(installer.index_stanza(repo, p) for p in archives).encode()
    (repo / 'Packages').write_bytes(index)
    compressed = gzip.compress(index, mtime=0)
    (repo / 'Packages.gz').write_bytes(compressed)
    release = ['Origin: Fixture', 'Label: Fixture', 'Date: ' + formatdate(usegmt=True), 'SHA256:']
    for name, data in [('Packages', index), ('Packages.gz', compressed)]:
        release.append(f' {hashlib.sha256(data).hexdigest()} {len(data)} {name}')
    (repo / 'Release').write_text('\n'.join(release) + '\n')
    keys = work / 'gnupg'
    keys.mkdir(mode=0o700)
    gpg = ['gpg', '--homedir', keys, '--batch', '--pinentry-mode', 'loopback', '--passphrase', '']
    run(*gpg, '--quick-generate-key', 'APT fixture <fixture@example.invalid>', 'rsa2048', 'sign', '0')
    run(*gpg, '--output', repo / 'key.gpg', '--export')
    run(*gpg, '--output', repo / 'InRelease', '--clearsign', repo / 'Release')


def main(program):
    if os.geteuid() or not Path('/etc/release-disposable-test-root').is_file():
        sys.exit('requires a disposable root')
    store = Path('/var/lib/piano/releases')
    if store.exists():
        sys.exit('requires an empty Piano release store')
    spec = importlib.util.spec_from_file_location('installer', program)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    names = ('test-capture-lib', 'test-capture-common')
    original = '50.1-0ubuntu2'
    replacement = '50.1-0ubuntu2+sm8750.1'
    with tempfile.TemporaryDirectory(dir='/opt') as tmp:
        work = Path(tmp)
        work.chmod(0o755)
        distro, old, new = (work / name for name in ('distribution', 'previous', 'next'))
        for repo in (distro, old, new):
            (repo / 'pool').mkdir(parents=True)
        originals = [build_deb(work, distro / 'pool', n, 'stock-source', original) for n in names]
        build_deb(work, old / 'pool', 'test-capture-base', 'base-source', '1')
        for n in names:
            build_deb(work, new / 'pool', n, 'piano-replacement-source', replacement)
        unchanged = build_deb(work, new / 'pool', 'test-capture-existing', 'other-source', '1')
        installer.inventory(old, 'fixture-old', 'piano', ['test-capture-base'])
        installer.inventory(new, 'fixture-new', 'piano', [*names, 'test-capture-existing'])
        run('dpkg', '-i', unchanged, *originals, *sorted((old / 'pool').glob('*.deb')))
        boot = Path('/var/lib/piano/switch/ubuntu')
        boot.mkdir(parents=True, exist_ok=True)
        for image in ('boot.img', 'init_boot.img', 'vendor_boot.img'):
            (boot / image).write_bytes(b'fixture')
        upgrade = [sys.executable, program, 'upgrade', new, '--previous', old]
        # dpkg's status record alone cannot supply a rollback archive.
        denied = subprocess.run(upgrade, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert denied.returncode and ('unavailable' in denied.stdout or 'apt-get' in denied.stdout), denied.stdout
        assert not (store / 'transaction.json').exists()
        sign_repository(work, distro, installer, originals)
        source = Path('/etc/apt/sources.list.d/capture-fixture.list')
        source.write_text(f'deb [signed-by={distro}/key.gpg] file:{distro} ./\n')
        run('apt-get', 'update')
        run(*upgrade)
        snapshot = store / 'fixture-old-before-fixture-new'
        saved = {r['package']: r for r in installer.verify(snapshot)['packages']}
        for n, original_file in zip(names, originals):
            assert saved[n]['source'] == 'stock-source'
            assert installer.sha(snapshot / saved[n]['file']) == installer.sha(original_file)
            assert Path('/usr/share', n, 'build').read_text() == replacement
        assert not subprocess.check_output(['dpkg', '--audit']).strip()
        source.unlink()
        distro.rename(work / 'archive-offline')
        run(sys.executable, program, 'rollback', '--product', 'piano')
        for n in names:
            status = subprocess.check_output(['dpkg-query', '-W', '-f=${Status}\t${Version}', n], text=True)
            assert status == 'install ok installed\t' + original, status
            assert Path('/usr/share', n, 'build').read_text() == original
        assert not subprocess.check_output(['dpkg', '--audit']).strip()
        assert Path('/usr/share/test-capture-existing/build').read_text() == '1'
        print('PASS signed exact capture, project upgrade, offline rollback, clean audit')


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
