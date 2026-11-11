#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prepare and build the pinned Ubuntu Mesa source package in a resolute root.

prepare runs without root. build runs inside a disposable native ARM64 (or
qemu-user ARM64) build root, never on the installed desktop system.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[1]

def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def run(argv, **kw):
    return subprocess.run(argv, check=True, **kw)

def extract(archive, dest):
    with tarfile.open(archive) as t:
        t.extractall(dest, filter='data')

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('prepare', 'build'))
    ap.add_argument('--inputs', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--jobs', type=int, default=os.cpu_count())
    a = ap.parse_args()
    lock = json.loads((REPO / 'mesa/ubuntu/source.json').read_text())
    mesa = json.loads((REPO / 'mesa/source.json').read_text())
    inputs, work = a.inputs.resolve(), a.work.resolve()
    if a.action == 'prepare':
        if work.exists():
            ap.error('work directory already exists; reuse only with build')
        work.mkdir(parents=True)
        for c in lock['components'].values():
            for f in c['files']:
                if sha(inputs / f['file']) != f['sha256']:
                    raise ValueError('input checksum mismatch: ' + f['file'])
        if sha(inputs / mesa['archive']) != mesa['archive_sha256']:
            raise ValueError('Mesa upstream archive checksum mismatch')
        records = []
        for name in ('mesa',):
            spec = lock['components'][name]
            parent = work / name
            parent.mkdir()
            extract(inputs / mesa['archive'], parent)
            source = parent / ('mesa-' + mesa['version'])
            debian = next(f for f in spec['files'] if '.debian.tar.' in f['file'])
            extract(inputs / debian['file'], source)
            series = source / 'debian/patches/series'
            keep = []
            for line in series.read_text().splitlines():
                if not line.strip() or line.startswith('#'):
                    continue
                patch = source / 'debian/patches' / line.split()[0]
                result = subprocess.run(['patch', '--dry-run', '--force', '-R', '-p1', '-i', str(patch)],
                                        cwd=source, capture_output=True, text=True)
                state = 'already-in-upstream' if result.returncode == 0 else 'apply-distribution-patch'
                if result.returncode:
                    run(['patch', '--dry-run', '--force', '-p1', '-i', str(patch)], cwd=source)
                    keep.append(line)
                records.append(dict(patch=patch.name, sha256=sha(patch), state=state))
            patches = [dict(path=mesa['patch'], sha256=mesa['patch_sha256']), *mesa['additional_patches']]
            for item in patches:
                patch = REPO / item['path']
                if sha(patch) != item['sha256']:
                    raise ValueError('local patch checksum mismatch: ' + str(patch))
                target = source / 'debian/patches' / ('sm8750-' + patch.name)
                shutil.copyfile(patch, target)
                keep.append(target.name)
                records.append(dict(patch=target.name, sha256=sha(target), state='apply-local-unchanged'))
            for item in lock.get('build_patches', []):
                patch = REPO / 'mesa/ubuntu' / item['file']
                if sha(patch) != item['sha256']:
                    raise ValueError('build patch checksum mismatch: ' + item['file'])
                target = source / 'debian/patches' / item['file']
                shutil.copyfile(patch, target)
                keep.append(target.name)
                records.append(dict(patch=target.name, sha256=sha(target), state='apply-test-build-only'))
            series.write_text('\n'.join(keep) + '\n')
            changelog = source / 'debian/changelog'
            entry = (f'{name} ({spec["local_version"]}) resolute; urgency=medium\n\n'
                     '  * Rebuild the Ubuntu packaging for the supported ARM64 graphics stack.\n\n'
                     ' -- yzddmr6 <46088090+yzddmr6@users.noreply.github.com>  Wed, 11 Nov 2026 00:00:00 +0000\n\n')
            changelog.write_text(entry + changelog.read_text())
            (parent / 'source-path').write_text(str(source.relative_to(work)) + '\n')
            run(['dpkg-source', '--before-build', str(source)])
        (work / 'prepared.json').write_text(json.dumps(dict(lock=lock, mesa=mesa, patches=records,
            recipe_sha256=sha(__file__)), indent=2) + '\n')
        return
    # This opt-in sentinel prevents accidental installs into the host/tablet OS.
    if not Path('/etc/mesa-disposable-build-root').is_file():
        ap.error('build must run in the dedicated disposable build root')
    if os.uname().machine not in ('aarch64', 'arm64'):
        ap.error('build requires an ARM64 root (native or qemu-user)')
    prepared = json.loads((work / 'prepared.json').read_text())
    if prepared['lock'] != lock or prepared['mesa'] != mesa or prepared['recipe_sha256'] != sha(__file__):
        ap.error('source/recipe identity changed since prepare')
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive', LC_ALL='C.UTF-8', TZ='UTC',
               PYTHONHASHSEED='0', SOURCE_DATE_EPOCH='1794355200',
               DEB_BUILD_OPTIONS=f'parallel={a.jobs}', DPKG_DEB_THREADS_MAX=str(a.jobs))
    ubuntu_debs = []
    for item in lock['ubuntu_binaries']['files']:
        path = inputs / item['file']
        if sha(path) != item['sha256']:
            raise ValueError('Ubuntu binary checksum mismatch: ' + str(path))
        ubuntu_debs.append(str(path))
    run(['apt-get', '-y', '--allow-downgrades', 'install', *ubuntu_debs], env=env)
    for name in ('mesa',):
        source = work / (work / name / 'source-path').read_text().strip()
        done = work / name / 'build-complete.json'
        if not done.exists():
            run(['apt-get', '-y', '--no-install-recommends', 'build-dep', str(source)], env=env)
            selected = subprocess.check_output(['dh_listpackages'], cwd=source, text=True).split()
            (work / 'binary-packages.json').write_text(json.dumps({'mesa': selected}, indent=2) + '\n')
            (work / 'build-environment.txt').write_text(subprocess.check_output(
                ['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n'], text=True))
            run(['dpkg-buildpackage', '-b', '-us', '-uc', f'-j{a.jobs}'], cwd=source, env=env)
            debs = sorted(source.parent.glob('*.deb'))
            if not debs:
                raise RuntimeError('no packages built: ' + name)
            done.write_text(json.dumps({p.name:sha(p) for p in debs}, indent=2) + '\n')
    (work / 'ubuntu-binaries').mkdir(exist_ok=True)
    for item in lock['ubuntu_binaries']['files']:
        shutil.copyfile(inputs / item['file'], work / 'ubuntu-binaries' / item['file'])
    packages = []
    for p in sorted(work.glob('*/*.deb')):
        fields = subprocess.check_output(['dpkg-deb', '-f', str(p), 'Package', 'Version', 'Architecture', 'Source'], text=True)
        packages.append(dict(file=str(p.relative_to(work)), sha256=sha(p), bytes=p.stat().st_size, control=fields))
    (work / 'packages.json').write_text(json.dumps(packages, indent=2) + '\n')
    print('HOST_MESA_PACKAGES_BUILT', flush=True)

if __name__ == '__main__':
    main()
