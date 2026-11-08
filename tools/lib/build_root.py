# SPDX-License-Identifier: GPL-2.0-only
# Adapted for piano, 2026-10-11; see mesa/snap/provenance.json.
"""Shared pinned ARM64 build-root helpers; no device or package policy."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import importlib.util

_deps_spec = importlib.util.spec_from_file_location('pinned_deps', Path(__file__).with_name('deps.py'))
_deps = importlib.util.module_from_spec(_deps_spec)
_deps_spec.loader.exec_module(_deps)
fetch = _deps.fetch

REPO = Path(__file__).resolve().parents[2]
if (REPO.parent.parent / 'AGENTS.md').is_file() and (REPO.parent.parent / 'kernel/linux').is_dir():
    WS = Path(os.environ.get('PIANO_WORKSPACE', REPO.parent.parent)).resolve()
else:
    WS = Path(os.environ.get('PIANO_WORKSPACE', str(REPO))).resolve()
HOST_CA = Path('/etc/ssl/certs/ca-certificates.crt')
CHROOT_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'


def die(message):
    raise SystemExit(f'build-root: {message}')


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()



def chroot(root, script, env=None, log=None):
    """Run a bash script as root inside the arm64 build root."""
    variables = {'PATH': CHROOT_PATH, 'HOME': '/root', 'LC_ALL': 'C.UTF-8',
                 'DEBIAN_FRONTEND': 'noninteractive', 'QEMU_CPU': 'max,pauth-impdef=on', **(env or {})}
    inner = ['/usr/bin/env', '-i', *[f'{k}={v}' for k, v in variables.items()],
             '/bin/bash', '-euo', 'pipefail', '-c', script]
    wrapper = ('mount --rbind /dev "$1/dev" && mount -t proc proc "$1/proc" && '
               'shift && exec chroot "$@"')
    command = ['unshare', '--map-root-user', '--mount', '--pid', '--fork',
               '--kill-child', 'sh', '-c', wrapper, 'sh', str(root), str(root), *inner]
    with open(log, 'a') if log else open(os.devnull, 'w') as sink:
        result = subprocess.run(command, stdout=sink if log else None,
                                stderr=subprocess.STDOUT if log else None)
    if result.returncode:
        die(f'build step failed ({result.returncode})' + (f'; see {log}' if log else ''))


def capture(root, script):
    result = subprocess.run(
        ['unshare', '--map-root-user', '--mount', '--pid', '--fork', '--kill-child',
         'sh', '-c', 'mount -t proc proc "$1/proc" && exec chroot "$1" /bin/bash -euo pipefail -c "$2"',
         'sh', str(root), script],
        capture_output=True, text=True, env={'PATH': CHROOT_PATH, 'LC_ALL': 'C.UTF-8'})
    if result.returncode:
        die(f'chroot query failed: {result.stderr.strip()}')
    return result.stdout


def prepare_root(work, lock, base, log):
    """Unpack the pinned build root and install the pinned build dependencies."""
    root = work / 'root'
    identity = {'build_root': lock['build_root'], 'apt': lock['apt']}
    marker = work / 'root.json'
    if marker.is_file():
        if json.loads(marker.read_text()) != identity:
            die(f'{work} was prepared from different inputs; select a new --work')
        return root
    if root.exists():
        die(f'{root} exists without a record of its inputs; select a new --work')
    print('  unpacking the build root', flush=True)
    root.mkdir(parents=True)
    with tarfile.open(base) as tar:
        members = [m for m in tar.getmembers()
                   if not (m.isdev() or m.name.lstrip('./').startswith('dev/'))]
        tar.extractall(root, members=members, filter='fully_trusted', numeric_owner=True)
    (root / 'dev').mkdir(exist_ok=True)
    apt = lock['apt']
    sources = root / 'etc/apt/sources.list.d/ubuntu.sources'
    sources.write_text(f'Types: deb\nURIs: {apt["uri"]}\nSuites: {" ".join(apt["suites"])}\n'
                       f'Components: {" ".join(apt["components"])}\n'
                       'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n')
    (root / 'etc/apt/apt.conf.d/99piano-build').write_text(
        'APT::Sandbox::User "root";\n'
        'Acquire::https::CaInfo "/etc/apt/piano-build-ca.crt";\n'
        'Acquire::Check-Valid-Until "false";\n'
        'APT::Install-Recommends "false";\n'
        'Acquire::Languages "none";\n')
    shutil.copyfile(HOST_CA, root / 'etc/apt/piano-build-ca.crt')
    resolv = root / 'etc/resolv.conf'
    if resolv.is_symlink() or resolv.exists():
        resolv.unlink()
    shutil.copyfile('/etc/resolv.conf', resolv)
    print(f'  installing {len(apt["packages"])} build dependencies from snapshot '
          f'{apt["snapshot"]} (slow under qemu-user)', flush=True)
    chroot(root, 'apt-get update -q && apt-get install -y -q ' + ' '.join(apt['packages'])
           + ' && apt-get clean && rm -rf /var/lib/apt/lists/*', log=log)
    marker.write_text(json.dumps(identity, indent=2) + '\n')
    return root
