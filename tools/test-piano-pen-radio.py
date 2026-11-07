#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Protocol and event scheduling tests; never opens a Bluetooth adapter."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pen_radio', ROOT / 'device/bluetooth/piano-pen-radio.py')
radio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(radio)


class Clock:
    def __init__(self):
        self.sources = {}
        self.next_id = 1
    def arm(self, interval, callback):
        ident = self.next_id
        self.next_id += 1
        self.sources[ident] = callback
        return ident
    def cancel(self, ident):
        self.sources.pop(ident, None)


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.requests = []
        self.failures = []
        self.p = radio.profile(ROOT / 'device/bluetooth/p81c-radio.json')
        self.session = radio.Session(self.p, bytes.fromhex('020000000001'),
            lambda data, done: self.requests.append((data, done)),
            self.clock.arm, self.clock.cancel, lambda: self.failures.append(True), 123)

    def reply(self):
        prefix, size = self.session.pending
        self.session.notification(prefix + bytes(size - len(prefix)))

    def finish(self):
        budget = 40
        while self.session.pending and budget:
            self.reply()
            budget -= 1
        self.assertGreater(budget, 0)
        self.assertEqual(self.clock.sources, {})

    def test_protocol_and_event_only_updates(self):
        self.session.update(True, True)
        self.session.start()
        self.finish()
        self.assertTrue(self.session.initialized)
        packets = [data for data, _ in self.requests]
        self.assertEqual(sum(data.startswith(b'\x30') for data in packets), 2)
        self.assertEqual(sum(data.startswith(b'\x31') for data in packets), 2)
        self.assertEqual(sum(data.startswith(b'\x32') for data in packets), 2)
        self.assertIn(b'\x33\x04\x00\x00\x00\x7b', packets)
        count = len(self.requests)
        self.session.update(True, True)
        self.assertEqual(len(self.requests), count)
        self.session.update(False, False)
        self.finish()
        self.assertEqual([data for data, _ in self.requests[count:]], [b'\x51\x01\x01', b'\x61\x01\x00'])
        self.assertFalse(self.failures)

    def test_response_timeout_stops_without_retry(self):
        self.session.start()
        callback = next(iter(self.clock.sources.values()))
        callback()
        self.assertTrue(self.session.closed)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.failures), 1)
        self.assertFalse(self.clock.sources)

    def test_write_error_is_isolated(self):
        self.session.start()
        self.requests[-1][1](False)
        self.assertTrue(self.session.closed)
        self.assertEqual(len(self.failures), 1)
        self.assertFalse(self.clock.sources)

    def test_unrelated_reply_and_late_write_result(self):
        self.session.start()
        first_done = self.requests[0][1]
        self.session.notification(b'\xff\x00')
        self.assertEqual(len(self.requests), 1)
        self.reply()
        first_done(False)
        self.assertFalse(self.session.closed)
        self.session.close()
        self.assertFalse(self.clock.sources)

    def test_unassigned_id_never_programmed(self):
        self.session.start()
        while self.requests[-1][0] != b'\x04\x00':
            self.reply()
        self.session.notification(b'\x84\x01\xff')
        self.assertTrue(self.session.closed)
        self.assertFalse(any(data.startswith(b'\x30') for data, _ in self.requests))

    def test_scan_gate(self):
        self.assertFalse(radio.scan_allowed(False, None, {}, False))
        self.assertFalse(radio.scan_allowed(None, 'known', {}, False))
        self.assertFalse(radio.scan_allowed(True, None, {}, False))
        self.assertFalse(radio.scan_allowed(True, 'known', {'Paired': True}, False))
        self.assertFalse(radio.scan_allowed(True, 'known', {}, True))
        self.assertTrue(radio.scan_allowed(True, 'known', {}, False))

    def test_idle_controller_schedules_nothing(self):
        app = radio.Controller.__new__(radio.Controller)
        app.stopping = app.sleeping = False
        app.objects = {}
        app.session_path = app.address = app.docked = None
        calls = []
        app.call = lambda *a, **k: calls.append((a, k))
        app.reconcile()
        self.assertEqual(calls, [])
        self.assertFalse(self.clock.sources)

    def test_stale_magnetic_identity_cannot_pair(self):
        app = radio.Controller.__new__(radio.Controller)
        app.stopping = app.sleeping = False
        app.address = 'OLD'
        app.docked = True
        app.dock_verified = False
        app.session_path = None
        app.objects = {'/old': {radio.DEVICE: {'Address': 'OLD', 'Paired': False}}}
        calls = []
        app.call = lambda *a, **k: calls.append((a, k))
        app.reconcile()
        self.assertEqual(calls, [])

    def test_discovery_is_bounded_and_owned(self):
        app = radio.Controller.__new__(radio.Controller)
        class GLib:
            Variant = staticmethod(lambda signature, value: value)
            timeout_add_seconds = self.clock.arm
            source_remove = self.clock.cancel
        app.GLib = GLib
        app.scan_owned = app.scan_busy = app.scan_wanted = app.scanned = False
        app.scan_adapter = app.scan_timer = None
        calls = []
        app.call = lambda *a, **k: calls.append((a, k))
        app.stop_scan()
        self.assertEqual(calls, [])
        app.start_scan('/adapter')
        calls[-1][1]['done'](None, True)
        self.assertEqual(calls[-1][0][2], 'StartDiscovery')
        calls[-1][1]['done'](None, True)
        self.assertEqual(len(self.clock.sources), 1)
        next(iter(self.clock.sources.values()))()
        self.assertEqual(calls[-1][0][2], 'StopDiscovery')
        calls[-1][1]['done'](None, True)
        self.assertFalse(app.scan_owned)
        self.assertFalse(self.clock.sources)
        count = len(calls)
        app.stop_scan()
        self.assertEqual(len(calls), count)

    def test_removed_during_discovery_start(self):
        app = radio.Controller.__new__(radio.Controller)
        class GLib:
            Variant = staticmethod(lambda signature, value: value)
            timeout_add_seconds = self.clock.arm
            source_remove = self.clock.cancel
        app.GLib = GLib
        app.scan_owned = app.scan_busy = app.scan_wanted = app.scanned = False
        app.scan_adapter = app.scan_timer = None
        calls = []
        app.call = lambda *a, **k: calls.append((a, k))
        app.start_scan('/adapter')
        app.stop_scan()
        calls[-1][1]['done'](None, True)
        self.assertEqual(len(calls), 1)
        self.assertFalse(app.scan_busy)
        self.assertFalse(app.scan_owned)
        self.assertFalse(self.clock.sources)


if __name__ == '__main__':
    unittest.main()
