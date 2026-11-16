#!/usr/bin/python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 yzddmr6
"""Root-side release preparation and transaction control."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, '/usr/lib/python3/dist-packages')
from mainline_update.release import latest, valid_version


DEFAULT_PROJECT = Path('/usr/share/mainline-update/project.json')
CACHE_ROOT = Path('/var/cache/mainline-update')
STATE_ROOT = Path('/var/lib/mainline-update')
MAX_ASSET_SIZE = 4 * 1024 * 1024 * 1024
MAX_EXPANDED_SIZE = 10 * 1024 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


def _secure_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = path.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or
            stat.S_IMODE(st.st_mode) & 0o077):
        raise UpdateError(f'insecure directory: {path}')


def _atomic_json(path: Path, value):
    _secure_directory(path.parent)
    temporary = path.with_name('.' + path.name + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _safe_name(name):
    return bool(name and name not in ('.', '..') and '/' not in name and '\\' not in name)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _checksum_table(path):
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?(.+)', line.strip())
        if match:
            name = match[2].strip()
            if not _safe_name(name) or name in result:
                raise UpdateError('invalid or duplicate filename in release checksum list')
            result[name] = match[1].lower()
    if not result:
        raise UpdateError('release checksum list has no SHA-256 entries')
    return result


def _extract_safe(archive: Path, destination: Path, archive_format: str):
    if destination.exists():
        shutil.rmtree(destination)
    temporary = destination.with_name(destination.name + '.extracting')
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(mode=0o700)
    total = 0

    def target_for(name):
        name = name.rstrip('/')
        member = PurePosixPath(name)
        if member.is_absolute() or not member.parts or any(part in ('', '.', '..') for part in member.parts):
            raise UpdateError(f'unsafe archive path: {name}')
        target = temporary.joinpath(*member.parts)
        if not target.resolve().is_relative_to(temporary.resolve()):
            raise UpdateError(f'archive path escapes destination: {name}')
        return target

    try:
        if archive_format == 'zip':
            with zipfile.ZipFile(archive) as bundle:
                for item in bundle.infolist():
                    target = target_for(item.filename)
                    mode = (item.external_attr >> 16) & 0o170000
                    if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise UpdateError(f'unsupported archive entry: {item.filename}')
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                        continue
                    total += item.file_size
                    if total > MAX_EXPANDED_SIZE:
                        raise UpdateError('expanded release exceeds the size limit')
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with bundle.open(item) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output, 1 << 20)
                    os.chmod(target, 0o755 if (item.external_attr >> 16) & 0o111 else 0o644)
        elif archive_format == 'tar':
            with tarfile.open(archive, 'r:') as bundle:
                for item in bundle:
                    target = target_for(item.name)
                    if item.isdir():
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                        continue
                    if item.islnk():
                        source = target_for(item.linkname)
                        if not source.is_file() or source.is_symlink():
                            raise UpdateError('hard link does not reference an extracted regular file')
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        os.link(source, target)
                        continue
                    if not item.isreg():
                        raise UpdateError(f'unsupported archive entry: {item.name}')
                    total += item.size
                    if total > MAX_EXPANDED_SIZE:
                        raise UpdateError('expanded release exceeds the size limit')
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    source = bundle.extractfile(item)
                    if source is None:
                        raise UpdateError(f'cannot read archive entry: {item.name}')
                    with source, target.open('xb') as output:
                        shutil.copyfileobj(source, output, 1 << 20)
                    os.chmod(target, 0o755 if item.mode & 0o111 else 0o644)
        else:
            raise UpdateError(f'unsupported archive format: {archive_format}')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(temporary, destination)
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as error:
        raise UpdateError(f'cannot extract release archive: {error}') from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


class Updater:
    def __init__(self, project_path=None, progress=None):
        self.project_path = Path(project_path or os.environ.get('MAINLINE_UPDATE_PROJECT', DEFAULT_PROJECT))
        try:
            self.project = json.loads(self.project_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise UpdateError(f'cannot read project settings: {error}') from error
        self._validate_project()
        self.cache_root = Path(os.environ.get('MAINLINE_UPDATE_CACHE', CACHE_ROOT))
        self.state_root = Path(os.environ.get('MAINLINE_UPDATE_STATE', STATE_ROOT))
        self.progress = progress
        _secure_directory(self.cache_root)
        _secure_directory(self.state_root)

    def _event(self, **value):
        if self.progress:
            self.progress(value)

    def _validate_project(self):
        required = ('display_name', 'repository', 'repo_slug', 'api_base', 'product',
                    'installed_version_file', 'checksum_asset', 'package_asset',
                    'archive_format', 'new_batch', 'previous_batch')
        if any(not isinstance(self.project.get(k), str) or not self.project[k] for k in required):
            raise UpdateError('incomplete project configuration')
        if self.project['product'] not in ('piano', 'erhai'):
            raise UpdateError('invalid product identifier')
        if self.project['archive_format'] not in ('zip', 'tar'):
            raise UpdateError('invalid archive format')
        parsed = urllib.parse.urlparse(self.project['repository'])
        api = urllib.parse.urlparse(self.project['api_base'])
        if parsed.scheme not in ('https', 'http') or not parsed.netloc or not api.netloc:
            raise UpdateError('invalid release service URL')
        if parsed.scheme != 'https' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise UpdateError('HTTP is permitted only for loopback offline tests')
        if api.scheme != 'https' and api.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise UpdateError('HTTP API is permitted only for loopback offline tests')

    def _installed_version(self):
        path = Path(self.project['installed_version_file'])
        try:
            version = path.read_text(encoding='utf-8').strip()
        except OSError as error:
            raise UpdateError(f'cannot read installed release version: {error}') from error
        if not valid_version(version):
            raise UpdateError('installed release version is invalid')
        return version

    def _release_url(self, version=None):
        if version is None:
            found = latest(self.project['repository'])
            if found is None:
                raise UpdateError('cannot find the latest release; check the network and try again')
            return found[0], found[1].rsplit('/', 1)[-1]
        if not valid_version(version):
            raise UpdateError('invalid target version')
        tag = self.project.get('tag_prefix', 'v') + version
        return version, tag

    def _api_json(self, path):
        url = self.project['api_base'].rstrip('/') + path
        request = urllib.request.Request(url, headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'Mainline-Update',
            'X-GitHub-Api-Version': '2022-11-28',
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as error:
            raise UpdateError(f'release service request failed: {error}') from error
        if not isinstance(data, dict):
            raise UpdateError('release service returned invalid JSON')
        return data

    def _release(self, version=None):
        version, tag = self._release_url(version)
        encoded = urllib.parse.quote(tag, safe='')
        release = self._api_json(f'/repos/{self.project["repo_slug"]}/releases/tags/{encoded}')
        if release.get('tag_name') != tag or release.get('draft') or release.get('prerelease'):
            raise UpdateError('release metadata does not match the requested stable tag')
        return version, release

    def _assets(self, release):
        assets = {}
        for item in release.get('assets', []):
            name, size, url = item.get('name'), item.get('size'), item.get('browser_download_url')
            if not isinstance(name, str) or not isinstance(size, int) or not isinstance(url, str):
                continue
            if not _safe_name(name) or size < 0 or size > MAX_ASSET_SIZE or name in assets:
                raise UpdateError('release contains invalid or duplicate attachment metadata')
            parsed = urllib.parse.urlparse(url)
            test_hosts = ('127.0.0.1', 'localhost', '::1')
            if parsed.scheme != 'https' and parsed.hostname not in test_hosts:
                raise UpdateError('attachment URL is not HTTPS')
            if not url.startswith(self.project['repository'].rstrip('/') + '/releases/download/'):
                raise UpdateError('attachment URL does not belong to this project release')
            assets[name] = item
        return assets

    def _download(self, asset, directory, digest=None):
        name, expected_size = asset['name'], asset['size']
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _secure_directory(directory)
        final = directory / name
        part = directory / (name + '.part')
        for path in (final, part):
            if path.is_symlink():
                raise UpdateError(f'refusing symlink in download cache: {path.name}')
        if final.exists():
            st = final.stat()
            if (st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o600 and
                    st.st_size == expected_size and (digest is None or _sha256(final) == digest)):
                return final
            final.unlink()
        offset = part.stat().st_size if part.exists() else 0
        if part.exists():
            st = part.stat()
            if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o600:
                raise UpdateError(f'insecure partial download file: {part.name}')
        if offset > expected_size:
            part.unlink()
            offset = 0
        elif offset == expected_size and digest and _sha256(part) != digest:
            part.unlink()
            offset = 0
        elif offset == expected_size:
            os.replace(part, final)
            return final
        started = time.monotonic()
        initial_offset = offset
        headers = {'User-Agent': 'Mainline-Update', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        request = urllib.request.Request(asset['browser_download_url'], headers=headers)
        self._event(phase='downloading', file=name, received=offset, size=expected_size)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = getattr(response, 'status', response.getcode())
                if offset and status == 206:
                    content_range = response.headers.get('Content-Range', '')
                    if not content_range.startswith(f'bytes {offset}-'):
                        part.unlink(missing_ok=True)
                        raise UpdateError('server resumed from the wrong byte offset')
                    mode = 'ab'
                else:
                    offset = 0
                    initial_offset = 0
                    mode = 'wb'
                fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW |
                             (os.O_APPEND if mode == 'ab' else os.O_TRUNC), 0o600)
                count = offset
                with os.fdopen(fd, mode) as output:
                    while True:
                        block = response.read(1 << 20)
                        if not block:
                            break
                        output.write(block)
                        count += len(block)
                        if count == expected_size or count % (16 << 20) < len(block):
                            self._event(phase='downloading', file=name,
                                        received=count, size=expected_size,
                                        bytes_per_second=int((count-initial_offset)/max(.001,time.monotonic()-started)))
                        if count > expected_size:
                            raise UpdateError(f'{name}: downloaded more bytes than the release declares')
                    output.flush()
                    os.fsync(output.fileno())
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            raise UpdateError(f'{name}: download interrupted; it can resume: {error}') from error
        if count != expected_size:
            raise UpdateError(f'{name}: expected {expected_size} bytes, received {count}; retry to resume')
        if digest:
            self._event(phase='verifying', file=name, size=expected_size)
            if _sha256(part) != digest:
                part.unlink(missing_ok=True)
                raise UpdateError(f'{name}: SHA-256 does not match the release checksum')
        os.replace(part, final)
        os.chmod(final, 0o600)
        return final

    def _batch(self, extraction, pattern, **values):
        name = pattern.format(**values)
        if not _safe_name(name):
            raise UpdateError('invalid repository batch name')
        path = extraction / 'repositories' / name
        manifest = path / 'manifest.json'
        installer = path / 'install.py'
        if not path.is_dir() or not manifest.is_file() or not installer.is_file():
            raise UpdateError(f'release archive is missing repositories/{name}/install.py')
        try:
            metadata = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise UpdateError(f'invalid release batch manifest: {error}') from error
        if metadata.get('product') != self.project['product'] or metadata.get('id') != name:
            raise UpdateError('release batch belongs to another product')
        return path

    def _download_release(self, version, cache, expected_batch):
        version, release = self._release(version)
        assets = self._assets(release)
        package_name = self.project['package_asset'].format(version=version)
        checksum_name = self.project['checksum_asset']
        if package_name not in assets or checksum_name not in assets:
            raise UpdateError(f'release is missing {package_name} or {checksum_name}')
        checksum_file = self._download(assets[checksum_name], cache / 'assets')
        checksums = _checksum_table(checksum_file)
        if package_name not in checksums:
            raise UpdateError(f'{package_name} is missing from {checksum_name}')
        package = self._download(assets[package_name], cache / 'assets', checksums[package_name])
        extracted = cache / 'extracted'
        self._event(phase='extracting', archive=package.name)
        _extract_safe(package, extracted, self.project['archive_format'])
        batch = self._batch(extracted, expected_batch, version=version)
        return version, release, assets, package, extracted, batch

    def _with_lock(self):
        path = self.state_root / '.lock'
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        st = os.fstat(fd)
        if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o600:
            os.close(fd)
            raise UpdateError('transaction lock is not root-owned')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(fd)
            raise UpdateError('another update transaction is running') from error
        return fd

    def _state_path(self):
        return self.state_root / 'state.json'

    def state(self):
        try:
            path = self._state_path()
            if path.is_symlink():
                raise UpdateError('refusing a symlinked updater state')
            st = path.stat()
            if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o600:
                raise UpdateError('updater state file has unsafe ownership or permissions')
            value = json.loads(path.read_text(encoding='utf-8'))
            transaction = Path('/var/lib') / self.project['product'] / 'releases/transaction.json'
            if value.get('phase') in ('installing', 'recovering', 'rolling_back', 'failed_rollback') and transaction.is_file():
                value['installer_phase'] = json.loads(transaction.read_text()).get('phase')
            return value
        except FileNotFoundError:
            return {'phase': 'idle', 'current': self._installed_version()}
        except (OSError, ValueError) as error:
            raise UpdateError(f'cannot read updater state: {error}') from error

    def _save(self, state):
        _atomic_json(self._state_path(), state)
        self._event(**state)

    def _release_notes(self, release):
        heading = self.project.get('notes_heading', 'Changes')
        body = release.get('body') or ''
        notes = ''
        match = re.search(rf'^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)', body,
                          flags=re.M | re.S | re.I)
        if match:
            notes = match[1].strip()
        return notes

    def overview(self, requested_version=None):
        current = self._installed_version()
        version, release = self._release(requested_version)
        if subprocess.run(['dpkg', '--compare-versions', version, 'gt', current],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            state = self.state()
            return {'phase': 'up_to_date', 'current': current, 'version': current,
                    'rollback_available': (state.get('phase') == 'reboot_pending' and
                        state.get('version') == current and self._has_transaction(state))}
        assets = self._assets(release)
        names = [self.project['package_asset'].format(version=version)]
        rollback_pattern = self.project.get('rollback_asset')
        if rollback_pattern:
            names.append(rollback_pattern.format(version=version))
        if any(name not in assets for name in names):
            raise UpdateError('release is missing required update attachments')
        notes = self._release_notes(release)
        return {
            'current': current,
            'version': version,
            'tag': release['tag_name'],
            'display_name': self.project['display_name'],
            'notes': notes,
            'assets': [{'name': name, 'size': assets[name]['size']} for name in names],
            'total_bytes': sum(assets[name]['size'] for name in names),
            'release_url': release.get('html_url') or '',
            'phase': 'idle',
        }

    def prepare(self, version):
        fd = self._with_lock()
        try:
            with self._inhibit_sleep():
                previous_state = self.state()
                completed = (previous_state.get('phase') == 'reboot_pending' and
                             previous_state.get('completion_notified'))
                if not completed and previous_state.get('phase') in ('installing', 'recovering', 'rolling_back', 'failed_rollback', 'prepared', 'reboot_pending'):
                    raise UpdateError('finish, continue or roll back the existing update first')
                current = self._installed_version()
                target, release = self._release(version)
                if subprocess.run(['dpkg', '--compare-versions', target, 'gt', current],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                    raise UpdateError('the selected release is not newer than the installed version')
                cache = self.cache_root / target
                _secure_directory(cache)
                self._save({'phase': 'downloading', 'current': current, 'version': target})
                _, release, assets, package_file, new_extract, new_batch = self._download_release(
                    target, cache / 'new', self.project['new_batch'])
                package_name = self.project['package_asset'].format(version=target)
                checksum_file = cache / 'new' / 'assets' / self.project['checksum_asset']
                checksums = _checksum_table(checksum_file)
                rollback_path = None
                rollback_pattern = self.project.get('rollback_asset')
                previous_name = self.project['previous_batch']
                rollback_dir_name = previous_name.format(current=current, version=target)
                if rollback_pattern:
                    rollback_name = rollback_pattern.format(version=target)
                    if rollback_name not in assets or rollback_name not in checksums:
                        raise UpdateError(f'{rollback_name} is missing from the release or its checksum list')
                    rollback_file = self._download(assets[rollback_name], cache / 'new' / 'assets',
                                                   checksums[rollback_name])
                    rollback_extract = cache / 'rollback-extracted'
                    self._event(phase='extracting', archive=rollback_file.name)
                    _extract_safe(rollback_file, rollback_extract, self.project['archive_format'])
                    candidate = rollback_extract / 'repositories' / rollback_dir_name
                    if candidate.is_dir():
                        rollback_path = self._batch(rollback_extract, previous_name,
                                                    current=current, version=target)
                    else:
                        self._save({'phase': 'downloading', 'current': current, 'version': target,
                                    'message': 'fetching the installed release for jump-version previous batch'})
                        _, _, _, _, old_extract, _ = self._download_release(
                            current, cache / 'installed-release', self.project['new_batch'])
                        rollback_path = self._batch(old_extract, self.project['new_batch'], version=current)
                else:
                    candidate = new_extract / 'repositories' / rollback_dir_name
                    if candidate.is_dir():
                        rollback_path = self._batch(new_extract, previous_name,
                                                    current=current, version=target)
                    else:
                        self._save({'phase': 'downloading', 'current': current, 'version': target,
                                    'message': 'fetching the installed release for jump-version previous batch'})
                        _, _, _, _, old_extract, _ = self._download_release(
                            current, cache / 'installed-release', self.project['new_batch'])
                        rollback_path = self._batch(old_extract, self.project['new_batch'], version=current)
                if not os.access(new_batch / 'install.py', os.X_OK):
                    raise UpdateError('release installer is not executable')
                self._save({'phase': 'extracting', 'current': current, 'version': target})
                cleanup = self._run([sys.executable, str(new_batch / 'install.py'), 'preview-upgrade',
                                     str(new_batch), '--previous', str(rollback_path)])
                prepared = {
                    'phase': 'prepared', 'current': current, 'version': target,
                    'new_batch': str(new_batch), 'previous_batch': str(rollback_path),
                    'release_url': release.get('html_url', ''), 'cleanup_preview': cleanup,
                    'notes': self._release_notes(release),
                    'package_bytes': (cache / 'new' / 'assets' / package_name).stat().st_size,
                }
                self._save(prepared)
                return prepared
        except Exception as error:
            state = self.state()
            if state.get('phase') in ('downloading', 'verifying', 'extracting'):
                state.update(phase='failed_download', error=str(error))
                self._save(state)
            raise
        finally:
            os.close(fd)

    def _run(self, argv, env=None):
        process = subprocess.Popen(list(map(str, argv)), text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=env, bufsize=1,
                                   errors='replace')
        tail = []
        for line in process.stdout:
            line = re.sub(r'/(?:home|root)/[^\s\"\']+', '[private]', line)
            print(line.rstrip(), flush=True)
            tail.append(line)
            if len(tail) > 20000:
                tail.pop(0)
            if self.progress and line.strip():
                phase = self.state().get('phase', 'installing')
                self._event(phase=phase, message=line.strip()[:240])
        code = process.wait()
        output = ''.join(tail)
        if code:
            raise UpdateError(output[-4000:].strip() or f'command failed: {argv[0]}')
        return self._last_json(output) or {'text': output[-4000:].strip()}

    @staticmethod
    def _last_json(text):
        decoder = json.JSONDecoder()
        found = None
        i = 0
        while i < len(text):
            if text[i] != '{':
                i += 1
                continue
            try:
                value, length = decoder.raw_decode(text[i:])
                if isinstance(value, dict):
                    found = value
                i += length
            except ValueError:
                i += 1
        return found

    @contextlib.contextmanager
    def _inhibit_sleep(self):
        if os.environ.get('MAINLINE_UPDATE_TEST_NO_INHIBIT') == '1':
            yield
            return
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        try:
            result, descriptors = bus.call_with_unix_fd_list_sync(
            'org.freedesktop.login1', '/org/freedesktop/login1',
            'org.freedesktop.login1.Manager', 'Inhibit',
            GLib.Variant('(ssss)', ('sleep', 'Mainline Update', 'System update', 'block')),
            GLib.VariantType.new('(h)'), Gio.DBusCallFlags.NONE, -1, None, None)
        except GLib.Error as error:
            raise UpdateError('could not inhibit suspend: ' + str(error)) from error
        fd = descriptors.get(result.unpack()[0])
        try:
            yield
        finally:
            os.close(fd)

    def _installer(self, prepared):
        new_batch = Path(prepared['new_batch'])
        previous_batch = Path(prepared['previous_batch'])
        for path in (new_batch, previous_batch):
            if not path.is_dir() or not path.resolve().is_relative_to(self.cache_root.resolve()):
                raise UpdateError('prepared installer path is outside the private update cache')
        installer = new_batch / 'install.py'
        if not installer.is_file() or not os.access(installer, os.X_OK):
            raise UpdateError('prepared release installer is missing or not executable')
        return installer, new_batch, previous_batch

    def _upgrade_once(self, prepared):
        installer, new_batch, previous_batch = self._installer(prepared)
        argv = [sys.executable, str(installer), 'upgrade', str(new_batch), '--previous', str(previous_batch)]
        self._save({**prepared, 'phase': 'installing', 'last_command': argv})
        return self._run(argv, env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))

    def _has_transaction(self, prepared):
        try:
            transaction = Path('/var/lib') / self.project['product'] / 'releases/transaction.json'
            return json.loads(transaction.read_text()).get('to') == Path(prepared['new_batch']).name
        except (OSError, ValueError, KeyError):
            return False

    def install(self, resume=False):
        fd = self._with_lock()
        try:
            prepared = self.state()
            if resume:
                if prepared.get('phase') not in ('installing', 'recovering', 'interrupted', 'failed_rollback'):
                    raise UpdateError('there is no interrupted installation to continue')
                prepared['phase'] = 'prepared'
                self._save(prepared)
            if prepared.get('phase') != 'prepared':
                raise UpdateError('update files are not prepared')
            current = self._installed_version()
            if current not in (prepared.get('current'), prepared.get('version')):
                raise UpdateError('installed version changed after update preparation')
            try:
                with self._inhibit_sleep():
                    try:
                        result = self._upgrade_once(prepared)
                    except UpdateError as first:
                        self._save({**prepared, 'phase': 'recovering', 'error': str(first),
                                    'recovery': 'dpkg --configure -a; apt-get -y -f install; retry the same upgrade'})
                        self._run(['/usr/bin/dpkg', '--configure', '-a'],
                                  env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
                        self._run(['/usr/bin/apt-get', '-y', '-f', 'install'],
                                  env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
                        result = self._upgrade_once(prepared)
            except UpdateError as error:
                state = {**prepared, 'phase': ('failed_rollback' if self._has_transaction(prepared) else 'prepared'), 'error': str(error),
                         'recovery': 'UPGRADE recovery attempted; see journal for the failing step'}
                self._save(state)
                return state
            freed_bytes = result.get('freed_bytes', 0)
            state = {**prepared, 'phase': 'reboot_pending', 'freed_bytes': freed_bytes, 'completion_notified': False,
                     'installer_result': result, 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
            self._save(state)
            return state
        finally:
            os.close(fd)

    def resume(self):
        if self.state().get('phase') == 'rolling_back':
            return self.rollback()
        return self.install(resume=True)

    def rollback(self):
        fd = self._with_lock()
        try:
            state = self.state()
            if state.get('phase') not in ('failed_rollback', 'reboot_pending', 'installing', 'recovering', 'rolling_back'):
                raise UpdateError('there is no update available to roll back')
            prepared = state
            if not self._has_transaction(prepared):
                raise UpdateError('the installer has no matching transaction to roll back')
            installer, new_batch, _ = self._installer(prepared)
            self._save({**state, 'phase': 'rolling_back'})
            try:
                with self._inhibit_sleep():
                    result = self._run([sys.executable, str(installer), 'rollback',
                                        '--product', self.project['product']],
                                       env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
            except UpdateError as error:
                state = {**state, 'phase': 'failed_rollback', 'rollback_error': str(error)}
                self._save(state)
                return state
            state = {**state, 'phase': 'rolled_back', 'installer_result': result,
                     'rollback_version': state.get('current'), 'completion_notified': False,
                     'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
            self._save(state)
            return state
        finally:
            os.close(fd)

    def notify_completion(self):
        result = self.completion_notice()
        if result:
            self.mark_completion_notified()
        return result

    def completion_notice(self):
        state = self.state()
        if state.get('completion_notified') or state.get('phase') not in ('reboot_pending', 'rolled_back'):
            return None
        if state.get('boot_id') == Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            return None
        installed = self._installed_version()
        expected = state.get('version') if state['phase'] == 'reboot_pending' else state.get('current')
        if installed != expected:
            return None
        return {'version': installed, 'freed_bytes': state.get('freed_bytes', 0),
                'rolled_back': state['phase'] == 'rolled_back'}

    def mark_completion_notified(self):
        fd = self._with_lock()
        try:
            if not self.completion_notice():
                raise UpdateError('there is no completed update to acknowledge')
            state = self.state()
            state['completion_notified'] = True
            self._save(state)
        finally:
            os.close(fd)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit('usage: mainline-update-backend overview|prepare|install|resume|rollback|state [version]')
    updater = Updater()
    action = args.pop(0)
    if action == 'overview':
        result = updater.overview(args[0] if args else None)
    elif action == 'prepare' and args:
        result = updater.prepare(args[0])
    elif action == 'install':
        result = updater.install()
    elif action == 'resume':
        result = updater.resume()
    elif action == 'rollback':
        result = updater.rollback()
    elif action == 'state':
        result = updater.state()
    else:
        raise SystemExit('invalid command')
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except UpdateError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
