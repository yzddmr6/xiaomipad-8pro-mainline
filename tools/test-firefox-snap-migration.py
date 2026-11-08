#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Run as root with busybox-static; all commands and data stay in a temporary chroot."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / 'packaging/firefox-snap-migration/usr/libexec/firefox-snap-remove'
CHROOT = shutil.which('chroot')


@unittest.skipUnless(os.geteuid() == 0, 'temporary chroot requires root')
class Migration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for directory in ('bin', 'usr/bin', 'usr/lib/firefox', 'home', 'root'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        shutil.copy2('/usr/bin/busybox', self.root / 'bin/busybox')
        for applet in ('sh', 'mkdir'):
            (self.root / 'bin' / applet).symlink_to('busybox')
        shutil.copy2(HELPER, self.root / 'usr/bin/firefox-snap-remove')
        self.browser = self.root / 'usr/lib/firefox/firefox'
        self.browser.write_text('#!/bin/sh\nexit 0\n')
        self.browser.chmod(0o755)
        snap = self.root / 'usr/bin/snap'
        snap.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> /calls
case "$1" in
list) printf '%s\\n' 'firefox 1 stable mozilla';;
remove) test ! -e /fail-remove;;
*) exit 1;;
esac
''')
        snap.chmod(0o755)
        self.marker = self.root / 'var/lib/piano/firefox-snap-removed'

    def tearDown(self):
        self.temp.cleanup()

    def run_helper(self, marker=True):
        return subprocess.run([CHROOT, str(self.root), '/bin/sh', '/usr/bin/firefox-snap-remove',
                               *(['/var/lib/piano/firefox-snap-removed'] if marker else [])],
                              env=dict(os.environ, PATH='/usr/bin:/bin'), text=True,
                              capture_output=True)

    def test_existing_data_is_preserved(self):
        for path, dangling in [('home/alice/snap/firefox', False),
                               ('root/snap/firefox', False),
                               ('home/alice/snap/firefox', True)]:
            with self.subTest(path=path, dangling=dangling):
                profile = self.root / path
                profile.parent.mkdir(parents=True, exist_ok=True)
                if dangling:
                    profile.symlink_to('/unavailable-storage/firefox')
                else:
                    profile.mkdir()
                    (profile / 'prefs.js').write_text('user data\n')
                result = self.run_helper()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Keeping Firefox snap: existing user data detected.', result.stdout)
                self.assertFalse((self.root / 'calls').exists())
                self.assertFalse(self.marker.exists())
                if dangling:
                    self.assertTrue(profile.is_symlink())
                    profile.unlink()
                else:
                    self.assertEqual((profile / 'prefs.js').read_text(), 'user data\n')
                    shutil.rmtree(profile)

    def test_unused_snap_is_removed_without_purge(self):
        for marker in (False, True):
            with self.subTest(marker=marker):
                calls = self.root / 'calls'
                calls.unlink(missing_ok=True)
                result = self.run_helper(marker)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls.read_text().splitlines(), ['list firefox', 'remove firefox'])
                self.assertEqual(self.marker.exists(), marker)

    def test_missing_deb_or_failed_removal_never_marks_completion(self):
        self.browser.unlink()
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Mozilla Firefox is not installed', result.stdout)
        self.assertFalse((self.root / 'calls').exists())
        self.assertFalse(self.marker.exists())
        self.browser.touch(mode=0o755)
        (self.root / 'fail-remove').touch()
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
