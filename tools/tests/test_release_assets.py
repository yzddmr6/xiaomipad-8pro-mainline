# SPDX-License-Identifier: MIT
"""Small, host-only release round trips with real zstd, unzip and sha256sum.

Run: python3 -m pytest tools/tests/test_release_assets.py
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('package_release_assets', TOOLS / 'package-release-assets.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
from release_notes import render_notes


def notes_for(version='v1.2.3', update='- **Test**: Keep $literal and `code`.'):
    changes = ('GPU 更新。\n\n## 更新\n\n' + update + '\n\n## 已知限制\n\n保留默认音量。\n\n---\n\nGPU update.\n\n## Changes\n\n' + update + '\n\n## Known limitations\n\nKeep the default volume.\n')
    return render_notes(
        {'release_name': version, 'kernel_release': '7.2.6-piano10',
         'packages': [{'package': 'linux-image-7.2.6-piano10', 'version': '7.2.6-10.1'}]},
        'OS3.0.303.0.WPYCNXM', 'a' * 40, {'url': 'https://example.org/kernel', 'commit': 'b' * 40}, changes)


def write_sums(directory):
    paths = sorted(p for p in directory.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
    # Include the independent boot/SHA256SUMS too.
    paths += [directory / 'boot/SHA256SUMS']
    (directory / 'SHA256SUMS').write_text(''.join(
        f'{tool.sha(p)}  {p.relative_to(directory).as_posix()}\n' for p in paths))


@pytest.fixture
def release(tmp_path):
    for program in ('zstd', 'unzip', 'sha256sum'):
        if not shutil.which(program):
            pytest.fail(f'Required host program missing: {program}')
    directory = tmp_path / 'release'
    files = {
        'boot/boot.img': b'boot data' * 100, 'boot/SHA256SUMS': b'boot-local checksums\n',
        'boot/subdir/file with spaces.txt': b'nested metadata',
        'optional/virtualization/piano-vm.deb': b'optional VM package',
        'optional/virtualization/piano-virtualization-source.tar.zst': b'corresponding source',
        'debs/package.deb': b'deb data', 'tools/switch.py': b'#!/usr/bin/python3\n',
        'rootfs/root.sparse.img': os.urandom(24_000), 'rootfs/manifest.json': b'{}\n',
        'rootfs/verification.json': b'{"passed": true}\n',
        'release-manifest.json': b'{"release_name": "v1.2.3"}\n',
        'RELEASE-NOTES.md': notes_for().encode(),
    }
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (directory / 'tools/switch.py').chmod(0o755)
    write_sums(directory)
    return directory


def assert_same_files(left, right):
    left_files, right_files = tool.file_tree(left), tool.file_tree(right)
    assert left_files.keys() == right_files.keys()
    for name in left_files:
        assert left_files[name].read_bytes() == right_files[name].read_bytes(), name
    assert (right / 'tools/switch.py').stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize('part_bytes', [tool.PART_BYTES, 10_000, 2_000])
def test_roundtrip_and_documented_shell_commands(release, tmp_path, part_bytes):
    assets, restored = tmp_path / 'assets', tmp_path / 'restored'
    original = {n: tool.sha(p) for n, p in tool.file_tree(release).items()}
    result = tool.package(release, assets, part_bytes=part_bytes)
    checked = tool.verify(assets, restored)
    assert checked['files_verified'] == len(original) - 1
    assert_same_files(release, restored)
    assert original == {n: tool.sha(p) for n, p in tool.file_tree(release).items()}
    manifest = json.loads((assets / 'assets-manifest.json').read_text())
    assert manifest['compression'] == ['zstd', '-T0', '-10', '--long=27']
    for entry in manifest['assets']:
        assert entry['bytes'] == result['sizes'][entry['name']]
        assert entry['sha256'] == tool.sha(assets / entry['name'])
        assert entry['contents'] and entry['restore_command']
    package_zip = next(x for x in manifest['assets'] if x['name'].endswith('-packages.zip'))
    assert 'optional/virtualization/piano-vm.deb' in package_zip['contents']
    assert 'optional/virtualization/piano-virtualization-source.tar.zst' in package_zip['contents']
    parts = manifest['rootfs_parts']
    assert all((assets / n).stat().st_size == part_bytes for n in parts[:-1])
    # Exercise exactly the user-facing shell recipe as well as the Python CLI.
    shell = tmp_path / 'shell'
    shell.mkdir()
    for name in result['sizes']:
        os.link(assets / name, shell / name)
    subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', '\n'.join(manifest['restore_commands'])],
                   cwd=shell, check=True, capture_output=True)
    for name, digest in original.items():
        assert tool.sha(shell / name) == digest
    if part_bytes == 2_000:
        assert len(parts) > 10


def test_cli(release, tmp_path):
    assets = tmp_path / 'assets'
    subprocess.run([sys.executable, str(TOOLS / 'package-release-assets.py'), '--release', str(release),
                    '--output', str(assets)], check=True, capture_output=True)
    subprocess.run([sys.executable, str(TOOLS / 'package-release-assets.py'), 'verify',
                    '--assets', str(assets), '--output', str(tmp_path / 'restored')],
                   check=True, capture_output=True)
    assert_same_files(release, tmp_path / 'restored')


def test_packaged_notes_preserve_body_without_size_tables(release, tmp_path):
    update = '- **Test**: Handwritten **$text** and `code`.'
    notes = notes_for(update=update)
    (release / 'RELEASE-NOTES.md').write_text(notes)
    write_sums(release)
    result = tool.package(release, tmp_path / 'assets')
    body = (tmp_path / 'assets/RELEASE-NOTES.md').read_text()
    assert body == notes and body.count(update) == 2
    assert body.index('## 更新') < body.index('## 获取、安装与升级')
    assert body.index('## Changes') < body.index('## Get, install and upgrade')
    for size in result['sizes'].values():
        assert f'{size:,} B' not in body
    tool.verify(tmp_path / 'assets', tmp_path / 'restored')
    assert_same_files(release, tmp_path / 'restored')


@pytest.mark.parametrize('mutation', ['hash', 'table', 'path', 'marker', 'missing-section', 'changed-body'])
def test_release_body_is_checked_before_restore(release, tmp_path, mutation):
    assets = tmp_path / 'assets'
    tool.package(release, assets)
    path = assets / 'RELEASE-NOTES.md'
    notes = path.read_text()
    if mutation == 'missing-section':
        notes = notes.replace('## 更新', '## 下载')
    elif mutation == 'changed-body':
        notes = notes.replace('**Test**', '**Changed**')
    else:
        notes += {'hash': 'a' * 64, 'table': '| File | 123 B |',
                  'path': '/home/builder/private/result', 'marker': '<!-- restore:start -->'}[mutation]
    path.write_text(notes)
    restored = tmp_path / 'restored'
    with pytest.raises(ValueError):
        tool.verify(assets, restored)
    assert not restored.exists()


@pytest.mark.parametrize('mutation', ['corrupt', 'missing', 'extra', 'manifest'])
def test_damaged_assets_fail_before_restore(release, tmp_path, mutation):
    assets, restored = tmp_path / 'assets', tmp_path / 'restored'
    tool.package(release, assets, part_bytes=10_000)
    part = assets / 'piano-ubuntu-v1.2.3-rootfs.img.zst.part0'
    if mutation == 'missing':
        part.unlink()
    elif mutation == 'extra':
        (assets / 'piano-ubuntu-v1.2.3-rootfs.img.zst.part99').write_bytes(b'extra')
    elif mutation == 'manifest':
        (assets / 'assets-manifest.json').write_text('{}')
    else:
        part.write_bytes(b'corrupt')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        tool.verify(assets, restored)
    assert not restored.exists()


def refresh_integrity(assets, manifest):
    for entry in manifest['assets']:
        path = assets / entry['name']
        entry.update(bytes=path.stat().st_size, sha256=tool.sha(path))
    (assets / 'assets-manifest.json').write_text(json.dumps(manifest))
    names = [a['name'] for a in manifest['assets']] + ['assets-manifest.json']
    (assets / 'SHA256SUMS.assets').write_text(''.join(f'{tool.sha(assets / n)}  {n}\n' for n in names))


@pytest.mark.parametrize('mutation', ['order', 'truncated', 'zip-traversal', 'inner-checksum'])
def test_consistent_outer_hashes_do_not_bypass_restore_checks(release, tmp_path, mutation):
    assets, restored = tmp_path / 'assets', tmp_path / 'restored'
    tool.package(release, assets, part_bytes=10_000)
    manifest = json.loads((assets / 'assets-manifest.json').read_text())
    if mutation == 'order':
        manifest['rootfs_parts'].reverse()
    elif mutation == 'truncated':
        path = assets / manifest['rootfs_parts'][-1]
        path.write_bytes(path.read_bytes()[:-3])
    else:
        entry = manifest['assets'][0]
        archive = assets / entry['name']
        with zipfile.ZipFile(archive) as z:
            data = {n: z.read(n) for n in z.namelist()}
        if mutation == 'zip-traversal':
            data['../escape'] = b'invalid'
            entry['contents'] = sorted(data)
        else:
            data['boot/boot.img'] = b'wrong bytes'
        with zipfile.ZipFile(archive, 'w') as z:
            for name, content in data.items():
                z.writestr(name, content)
    refresh_integrity(assets, manifest)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        tool.verify(assets, restored)
    assert not restored.exists()
    assert not (tmp_path / 'escape').exists()
    assert not list(tmp_path.glob('.restored-*'))


@pytest.mark.parametrize('mutation', ['symlink', 'unlisted', 'corrupt', 'version', 'unsafe-checksum'])
def test_invalid_release_is_rejected(release, tmp_path, mutation):
    if mutation == 'symlink':
        (release / 'tools/link').symlink_to('/etc/passwd')
    elif mutation == 'unlisted':
        (release / 'tools/unlisted').write_text('extra')
    elif mutation == 'version':
        (release / 'release-manifest.json').write_text('{"release_name":"../bad"}')
    elif mutation == 'unsafe-checksum':
        with (release / 'SHA256SUMS').open('a') as f:
            f.write('0' * 64 + '  ../outside\n')
    else:
        (release / 'boot/boot.img').write_bytes(b'corrupt')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        tool.package(release, tmp_path / 'assets')
    assert not (tmp_path / 'assets').exists()


def test_output_must_be_new_and_disjoint(release, tmp_path):
    for output in (release, release / 'assets', tmp_path):
        with pytest.raises(ValueError):
            tool.package(release, output)


@pytest.mark.parametrize('size, expected', [(20, [10, 10]), (21, [10, 10, 1]), (0, [])])
def test_exact_split_boundary_without_empty_tail(tmp_path, size, expected):
    data = bytes(range(size))
    rows = tool.split_stream(io.BytesIO(data), tmp_path, 'test', 10)
    assert [r['bytes'] for r in rows] == expected
    assert b''.join((tmp_path / r['name']).read_bytes() for r in rows) == data


def test_compressor_failure_does_not_publish_partial_output(release, tmp_path, monkeypatch):
    def fail(*args):
        raise subprocess.CalledProcessError(1, ['zstd'])
    monkeypatch.setattr(tool, 'compress_root', fail)
    with pytest.raises(subprocess.CalledProcessError):
        tool.package(release, tmp_path / 'assets')
    assert not (tmp_path / 'assets').exists()
    assert not list(tmp_path.glob('.assets-*'))
