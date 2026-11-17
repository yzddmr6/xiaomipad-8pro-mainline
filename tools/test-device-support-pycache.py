#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""piano-device-support drops stale byte code of its private modules on configure and prerm.

Package files carry mtime 0, so after an upgrade a module whose new text kept its size still
matches the .pyc root compiled from the old text, and Python runs the old code. The test builds
the package, plants such a .pyc in a staging root and runs the shipped maintainer scripts there
(DPKG_ROOT, with the system tools they call stubbed out).
"""
import importlib.util
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
AVBTOOL = REPO / 'tools/local/aosp-avb/avbtool.py'
MKBOOTIMG = REPO / 'tools/local/aosp-mkbootimg/mkbootimg.py'
OLD = 'def answer():\n    return "old"\n'
NEW = 'def answer():\n    return "new"\n'
SCRIPTS = (('postinst', ['configure', '1.6.0-1']), ('prerm', ['upgrade', '1.7.0-1']),
           ('prerm', ['remove']), ('prerm', ['deconfigure', 'in-favour', 'other', '1']))


@unittest.skipUnless(AVBTOOL.is_file() and MKBOOTIMG.is_file(), 'tools/local AOSP inputs not fetched')
class BootPycache(unittest.TestCase):
    def test_stale_bytecode_removed(self):
        self.assertEqual(len(OLD), len(NEW))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name in ('thp-live', 'busybox'):
                (tmp / name).write_bytes(b'#!/bin/sh\nexit 0\n')
            out = tmp / 'debs'
            subprocess.run([sys.executable, str(REPO / 'tools/build-device-debs.py'), '--support-only',
                            '--version', '0.0~test', '--thp-live', str(tmp / 'thp-live'),
                            '--busybox', str(tmp / 'busybox'), '--avbtool', str(AVBTOOL),
                            '--output', str(out)], check=True, stdout=subprocess.DEVNULL)
            deb, = out.glob('piano-device-support_*.deb')
            control = tmp / 'control'
            subprocess.run(['dpkg-deb', '-e', str(deb), str(control)], check=True)
            stubs = tmp / 'stubs'
            stubs.mkdir()
            for tool in ('systemd-sysusers', 'udevadm', 'deb-systemd-helper', 'deb-systemd-invoke', 'systemctl'):
                (stubs / tool).write_text('#!/bin/sh\nexit 0\n')
                (stubs / tool).chmod(0o755)
            env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
            env['PATH'] = f'{stubs}:/usr/bin:/bin'
            probe = 'import piano_bootslot; print(piano_bootslot.answer())'
            for script, args in SCRIPTS:
                with self.subTest(script=script, action=args[0]):
                    boot = tmp / ('root-' + script + '-' + args[0]) / 'usr/lib/piano/boot'
                    (boot / 'gki').mkdir(parents=True)
                    (boot / 'gki/__init__.py').write_text('')
                    module = boot / 'piano_bootslot.py'
                    # Root imported the old package text, as the installed tools do...
                    module.write_text(OLD)
                    os.utime(module, (0, 0))
                    for source in (module, boot / 'gki/__init__.py'):
                        py_compile.compile(str(source), cfile=importlib.util.cache_from_source(str(source)), doraise=True)
                    # ...then the upgrade unpacked a new text of the same size, again with mtime 0.
                    module.write_text(NEW)
                    os.utime(module, (0, 0))
                    run = lambda *flags: subprocess.run([sys.executable, *flags, '-c', probe], cwd=boot, env=env,
                                                        capture_output=True, text=True, check=True).stdout.strip()
                    self.assertEqual(run(), 'old', 'precondition: Python accepts the same-size, same-mtime .pyc')
                    subprocess.run(['sh', str(control / script), *args], env=dict(env, DPKG_ROOT=str(boot.parents[3])),
                                   check=True, stdout=subprocess.DEVNULL)
                    self.assertFalse(list(boot.rglob('__pycache__')))
                    self.assertTrue(module.is_file() and (boot / 'gki/__init__.py').is_file(), 'only byte code goes')
                    self.assertEqual(run('-B'), 'new')


if __name__ == '__main__':
    unittest.main()
