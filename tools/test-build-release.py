#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Release identity checks and the concise bilingual, data-driven note template."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from release_notes import render_notes, validate_notes

TOOL = Path(__file__).with_name('build-release.py')


def changes_document(update):
    return ('本版概括。\n\n## 更新\n\n' + update.rstrip() + '\n\n## 已知限制\n\n保留默认音量。\n\n---\n\nRelease summary.\n\n## Changes\n\n' + update.rstrip() + '\n\n## Known limitations\n\nKeep the default volume.\n')


def load():
    loader = importlib.machinery.SourceFileLoader('build_release', str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def fdt(properties):
    """A one-node devicetree with the given property names (4-byte values)."""
    strings = b''
    body = struct.pack('>I', 1) + b'\0\0\0\0'  # FDT_BEGIN_NODE "" (root)
    for name in properties:
        body += struct.pack('>III', 3, 4, len(strings)) + b'\0\0\0\1'
        strings += name.encode() + b'\0'
    body += struct.pack('>II', 2, 9)
    off_struct = 40 + 16
    header = struct.pack('>10I', 0xd00dfeed, off_struct + len(body) + len(strings), off_struct,
                         off_struct + len(body), 40, 17, 16, 0, len(strings), len(body))
    return header + b'\0' * 16 + body + strings


class Release(unittest.TestCase):
    def setUp(self):
        self.tool = load()

    def test_fdt_property_names(self):
        names = self.tool.fdt_property_names(fdt(['compatible', 'local-mac-address']))
        self.assertEqual(names, {'compatible', 'local-mac-address'})
        with self.assertRaises(ValueError):
            self.tool.fdt_property_names(b'\0' * 64)

    def test_identity_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'bt').write_text('02:11:22:33:44:5A\n')
            patterns = self.tool.identity_patterns(d / 'bt')
            for p in (b'02:11:22:33:44:5a', b'02:11:22:33:44:5A', b'5a:44:33:22:11:02', b'02112233445a',
                      bytes.fromhex('02112233445a'), bytes.fromhex('5a4433221102')):
                self.assertIn(p, patterns)
            (d / 'wlan').write_text('wlan0=0211223344ab\n')
            self.assertIn(bytes.fromhex('0211223344ab'), self.tool.identity_patterns(d / 'wlan'))
            (d / 'profile.json').write_text(json.dumps({'serial': 'abc1234', 'boot': {}}))
            self.assertEqual(self.tool.identity_patterns(d / 'profile.json'), {b'abc1234', b'ABC1234'})
            (d / 'other').write_text('not an identifier')
            with self.assertRaises(ValueError):
                self.tool.identity_patterns(d / 'other')

    def test_scan_finds_a_pattern_across_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'blob'
            f.write_bytes(b'x' * 1000 + b'abc1234' + b'y' * 1000)
            for chunk in (1003, 1004, 1006, 4096):
                self.assertTrue(self.tool.scan(f, {b'abc1234'}, chunk=chunk), chunk)
            self.assertFalse(self.tool.scan(f, {b'abc1235'}, chunk=1003))

    def test_short_bilingual_notes_and_verbatim_changes(self):
        changes = '- **显示**：修复显示。\n\n  **Keep $literal and `code` unchanged.**\n'
        rootfs = {'release_name': 'v9.8.7', 'kernel_release': '7.2.6-piano10',
                  'apt_snapshot': '20261001T000000Z',
                  'packages': [{'package': 'linux-image-7.2.6-piano10', 'version': '7.2.6-10.1'},
                               {'package': 'a-package-not-for-the-notes', 'version': '1'}]}
        notes = render_notes(rootfs, 'OS3.0.303.0.WPYCNXM', '1234567' + '0' * 33,
                             {'url': 'https://example.org/kernel', 'commit': 'abcdef0' + '1' * 33}, changes_document(changes))
        self.assertEqual(notes.count(changes), 2)
        self.assertLess(notes.index('## 更新'), notes.index('## 获取、安装与升级'))
        self.assertLess(notes.index('## 已知限制'), notes.index('## Changes'))
        self.assertIn('内核：Linux 7.2.6；', notes)
        self.assertIn('Kernel: Linux 7.2.6;', notes)
        self.assertNotIn('piano10', notes)
        self.assertNotIn('1234567' + '0' * 33, notes)
        self.assertIn('https://example.org/kernel/tree/v9.8.7', notes)
        self.assertIn('busybox/1.36.1-6ubuntu3.1', notes)
        self.assertIn('SHA256SUMS.assets', notes)
        self.assertNotRegex(notes, r'[0-9a-f]{64}')
        for absent in ('preview-5', 'repack', 'no device was accessed', 'QEMU', 'AVB',
                       'Packages installed in the image', 'a-package-not-for-the-notes'):
            self.assertNotIn(absent, notes)
        for doc in ('INSTALL', 'UPGRADE'):
            for suffix in ('.md', '.zh-CN.md'):
                self.assertIn(f'/docs/{doc}{suffix}', notes)
        validate_notes(notes, 'v9.8.7')
        self.assertNotIn('|', notes)
        self.assertNotIn('```', notes)

    def test_build_cli_embeds_changes_and_keeps_package_inventory(self):
        """Assemble tiny synthetic inputs through the real --changes CLI."""
        with tempfile.TemporaryDirectory() as temporary:
            d = Path(temporary)
            build, maintenance, output = d / 'build', d / 'maintenance', d / 'release'
            s = build / 'ubuntu-set'
            s.mkdir(parents=True)
            maintenance.mkdir()
            dtb = fdt(['compatible'])
            command = 'root=PARTLABEL=linux_rootfs rw'
            for name in self.tool.SET_FILES:
                (s / name).write_bytes(b'synthetic ' + name.encode())
            (s / 'boot.img').write_bytes(command.encode())
            (s / 'piano-boot.dtb').write_bytes(dtb)
            (s / 'vendor_boot.img').write_bytes(dtb)
            stock = {'boot': {'avb_properties': {'com.android.build.boot.fingerprint':
                     'Xiaomi/piano/piano:16/ID/OS3.0.303.0.WPYCNXM:user/release-keys'}},
                     'init_boot': {}, 'vendor_boot': {}, 'scope': 'stock'}
            (s / 'profile.json').write_text(json.dumps(stock))
            (build / 'boot-profile.json').write_text(json.dumps(stock))
            manifest = {'cmdline': command, 'debug': False, 'inputs': {'kernel': 'synthetic'},
                        'private_bt_address_injected': False, 'private_wlan_address_injected': False,
                        'effective_dtb_sha256': self.tool.sha(s / 'piano-boot.dtb'),
                        'outputs': {n: {'sha256': self.tool.sha(s / f'{n}.img')}
                                    for n in ('boot', 'init_boot', 'vendor_boot')}}
            (s / 'manifest.json').write_text(json.dumps(manifest))
            (build / 'root.sparse.img').write_bytes(b'synthetic root')
            package = d / 'kernel.deb'
            package.write_bytes(b'synthetic package')
            packages = [{'package': 'linux-image-7.2.6-piano9', 'version': '7.2.6-9.1',
                         'source': 'piano', 'file': package.name, 'sha256': self.tool.sha(package)}]
            patched = d / 'gnome-settings-daemon.deb'
            patched.write_bytes(b'synthetic rebuilt distribution package')
            packages.append({'package': 'gnome-settings-daemon', 'version': '50.0-1ubuntu1+piano1',
                             'source': 'piano', 'file': patched.name, 'sha256': self.tool.sha(patched)})
            rootfs = {'state': 'ROOT_IMAGE_BUILT_OFFLINE_CHECKS_PASS_NOT_BOOTED',
                      'release_name': 'v1.2.3', 'kernel_release': '7.2.6-piano9',
                      'apt_snapshot': '20261001T000000Z', 'packages': packages,
                      'outputs': {'root.sparse.img': {'sha256': self.tool.sha(build / 'root.sparse.img')}}}
            (build / 'manifest.json').write_text(json.dumps(rootfs))
            (build / 'verification.json').write_text('{"passed": true}')
            (build / 'inputs-local.json').write_text(json.dumps({package.name: str(package), patched.name: str(patched)}))
            (maintenance / 'maintenance.img').write_bytes(b'synthetic maintenance')
            maint = {'kind': 'piano-maintenance-ram', 'kernel_release': rootfs['kernel_release'],
                     'inputs': {'kernel': 'synthetic', 'dtb': manifest['effective_dtb_sha256']},
                     'serial_in_cmdline': False, 'cmdline': 'rdinit=/init',
                     'image': {'file': 'maintenance.img', 'sha256': self.tool.sha(maintenance / 'maintenance.img')}}
            (maintenance / 'manifest.json').write_text(json.dumps(maint))
            changes = '- **Changes**: 本版手写变化 / Handwritten changes: **$literal**\n'
            (d / 'changes.md').write_text(changes_document(changes))
            subprocess.run([sys.executable, str(TOOL), '--rootfs-build', str(build),
                            '--maintenance', str(maintenance), '--output', str(output),
                            '--changes', str(d / 'changes.md')], check=True, capture_output=True)
            self.assertEqual((output / 'RELEASE-NOTES.md').read_text().count(changes), 2)
            self.assertEqual(json.loads((output / 'release-manifest.json').read_text())['packages'], packages)
            subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=output, check=True, capture_output=True)
            self.assertEqual((output / 'debs' / patched.name).read_bytes(), patched.read_bytes())
            patched.write_bytes(b'changed after image construction')
            failed = subprocess.run([sys.executable, str(TOOL), '--rootfs-build', str(build),
                                     '--maintenance', str(maintenance), '--output', str(d / 'tampered'),
                                     '--changes', str(d / 'changes.md')],
                                    capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('changed after the image build', failed.stderr)


if __name__ == '__main__':
    unittest.main()
