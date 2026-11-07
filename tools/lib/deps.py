# SPDX-License-Identifier: MIT
"""Pinned inputs: local cache, owned copy, then immutable upstream.

DEPS_ARCHIVE and DEPS_MIRRORS select local owned copies. DEPS_OFFLINE=1
forbids all network; DEPS_OWN_ONLY=1 forbids upstream fallback.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[2]
ARCHIVE_URL = 'https://github.com/yzddmr6/deps-archive/releases/download/inputs-20261108/'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_hash(path, expected):
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('A full SHA-256 is required')
    if digest(path) != expected:
        raise ValueError(f'SHA-256 mismatch: {path}')
    return Path(path)


def check_ref(ref):
    if not re.fullmatch(r'[0-9a-f]{40}', ref):
        raise ValueError('Git inputs require a full commit, not a branch or tag: ' + ref)


def check_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('https', 'http', 'file'):
        raise ValueError('Unsupported source URL: ' + url)
    path = urllib.parse.unquote(parts.path)
    if parts.hostname in ('snapshot.ubuntu.com', 'snapshot.debian.org') and re.search(r'/[0-9]{8}T[0-9]{6}Z/pool/main/', path):
        path = path.replace('/pool/main/', '/pool/component/')
    if re.search(r'(^|[/=?&])(main|master|HEAD|latest|current|dev)([/&?=.]|$)', path + '?' + parts.query):
        raise ValueError('Floating source URL: ' + url)


def inventory():
    path = REPO / 'deps/sources.json'
    return json.loads(path.read_text()) if path.exists() else {'files': [], 'git': []}


def fetch(url, target, expected, mirrors=(), ref=None):
    """All candidates must match one hash. Integrity failures never fall back."""
    target = Path(target)
    if ref is not None:
        check_ref(ref)
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('A full SHA-256 is required')
    # Validate even on a cache hit, so cached bytes cannot hide a floating lock.
    check_url(url)
    for candidate in mirrors:
        check_url(candidate)
    if target.exists():
        return check_hash(target, expected)
    rows = [r for r in inventory()['files'] if r['sha256'] == expected]
    row = rows[0] if rows else {}
    filename = row.get('filename', expected + '-' + Path(urllib.parse.urlsplit(url).path).name)
    candidates = []
    if row.get('vendored'):
        candidates.append((REPO / row['vendored']).as_uri())
    if os.environ.get('DEPS_ARCHIVE'):
        candidates.append((Path(os.environ['DEPS_ARCHIVE']).resolve() / filename).as_uri())
    if row.get('own_url'):
        candidates.append(row['own_url'])
    elif not row or row.get('preservation') == 'C':
        candidates.append(ARCHIVE_URL + filename)
    if os.environ.get('DEPS_OWN_ONLY') != '1':
        candidates.extend(row.get('upstream', [url]))
        candidates.extend(mirrors)
    target.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for candidate in dict.fromkeys(candidates):
        check_url(candidate)
        if os.environ.get('DEPS_OFFLINE') == '1' and not candidate.startswith('file:'):
            continue
        fd, name = tempfile.mkstemp(prefix=target.name + '.part-', dir=target.parent)
        temporary = Path(name)
        try:
            request = urllib.request.Request(candidate, headers={'User-Agent': 'pinned-dependency-fetch', 'Accept': 'application/octet-stream'})
            try:
                with os.fdopen(fd, 'wb') as out, urllib.request.urlopen(request, timeout=60) as source:
                    shutil.copyfileobj(source, out, 1024 * 1024)
            except (urllib.error.URLError, TimeoutError, ConnectionError, FileNotFoundError) as exc:
                errors.append(f'{candidate}: {exc}')
                continue
            check_hash(temporary, expected)
            temporary.replace(target)
            print('source:', candidate, flush=True)
            return target
        finally:
            temporary.unlink(missing_ok=True)
    raise RuntimeError('No pinned source available for ' + target.name + '\n' + '\n'.join(errors))


def git_source(target, url, commit, name, local=None):
    """Return a Git object store with the exact commit; never update a checkout."""
    check_ref(commit)
    target = Path(target)
    def has(repo):
        return subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', commit + '^{commit}'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if local is not None:
        if not has(local):
            raise ValueError('Explicit source lacks pinned commit: ' + str(local))
        return Path(local)
    # Do not let git search a containing repository for an absent input cache.
    if (target / 'HEAD').is_file() and has(target):
        return target
    if not target.exists():
        subprocess.run(['git', 'init', '--bare', '-q', str(target)], check=True)
    elif not (target / 'HEAD').is_file():
        raise ValueError('Not a dependency object store: ' + str(target))
    candidates = []
    if os.environ.get('DEPS_MIRRORS'):
        candidates.append(str(Path(os.environ['DEPS_MIRRORS']).resolve() / (name + '.git')))
    candidates.append('https://github.com/yzddmr6/' + name + '.git')
    if os.environ.get('DEPS_OWN_ONLY') != '1':
        candidates.append(url)
    for source in dict.fromkeys(candidates):
        if os.environ.get('DEPS_OFFLINE') == '1' and '://' in source and not source.startswith('file:'):
            continue
        result = subprocess.run(['git', '-C', str(target), 'fetch', '--no-tags', '--no-recurse-submodules',
                                 source, commit + ':refs/heads/pinned/' + commit],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            if not has(target):
                raise ValueError('Mirror did not supply pinned commit: ' + source)
            print('git source:', source, commit, flush=True)
            return target
    raise RuntimeError('No source for pinned Git commit: ' + name + ' ' + commit)


def source_repo(name, lock, local=None, cache=None):
    root = Path(cache or os.environ.get('DEPS_CACHE', REPO / 'cache/deps'))
    return git_source(root / (name + '.git'), lock['url'], lock['commit'], name, local)


def export_vendor(name, dest):
    """Export the repository-owned source archive, checking its locked hash."""
    import tarfile
    row = next(r for r in inventory()['files'] if r['name'] == name and r.get('vendored'))
    source = check_hash(REPO / row['vendored'], row['sha256'])
    Path(dest).mkdir(parents=True, exist_ok=True)
    with tarfile.open(source) as archive:
        archive.extractall(dest, filter='data')
    return row
