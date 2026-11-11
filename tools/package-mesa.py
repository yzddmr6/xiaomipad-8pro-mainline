#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Package the standard Ubuntu Mesa cohort and its upgrade-compatible metadata."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
RUNTIME = ('libgbm1', 'libegl-mesa0', 'libglx-mesa0', 'libgl1-mesa-dri',
           'mesa-libgallium', 'mesa-vulkan-drivers')

def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def metadata(p):
    s = subprocess.check_output(['dpkg-deb', '-f', str(p)], text=True)
    return dict(l.split(': ', 1) for l in s.splitlines() if ': ' in l and not l.startswith(' '))

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mesa-build', type=Path, required=True, help='build-host-mesa work directory')
    ap.add_argument('--ubuntu-inputs', type=Path, required=True, help='Verified original Ubuntu libdrm downloads')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--version', required=True, help='increasing piano-mesa transition version')
    ap.add_argument('--release-id', required=True, help='whole release batch identifier, for example piano-1.2.1')
    ap.add_argument('--release-packages', type=Path, action='append', default=[], help='additional packages supplied by this release')
    a = ap.parse_args()
    build, output = a.mesa_build.resolve(), a.output.resolve()
    lock = json.loads((REPO / 'mesa/ubuntu/source.json').read_text())
    source = json.loads((REPO / 'mesa/source.json').read_text())
    prepared = json.loads((build / 'prepared.json').read_text())
    if prepared['lock'] != lock or prepared['mesa'] != source:
        ap.error('build inputs differ from the source locks')
    receipt = json.loads((build / 'packages.json').read_text())
    # Ubuntu's automatic debug packages are outside debian/control and use
    # .ddeb. Preserve them too, under .deb names so the same APT index serves
    # the complete actual build output without another repository type.
    for path in sorted(build.glob('mesa/*.ddeb')):
        receipt.append(dict(file=str(path.relative_to(build)), sha256=sha(path)))
    output.mkdir(parents=True, exist_ok=True)
    repo_id = a.release_id
    repo = output / repo_id
    repo.mkdir()
    pool = repo / 'pool'; pool.mkdir()
    records, names, pin_groups = [], set(), {}
    for component in ('mesa',):
        src = build / (build / component / 'source-path').read_text().strip()
        declared = set(re.findall(r'^Package: (\S+)', (src / 'debian/control').read_text(), re.M))
        actual = set()
        for row in receipt:
            if not row['file'].startswith(component + '/'):
                continue
            p = build / row['file']
            if sha(p) != row['sha256']:
                ap.error('package hash mismatch: ' + str(p))
            m = metadata(p)
            name = m['Package']
            if (name not in declared and name.removesuffix('-dbgsym') not in declared) or name in names:
                ap.error('unexpected or duplicate binary package: ' + name)
            if m['Version'] != lock['components'][component]['local_version'] or m['Architecture'] not in ('arm64', 'all'):
                ap.error('package version/architecture differs from cohort')
            actual.add(name); names.add(name)
            filename = p.with_suffix('.deb').name
            shutil.copyfile(p, pool / filename)
            records.append(dict(package=name, version=m['Version'], architecture=m['Architecture'],
                                source=component, file='pool/' + filename, sha256=sha(p), bytes=p.stat().st_size))
        native_names = json.loads((build / 'binary-packages.json').read_text())[component]
        if native_names and not set(native_names).issubset(actual):
            ap.error('missing binary packages: ' + str(set(native_names) - actual))
        if not actual:
            ap.error('component contains no packages: ' + component)
        pin_groups[component] = sorted(actual)
    for item in lock['ubuntu_binaries']['files']:
        p = a.ubuntu_inputs / item['file']
        if sha(p) != item['sha256'] or metadata(p)['Version'] != item['version']:
            ap.error('Ubuntu libdrm binary differs from lock: ' + str(p))
        shutil.copyfile(p, pool / p.name)
        records.append(dict(package=item['package'], version=item['version'], architecture=item['architecture'],
                            source='libdrm', file='pool/' + p.name, sha256=sha(p), bytes=p.stat().st_size))
    if not set(RUNTIME).issubset(names):
        ap.error('missing runtime package')
    pin = ''
    for component, packages in pin_groups.items():
        joined = ' '.join(packages)
        pin += (f'Package: {joined}\nPin: version {lock["components"][component]["local_version"]}\n'
                'Pin-Priority: 1001\n\n'
                f'Package: {joined}\nPin: version *\nPin-Priority: -1\n\n')
    stage = output / 'stages/piano-mesa'
    (stage / 'DEBIAN').mkdir(parents=True)
    depends = ', '.join(f'{n} (= {lock["components"]["mesa"]["local_version"]})' for n in RUNTIME)
    (stage / 'DEBIAN/control').write_text(
        f'Package: piano-mesa\nVersion: {a.version}\nArchitecture: arm64\n'
        'Maintainer: yzddmr6 <46088090+yzddmr6@users.noreply.github.com>\n'
        'Section: oldlibs\nPriority: optional\n'
        f'Depends: {depends}, python3, init-system-helpers\n'
        'Description: Matched Ubuntu graphics packages for supported SM8750 tablets\n'
        ' Transition from the former private Mesa prefix to standard system packages.\n')
    conf = stage / 'etc/apt/preferences.d/piano-host-mesa'
    conf.parent.mkdir(parents=True); conf.write_text(pin)
    # Generated cohort policy is an ordinary dpkg-owned file: downgrading to the
    # old transition package removes it instead of retaining an obsolete conffile.
    legacy = ('/etc/ld.so.conf.d/00-piano-mesa.conf', '/etc/piano-mesa-prefix',
              '/etc/piano-mesa.json', '/etc/piano-mesa.sha256')
    for phase in ('preinst', 'postinst', 'postrm'):
        script = '#!/bin/sh\nset -e\n' + (REPO / 'mesa/ubuntu/conffile-migration.sh').read_text() + '\n'
        if phase == 'preinst':
            script += ('if [ -r /proc/device-tree/compatible ] && '
                       'tr "\\000" "\\n" < /proc/device-tree/compatible | grep -Eq "xiaomi,liuqin|qcom,sm8450"; then\n'
                       '  echo "This graphics cohort is not for this device" >&2; exit 1\nfi\n')
        for path in legacy:
            script += f'retire_mesa_conffile piano-mesa {a.version} {path} "$@"\n'
        if phase in ('postinst', 'postrm'):
            script += 'ldconfig\n'
        f = stage / 'DEBIAN' / phase; f.write_text(script); f.chmod(0o755)
    doc = stage / 'usr/share/doc/piano-mesa'; doc.mkdir(parents=True)
    shutil.copyfile(REPO / 'LICENSE', doc / 'copyright')
    (doc / 'build.json').write_text(json.dumps(dict(source=source, layout='ubuntu-multiarch',
        ubuntu=lock, packages=records, pin_sha256=sha(conf),
        recipe_sha256=sha(__file__), conffile_migration_sha256=sha(REPO / 'mesa/ubuntu/conffile-migration.sh')), indent=2)+'\n')
    package = output / f'piano-mesa_{a.version}_arm64.deb'
    subprocess.run(['dpkg-deb','--root-owner-group','-Zxz','-z1','--build',str(stage),str(package)],check=True,
                   env=dict(os.environ,SOURCE_DATE_EPOCH='1794355200'))
    shutil.copyfile(package,pool/package.name)
    records.append(dict(package='piano-mesa',version=a.version,architecture='arm64',source='piano-mesa',
                        file='pool/'+package.name,sha256=sha(package),bytes=package.stat().st_size))
    for directory in a.release_packages:
        for p in directory.rglob('*.deb'):
            dest = pool / p.name
            if dest.exists() and sha(dest) != sha(p):
                ap.error('conflicting release package: ' + p.name)
            shutil.copyfile(p, dest)
    import importlib.util
    spec = importlib.util.spec_from_file_location('release_repository', REPO / 'tools/release-repository.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    defaults = {'piano-mesa'}
    for directory in a.release_packages:
        defaults.update(metadata(p)['Package'] for p in directory.rglob('*.deb'))
    module.inventory(repo, repo_id, 'piano', defaults)
    module.verify(repo)
    result=dict(file=package.name,sha256=sha(package),depends=depends,repository=repo.name,
                repository_manifest_sha256=sha(repo/'manifest.json'),files={})
    (output/'mesa-package.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':
    main()
