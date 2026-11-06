#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Session call/window/Notify* logic adapted from onepluspad-2pro-mainline:
# tools/gpu-app-coverage-probe.py, 43d38623312ee4d2edc4d2660c06ae0c25bcd5cf
# tools/gpu-desktop-probe.py, cdb808739d7f7048234fe4a306bc72d2b9ac60e2
# Original author: Erwin. See NOTICE for provenance and adaptation details.
"""Drive the logged-in user's GNOME Wayland session (run as that user, not root).

Requires python3-gi, libxkbcommon, GNOME Shell/Mutter, xdg-desktop-portal and its
GNOME backend. Coordinates are global logical pixels on the primary monitor:
3200x2136 at 200% means 1600x1068, so its centre is (800, 534).
`tap`/`swipe` use touch; --pointer uses a left mouse click/drag instead.
Numeric `key` arguments are Linux evdev keycodes (28 = Return, no X11 +8).
Named arguments are XKB keysyms (Return, Escape, BackSpace, a, ...).

shot grants screenshot permission to the non-sandboxed app id ''. windows and
wait-window enable org.gnome.shell introspect. These user settings persist;
previous values are reported on stderr. See the desktop-drive research note
for restoration. No Shell Eval, borrowed bus names, uinput or new compositor.
"""
import argparse
from contextlib import contextmanager
import ctypes
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit
import uuid

RD = 'org.gnome.Mutter.RemoteDesktop'
SC = 'org.gnome.Mutter.ScreenCast'
PROPS = 'org.freedesktop.DBus.Properties'
PORTAL = 'org.freedesktop.portal.Desktop'
PORTAL_PATH = '/org/freedesktop/portal/desktop'
PERMISSIONS = 'org.freedesktop.impl.portal.PermissionStore'
PERMISSIONS_PATH = '/org/freedesktop/impl/portal/PermissionStore'


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError('must be finite')
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--allow-locked', action='store_true',
                        help='allow key/tap input while locked; does not unlock the session')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('shot', help='portal screenshot, no interactive chooser').add_argument('out', type=Path)
    sub.add_parser('windows', help='window id -> properties, JSON')
    for name in ('tap', 'swipe'):
        p = sub.add_parser(name)
        p.add_argument('--pointer', action='store_true', help='left pointer click/drag instead of touch')
        for coord in (('x', 'y') if name == 'tap' else ('x1', 'y1', 'x2', 'y2')):
            p.add_argument(coord, type=finite)
        if name == 'swipe':
            p.add_argument('ms', type=int, nargs='?', default=400)
    sub.add_parser('key').add_argument('key')
    sub.add_parser('type').add_argument('text')
    sub.add_parser('launch').add_argument('desktop_id')
    p = sub.add_parser('wait-window')
    p.add_argument('title_regex')
    p.add_argument('timeout', type=finite, nargs='?', default=15.0)
    args = parser.parse_args(argv)
    if args.allow_locked and args.command not in ('key', 'tap'):
        parser.error('--allow-locked is only supported for key/tap')
    if args.command == 'swipe' and not 1 <= args.ms <= 60000:
        parser.error('swipe duration must be 1..60000 ms')
    if args.command == 'wait-window':
        if not 0 <= args.timeout <= 300:
            parser.error('timeout must be 0..300 seconds')
        try:
            re.compile(args.title_regex)
        except re.error as error:
            parser.error(f'invalid title regex: {error}')
    if args.command == 'launch' and (not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', args.desktop_id)):
        parser.error('expected a desktop ID, not a path or command')
    return args


def logical_to_stream(x, y, position, size, scale):
    """Mutter monitor streams divide incoming pixel coordinates by stage scale."""
    if not all(math.isfinite(v) for v in (x, y, *position, *size, scale)) or scale <= 0:
        raise ValueError('invalid coordinate geometry')
    local_x, local_y = x - position[0], y - position[1]
    if not (0 <= local_x < size[0] and 0 <= local_y < size[1]):
        raise ValueError(f'({x}, {y}) is outside primary monitor: position={position}, logical size={size}')
    return local_x * scale, local_y * scale


def key_event(value):
    if re.fullmatch(r'(?:[0-9]+|0x[0-9a-fA-F]+)', value):
        code = int(value, 16 if value.startswith('0x') else 10)
        if not 1 <= code <= 0x2ff:
            raise ValueError('evdev keycode must be 1..767')
        return 'NotifyKeyboardKeycode', code
    xkb = ctypes.CDLL('libxkbcommon.so.0')
    xkb.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_int]
    xkb.xkb_keysym_from_name.restype = ctypes.c_uint32
    code = xkb.xkb_keysym_from_name(value.encode(), 0)
    if not code:
        raise ValueError(f'unknown XKB keysym: {value!r}')
    return 'NotifyKeyboardKeysym', code


def text_keysym(char):
    special = {'\n': 0xff0d, '\r': 0xff0d, '\t': 0xff09, '\b': 0xff08}
    if char in special:
        return special[char]
    code = ord(char)
    if code < 32 or 0x7f <= code < 0xa0 or 0xd800 <= code <= 0xdfff:
        raise ValueError(f'unsupported control/surrogate U+{code:04X}')
    return code if code <= 0xff else 0x01000000 | code


def session_environment():
    uid = os.getuid()
    if uid == 0 or os.geteuid() != uid:
        raise RuntimeError('run as the logged-in desktop user, not root or setuid')
    runtime = Path(f'/run/user/{uid}')
    if runtime.stat().st_uid != uid or not stat.S_ISSOCK((runtime / 'bus').stat().st_mode):
        raise RuntimeError(f'no user session bus at {runtime}/bus; log into GNOME first')
    os.environ.update(XDG_RUNTIME_DIR=str(runtime), DBUS_SESSION_BUS_ADDRESS=f'unix:path={runtime}/bus')
    return runtime


class Desktop:
    def __init__(self, allow_locked=False):
        self.runtime = session_environment()
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owned = self.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                          'NameHasOwner', ('(s)', ('org.gnome.Shell',)), '(b)')[0]
        if not owned:
            raise RuntimeError('GNOME Shell is not running on this user bus')
        if not allow_locked and self.call('org.gnome.ScreenSaver', '/org/gnome/ScreenSaver',
                     'org.gnome.ScreenSaver', 'GetActive', reply='(b)')[0]:
            raise RuntimeError('session is locked; desktop-drive does not unlock it')

    def call(self, dest, path, iface, method, params=None, reply=None, timeout=10000):
        variant = self.GLib.Variant(*params) if params else None
        return self.bus.call_sync(dest, path, iface, method, variant,
                                  self.GLib.VariantType.new(reply) if reply else None,
                                  self.Gio.DBusCallFlags.NONE, timeout, None).unpack()

    def prop(self, dest, path, iface, name):
        return self.call(dest, path, PROPS, 'Get', ('(ss)', (iface, name)), '(v)')[0]

    def enable_introspect(self):
        schema = self.Gio.SettingsSchemaSource.get_default().lookup('org.gnome.shell', True)
        if schema is None or not schema.has_key('introspect'):
            raise RuntimeError('this GNOME Shell has no org.gnome.shell introspect setting; '
                               'GetWindows requires an allowlisted portal caller')
        settings = self.Gio.Settings.new('org.gnome.shell')
        previous = settings.get_user_value('introspect')
        print(json.dumps({'introspect_previous_user_value': None if previous is None else previous.unpack()}),
              file=sys.stderr)
        if not settings.set_boolean('introspect', True):
            raise RuntimeError('org.gnome.shell introspect is not writable')
        self.Gio.Settings.sync()

    def windows(self):
        return self.call('org.gnome.Shell.Introspect', '/org/gnome/Shell/Introspect',
                         'org.gnome.Shell.Introspect', 'GetWindows', reply='(a{ta{sv}})')[0]

    def shot(self, output):
        output = output.expanduser().absolute()
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            previous = self.call(PERMISSIONS, PERMISSIONS_PATH, PERMISSIONS, 'GetPermission',
                                 ('(sss)', ('screenshot', 'screenshot', '')), '(as)')[0]
        except self.GLib.Error as error:
            if self.Gio.DBusError.get_remote_error(error) != 'org.freedesktop.portal.Error.NotFound':
                raise
            previous = None
        print(json.dumps({'screenshot_previous_permission': previous}), file=sys.stderr)
        self.call(PERMISSIONS, PERMISSIONS_PATH, PERMISSIONS, 'SetPermission',
                  ('(sbssas)', ('screenshot', True, 'screenshot', '', ['yes'])))
        token = 'piano_' + uuid.uuid4().hex
        sender = self.bus.get_unique_name()[1:].replace('.', '_')
        request = PORTAL_PATH + '/request/' + sender + '/' + token
        result = {}
        loop = self.GLib.MainLoop()

        def response(_bus, _sender, _path, _iface, _signal, params):
            result['code'], result['data'] = params.unpack()
            loop.quit()

        subscription = self.bus.signal_subscribe(PORTAL, 'org.freedesktop.portal.Request',
            'Response', request, None, self.Gio.DBusSignalFlags.NONE, response)
        timer = None
        try:
            returned = self.call(PORTAL, PORTAL_PATH, 'org.freedesktop.portal.Screenshot', 'Screenshot',
                ('(sa{sv})', ('', {'interactive': self.GLib.Variant('b', False),
                                  'handle_token': self.GLib.Variant('s', token)})), '(o)')[0]
            if returned != request:
                raise RuntimeError('portal returned an unexpected request handle')
            def expired():
                loop.quit()
                return True  # removed in finally, including on timeout
            timer = self.GLib.timeout_add_seconds(20, expired)
            if not result:
                loop.run()
            if not result:
                raise TimeoutError('screenshot portal did not respond within 20 seconds')
            if result['code'] != 0:
                raise RuntimeError(f'screenshot portal response {result["code"]}: {result["data"]}')
            uri = urlsplit(result['data']['uri'])
            if uri.scheme != 'file' or uri.netloc not in ('', 'localhost'):
                raise RuntimeError('screenshot portal did not return a local file URI')
            source = Path(unquote(uri.path))
            if source.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
                raise RuntimeError('portal screenshot is not a PNG')
            if source != output:
                shutil.copyfile(source, output)
                # The portal saves every screenshot into the user's Pictures
                # folder as well; remove that copy so a check leaves no files.
                source.unlink()
            return {'path': str(output), 'portal_response': result['code']}
        finally:
            if not result:
                try:
                    self.call(PORTAL, request, 'org.freedesktop.portal.Request', 'Close', timeout=2000)
                except self.GLib.Error:
                    pass
            if timer is not None:
                self.GLib.source_remove(timer)
            self.bus.signal_unsubscribe(subscription)

    @contextmanager
    def input(self, coordinates=False):
        rd_path = self.call(RD, '/org/gnome/Mutter/RemoteDesktop', RD, 'CreateSession', reply='(o)')[0]
        try:
            geometry = None
            stream = None
            if coordinates:
                dc = 'org.gnome.Mutter.DisplayConfig'
                state = self.call(dc, '/org/gnome/Mutter/DisplayConfig', dc, 'GetCurrentState')
                primaries = [m for m in state[2] if m[4]]
                if len(primaries) != 1:
                    raise RuntimeError('expected one primary logical monitor')
                monitor = primaries[0]
                # Mutter scales stage views in logical layout mode (1).
                layout = state[3].get('layout-mode')
                if layout not in (1, 2):
                    raise RuntimeError('Mutter did not report a supported monitor layout mode')
                scale = monitor[2] if layout == 1 else 1.0
                session_id = self.prop(RD, rd_path, RD + '.Session', 'SessionId')
                sc_path = self.call(SC, '/org/gnome/Mutter/ScreenCast', SC, 'CreateSession',
                    ('(a{sv})', ({'remote-desktop-session-id': self.GLib.Variant('s', session_id)},)), '(o)')[0]
                stream = self.call(SC, sc_path, SC + '.Session', 'RecordMonitor',
                                   ('(sa{sv})', (monitor[5][0][0], {})), '(o)')[0]
                params = self.prop(SC, stream, SC + '.Stream', 'Parameters')
                geometry = (params['position'], params['size'], scale)
            self.rd(rd_path, 'Start')
            yield rd_path, stream, geometry
        finally:
            self.rd(rd_path, 'Stop')  # also closes the linked ScreenCast and its stream

    def rd(self, path, method, params=None):
        return self.call(RD, path, RD + '.Session', method, params, '()')

    def gesture(self, points, milliseconds, pointer=False):
        with self.input(coordinates=True) as (path, stream, geometry):
            points = [logical_to_stream(x, y, *geometry) for x, y in points]
            x, y = points[0]
            if pointer:
                self.rd(path, 'NotifyPointerMotionAbsolute', ('(sdd)', (stream, x, y)))
                self.rd(path, 'NotifyPointerButton', ('(ib)', (0x110, True)))
            else:
                self.rd(path, 'NotifyTouchDown', ('(sudd)', (stream, 0, x, y)))
            try:
                if len(points) == 1:
                    time.sleep(0.05)
                else:
                    steps = max(1, math.ceil(milliseconds / 16))
                    start = time.monotonic()
                    for step in range(1, steps + 1):
                        time.sleep(max(0, start + milliseconds / 1000 * step / steps - time.monotonic()))
                        end_x, end_y = points[-1]
                        px, py = x + (end_x - x) * step / steps, y + (end_y - y) * step / steps
                        if pointer:
                            self.rd(path, 'NotifyPointerMotionAbsolute', ('(sdd)', (stream, px, py)))
                        else:
                            self.rd(path, 'NotifyTouchMotion', ('(sudd)', (stream, 0, px, py)))
            finally:
                if pointer:
                    self.rd(path, 'NotifyPointerButton', ('(ib)', (0x110, False)))
                else:
                    self.rd(path, 'NotifyTouchUp', ('(u)', (0,)))
                time.sleep(0.03)

    def keyboard(self, events):
        with self.input() as (path, _stream, _geometry):
            for method, code in events:
                try:
                    self.rd(path, method, ('(ub)', (code, True)))
                    time.sleep(0.03)  # sibling's press/release pacing
                finally:
                    self.rd(path, method, ('(ub)', (code, False)))
                time.sleep(0.03)

    def launch(self, desktop_id):
        env = os.environ.copy()
        # SSH does not normally carry WAYLAND_DISPLAY. Use the desktop's user
        # manager environment; isolated offline sessions may have no manager.
        try:
            values = self.prop('org.freedesktop.systemd1', '/org/freedesktop/systemd1',
                               'org.freedesktop.systemd1.Manager', 'Environment')
        except self.GLib.Error:
            values = []
        env.pop('DISPLAY', None)
        env.pop('WAYLAND_DISPLAY', None)
        for entry in values:
            key, _, value = entry.partition('=')
            if key in ('WAYLAND_DISPLAY', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE', 'XDG_DATA_DIRS'):
                env[key] = value
        if not env.get('WAYLAND_DISPLAY'):
            sockets = [p for p in self.runtime.glob('wayland-*') if stat.S_ISSOCK(p.stat().st_mode)]
            if len(sockets) != 1:
                raise RuntimeError('cannot identify the desktop Wayland socket')
            env['WAYLAND_DISPLAY'] = sockets[0].name
        env['XDG_SESSION_TYPE'] = 'wayland'
        env.setdefault('XDG_CURRENT_DESKTOP', 'GNOME')
        log = self.runtime / 'piano-desktop-drive-launch.log'
        # A non-D-Bus-activatable app may inherit the launcher's descriptors.
        # Do not leave it holding SSH's stdout/stderr pipes open.
        with log.open('ab') as stream:
            subprocess.run(['gtk-launch', desktop_id], env=env, check=True, timeout=15,
                           stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                           start_new_session=True)
        return {'launched': desktop_id, 'log': str(log)}


def main(argv=None):
    args = parse_args(argv)
    # Validate the whole keyboard payload before sending the first event.
    events = ([key_event(args.key)] if args.command == 'key' else
              [('NotifyKeyboardKeysym', text_keysym(c)) for c in args.text] if args.command == 'type' else None)
    desktop = Desktop(allow_locked=args.allow_locked)
    result = {'command': args.command}
    if args.command == 'shot':
        result = desktop.shot(args.out)
    elif args.command in ('windows', 'wait-window'):
        desktop.enable_introspect()
        if args.command == 'windows':
            result = desktop.windows()
        else:
            deadline = time.monotonic() + args.timeout
            while True:
                result = {k: v for k, v in desktop.windows().items()
                          if re.search(args.title_regex, v.get('title', ''))}
                if result:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'no window matching {args.title_regex!r} within {args.timeout:g}s')
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    elif args.command == 'tap':
        desktop.gesture([(args.x, args.y)], 0, args.pointer)
    elif args.command == 'swipe':
        desktop.gesture([(args.x1, args.y1), (args.x2, args.y2)], args.ms, args.pointer)
    elif events is not None:
        desktop.keyboard(events)
    elif args.command == 'launch':
        result = desktop.launch(args.desktop_id)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'piano-desktop-drive: {error}', file=sys.stderr)
        sys.exit(1)
