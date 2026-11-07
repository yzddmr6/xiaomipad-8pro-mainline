#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic regressions of the flash check. Never a real device."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def load():
    path = Path(__file__).with_name('check-piano-flash.py')
    spec = importlib.util.spec_from_file_location('check_piano_flash', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FlashCheckTest(unittest.TestCase):
    def setUp(self):
        self.module = load()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        leds = self.root / 'sys/class/leds'
        (leds / 'flash0').mkdir(parents=True)
        (leds / 'flash0' / 'brightness').write_text('0\n')
        (leds / 'flash0' / 'max_brightness').write_text('255\n')
        (leds / 'flash0' / 'flash_brightness').write_text('0\n')
        (leds / 'flash0' / 'max_flash_brightness').write_text('400000\n')
        (leds / 'flash0' / 'flash_timeout').write_text('0\n')
        (leds / 'flash0' / 'max_flash_timeout').write_text('300000\n')
        (leds / 'flash1').mkdir(parents=True)
        (leds / 'flash1' / 'brightness').write_text('0\n')
        (leds / 'flash1' / 'max_brightness').write_text('255\n')
        (leds / 'flash1' / 'flash_brightness').write_text('0\n')
        (leds / 'flash1' / 'max_flash_brightness').write_text('400000\n')
        # A plain LED of another device cannot flash.
        (leds / 'input3').mkdir(parents=True)
        (leds / 'input3' / 'brightness').write_text('1\n')

    def test_report_lists_only_flash_devices(self):
        rows = self.module.report(self.root)
        self.assertEqual([row['name'] for row in rows], ['flash0', 'flash1'])
        self.assertEqual(rows[0]['max_brightness'], '255')
        self.assertEqual(rows[0]['brightness'], '0')
        self.assertEqual(rows[0]['max_flash_brightness'], '400000')
        self.assertEqual(rows[0]['max_flash_timeout'], '300000')

    def test_drive_writes_and_reads_back(self):
        rows = self.module.drive(self.root, 128)
        self.assertEqual([row['name'] for row in rows], ['flash0', 'flash1'])
        for row in rows:
            self.assertEqual(row['requested'], 128)
            self.assertEqual(row['read_back'], '128')
        self.assertTrue(all(row['ok'] for row in rows))
        self.assertEqual((self.root / 'sys/class/leds/flash0/brightness').read_text(), '0')

    def test_out_of_range_does_not_change_torch(self):
        self.assertTrue(all(not row['ok'] for row in self.module.drive(self.root, 256)))
        self.assertEqual((self.root / 'sys/class/leds/flash0/brightness').read_text(), '0\n')

    def test_mismatched_readback_fails_and_restores(self):
        from unittest.mock import patch
        write = Path.write_text
        def limited(path, value, *args, **kwargs):
            return write(path, '64' if value == '128' else value, *args, **kwargs)
        with patch.object(Path, 'write_text', limited):
            rows = self.module.drive(self.root, 128)
        self.assertTrue(all(not row['ok'] for row in rows))
        self.assertTrue(all(row['restored'] == 0 for row in rows))

    def test_write_failure_still_restores(self):
        from unittest.mock import patch
        write = Path.write_text
        def failing(path, value, *args, **kwargs):
            if value == '128':
                write(path, '64')
                raise OSError('synthetic partial write')
            return write(path, value, *args, **kwargs)
        with patch.object(Path, 'write_text', failing):
            rows = self.module.drive(self.root, 128)
        self.assertTrue(all(not row['ok'] for row in rows))
        self.assertTrue(all(row['restored'] == 0 for row in rows))

    def test_a_missing_tree_reports_nothing(self):
        self.assertEqual(self.module.report(self.root / 'absent'), [])


if __name__ == '__main__':
    unittest.main()
