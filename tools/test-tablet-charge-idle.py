#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Exercise the charge wakeup-source reader and GNOME suspend cookie lifecycle."""
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from gi.repository import GLib

policy = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                            'device/charge-policy/usr/libexec/tablet-charge-idle'))


class WakeSourceTest(unittest.TestCase):
    def test_inhibitor_follows_its_driver(self):
        for device, name in policy['WAKE_SOURCES'].items():
            with self.subTest(device=device), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                node = root / 'wakeup18'
                node.mkdir()
                other = root / 'wakeup2'
                other.mkdir()
                (other / 'name').write_text('unrelated')
                (other / 'active_time_ms').write_text('100')
                helper = object.__new__(policy['ChargeIdle'])
                helper.device = device
                helper.sysfs = root
                helper.owner = ':1.15'
                helper.cookie = None
                helper.bus = Mock()
                helper.bus.call_sync.return_value = GLib.Variant('(u)', (17,))
                helper.update()  # No matching driver yet.
                (node / 'name').write_text(name)
                (node / 'total_time_ms').write_text('90000')
                for duration, expected in [('0', None), ('bad', None), ('1', 17),
                                           ('45', 17), ('0', None), ('1', 17)]:
                    (node / 'active_time_ms').write_text(duration)
                    helper.update()
                    self.assertEqual(helper.cookie, expected, duration)
                (node / 'active_time_ms').unlink()
                helper.update()  # Unbind while held.
                calls = helper.bus.call_sync.call_args_list
                self.assertEqual([c.args[3] for c in calls],
                                 ['Inhibit', 'Uninhibit', 'Inhibit', 'Uninhibit'])
                self.assertEqual(calls[0].args[4].unpack()[3], 4)
                self.assertEqual(calls[1].args[4].unpack(), (17,))
                self.assertIsNone(helper.cookie)


if __name__ == '__main__':
    unittest.main()
