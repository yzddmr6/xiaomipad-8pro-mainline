# SPDX-License-Identifier: MIT
"""Exercise the shared battery parser on synthetic sysfs trees."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / 'switch-common.sh'


class BatteryParser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def battery(self, name='battery', capacity='40', **fields):
        path = self.root / name
        path.mkdir(exist_ok=True)
        for key, value in {'type': 'Battery', 'capacity': capacity, **fields}.items():
            (path / key).write_text(value + '\n')
        return path

    def parse(self, shell='sh'):
        argv = [shell] if shell != 'busybox' else ['busybox', 'sh']
        return subprocess.run(argv + [str(HELPER), '--battery', str(self.root)],
                              text=True, capture_output=True, timeout=5)

    def test_unknown_and_malformed(self):
        self.assertNotEqual(self.parse().returncode, 0)
        for value in ('', 'x', '-1', '+40', '101', '1000', '4 0', '40.0'):
            with self.subTest(value=value):
                self.battery(capacity=value)
                self.assertNotEqual(self.parse().returncode, 0)
        path = self.battery()
        (path / 'capacity').unlink()
        self.assertNotEqual(self.parse().returncode, 0)

    def test_minimum_system_battery_and_ignore_peripherals(self):
        self.battery('main', '80', scope='System')
        self.battery('second', '40')
        self.battery('keyboard', '0', scope='Device')
        result = self.parse()
        self.assertEqual((result.returncode, result.stdout.strip()), (0, '40'))
        self.battery('second', '39')
        self.assertEqual(self.parse().stdout.strip(), '39')
        self.battery('second', '40', present='0')
        self.assertNotEqual(self.parse().returncode, 0)

    def test_boundaries_on_both_shells(self):
        self.assertIsNotNone(shutil.which('busybox'))
        for shell in ('sh', 'busybox'):
            for value in ('0', '39', '40', '100', '009'):
                self.battery(capacity=value)
                result = self.parse(shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(int(result.stdout), int(value))
