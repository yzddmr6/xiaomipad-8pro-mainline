#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create, retain and install complete release batches using local APT repositories.

create runs on the build host. upgrade and rollback run on the target and retain
both package batches and a copy of the previous boot set. They never reboot.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def metadata(path):
    raw = subprocess.check_output(['dpkg-deb', '-f', str(path)], text=True)
    return dict(l.split(': ', 1) for l in raw.splitlines() if ': ' in l and not l.startswith(' '))


def read_json(path):
    return json.loads(path.read_text())


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def inventory(repo, identifier, product, default_names):
    rows = []
    names = set()
    for p in sorted((repo / 'pool').glob('*.deb')):
        m = metadata(p)
        if m['Package'] in names:
            raise ValueError('duplicate package in release: ' + m['Package'])
        if m['Architecture'] not in ('arm64', 'all'):
            raise ValueError('unexpected architecture: ' + p.name)
        names.add(m['Package'])
        rows.append(dict(package=m['Package'], version=m['Version'], architecture=m['Architecture'],
                         source=m.get('Source', m['Package']).split(' ')[0],
                         file='pool/' + p.name, sha256=sha(p), bytes=p.stat().st_size,
                         install=m['Package'] in default_names))
    if not rows or not set(default_names).issubset(names):
        raise ValueError('empty repository or missing default package')
    atomic_json(repo / 'manifest.json', dict(schema=1, id=identifier, product=product, packages=rows))
    with (repo / 'Packages').open('w') as out:
        run(['dpkg-scanpackages', 'pool', '/dev/null'], cwd=repo, stdout=out)
    shutil.copyfile(__file__, repo / 'install.py')
    (repo / 'install.py').chmod(0o755)
    checker = Path(__file__).with_name('check-host-mesa.py')
    if checker.is_file():
        shutil.copyfile(checker, repo / checker.name)
        (repo / checker.name).chmod(0o755)
    (repo / 'SHA256SUMS').write_text(''.join(
        f'{sha(p)}  {p.relative_to(repo)}\n' for p in sorted(repo.rglob('*'))
        if p.is_file() and p.name != 'SHA256SUMS'))
    return read_json(repo / 'manifest.json')


def verify(repo):
    repo = repo.resolve()
    sums = {}
    for line in (repo / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        p = repo / name
        if not p.resolve().is_relative_to(repo) or p.is_symlink() or sha(p) != digest:
            raise ValueError('repository integrity failure: ' + name)
        sums[name] = digest
    for p in repo.rglob('*'):
        if p.is_file() and p.name != 'SHA256SUMS' and str(p.relative_to(repo)) not in sums:
            raise ValueError('unlisted repository file: ' + str(p))
    m = read_json(repo / 'manifest.json')
    if not re.fullmatch(r'[a-z0-9][a-zA-Z0-9.+_~-]*', m['id']) or m['product'] not in ('piano', 'erhai'):
        raise ValueError('invalid release identity')
    for row in m['packages']:
        if sums.get(row['file']) != row['sha256']:
            raise ValueError('manifest checksum disagreement')
        actual = metadata(repo / row['file'])
        if any(actual[k] != row[k.lower()] for k in ('Package', 'Version', 'Architecture')):
            raise ValueError('manifest package identity disagreement')
    return m


def installed():
    text = subprocess.check_output(['dpkg-query', '-W', '-f=${db:Status-Abbrev}\t${Package}\t${Version}\n'], text=True)
    return {p: v for state, p, v in (l.split('\t') for l in text.splitlines()) if state.startswith('ii')}


def guard(product):
    if os.geteuid():
        raise ValueError('run this command as root on the target')
    compatible = Path('/proc/device-tree/compatible')
    if compatible.exists():
        data = compatible.read_bytes()
        expected = b'xiaomi,piano' if product == 'piano' else b'oneplus,erhai'
        if expected not in data or b'sm8450' in data:
            raise ValueError('release batch does not match this device')
    elif not Path('/etc/release-disposable-test-root').exists():
        raise ValueError('device identity unavailable; refusing installation')


def keep(repo, store):
    m = verify(repo)
    dest = store / m['id']
    if dest.exists():
        if verify(dest) != m or sha(dest / 'SHA256SUMS') != sha(repo / 'SHA256SUMS'):
            raise ValueError('release identifier already has different contents')
    else:
        tmp = store / ('.' + m['id'] + '.incoming')
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(repo, tmp)
        verify(tmp)
        tmp.rename(dest)
    return dest, m


def previous_for_installed(previous, old, new, before, store):
    """Retain locally updated Ubuntu dependencies alongside the previous release.

    Project release packages must match the supplied previous batch. Ubuntu
    Mesa/libdrm may have received normal archive updates since that release.
    Capture their exact cohort via authenticated APT before changing anything.
    """
    rows = {x['package']: x for x in old['packages']}
    managed = {x['package'] for x in old['packages'] + new['packages']}
    missing = {n: before[n] for n in managed & before.keys()
               if n not in rows or rows[n]['version'] != before[n]}
    if not missing:
        return previous, old
    source_by_name = {x['package']: x['source'] for x in old['packages'] + new['packages']}
    if any(source_by_name.get(n) not in ('mesa', 'libdrm') for n in missing):
        raise ValueError('previous batch is missing exact installed release packages: ' + json.dumps(missing))
    versions = {}
    for n, version in missing.items():
        source = source_by_name[n]
        if source in versions and versions[source] != version:
            raise ValueError('installed source cohort has mixed versions: ' + source)
        versions[source] = version
    identifier = old['id'] + '-before-' + new['id']
    dest = store / identifier
    if dest.exists():
        saved = verify(dest)
        available = {x['package']:x['version'] for x in saved['packages']}
        if all(available.get(n) == v for n, v in missing.items()):
            return dest, saved
        raise ValueError('existing rollback snapshot differs from the installed versions')
    temporary = store / ('.' + identifier + '.incoming')
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(previous, temporary)
    for row in old['packages']:
        if row['source'] in versions:
            (temporary / row['file']).unlink()
    with tempfile.TemporaryDirectory(prefix='release-archives-', dir='/var/tmp') as download:
        for name, source in sorted(source_by_name.items()):
            if source not in versions:
                continue
            version = versions[source]
            available = subprocess.run(['apt-cache', 'show', name + '=' + version],
                                       capture_output=True, text=True)
            if available.returncode or not available.stdout.strip():
                if name in before:
                    raise ValueError('exact installed Ubuntu package unavailable: ' + name + '=' + version)
                continue  # e.g. automatic debug packages absent from the old archive
            run(['apt-get', 'download', name + '=' + version], cwd=download)
        for archive in Path(download).glob('*.deb'):
            shutil.copyfile(archive, temporary / 'pool' / archive.name)
    saved = inventory(temporary, identifier, old['product'],
                      {x['package'] for x in old['packages'] if x['install']})
    available = {x['package']:x['version'] for x in saved['packages']}
    if any(available.get(n) != v for n, v in missing.items()):
        raise ValueError('incomplete exact Ubuntu rollback capture')
    verify(temporary); temporary.rename(dest)
    return dest, saved


def activate(repo, product):
    # The caller has verified this local immutable batch. Trust applies only to
    # this file: source, never to any network repository.
    p = Path('/etc/apt/sources.list.d') / (product + '-release.list')
    p.write_text(f'deb [trusted=yes] file:{repo} ./\n')
    run(['apt-get', 'update'])


@contextlib.contextmanager
def apt_preferences(product):
    # Only the transition package's ordinary, dpkg-owned Mesa policy is omitted
    # during a complete transaction; all other administrator policy is retained.
    name = 'piano-host-mesa'
    path = '/etc/apt/preferences.d/' + name
    owner = subprocess.run(['dpkg-query', '-S', path], capture_output=True, text=True)
    owned = owner.returncode == 0 and owner.stdout.startswith('piano-mesa: ')
    with tempfile.TemporaryDirectory(prefix='release-preferences-', dir='/var/tmp') as tmp:
        for src in Path('/etc/apt/preferences.d').iterdir():
            if src.is_file() and not (owned and src.name == name):
                shutil.copyfile(src, Path(tmp) / src.name)
        yield ['-o', 'Dir::Etc::preferencesparts=' + tmp]


def solve_and_install(targets, product):
    with apt_preferences(product) as options:
        command = ['apt-get', *options, '--allow-downgrades', '--no-install-recommends', 'install', *targets]
        run([*command[:1], '-s', *command[1:]])
        run([*command[:1], '-y', *command[1:]], env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
    run(['dpkg', '--configure', '-a'])
    audit = subprocess.check_output(['dpkg', '--audit'], text=True)
    if audit.strip():
        raise ValueError('dpkg audit failed: ' + audit)


def switch_boot(product, directory):
    command = (['piano-boot-switch', 'import-ubuntu', '--from', str(directory)] if product == 'piano'
               else ['erhai-switch-install-ubuntu-images', str(directory)])
    run(command)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='action', required=True)
    p = sub.add_parser('create')
    p.add_argument('--id', required=True)
    p.add_argument('--product', choices=('piano', 'erhai'), required=True)
    p.add_argument('--packages', type=Path, action='append', required=True)
    p.add_argument('--default-package', action='append', default=[])
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('verify'); p.add_argument('repository', type=Path)
    p = sub.add_parser('upgrade'); p.add_argument('repository', type=Path)
    p.add_argument('--previous', type=Path, required=True)
    p = sub.add_parser('rollback'); p.add_argument('--product', choices=('piano', 'erhai'), required=True)
    a = ap.parse_args()
    if a.action == 'create':
        a.output.mkdir(parents=True, exist_ok=False); (a.output / 'pool').mkdir()
        for directory in a.packages:
            for src in sorted(directory.rglob('*.deb')):
                dest = a.output / 'pool' / src.name
                if dest.exists() and sha(dest) != sha(src):
                    raise ValueError('conflicting package filename')
                shutil.copyfile(src, dest)
        inventory(a.output, a.id, a.product, a.default_package)
        verify(a.output)
        return
    if a.action == 'verify':
        print(json.dumps(verify(a.repository), indent=2)); return
    product = verify(a.repository)['product'] if a.action == 'upgrade' else a.product
    guard(product)
    store = Path('/var/lib') / product / 'releases'; store.mkdir(parents=True, exist_ok=True)
    with (store / '.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = store / 'transaction.json'
        if a.action == 'upgrade':
            repo, m = keep(a.repository, store)
            previous, old = keep(a.previous, store)
            if old['product'] != product or old['id'] == m['id']:
                raise ValueError('previous release is not a different batch of this product')
            state = read_json(state_path) if state_path.exists() else None
            if not state or state['to'] != m['id']:
                before = installed()
                previous, old = previous_for_installed(previous, old, m, before, store)
                managed = {x['package'] for x in old['packages'] + m['packages']}
                boot = Path('/var/lib') / product / 'switch/ubuntu'
                saved = store / ('boot-before-' + m['id'])
                if not saved.exists():
                    shutil.copytree(boot, saved)
                state = dict(product=product, to=m['id'], previous=old['id'], before={n:before[n] for n in managed & before.keys()},
                             boot=str(saved), phase='prepared')
                atomic_json(state_path, state)
            current = installed()
            targets = [f'{x["package"]}={x["version"]}' for x in m['packages']
                       if x['install'] or x['package'] in current]
            activate(repo, product)
            solve_and_install(targets, product)
            new_boot = store / ('boot-' + m['id'])
            if new_boot.exists():
                if state['phase'] in ('rolled-back', 'userspace-restored'):
                    switch_boot(product, new_boot)
            else:
                shutil.copytree(Path('/var/lib') / product / 'switch/ubuntu', new_boot)
            state['phase'] = 'upgraded'; atomic_json(state_path, state)
        else:
            state = read_json(state_path)
            already_rolled_back = state['phase'] == 'rolled-back'
            repo = store / state['previous']; old = verify(repo)
            newer = verify(store / state['to'])
            current = installed(); rows = {x['package']:x for x in old['packages']}
            selected = set(state['before']) | {x['package'] for x in old['packages'] if x['install']}
            # Keep optional packages installed after upgrading when the preceding
            # complete source cohort contains their earlier versions.
            selected |= current.keys() & rows.keys()
            targets = [f'{n}={rows[n]["version"]}' for n in sorted(selected)]
            targets += [x['package'] + '-' for x in newer['packages']
                        if x['package'] in current and x['package'] not in rows
                        and not x['package'].startswith(('linux-image-', 'linux-headers-', 'erhai-kernel-image-'))]
            activate(repo, product)
            solve_and_install(targets, product)
            state['phase'] = 'userspace-restored'; atomic_json(state_path, state)
            if not already_rolled_back:
                switch_boot(product, state['boot'])
            state['phase'] = 'rolled-back'; atomic_json(state_path, state)
        print(json.dumps(state, indent=2))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
