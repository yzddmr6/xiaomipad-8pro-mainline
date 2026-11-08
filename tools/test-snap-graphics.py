#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GPU content connection fixture tests; no snapd, GPU or device access."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'tools/fixtures/snap-graphics'
loader = importlib.machinery.SourceFileLoader('connect', str(
    REPO / 'packaging/piano-snap-graphics/usr/bin/piano-snap-graphics-connect'))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


class Graphics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tool.PAYLOAD = self.root / 'payload'
        tool.PAYLOAD.mkdir()
        payload = tool.PAYLOAD / 'piano-mesa-2404.snap'
        payload.write_bytes(b'fixture payload, not a snap')
        (tool.PAYLOAD / 'provider.json').write_text(json.dumps({
            'version': 'fixture-version', 'sha256': hashlib.sha256(payload.read_bytes()).hexdigest()}))
        tool.MARKER = self.root / 'marker'
        tool.DISCARD_NS = str(FIXTURE / 'snap-discard-ns')
        self.state = {'seeded': True, 'connections': (FIXTURE / 'connections.txt').read_text()}
        self.save()
        env = patch.dict(os.environ, PATH=str(FIXTURE) + ':' + os.environ['PATH'],
                         SNAP_GRAPHICS_FIXTURE=str(self.root))
        env.start()
        self.addCleanup(env.stop)

    def save(self):
        (self.root / 'state.json').write_text(json.dumps(self.state))

    def run_cli(self, *args):
        with patch.object(sys, 'argv', ['connect', *args]), contextlib.redirect_stdout(io.StringIO()):
            tool.main()

    def calls(self, verb):
        return [row['args'] for row in map(json.loads, (self.root / 'calls.jsonl').read_text().splitlines())
                if row['args'][0] == verb]

    def test_install_connect_and_repeat(self):
        self.run_cli('--install')
        self.assertTrue(tool.MARKER.exists())
        state = json.loads((self.root / 'state.json').read_text())
        for row in [line.split() for line in state['connections'].splitlines()][1:]:
            if row[1].endswith(':gpu-2404'):
                self.assertEqual(row[2], tool.PROVIDER)
        self.assertEqual(len(self.calls('install')), 1)
        self.assertEqual([row[1] for row in self.calls('connect')],
                         ['future-app_test:gpu-2404', 'snap-store:gpu-2404', 'thunderbird:gpu-2404'])
        self.run_cli('--install')
        self.run_cli()
        self.assertEqual(len(self.calls('install')), 1)
        self.assertEqual(len(self.calls('connect')), 3)
        self.assertEqual(len(self.calls('disconnect')), 2)
        for consumer in ('future-app_test', 'snap-store', 'thunderbird'):
            mounts = json.loads(tool.snap('fixture-enter', consumer).stdout)
            self.assertEqual(mounts, [{'target':'gpu-2404', 'source':tool.PROVIDER}])
        calls = [row['args'] for row in map(json.loads, (self.root/'calls.jsonl').read_text().splitlines())]
        for plug in ('snap-store:gpu-2404', 'thunderbird:gpu-2404'):
            self.assertLess(calls.index(['disconnect', plug, 'mesa-2404:gpu-2404']),
                            calls.index(['connect', plug, tool.PROVIDER]))
        for consumer in ('future-app_test', 'snap-store', 'thunderbird'):
            self.assertLess(calls.index(['connect', consumer + ':gpu-2404', tool.PROVIDER]),
                            calls.index(['discard-ns', consumer]))

    def test_partial_connect_failure_then_explicit_resume(self):
        self.state['fail_connect'] = 'snap-store:gpu-2404'
        self.save()
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_cli('--install')
        self.assertFalse(tool.MARKER.exists())
        self.assertEqual(len(self.calls('connect')), 2)  # stop, no retries
        self.assertEqual(self.calls('discard-ns'), [['discard-ns', 'future-app_test']])
        self.state = json.loads((self.root / 'state.json').read_text())
        del self.state['fail_connect']
        self.save()
        self.run_cli('--install')
        self.assertTrue(tool.MARKER.exists())
        self.assertEqual(len(self.calls('install')), 1)
        self.assertEqual(len(self.calls('connect')), 4)

    def test_old_slot_disconnect_failure_leaves_no_marker(self):
        self.state['fail_disconnect'] = 'snap-store:gpu-2404'
        self.save()
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_cli('--install')
        self.assertFalse(tool.MARKER.exists())
        self.assertEqual(len(self.calls('disconnect')), 1)
        self.assertEqual(self.calls('connect'), [['connect', 'future-app_test:gpu-2404', tool.PROVIDER]])
        self.state = json.loads((self.root / 'state.json').read_text())
        del self.state['fail_disconnect']
        self.save()
        self.run_cli('--install')
        self.assertTrue(tool.MARKER.exists())
        self.assertEqual(len(self.calls('install')), 1)
        self.assertEqual(len(self.calls('connect')), 3)

    def test_existing_connection_discards_stale_mounts(self):
        self.state['connections'] = 'Interface Plug Slot Notes\ncontent thunderbird:gpu-2404 piano-mesa-2404:gpu-2404 manual\n'
        self.state['mounts'] = {'thunderbird': [{'target':'gpu-2404','source':tool.PROVIDER},
                                              {'target':'gpu-2404-2','source':'rootfs:stale-placeholder'}]}
        self.save()
        self.run_cli()
        mounts = json.loads(tool.snap('fixture-enter', 'thunderbird').stdout)
        self.assertEqual(mounts, [{'target':'gpu-2404','source':tool.PROVIDER}])
        self.assertEqual(self.calls('connect'), [])
        self.assertEqual(self.calls('discard-ns'), [['discard-ns','thunderbird']])

    def test_discard_failure_leaves_no_marker(self):
        self.state['fail_discard'] = 'snap-store'
        self.save()
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_cli('--install')
        self.assertFalse(tool.MARKER.exists())
        self.assertEqual(self.calls('discard-ns'), [['discard-ns','future-app_test'],['discard-ns','snap-store']])
        self.state = json.loads((self.root / 'state.json').read_text())
        del self.state['fail_discard']
        self.save()
        self.run_cli('--install')
        self.assertTrue(tool.MARKER.exists())
        self.assertEqual(len(self.calls('connect')), 3)
        mounts = json.loads(tool.snap('fixture-enter', 'snap-store').stdout)
        self.assertEqual(mounts, [{'target': 'gpu-2404', 'source': tool.PROVIDER}])

    def test_both_providers_already_connected(self):
        self.state['connections'] = ('Interface Plug Slot Notes\n'
                                     'content thunderbird:gpu-2404 mesa-2404:gpu-2404 manual\n'
                                     'content thunderbird:gpu-2404 piano-mesa-2404:gpu-2404 manual\n')
        self.state['mounts'] = {'thunderbird': [
            {'target': 'gpu-2404', 'source': 'mesa-2404:gpu-2404'},
            {'target': 'gpu-2404-2', 'source': tool.PROVIDER}]}
        self.save()
        self.run_cli()
        self.assertEqual(self.calls('connect'), [])
        calls = [row['args'] for row in map(json.loads, (self.root / 'calls.jsonl').read_text().splitlines())]
        self.assertEqual(calls[1:], [
            ['disconnect', 'thunderbird:gpu-2404', 'mesa-2404:gpu-2404'],
            ['discard-ns', 'thunderbird']])
        mounts = json.loads(tool.snap('fixture-enter', 'thunderbird').stdout)
        self.assertEqual(mounts, [{'target': 'gpu-2404', 'source': tool.PROVIDER}])

    def test_no_consumers(self):
        self.state['connections'] = 'Interface Plug Slot Notes\n'
        self.save()
        self.run_cli('--install')
        self.assertTrue(tool.MARKER.exists())
        self.assertEqual(self.calls('connect'), [])
        self.assertEqual(self.calls('disconnect'), [])
        self.assertEqual(self.calls('discard-ns'), [])

    def test_selected_consumer_preserves_other_connections_and_namespaces(self):
        self.state['version'] = 'fixture-version'
        self.state['connections'] += 'content firefox:gpu-2404 mesa-2404:gpu-2404 manual\n'
        self.state['mounts'] = {'snap-store': [{'target': 'gpu-2404', 'source': 'mesa-2404:gpu-2404'}]}
        self.save()
        self.run_cli('--consumer', 'firefox')
        state = json.loads((self.root / 'state.json').read_text())
        self.assertEqual(self.calls('disconnect'), [['disconnect', 'firefox:gpu-2404', 'mesa-2404:gpu-2404']])
        self.assertEqual(self.calls('connect'), [['connect', 'firefox:gpu-2404', tool.PROVIDER]])
        self.assertEqual(self.calls('discard-ns'), [['discard-ns', 'firefox']])
        self.assertEqual(state['mounts']['snap-store'], self.state['mounts']['snap-store'])
        self.assertEqual(tool.connections(state['connections'])['snap-store:gpu-2404'],
                         tool.connections(self.state['connections'])['snap-store:gpu-2404'])
        self.assertFalse(tool.MARKER.exists())

    def test_missing_selected_consumer_changes_nothing(self):
        with self.assertRaisesRegex(ValueError, 'no installed gpu-2404 plug'):
            self.run_cli('--consumer', 'snap-store', '--consumer', 'missing')
        self.assertEqual(self.calls('connect'), [])
        self.assertEqual(self.calls('disconnect'), [])
        self.assertEqual(self.calls('discard-ns'), [])

    def test_selected_consumer_cannot_mark_first_boot_complete(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli('--install', '--consumer', 'snap-store')
        self.assertFalse((self.root / 'calls.jsonl').exists())
        self.assertFalse(tool.MARKER.exists())

    def test_install_failure_leaves_no_marker(self):
        self.state['fail_install'] = True
        self.save()
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_cli('--install')
        self.assertFalse(tool.MARKER.exists())
        self.assertEqual(len(self.calls('install')), 1)
        self.assertEqual(self.calls('connect'), [])

    def test_hash_failure_has_no_snap_calls(self):
        (tool.PAYLOAD / 'piano-mesa-2404.snap').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.run_cli('--install')
        self.assertFalse((self.root / 'calls.jsonl').exists())
        self.assertFalse(tool.MARKER.exists())

    def test_parsing_existing_duplicate_slot_and_empty(self):
        text = self.state['connections'] + 'content thunderbird:gpu-2404 piano-mesa-2404:gpu-2404 manual\n'
        self.assertEqual(tool.connections(text)['thunderbird:gpu-2404'],
                         {'mesa-2404:gpu-2404', tool.PROVIDER})
        self.assertEqual(tool.connections('Interface Plug Slot Notes\n'), {})
        for text in ('', 'error: cannot contact snapd', 'Interface Plug Slot Notes\ncontent bad:gpu-2404\n',
                     'Interface Plug Slot Notes\nnetwork bad:gpu-2404 - -\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                tool.connections(text)

    def test_seed_failure_does_not_install(self):
        self.state['seeded'] = False
        self.save()
        with self.assertRaisesRegex(RuntimeError, 'before seeded'):
            self.run_cli('--install')
        self.assertFalse(tool.MARKER.exists())
        self.assertEqual(self.calls('install'), [])


if __name__ == '__main__':
    unittest.main()
