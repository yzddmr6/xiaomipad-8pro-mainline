#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Offline HTTP/notification tests. Run with dbus-run-session -- python3 ..."""
import http.server
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from gi.repository import Gio, GLib

SCRIPT = Path(__file__).resolve().parents[1] / 'packaging/release-notifier/usr/libexec/mainline-release-notifier'
loader = importlib.machinery.SourceFileLoader('notifier', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
n = importlib.util.module_from_spec(spec)
loader.exec_module(n)


class Handler(http.server.BaseHTTPRequestHandler):
    status = 302
    location = '/releases/tag/v1.2.0'
    requests = []

    def do_GET(self):
        self.requests.append((self.path, dict(self.headers)))
        self.send_response(self.status)
        if self.location is not None:
            self.send_header('Location', self.server.repository + self.location
                             if self.location.startswith('/') else self.location)
        self.end_headers()

    def log_message(self, *args):
        pass


class Releases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        Handler.status, Handler.location, Handler.requests = 302, '/releases/tag/v1.2.0', []
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.repository = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.started = mock.Mock()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def check(self, installed='1.1.0'):
        n.check(self.server.repository, installed, self.state, self.started)

    def test_new_equal_old_and_once_per_version(self):
        self.check('1.2.0')
        self.check('1.3.0')
        self.started.assert_not_called()
        self.check()
        self.started.assert_called_once()
        self.assertEqual(json.loads((self.state/'pending.json').read_text()),
                         {'version': '1.2.0', 'url': self.server.repository + Handler.location})
        n.write_json(self.state/'notified.json', ['1.2.0'])
        self.check()
        self.started.assert_called_once()
        Handler.location = '/releases/tag/v1.3.0'
        self.check()
        self.assertEqual(self.started.call_count, 2)
        n.write_json(self.state/'notified.json', ['1.2.0', '1.3.0'])
        Handler.location = '/releases/tag/v1.2.0'
        self.check()  # GitHub latest can move backwards; still do not repeat.
        self.assertEqual(self.started.call_count, 2)
        self.assertTrue(all(path == '/releases/latest' for path, _ in Handler.requests))
        for _, headers in Handler.requests:
            self.assertEqual(headers['User-Agent'], 'Release-Notifier')
            self.assertFalse(set(headers) & {'Authorization', 'Cookie', 'Accept-Language'})

    def test_bad_http_and_untrusted_locations(self):
        for status, location in [(200, '/releases/tag/v2.0'), (404, None), (503, None),
                                 (302, None), (302, 'https://other.invalid/releases/tag/v2'),
                                 (302, '/releases/tag/v2?token=a'), (302, '/releases/tag/v2/evil'),
                                 (302, '/releases/tag/banana'), (302, '/releases/tag/v2<script>')]:
            with self.subTest(status=status, location=location):
                Handler.status, Handler.location = status, location
                self.check()
        self.started.assert_not_called()
        self.assertFalse((self.state/'pending.json').exists())

    def test_offline_is_silent(self):
        self.server.shutdown()
        self.server.server_close()
        with mock.patch('sys.stderr') as stderr:
            self.check()
        self.started.assert_not_called()
        stderr.write.assert_not_called()

    def test_persistent_catchup_waits_for_startup_trigger(self):
        (self.state/'project.json').write_text(json.dumps(['https://github.com/example/project', 'Tablet']))
        (self.state/'version').write_text('1.0')
        with mock.patch.object(n, 'DATA', self.state), \
             mock.patch.object(n, 'RELEASE', self.state/'version'), \
             mock.patch.object(n, 'state_directory', return_value=self.state), \
             mock.patch.object(n, 'check') as check, \
             mock.patch.object(n.subprocess, 'check_output', return_value='100000000'), \
             mock.patch.object(n.sys, 'argv', ['notifier', 'check']):
            with mock.patch.object(n.time, 'monotonic', return_value=699):
                n.main()
            check.assert_not_called()
            with mock.patch.object(n.time, 'monotonic', return_value=700):
                n.main()
            check.assert_called_once()

    def test_dpkg_order_and_locale(self):
        self.assertTrue(n.newer('1.10', '1.9'))
        self.assertFalse(n.newer('1.2~rc1', '1.2'))
        self.assertTrue(n.newer('1.2', '1.2~rc1'))
        with mock.patch.dict(os.environ, {'LC_ALL': 'C', 'LANG': 'zh_CN.UTF-8'}):
            self.assertFalse(n.chinese())
        with mock.patch.dict(os.environ, {'LC_ALL': 'zh_TW.UTF-8'}):
            self.assertTrue(n.chinese())


PORTAL_XML = '''<node><interface name="org.freedesktop.portal.OpenURI">
<method name="OpenURI"><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
</interface></node>'''


XML = '''<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/>
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="as" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
<arg type="u" direction="out"/></method>
<method name="CloseNotification"><arg type="u" direction="in"/></method>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
<signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
</interface></node>'''


class Notifications(unittest.TestCase):
    def test_real_dbus_actions_close_failure_and_dedup(self):
        if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            self.fail('Run under dbus-run-session')
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                      'org.freedesktop.DBus', 'RequestName',
                      GLib.Variant('(su)', (n.BUS, 0)), None,
                      Gio.DBusCallFlags.NONE, 2000, None)
        bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                      'org.freedesktop.DBus', 'RequestName',
                      GLib.Variant('(su)', (n.PORTAL, 0)), None,
                      Gio.DBusCallFlags.NONE, 2000, None)
        browsers = []
        portal_calls = []
        def portal(connection, sender, path, interface, name, params, invocation):
            parent, url, options = params.unpack()
            portal_calls.append((parent, url, options))
            handle = n.PORTAL_OBJECT + '/request/' + sender[1:].replace('.', '_') + '/' + options['handle_token']
            invocation.return_value(GLib.Variant('(o)', (handle,)))
            def opened():
                # Launch from the portal, after returning the request handle.
                # This process must outlive the notification receiver.
                browsers.append(subprocess.Popen(['sleep', '60'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL))
                connection.emit_signal(sender, handle, 'org.freedesktop.portal.Request',
                                       'Response', GLib.Variant('(ua{sv})', (0, {})))
                return False
            GLib.timeout_add(75, opened)
        portal_registration = bus.register_object(n.PORTAL_OBJECT,
            Gio.DBusNodeInfo.new_for_xml(PORTAL_XML).interfaces[0], portal, None, None)
        calls = []
        action = 'view'
        fail = False

        def method(connection, sender, path, interface, name, params, invocation):
            if name == 'Notify':
                calls.append(params.unpack())
                if fail:
                    invocation.return_dbus_error('org.freedesktop.DBus.Error.Failed', 'test failure')
                    return
                invocation.return_value(GLib.Variant('(u)', (42,)))
                def emit():
                    signal, values = ('NotificationClosed', GLib.Variant('(uu)', (42, 2))) if action == 'close' else ('ActionInvoked', GLib.Variant('(us)', (42, action)))
                    connection.emit_signal(sender, n.OBJECT, n.BUS, 'ActivationToken',
                                           GLib.Variant('(us)', (42, 'test-token')))
                    connection.emit_signal(sender, n.OBJECT, n.BUS, signal, values)
                    return False
                GLib.timeout_add(50, emit)
            else:
                invocation.return_value(None)

        registration = bus.register_object(n.OBJECT, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], method, None, None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state = root/n.APP
                state.mkdir()
                release = root/'version'
                release.write_text('1.0\n')
                opener = root/'xdg-open'
                opener.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$OPEN_LOG"\n')
                opener.chmod(0o755)
                config = root/'project.json'
                repository = 'https://github.com/example/project'
                config.write_text(json.dumps([repository, 'Tablet']))
                env = dict(os.environ, XDG_STATE_HOME=tmp, PATH=tmp+':'+os.environ['PATH'],
                           OPEN_LOG=str(root/'opened'), LC_ALL='C', LANG='en_US.UTF-8')
                code = ('import importlib.machinery; from pathlib import Path; '
                        f'n=importlib.machinery.SourceFileLoader("n", {str(SCRIPT)!r}).load_module(); '
                        f'n.DATA=Path({tmp!r}); n.RELEASE=Path({str(release)!r}); n.main()')
                def run():
                    proc = subprocess.Popen(['/usr/bin/python3', '-c', code, 'notify'], env=env,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    # Include interpreter startup under arm64 emulation. A main
                    # loop also avoids repeatedly entering PyGObject's event policy.
                    deadline = time.monotonic()+30
                    loop = GLib.MainLoop()
                    def completed():
                        if proc.poll() is not None or time.monotonic() >= deadline:
                            loop.quit()
                            return False
                        return True
                    GLib.timeout_add(50, completed)
                    loop.run()
                    if proc.poll() is None:
                        proc.kill()
                        output = proc.communicate()
                        self.fail(f'receiver timeout; Notify calls={len(calls)}, output={output!r}')
                    return proc.communicate(), proc.returncode
                for i, action in enumerate(('view', 'default', 'close'), 2):
                    url = repository + f'/releases/tag/v{i}.0'
                    n.write_json(state/'pending.json', {'version': f'{i}.0', 'url': url})
                    self.assertEqual(run()[1], 0)
                    self.assertEqual(calls[-1][3], 'New release available')
                    self.assertEqual(calls[-1][5], ['default', 'View release notes', 'view', 'View release notes'])
                    self.assertEqual(calls[-1][6]['desktop-entry'], n.APP)
                    count = len(calls)
                    self.assertEqual(run()[1], 0)
                    self.assertEqual(len(calls), count)
                self.assertEqual([entry[1] for entry in portal_calls],
                                 [repository+'/releases/tag/v2.0', repository+'/releases/tag/v3.0'])
                self.assertTrue(all(entry[0] == '' and entry[2]['activation_token'] == 'test-token'
                                    for entry in portal_calls))
                self.assertEqual(len(browsers), 2)
                self.assertTrue(all(proc.poll() is None for proc in browsers))
                self.assertFalse((root/'opened').exists(), 'portal path must not call xdg-open')
                fail = True
                n.write_json(state/'pending.json', {'version':'5.0', 'url':repository+'/releases/tag/v5.0'})
                self.assertNotEqual(run()[1], 0)
                self.assertNotIn('5.0', n.read_json(state/'notified.json', []))
                fail, action = False, 'close'
                env['LC_ALL'] = 'zh_CN.UTF-8'
                self.assertEqual(run()[1], 0)
                self.assertEqual(calls[-1][3], '有新版本可用')
                self.assertEqual(calls[-1][5][3], '查看更新说明')
        finally:
            for proc in browsers:
                proc.terminate()
                proc.wait(timeout=5)
            bus.unregister_object(portal_registration)
            bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                          'ReleaseName', GLib.Variant('(s)', (n.PORTAL,)), None,
                          Gio.DBusCallFlags.NONE, 2000, None)
            bus.unregister_object(registration)
            bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                          'ReleaseName', GLib.Variant('(s)', (n.BUS,)), None,
                          Gio.DBusCallFlags.NONE, 2000, None)


class OpenURI(unittest.TestCase):
    def test_only_missing_portal_falls_back_to_scope(self):
        for error_name in ('ServiceUnknown', 'UnknownMethod', 'AccessDenied', 'NoReply'):
            with self.subTest(error_name=error_name):
                bus = mock.Mock()
                bus.get_unique_name.return_value = ':1.42'
                bus.call_sync.side_effect = Gio.dbus_error_new_for_dbus_error(
                    'org.freedesktop.DBus.Error.' + error_name, 'test')
                with mock.patch.object(n.subprocess, 'run') as run:
                    if error_name in ('AccessDenied', 'NoReply'):
                        with self.assertRaises(GLib.Error):
                            n.open_uri(bus, 'https://example.invalid/', 'activation')
                        run.assert_not_called()
                    else:
                        n.open_uri(bus, 'https://example.invalid/', 'activation')
                        argv = run.call_args.args[0]
                        self.assertEqual(argv[:4], ['systemd-run', '--user', '--scope', '--collect'])
                        self.assertEqual(argv[-1], 'https://example.invalid/')
                        self.assertEqual(run.call_args.kwargs['env']['XDG_ACTIVATION_TOKEN'], 'activation')
                        self.assertTrue(run.call_args.kwargs['check'])
                bus.signal_unsubscribe.assert_called_once()

    def test_response_cancellation_and_failure_do_not_fall_back(self):
        for response in (0, 1, 2):
            with self.subTest(response=response):
                bus = mock.Mock()
                bus.get_unique_name.return_value = ':1.42'
                def reply(*args):
                    token = args[4].unpack()[2]['handle_token']
                    handle = n.PORTAL_OBJECT + '/request/1_42/' + token
                    callback = bus.signal_subscribe.call_args.args[-1]
                    callback(bus, n.PORTAL, handle, 'org.freedesktop.portal.Request',
                             'Response', GLib.Variant('(ua{sv})', (response, {})))
                    return GLib.Variant('(o)', (handle,))
                bus.call_sync.side_effect = reply
                with mock.patch.object(n.subprocess, 'run') as run:
                    if response == 2:
                        with self.assertRaises(ValueError):
                            n.open_uri(bus, 'https://example.invalid/')
                    else:
                        n.open_uri(bus, 'https://example.invalid/')
                    run.assert_not_called()
                bus.signal_unsubscribe.assert_called_once()


if __name__ == '__main__':
    unittest.main()
