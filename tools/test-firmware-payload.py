#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the repository firmware tree against its locks and the staging CLI, including Python -S runs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).with_name('stage-firmware.py')
sys.path.insert(0, str(TOOL.parent))
import firmware_payload  # noqa: E402


class Payload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'inputs'
        self.source.mkdir()
        self.files = {'adsp.mdt': b'header', 'adsp.b01': b'segment',
                      'adsp_dtb.mdt': b'dtb header', 'adsp_dtb.b01': b'dtb segment',
                      'cdsp.mdt': b'compute header', 'cdsp.b01': b'compute segment',
                      'cdsp_dtb.mdt': b'compute dtb', 'cdsp_dtb.b01': b'compute dtb segment',
                      'adsps.jsn': b'private service map'}
        for name, data in self.files.items():
            (self.source / name).write_bytes(data)
        self.lock = {'schema': 1, 'stock_build': 'synthetic-only',
                     'firmware_directory': 'usr/lib/firmware/qcom/test',
                     'images': ['adsp.mdt', 'adsp_dtb.mdt', 'cdsp.mdt', 'cdsp_dtb.mdt'], 'redistribution': 'fixture',
                     'files': [{'name': n, 'bytes': len(d), 'sha256': hashlib.sha256(d).hexdigest()}
                               for n, d in self.files.items()]}

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, name='result', images=()):
        path = self.root / 'lock.json'
        path.write_text(json.dumps(self.lock))
        # -S excludes site-packages: the payload path must not import python-debian/apt.
        words = [sys.executable, '-S', str(TOOL), '--lock', str(path), '--firmware-dir',
                 str(self.source), '--output', str(self.root / name)]
        for image in images:
            words += ['--image', image]
        return subprocess.run(words, capture_output=True, text=True, timeout=15)

    def test_plain_payload_and_adsp_selection_without_site_packages(self):
        for name, images, expected in [('all', (), set(self.files)),
                                       ('adsp', ('adsp.mdt',), {'adsp.mdt', 'adsp.b01'})]:
            with self.subTest(images=images):
                result = self.invoke(name, images)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = self.root / name / 'root/usr/lib/firmware/qcom/test'
                self.assertEqual({p.name for p in payload.iterdir()}, expected)
                for p in payload.iterdir():
                    self.assertEqual(p.read_bytes(), self.files[p.name])
                    self.assertEqual(p.stat().st_mode & 0o777, 0o644)
                manifest = json.loads((self.root / name / 'manifest.json').read_text())
                self.assertEqual({r['name'] for r in manifest['files']}, expected)

    def test_bad_bytes_do_not_publish_a_payload(self):
        (self.source / 'adsp.mdt').write_bytes(b'BADBAD')
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Firmware hash mismatch', result.stderr)
        self.assertFalse((self.root / 'result').exists())
        self.assertFalse(list(self.root.glob('.piano-firmware-*')))

    def test_symlink_input_rejected(self):
        (self.source / 'adsp.mdt').unlink()
        target = self.root / 'external'
        target.write_bytes(self.files['adsp.mdt'])
        (self.source / 'adsp.mdt').symlink_to(target)
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertFalse((self.root / 'result').exists())

    def test_invalid_names_and_namespace_rejected(self):
        for name in ('../adsp.mdt', '/adsp.mdt', '.', 'adsp.b01'):
            with self.subTest(name=name):
                self.lock['files'][0]['name'] = name
                self.assertNotEqual(self.invoke().returncode, 0)
                self.assertFalse((self.root / 'result').exists())
        self.lock['files'][0]['name'] = 'adsp.mdt'
        for directory in ('/usr/lib/firmware', '../escape', 'usr/../escape'):
            self.lock['firmware_directory'] = directory
            self.assertNotEqual(self.invoke().returncode, 0)

    def test_unknown_or_repeated_image_rejected(self):
        for images in (('missing.mdt',), ('adsp.mdt', 'adsp.mdt')):
            self.assertNotEqual(self.invoke(images=images).returncode, 0)
            self.assertFalse((self.root / 'result').exists())

    def test_default_stages_both_adsp_and_cdsp(self):
        result = subprocess.run([sys.executable, '-S', str(TOOL), '--output', str(self.root / 'repo')],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.root / 'repo/root/usr/lib/firmware/qcom/sm8750/piano'
        expected = {r['path'].rsplit('/', 1)[1]: r for r in firmware_payload.load_tree()
                    if r['path'].startswith('qcom/sm8750/piano/')}
        self.assertTrue({'adsp.mdt','adsp_dtb.mdt','cdsp.mdt','cdsp_dtb.mdt'} <= expected.keys())
        self.assertEqual({p.name for p in payload.iterdir()}, set(expected))
        self.assertTrue(all(firmware_payload.sha(payload/n) == r['sha256'] for n,r in expected.items()))

    def test_tree_locks_reject_unsafe_or_repeated_paths(self):
        for files in ({'../escape.bin': None}, {'qca/./x.bin': None}, {'qca/x.bin': None}):
            with self.subTest(files=files):
                locks = self.root / 'locks'
                locks.mkdir(exist_ok=True)
                rows = {k: {'bytes': 1, 'sha256': '0' * 64} for k in files}
                (locks / 'piano-a.json').write_text(json.dumps({'schema': 1, 'files': rows}))
                (locks / 'piano-b.json').write_text(json.dumps(
                    {'schema': 1, 'install_dir': '/usr/lib/firmware/qca', 'files': {'x.bin': rows[next(iter(rows))]}}))
                with self.assertRaises(ValueError):
                    firmware_payload.load_tree(locks)

    def test_stage_tree_rejects_mismatch_and_parent_symlink(self):
        data = b'firmware'
        rows = [{'path': 'vendor/fw.bin', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'fw.bin').write_bytes(data)
        linked = self.root / 'linked'
        linked.mkdir()
        (linked / 'vendor').symlink_to(outside)
        bad = self.root / 'bad/vendor'
        bad.mkdir(parents=True)
        (bad / 'fw.bin').write_bytes(b'FIRMWARE')
        for source in (linked, bad.parent):
            with self.subTest(source=source.name), self.assertRaises(ValueError):
                firmware_payload.stage_tree(rows, source, self.root / f'stage-{source.name}')

    def test_existing_output_preserved(self):
        output = self.root / 'result'
        output.mkdir()
        (output / 'keep').write_bytes(b'previous output')
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual((output / 'keep').read_bytes(), b'previous output')
        self.assertEqual({p.name for p in output.iterdir()}, {'keep'})


class RepositoryTree(unittest.TestCase):
    def test_tree_holds_exactly_the_locked_files(self):
        rows = firmware_payload.load_tree()
        root = firmware_payload.REPO_FIRMWARE
        tree = {str(p.relative_to(root)) for p in root.glob('*/**/*') if p.is_file() or p.is_symlink()}
        sensors = json.loads((root / 'sensors/source.json').read_text())['files']
        sensor_paths = {'sensors/' + r['path'] for r in sensors}
        self.assertEqual(len(sensor_paths), len(sensors))
        npu = json.loads((root/'npu/source.json').read_text())['files']
        npu_paths = {'npu/'+r['name'] for r in npu} | {'npu/source.json','npu/README.md'}
        self.assertEqual(tree, {r['path'] for r in rows} | sensor_paths | npu_paths |
                         {'sensors/source.json', 'sensors/README.md'})
        for row in npu:
            path=root/'npu'/row['name']
            self.assertFalse(path.is_symlink())
            self.assertEqual((path.stat().st_size,firmware_payload.sha(path)),(row['bytes'],row['sha256']))
        for row in sensors:
            path = root / 'sensors' / row['path']
            with self.subTest(sensor=row['path']):
                self.assertTrue(path.resolve().is_relative_to((root / 'sensors').resolve()))
                self.assertFalse(path.is_symlink())
                self.assertEqual(firmware_payload.sha(path), row['sha256'])
        for row in rows:
            path = root / row['path']
            with self.subTest(path=row['path']):
                self.assertFalse(path.is_symlink())
                self.assertEqual((path.stat().st_size, firmware_payload.sha(path)), (row['bytes'], row['sha256']))


if __name__ == '__main__':
    unittest.main()
