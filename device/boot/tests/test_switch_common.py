# SPDX-License-Identifier: MIT
"""Exercise the shared switch helper on both shells the products run it with."""
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / 'switch-common.sh'


class SharedHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def shell(self, script, shell='sh'):
        argv = [shell] if shell != 'busybox' else ['busybox', 'sh']
        prelude = f'log() {{ printf "LOG %s\\n" "$*"; }}\nPROG=test\nSTORE={shlex.quote(str(self.root))}\n. {shlex.quote(str(HELPER))}\n'
        return subprocess.run(argv + ['-c', prelude + script], text=True, capture_output=True, timeout=5)

    def test_bootconfig_slot_claims_on_both_shells(self):
        self.assertIsNotNone(shutil.which('busybox'))
        bootconfig = self.root / 'bootconfig'
        for shell in ('sh', 'busybox'):
            for text, claims in (('androidboot.slot_suffix = "_a"\n', ['_a']),
                                 ('androidboot.slot_suffix=_b\nandroidboot.serialno = "x"\n', ['_b']),
                                 ('androidboot.slot_suffix = "_b"\nandroidboot.slot_suffix = "_a"\n', ['_b', '_a']),
                                 ('androidboot.slot_suffix = "_c"\n', ['androidboot.slot_suffix = "_c"']),
                                 ('androidboot.serialno = "x"\n', [])):
                with self.subTest(shell=shell, text=text):
                    bootconfig.write_text(text)
                    result = self.shell(f'switch_bootconfig_slots {shlex.quote(str(bootconfig))}', shell)
                    self.assertEqual((result.returncode, result.stdout.splitlines()), (0, claims), result.stderr)

    def test_run_logged_keeps_status_and_stderr(self):
        result = self.shell('run_logged sh -c "echo oops >&2; exit 3"; echo "rc=$?"')
        self.assertIn('oops', result.stderr)
        self.assertIn('LOG oops', result.stdout)
        self.assertIn('rc=3', result.stdout)

    def test_no_charge_gate(self):
        """Switching never depends on the battery level, readable or not."""
        result = self.shell('command -v require_switch_battery switch_battery || echo none')
        self.assertEqual(result.stdout.strip(), 'none')
        self.assertNotIn('power_supply', HELPER.read_text())


if __name__ == '__main__':
    unittest.main()
