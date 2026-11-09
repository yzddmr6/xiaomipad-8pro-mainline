#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Resolve and optionally download ARM64 packages against an immutable Desktop root.

Uses native host APT with isolated configuration, status, caches and logs.
Does not install packages, run maintainer scripts, alter the rootfs or access a tablet.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

import apt_pkg
from debian.deb822 import Deb822


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_removals(plan, replace_ppd=False):
    removed = set(re.findall(r'^Remv ([^ :]+)(?::\S+)?(?: |$)', plan, re.M))
    allowed = {'power-profiles-daemon'} if replace_ppd else set()
    if removed - allowed:
        raise ValueError('Unexpected APT removals: ' + ', '.join(sorted(removed - allowed)))
    return sorted(removed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', required=True, type=Path, help='Pristine Desktop SquashFS')
    parser.add_argument('--snapshot', required=True, help='Ubuntu snapshot UTC timestamp, YYYYMMDDTHHMMSSZ')
    parser.add_argument('--output', required=True, type=Path, help='New isolated APT directory')
    parser.add_argument('--archive-cache', type=Path, action='append', default=[],
                        help='Seed APT archives from existing deb caches; signed indexes still select versions')
    parser.add_argument('--download', action='store_true', help='Download only; never invoke dpkg installation')
    parser.add_argument('--mozilla-firefox', action='store_true',
                        help='Include the locked Mozilla Firefox deb and its signed repository policy')
    parser.add_argument('packages', nargs='+', help='Package names or name=version selections')
    args = parser.parse_args()
    replace_ppd = any(p.split('=')[0] == 'tuned-ppd' for p in args.packages)
    try:
        datetime.datetime.strptime(args.snapshot, '%Y%m%dT%H%M%SZ')
    except ValueError:
        parser.error('Snapshot must be a UTC timestamp')
    if not all(re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?:=[A-Za-z0-9.+:~_-]+)?', p) for p in args.packages):
        parser.error('Use package names or exact name=version selections, not APT command options')
    rootfs = args.rootfs.resolve()
    repo = Path(__file__).resolve().parent.parent
    profile = repo / 'ubuntu/26.04.sources'

    def read_root(name):
        return subprocess.check_output(['unsquashfs', '-cat', str(rootfs), name])

    release = dict(word.split('=', 1) for word in shlex.split(read_root('etc/os-release').decode(), comments=True))
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '26.04':
        parser.error('This profile supports Ubuntu 26.04 only')
    status = read_root('var/lib/dpkg/status')
    installed = list(Deb822.iter_paragraphs(status.decode()))
    architectures = {p['Architecture'] for p in installed if p.get('Status') == 'install ok installed'}
    if architectures - {'arm64', 'all'} or 'arm64' not in architectures:
        parser.error('Rootfs dpkg status is not an ARM64 installation')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name in ['etc/apt/sources.list.d', 'etc/apt/apt.conf.d', 'etc/apt/preferences.d', 'etc/apt/trusted.gpg.d',
                 'state/lists/partial', 'cache/archives/partial', 'log']:
        (out / name).mkdir(parents=True, exist_ok=True)
    (out / 'state/status').write_bytes(status)
    key = out / 'etc/apt/ubuntu-archive-keyring.gpg'
    key.write_bytes(read_root('usr/share/keyrings/ubuntu-archive-keyring.gpg'))
    sources = list(Deb822.iter_paragraphs(profile.read_text()))
    for source in sources:
        source['Signed-By'] = str(key)
        source['URIs'] = f'https://snapshot.ubuntu.com/ubuntu/{args.snapshot}/'
    (out / 'etc/apt/sources.list.d/ubuntu.sources').write_text('\n'.join(p.dump() for p in sources))

    firefox = None
    if args.mozilla_firefox:
        firefox_dir = repo / 'ubuntu/firefox'
        firefox = json.loads((firefox_dir / 'source.json').read_text())
        mozilla_key = out / 'etc/apt/packages.mozilla.org.asc'
        mozilla_key.write_bytes((firefox_dir / mozilla_key.name).read_bytes())
        if sha(mozilla_key) != firefox['key_sha256']:
            raise ValueError('Mozilla signing key differs from the input lock')
        fingerprints = subprocess.check_output(
            ['gpg', '--batch', '--show-keys', '--with-colons', str(mozilla_key)], text=True)
        if 'fpr:::::::::' + firefox['key_fingerprint'] + ':' not in fingerprints:
            raise ValueError('Mozilla signing key fingerprint differs from the input lock')
        for name, field in [('InRelease', 'inrelease_sha256'), ('Packages', 'arm64_packages_sha256')]:
            if sha(firefox_dir / name) != firefox[field]:
                raise ValueError(f'Mozilla {name} differs from the input lock')
        # Mozilla has no timestamped snapshot endpoint. Archive and authenticate
        # the small arm64 index so a later live-index update cannot change this build.
        keyring = out / 'etc/apt/mozilla.gpg'
        subprocess.run(['gpg', '--batch', '--dearmor', '--output', str(keyring), str(mozilla_key)], check=True)
        release = out / 'mozilla.Release'
        subprocess.run(['gpgv', '--keyring', str(keyring), '--output', str(release),
                        str(firefox_dir / 'InRelease')], check=True)
        index = firefox_dir / 'Packages'
        if not re.search(r'^ ' + firefox['arm64_packages_sha256'] + ' ' + str(index.stat().st_size)
                         + r' main/binary-arm64/Packages$', release.read_text(), re.M):
            raise ValueError('Mozilla Packages not covered by the signed Release')
        browser = [p for p in Deb822.iter_paragraphs(index.read_text())
                   if p.get('Package') == 'firefox' and p.get('Version') == firefox['version']]
        if len(browser) != 1 or any(browser[0].get(k) != str(firefox[v]) for k, v in
                                   [('Architecture', 'architecture'), ('SHA256', 'sha256'), ('Size', 'bytes')]):
            raise ValueError('Firefox package lock differs from the signed index')
        if firefox['repository'] + '/' + browser[0]['Filename'] != firefox['url']:
            raise ValueError('Firefox URL differs from the signed index')
        source = Deb822((firefox_dir / 'mozilla.sources').read_text())
        source['Signed-By'] = str(mozilla_key)
        (out / 'etc/apt/sources.list.d/mozilla.sources').write_text(source.dump())
        (out / 'etc/apt/preferences.d/mozilla').write_bytes((firefox_dir / 'mozilla.pref').read_bytes())
        args.packages = [p for p in args.packages if p.split('=')[0] != 'firefox']
        args.packages.append('firefox=' + firefox['version'])

    config = apt_pkg.Configuration()
    for name, value in {
        'Dir::Etc': str(out / 'etc/apt'), 'Dir::Etc::main': '/dev/null',
        'Dir::Etc::parts': str(out / 'etc/apt/apt.conf.d'),
        'Dir::Etc::sourcelist': '/dev/null', 'Dir::Etc::trusted': '/dev/null',
        'Dir::State': str(out / 'state'), 'Dir::State::status': str(out / 'state/status'),
        'Dir::Cache': str(out / 'cache'), 'Dir::Log': str(out / 'log'),
        'APT::Architecture': 'arm64', 'APT::Architectures::': 'arm64',
        'Acquire::Languages': 'none', 'Acquire::http::Timeout': '30',
        'Acquire::https::Timeout': '30', 'APT::Update::Error-Mode': 'any',
        'Acquire::IndexTargets::deb::DEP-11::DefaultEnabled': 'false',
    }.items():
        config.set(name, value)
    conf = out / 'apt.conf'
    conf.write_text(config.dump())
    env = dict(os.environ, APT_CONFIG=str(conf), LC_ALL='C')
    result = {'state': 'PREPARING', 'scope': 'APT resolution/download only, not package installation or boot',
              'snapshot': args.snapshot, 'rootfs_sha256': sha(rootfs), 'rootfs_bytes': rootfs.stat().st_size,
              'dpkg_status_sha256': hashlib.sha256(status).hexdigest(), 'keyring_sha256': sha(key),
              'profile_sha256': sha(profile), 'tool_sha256': sha(Path(__file__)),
              'apt_version': subprocess.check_output(['apt-get', '--version'], text=True).splitlines()[0],
              'requested': args.packages, 'commands': [], 'debs': [], 'mozilla_firefox': firefox}

    def save():
        (out / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')

    def apt(stage, words):
        command = ['apt-get', *words]
        print(stage, shlex.join(command), flush=True)
        with (out / (stage + '.log')).open('wb') as log:
            proc = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        result['commands'].append({'stage': stage, 'argv': command, 'returncode': proc.returncode,
                                   'log_sha256': sha(out / (stage + '.log'))})
        save()
        if proc.returncode:
            raise RuntimeError(f'APT {stage} failed; inspect {out / (stage + ".log")}')

    save()
    try:
        # Update only the immutable Ubuntu snapshot. Mozilla lists below are
        # authenticated above and deliberately never fetched from the live index.
        apt('update', ['-o', 'Dir::Etc::sourcelist=' + str(out / 'etc/apt/sources.list.d/ubuntu.sources'),
                       '-o', 'Dir::Etc::sourceparts=-', 'update'] if firefox else ['update'])
        if firefox:
            prefix = out / 'state/lists/packages.mozilla.org_apt_dists_mozilla_'
            for name, suffix in [('InRelease', 'InRelease'), ('Packages', 'main_binary-arm64_Packages')]:
                shutil.copyfile(firefox_dir / name, str(prefix) + suffix)
        # Only the standard PPD provider replacement may remove a base package.
        removal_guard = [] if replace_ppd else ['--no-remove']
        apt('plan', ['--simulate', *removal_guard, 'install', *args.packages])
        result['removed_packages'] = check_removals((out / 'plan.log').read_text(), replace_ppd)
        downgrade = ['--allow-downgrades'] if firefox else []
        apt('uris', ['--print-uris', '--download-only', *downgrade, '--assume-yes', *removal_guard, 'install', *args.packages])
        result['index_sha256'] = {p.name: sha(p) for p in sorted((out / 'state/lists').iterdir())
                                  if p.is_file() and p.name != 'lock'}
        if args.download:
            # APT cleanly ignores unselected archives; omit them from the input lock.
            selected = {line.split()[1] for line in (out / 'uris.log').read_text().splitlines()
                        if line.startswith("'") and len(line.split()) >= 4}
            for cache in args.archive_cache:
                for name in sorted(selected):
                    cached = cache / name
                    if cached.is_file():
                        shutil.copyfile(cached, out / 'cache/archives' / name)
            apt('download', ['--download-only', *downgrade, '--assume-yes', *removal_guard, 'install', *args.packages])
            for path in sorted((out / 'cache/archives').glob('*.deb')):
                fields = Deb822(subprocess.check_output(['dpkg-deb', '--field', str(path)], text=True))
                if fields['Architecture'] not in ('all', 'arm64'):
                    raise RuntimeError(f'Unexpected downloaded architecture: {path.name}')
                result['debs'].append({'file': str(path.relative_to(out)), 'package': fields['Package'],
                                       'version': fields['Version'], 'architecture': fields['Architecture'],
                                       'bytes': path.stat().st_size, 'sha256': sha(path),
                                       'source': firefox['repository'] if firefox and fields['Package'] == 'firefox'
                                       else f'Ubuntu snapshot {args.snapshot}'})
        if firefox and args.download:
            browser = [d for d in result['debs'] if d['package'] == 'firefox']
            if len(browser) != 1 or any(browser[0][key] != firefox[key]
                                        for key in ('version', 'architecture', 'sha256', 'bytes')):
                raise ValueError('Downloaded Firefox differs from ubuntu/firefox/source.json')
        result['state'] = 'DOWNLOADED_NOT_INSTALLED' if args.download else 'RESOLVED_NOT_INSTALLED'
    except Exception as exc:
        result['state'] = 'FAILED'
        result['error'] = str(exc)
        save()
        raise
    save()
    print(json.dumps({'state': result['state'], 'debs': len(result['debs']), 'output': str(out)}, indent=2))


if __name__ == '__main__':
    main()
