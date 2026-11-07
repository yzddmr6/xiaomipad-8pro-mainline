#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""The empty init_boot ramdisk is fixed bytes, independent of Python's gzip module; the root and
serial options of unit-independent sets.  The image test needs AOSP mkbootimg in tools/local
(fetch-aosp-mkbootimg.sh) and AVBTOOL=<avbtool.py>."""
import gzip
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).with_name('build-persistent-boot.py')
MKBOOTIMG = Path(__file__).with_name('local') / 'aosp-mkbootimg/mkbootimg.py'
AVBTOOL = os.environ.get('AVBTOOL')
# empty.cpio.gz of the 2026-10-08 release set (init_boot.img decc6d5a...), built
# with Python 3.12; Python 3.14's gzip.compress() would write OS byte 0xff.
RELEASE_EMPTY_SHA256 = 'af39e1c19af478aef7a03be98efa9d9baa228fb9f93e3842d6d7b14a1465a459'


def load():
    loader = importlib.machinery.SourceFileLoader('build_persistent_boot', str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class EmptyRamdisk(unittest.TestCase):
    def test_known_bytes_without_gzip_module(self):
        tool = load()
        with mock.patch.object(gzip, 'compress', side_effect=AssertionError('gzip.compress used')):
            data = tool.empty_ramdisk()
        self.assertEqual(hashlib.sha256(data).hexdigest(), RELEASE_EMPTY_SHA256)
        self.assertEqual(data[:10], b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\x03')

    def test_decompresses_to_the_newc_trailer(self):
        raw = gzip.decompress(load().empty_ramdisk())
        self.assertTrue(raw.startswith(b'070701'))
        self.assertIn(b'TRAILER!!!\0', raw)
        self.assertEqual(len(raw) % 4, 0)


def fake_inputs(d, serial=None, native=False):
    (d / 'Image').write_bytes(b'\0' * 4096 + b'Linux version 7.2.6-piano9 (test@host) (gcc)\0' + b'\0' * 4096)
    subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(d / 'dtb')],
                   input=('/dts-v1/; / { soc@0 { display-subsystem@ae00000 { status = "' +
                          ('okay' if native else 'disabled') + '"; }; }; };').encode(), check=True)
    (d / 'initrd').write_bytes(b'070701' + b'\0' * 1024)
    image = {'header_version': 4, 'capacity': 4 << 20, 'rollback_index': 0, 'avb_properties': {}}
    profile = {'boot': dict(image, avb_properties={'com.android.build.boot.os_version': '15',
                                                   'com.android.build.boot.security_patch': '2026-02-01'}),
               'init_boot': dict(image), 'vendor_boot': dict(image, page_size=4096, kernel_addr=0x8000,
                                                             ramdisk_addr=0x1000000, tags_addr=0x100,
                                                             dtb_addr=0x1f00000)}
    if serial:
        profile['serial'] = serial
    (d / 'profile.json').write_text(json.dumps(profile))
    subprocess.run(['openssl', 'genrsa', '-out', str(d / 'key.pem'), '4096'], check=True, capture_output=True)


def build(d, *options, output='out'):
    return subprocess.run([sys.executable, str(TOOL), '--kernel', str(d / 'Image'), '--dtb', str(d / 'dtb'),
                           '--initrd', str(d / 'initrd'), '--profile', str(d / 'profile.json'),
                           '--mkbootimg', str(MKBOOTIMG), '--avbtool', str(AVBTOOL), '--key', str(d / 'key.pem'),
                           '--output', str(d / output), *options], capture_output=True, text=True)


class RootAndSerial(unittest.TestCase):
    @unittest.skipUnless(MKBOOTIMG.is_file() and AVBTOOL, 'needs tools/local/aosp-mkbootimg and AVBTOOL')
    def test_persistent_native_display_uses_the_same_drm_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            fake_inputs(d, native=True)
            done = build(d, '--root-partlabel', 'linux_rootfs', '--no-serial')
            self.assertEqual(done.returncode, 0, done.stderr)
            manifest = json.loads((d / 'out/manifest.json').read_text())
            self.assertTrue(manifest['native_kms'])
            self.assertIn('msm.separate_gpu_kms=0', manifest['cmdline'])
            self.assertFalse(manifest['debug'])
            self.assertNotIn('piano.native_display=1', manifest['cmdline'])

    def test_keyboard_requires_bounded_debug_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            fake_inputs(d)
            for extra in ((), ('--debug', '--debug-timeout', '599'),
                          ('--debug', '--debug-timeout', '600', '--module-blacklist', 'hid_nanosic_wn8030')):
                result = build(d, '--root-partlabel', 'linux_rootfs', '--no-serial',
                               '--keyboard-transport', *extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('RAM experiment requires --debug --debug-timeout 600', result.stderr)
                self.assertFalse((d / 'out').exists())

    def test_ram_experiments_are_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            fake_inputs(d)
            result = build(d, '--root-partlabel', 'linux_rootfs', '--no-serial',
                           '--native-display', '--keyboard-transport')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Select one RAM experiment', result.stderr)
            self.assertFalse((d / 'out').exists())

    def test_ram_size_limit_includes_header_and_page_padding(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            fake_inputs(d)
            # 12 KiB kernel + 4 KiB each for header, ramdisk and DTB.
            profile = json.loads((d / 'profile.json').read_text())
            for capacity in (24575, 24576):
                profile['boot']['capacity'] = capacity
                (d / 'profile.json').write_text(json.dumps(profile))
                result = build(d, '--root-partlabel', 'linux_rootfs', '--no-serial')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('RAM image 24576 bytes must stay under the boot partition size', result.stderr)
                self.assertFalse((d / 'out').exists())
            profile['boot']['capacity'] = 24577
            self.assertEqual(load().ram_image_size(d / 'Image', d / 'initrd', d / 'dtb', profile), 24576)

    def test_root_options_are_exclusive_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            fake_inputs(d)
            both = build(d, '--root-partlabel', 'linux_rootfs',
                         '--root-partuuid', '00000000-0000-0000-0000-000000000001')
            self.assertIn('not allowed with argument', both.stderr)
            bad = build(d, '--root-partlabel', 'linux rootfs', '--no-serial')
            self.assertIn('GPT partition name', bad.stderr)
            self.assertFalse((d / 'out').exists())

    @unittest.skipUnless(MKBOOTIMG.is_file() and AVBTOOL, 'needs tools/local/aosp-mkbootimg and AVBTOOL')
    def test_unit_independent_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            fake_inputs(d)
            refused = build(d, '--root-partlabel', 'linux_rootfs')
            self.assertIn('Profile requires the verified device serial', refused.stderr)
            done = build(d, '--root-partlabel', 'linux_rootfs', '--no-serial', output='set')
            self.assertEqual(done.returncode, 0, done.stderr)
            manifest = json.loads((d / 'set/manifest.json').read_text())
            self.assertTrue(manifest['cmdline'].startswith('root=PARTLABEL=linux_rootfs rootfstype=ext4 '))
            self.assertNotIn('androidboot.serialno', manifest['cmdline'])
            self.assertFalse(any(w.startswith('console=') for w in manifest['cmdline'].split()))
            self.assertFalse(manifest['native_kms'])
            self.assertIn('msm.separate_gpu_kms=1', manifest['cmdline'])
            self.assertEqual((manifest['root'], manifest['root_partuuid'], manifest['serial_in_cmdline']),
                             ('PARTLABEL=linux_rootfs', None, False))
            header = (d / 'set/boot.img').read_bytes()[:4096]
            self.assertIn(manifest['cmdline'].encode(), header)

    @unittest.skipUnless(MKBOOTIMG.is_file() and AVBTOOL, 'needs tools/local/aosp-mkbootimg and AVBTOOL')
    def test_unit_set_keeps_partuuid_and_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            fake_inputs(d, serial='abc1234')
            done = build(d, '--root-partuuid', '00000000-0000-0000-0000-000000000001')
            self.assertEqual(done.returncode, 0, done.stderr)
            manifest = json.loads((d / 'out/manifest.json').read_text())
            self.assertTrue(manifest['cmdline'].startswith(
                'root=PARTUUID=00000000-0000-0000-0000-000000000001 '))
            self.assertTrue(manifest['cmdline'].endswith(' androidboot.serialno=abc1234'))
            self.assertTrue(manifest['serial_in_cmdline'])


def newc(files):
    """gzip of a newc archive: directories for every parent, then the given regular files."""
    names, blob = [], b''
    for path in files:
        parts = path.split('/')
        names += ['/'.join(parts[:i]) for i in range(1, len(parts)) if '/'.join(parts[:i]) not in names]
    entries = [('.', 0o040755, b'')] + [(n, 0o040755, b'') for n in names] + \
              [(n, 0o100644, data) for n, data in files.items()] + [('TRAILER!!!', 0, b'')]
    for i, (name, mode, data) in enumerate(entries):
        raw = name.encode() + b'\0'
        blob += b'070701' + b''.join(f'{v:08x}'.encode() for v in
                                     (i, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(raw), 0)) + raw
        blob += b'\0' * (-len(blob) % 4) + data + b'\0' * (-len(data) % 4)
    return gzip.compress(blob)


def maintenance(d, *options, release='7.2.6-piano9', output='maint'):
    (d / 'maint.cpio.gz').write_bytes(newc({
        'etc/piano-maintenance.json': json.dumps({'kernel_release': release,
                                                  'payload_manifest_sha256': '0' * 64}).encode(),
        'etc/build-id': b'1234\n', 'bin/piano-maint': b'#!/bin/sh\n'}))
    return subprocess.run([sys.executable, str(TOOL), '--maintenance', '--kernel', str(d / 'Image'),
                           '--dtb', str(d / 'dtb'), '--initrd', str(d / 'maint.cpio.gz'),
                           '--profile', str(d / 'profile.json'), '--mkbootimg', str(MKBOOTIMG),
                           '--output', str(d / output), *options], capture_output=True, text=True)


class Maintenance(unittest.TestCase):
    def test_refuses_unit_and_debug_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            fake_inputs(d)
            for extra in (['--root-partlabel', 'linux_rootfs'], ['--debug'], ['--avbtool', 'x'],
                          ['--maintenance-idle', '29'], ['--maintenance-idle', '3601']):
                result = maintenance(d, '--no-serial', *extra)
                self.assertEqual(result.returncode, 2, extra)
            self.assertIn('not 7.2.6-piano9', maintenance(d, '--no-serial', release='7.2.6-piano8').stderr)
            self.assertFalse((d / 'maint').exists())
            plain = build(d, '--no-serial')
            self.assertIn('--avbtool, --key and --root-partuuid or --root-partlabel are required', plain.stderr)

    @unittest.skipUnless(MKBOOTIMG.is_file(), 'needs tools/local/aosp-mkbootimg')
    def test_address_free_and_unit_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            fake_inputs(d, serial='abc1234')
            done = maintenance(d, '--no-serial', '--maintenance-idle', '900')
            self.assertEqual(done.returncode, 0, done.stderr)
            manifest = json.loads((d / 'maint/manifest.json').read_text())
            self.assertEqual(manifest['cmdline'], 'rdinit=/init console=ttyMSM0,115200n8 log_buf_len=2M panic=15 '
                                                  'piano.diag=maintenance piano.maint_idle=900')
            self.assertEqual((manifest['kind'], manifest['serial_in_cmdline'], manifest['kernel_release']),
                             ('piano-maintenance-ram', False, '7.2.6-piano9'))
            image = (d / 'maint/boot-ram-maintenance.img').read_bytes()
            self.assertIn(manifest['cmdline'].encode() + b'\0', image[:4096])
            self.assertEqual(hashlib.sha256(image).hexdigest(), manifest['image']['sha256'])
            self.assertEqual(sorted(p.name for p in (d / 'maint').iterdir()),
                             ['SHA256SUMS', 'boot-ram-maintenance.img', 'manifest.json'])
            unit = maintenance(d, output='unit')
            self.assertEqual(unit.returncode, 0, unit.stderr)
            self.assertTrue(json.loads((d / 'unit/manifest.json').read_text())['cmdline'].endswith(
                ' piano.maint_idle=600 androidboot.serialno=abc1234'))


if __name__ == '__main__':
    unittest.main()
