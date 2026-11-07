#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the system monitor defaults, optionally from an extracted desktop deb."""
import argparse
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

MONITORS = (Path(__file__).resolve().parents[1] /
            'packaging/piano-ubuntu-desktop/etc/xdg/monitors.xml')

# DRM modes exposed by Mutter 49 GetCurrentState on piano. Only mode data;
# the offline tests do not access a device.
NATIVE_MODE_IDS = (
    '3200x2136@60.000',
    '3200x2136@90.000',
    '3200x2136@120.000',
    '3200x2136@144.000',
)
# The firmware framebuffer exposes only its fixed 60 Hz mode to Mutter.
FALLBACK_MODE_IDS = ('3200x2136@60.000',)


class DesktopMonitors(unittest.TestCase):
    def setUp(self):
        # Parsing also rejects malformed XML.
        self.root = ET.parse(MONITORS).getroot()

    def test_both_connectors_have_both_layout_modes(self):
        self.assertEqual((self.root.tag, self.root.attrib), ('monitors', {'version': '2'}))
        self.assertCountEqual(
            [(config.findtext('logicalmonitor/monitor/monitorspec/connector'),
              config.findtext('layoutmode')) for config in self.root],
            [(connector, layout) for connector in ('DSI-1', 'None-1')
             for layout in ('logical', 'physical')])

    def test_panel_identity_and_product_defaults(self):
        for config in self.root:
            self.assertEqual(config.tag, 'configuration')
            self.assertEqual(len(config.findall('logicalmonitor')), 1)
            logical = config.find('logicalmonitor')
            self.assertEqual(len(logical.findall('monitor')), 1)
            monitor = logical.find('monitor')
            spec = monitor.find('monitorspec')
            connector = spec.findtext('connector')
            with self.subTest(connector=connector, layout=config.findtext('layoutmode')):
                self.assertEqual([spec.findtext(k) for k in ('vendor', 'product', 'serial')],
                                 ['unknown'] * 3)
                self.assertEqual([logical.findtext(k) for k in ('x', 'y', 'scale', 'primary')],
                                 ['0', '0', '2', 'yes'])
                self.assertEqual([monitor.findtext('mode/' + k) for k in ('width', 'height', 'rate')],
                                 ['3200', '2136', {'DSI-1': '120.000', 'None-1': '60.000'}[connector]])

    def test_rates_match_captured_mutter_mode_ids(self):
        for monitor in self.root.findall('configuration/logicalmonitor/monitor'):
            connector = monitor.findtext('monitorspec/connector')
            mode = monitor.find('mode')
            mode_id = f"{mode.findtext('width')}x{mode.findtext('height')}@{mode.findtext('rate')}"
            with self.subTest(connector=connector, mode=mode_id):
                self.assertIn(mode_id, NATIVE_MODE_IDS)
                if connector == 'None-1':
                    self.assertIn(mode_id, FALLBACK_MODE_IDS)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monitors-xml', type=Path, default=MONITORS)
    args, unittest_args = parser.parse_known_args()
    MONITORS = args.monitors_xml
    unittest.main(argv=[__file__, *unittest_args])
