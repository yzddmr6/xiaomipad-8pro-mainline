#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Event-driven P81C radio initialization; no touch processing or polling."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import struct
import time

FE11 = '0000fe11-aa6c-462a-964a-7f2ed5b3e512'
FE12 = '0000fe12-aa6c-462a-964a-7f2ed5b3e512'
DEVICE = 'org.bluez.Device1'
ADAPTER = 'org.bluez.Adapter1'
GATT = 'org.bluez.GattCharacteristic1'

def profile(path):
    data = json.loads(path.read_text())
    if data.get('version') != 1 or data.get('device') != 'piano':
        raise ValueError('unsupported pen radio profile')
    lengths = {'parameter_prefix': 1, 'timing': 7, 'first_id_cycle': 1,
               'second_id_cycle': 1, 'intervals': 12, 'parameter_tail': 2,
               'frequency': 4, 'frequency_interval': 2, 'voltage': 2}
    for key, size in lengths.items():
        data[key] = bytes.fromhex(data[key])
        if len(data[key]) != size:
            raise ValueError('invalid ' + key + ' size')
    if type(data['double_tap']) is not bool:
        raise ValueError('double_tap must be boolean')
    if type(data['squeeze_motor_level']) is not int or not 0 <= data['squeeze_motor_level'] <= 5:
        raise ValueError('invalid squeeze motor level')
    for key in ('squeeze_threshold_down', 'squeeze_threshold_up'):
        if type(data[key]) is not int or not (1 if key.endswith('down') else 0) <= data[key] <= 900:
            raise ValueError('invalid squeeze threshold')
    return data


def initialization(p, host, docked, active, now):
    """Recovered SunUEFI command sequence; identities arrive only at runtime."""
    if len(host) != 6:
        raise ValueError('invalid adapter identity')
    if active is True:
        yield b'\x61\x01\x01', b'\xe1\x00', 2
    for command, response, size in ((0x52, b'\xd2\x01', 3), (0, b'\x80\x01', 3),
                                     (1, b'\x81\x02', 4), (2, b'\x82\x02', 4),
                                     (3, b'\x83\x03', 5)):
        yield bytes([command, 0]), response, size
    for cycle in ('first_id_cycle', 'second_id_cycle'):
        reply = yield b'\x04\x00', b'\x84\x01', 3
        pen_id = reply[2:3]
        if pen_id == b'\xff':
            raise ValueError('pen has no free radio slot')
        parameters = (host + p['parameter_prefix'] + pen_id + p['timing'] +
                      p[cycle] + p['intervals'] + p['parameter_tail'])
        yield b'\x30' + bytes([len(parameters)]) + parameters, b'\xb0\x00', 2
        yield b'\x31\x08' + pen_id + b'\x03' + p['frequency'] + p['frequency_interval'], b'\xb1\x00', 2
        yield b'\x32\x03' + pen_id + p['voltage'], b'\xb2\x00', 2
    yield b'\x33\x04' + struct.pack('>I', now & 0xffffffff), b'\xb3\x00', 2
    yield b'\x5f\x01' + bytes([int(p['double_tap'])]), b'\xdf\x01\x01', 3
    yield b'\x5a\x01' + bytes([p['squeeze_motor_level']]), b'\xda\x01\x01', 3
    yield b'\x5c\x04' + struct.pack('>HH', p['squeeze_threshold_down'],
                                    p['squeeze_threshold_up']), b'\xdc\x01\x01', 3


class Session:
    """One outstanding command and one finite response deadline, no retries."""
    def __init__(self, p, host, write, arm, cancel, failed, now):
        self.write, self.arm, self.cancel, self.failed = write, arm, cancel, failed
        self.inputs = (p, host, now)
        self.program = None
        self.pending = None
        self.timer = None
        self.token = 0
        self.closed = False
        self.initialized = False
        self.desired = (None, None)
        self.applied = (None, None)

    def start(self):
        p, host, now = self.inputs
        self.program = initialization(p, host, *self.desired, now)
        self.advance(None)

    def close(self):
        if self.timer is not None:
            self.cancel(self.timer)
        self.timer = self.pending = self.program = None
        self.closed = True

    def fail(self):
        if not self.closed:
            self.close()
            self.failed()
        return False

    def advance(self, response):
        if self.closed:
            return
        try:
            payload, prefix, size = self.program.send(response)
        except StopIteration:
            self.program = None
            self.initialized = True
            self.follow()
            return
        except (ValueError, IndexError):
            self.fail()
            return
        self.token += 1
        token = self.token
        self.pending = (prefix, size)
        self.timer = self.arm(3000, self.fail)
        self.write(payload, lambda success: self.write_done(token, success))

    def write_done(self, token, success):
        if not success and token == self.token and self.pending is not None:
            self.fail()

    def notification(self, data):
        if self.closed or self.pending is None:
            return
        prefix, size = self.pending
        if not data.startswith(prefix) or len(data) != size:
            return
        self.cancel(self.timer)
        self.timer = self.pending = None
        self.advance(data)

    def update(self, docked, active):
        self.desired = (docked, active)
        self.follow()

    def follow(self):
        if self.closed or not self.initialized or self.program is not None:
            return
        desired = self.desired
        if not any(want is not None and want != old for want, old in zip(desired, self.applied)):
            return
        def commands():
            for index, (want, old) in enumerate(zip(desired, self.applied)):
                if want is None or want == old:
                    continue
                if index == 0:
                    yield b'\x51\x01' + bytes([int(not want)]), b'\xd1\x00', 2
                else:
                    yield b'\x61\x01' + bytes([int(want)]), b'\xe1\x00', 2
                applied = list(self.applied)
                applied[index] = want
                self.applied = tuple(applied)
        self.program = commands()
        self.advance(None)


def magnetic_address():
    try:
        root = Path('/sys/class/power_supply/sc96231-pen')
        if (root / 'present').read_text().strip() != '1':
            return None
        value = (root / 'serial_number').read_text().strip()
    except OSError:
        return None
    return value.upper() if re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', value) else None


def screen_on():
    for path in Path('/sys/class/drm').glob('card*-DSI-*/dpms'):
        try:
            value = path.read_text().strip()
        except OSError:
            continue
        if value in ('On', 'Standby', 'Suspend', 'Off'):
            return value == 'On'
    return None


def scan_allowed(docked, address, device, attempted):
    return docked is True and bool(address) and not device.get('Paired') and not attempted


class Controller:
    def __init__(self, p):
        from gi.repository import Gio, GLib
        import pyudev
        self.Gio, self.GLib, self.p = Gio, GLib, p
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.objects = {}
        self.generation = 0
        self.docked = None
        self.dock_verified = False
        self.active = screen_on()
        self.address = None
        self.dock_fd = None
        self.dock_watch = None
        self.scan_adapter = None
        self.scan_busy = self.scan_owned = self.scan_wanted = self.scanned = False
        self.scan_timer = None
        self.pair_attempted = self.connect_attempted = False
        self.agent_ready = self.agent_pending = False
        self.target = self.session_path = self.notify_path = None
        self.session = None
        self.failed_path = None
        self.stopping = self.sleeping = False
        self.agent_id = None
        self.loop = GLib.MainLoop()
        self.bus.signal_subscribe('org.bluez', 'org.freedesktop.DBus.ObjectManager', None,
            None, None, Gio.DBusSignalFlags.NONE, self.objects_changed)
        self.bus.signal_subscribe('org.bluez', 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
            None, None, Gio.DBusSignalFlags.NONE, self.properties_changed)
        self.bus.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
            '/org/freedesktop/DBus', 'org.bluez', Gio.DBusSignalFlags.NONE, self.owner_changed)
        self.bus.signal_subscribe('org.freedesktop.login1', 'org.freedesktop.login1.Manager',
            'PrepareForSleep', '/org/freedesktop/login1', None, Gio.DBusSignalFlags.NONE, self.sleep_changed)
        self.monitor = pyudev.Monitor.from_netlink(pyudev.Context())
        self.monitor.filter_by_tag('piano-pen')
        self.monitor.start()
        GLib.io_add_watch(self.monitor.fileno(), GLib.IO_IN, self.udev_changed)
        self.open_dock()
        self.refresh_address()
        self.query()

    def call(self, path, interface, method, args=None, done=None, timeout=5000):
        generation = self.generation
        def completed(connection, result, _data=None):
            try:
                reply = connection.call_finish(result)
                success = True
            except self.GLib.Error:
                reply, success = None, False
            if done and generation == self.generation and not self.stopping:
                done(reply, success)
        self.bus.call('org.bluez', path, interface, method, args, None,
            self.Gio.DBusCallFlags.NO_AUTO_START, timeout, None, completed)

    def query(self):
        def done(reply, success):
            if success:
                self.objects = reply.unpack()[0]
                self.reconcile()
        self.call('/', 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects', done=done)

    def owner_changed(self, _bus, _sender, _path, _iface, _member, args, _data=None):
        self.generation += 1
        self.close_session()
        self.stop_scan()
        self.objects.clear()
        self.scan_owned = self.scan_busy = False
        self.agent_ready = self.agent_pending = False
        self.failed_path = None
        if args.unpack()[2]:
            self.query()

    def objects_changed(self, _bus, _sender, _path, _iface, member, args, _data=None):
        path, interfaces = args.unpack()
        if member == 'InterfacesAdded':
            self.objects.setdefault(path, {}).update(interfaces)
        elif member == 'InterfacesRemoved':
            for interface in interfaces:
                self.objects.get(path, {}).pop(interface, None)
        self.reconcile()

    def properties_changed(self, _bus, _sender, path, _iface, _member, args, _data=None):
        interface, changed, invalid = args.unpack()
        properties = self.objects.setdefault(path, {}).setdefault(interface, {})
        properties.update(changed)
        for key in invalid:
            properties.pop(key, None)
        if interface == GATT and path == self.notify_path and self.session and 'Value' in changed:
            self.session.notification(bytes(changed['Value']))
        elif interface in (DEVICE, ADAPTER):
            if interface == DEVICE and changed.get('Connected') is False:
                self.failed_path = None
            self.reconcile()

    def open_dock(self):
        if self.dock_fd is not None:
            return
        for path in Path('/sys/class/input').glob('event*'):
            try:
                if (path / 'device/name').read_text().strip() != 'Xiaomi Piano Pen Dock':
                    continue
                self.dock_fd = os.open('/dev/input/' + path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                bits = bytearray(8)
                fcntl.ioctl(self.dock_fd, 0x8008451b, bits, True)
                self.set_dock(bool(int.from_bytes(bits, 'little') & (1 << 15)))
                self.dock_watch = self.GLib.io_add_watch(self.dock_fd,
                    self.GLib.IO_IN | self.GLib.IO_HUP | self.GLib.IO_ERR, self.dock_events)
                return
            except OSError:
                if self.dock_fd is not None:
                    os.close(self.dock_fd)
                    self.dock_fd = None

    def refresh_address(self):
        if self.docked is True:
            address = magnetic_address()
            self.dock_verified = address is not None
            if address is not None:
                if address != self.address:
                    self.scanned = self.pair_attempted = self.connect_attempted = False
                    self.failed_path = None
                self.address = address

    def set_dock(self, value):
        if value != self.docked:
            self.docked = value
            self.dock_verified = False
            self.scanned = self.pair_attempted = self.connect_attempted = False
            self.failed_path = None
        if value is not True:
            self.stop_scan()

    def dock_events(self, fd, condition):
        event = struct.Struct('llHHi')
        try:
            data = os.read(fd, event.size * 32)
            if not data:
                raise OSError()
            for offset in range(0, len(data), event.size):
                _, _, kind, code, value = event.unpack_from(data, offset)
                if kind == 5 and code == 15:
                    self.set_dock(bool(value))
                elif kind == 0 and code == 3:  # SYN_DROPPED
                    bits = bytearray(8)
                    fcntl.ioctl(fd, 0x8008451b, bits, True)
                    self.set_dock(bool(int.from_bytes(bits, 'little') & (1 << 15)))
        except BlockingIOError:
            return True
        except (OSError, struct.error):
            os.close(fd)
            self.dock_fd = self.dock_watch = None
            self.set_dock(None)
            self.reconcile()
            return False
        self.refresh_address()
        self.reconcile()
        return True

    def udev_changed(self, _fd, condition):
        if condition & (self.GLib.IO_HUP | self.GLib.IO_ERR | self.GLib.IO_NVAL):
            self.set_dock(None)
            self.close_session()
            return False
        while True:
            device = self.monitor.poll(timeout=0)
            if device is None:
                break
            if device.subsystem == 'input':
                self.open_dock()
            if device.subsystem == 'power_supply':
                self.refresh_address()
            active = device.properties.get('PIANO_TOUCH_ACTIVE')
            if active in ('0', '1'):
                self.active = active == '1'
        self.reconcile()
        return True

    def sleep_changed(self, _bus, _sender, _path, _iface, _member, args, _data=None):
        self.sleeping = bool(args.unpack()[0])
        self.active = False if self.sleeping else screen_on()
        if self.sleeping:
            self.stop_scan()
        self.reconcile()

    def start_scan(self, adapter):
        if self.scan_busy or self.scan_owned:
            return
        self.scanned = self.scan_wanted = self.scan_busy = True
        self.scan_adapter = adapter
        def started(_reply, success):
            self.scan_busy = False
            self.scan_owned = success
            if success:
                self.scan_timer = self.GLib.timeout_add_seconds(30, self.stop_scan)
                if not self.scan_wanted:
                    self.stop_scan()
        def filtered(_reply, success):
            if not success or not self.scan_wanted:
                self.scan_busy = False
                return
            self.call(adapter, ADAPTER, 'StartDiscovery', done=started)
        self.call(adapter, ADAPTER, 'SetDiscoveryFilter', self.GLib.Variant('(a{sv})', ({
            'Transport': self.GLib.Variant('s', 'le'),
            'DuplicateData': self.GLib.Variant('b', False),
        },)), done=filtered)

    def stop_scan(self):
        self.scan_wanted = False
        if self.scan_timer is not None:
            self.GLib.source_remove(self.scan_timer)
            self.scan_timer = None
        if self.scan_owned and not self.scan_busy:
            self.scan_busy = True
            def stopped(_reply, _success):
                self.scan_owned = self.scan_busy = False
                self.scan_adapter = None
            # BlueZ discovery ownership is per D-Bus connection.
            self.call(self.scan_adapter, ADAPTER, 'StopDiscovery', done=stopped)
        return False

    def prepare_agent(self):
        if self.agent_pending or self.agent_ready:
            return
        self.agent_pending = True
        xml = '<node><interface name="org.bluez.Agent1">' + ''.join(
            f'<method name="{name}">{arguments}</method>' for name, arguments in (
                ('Release', ''), ('Cancel', ''),
                ('RequestAuthorization', '<arg type="o" direction="in"/>'),
                ('RequestConfirmation', '<arg type="o" direction="in"/><arg type="u" direction="in"/>'),
                ('AuthorizeService', '<arg type="o" direction="in"/><arg type="s" direction="in"/>'),
            )) + '</interface></node>'
        node = self.Gio.DBusNodeInfo.new_for_xml(xml)
        if self.agent_id is None:
            self.agent_id = self.bus.register_object('/org/piano/PenPairing', node.interfaces[0], self.agent_call)
        def ready(_reply, success):
            self.agent_ready = success
            # A failed registration is not retried until BlueZ restarts.
            if success:
                self.reconcile()
        self.call('/org/bluez', 'org.bluez.AgentManager1', 'RegisterAgent',
                  self.GLib.Variant('(os)', ('/org/piano/PenPairing', 'NoInputNoOutput')), done=ready)

    def agent_call(self, _bus, sender, _path, _iface, method, args, invocation):
        values = args.unpack()
        allowed = method in ('Release', 'Cancel') or (
            self.docked is True and self.dock_verified and values and values[0] == self.target and
            self.objects.get(self.target, {}).get(DEVICE, {}).get('Address', '').upper() == self.address)
        if allowed:
            invocation.return_value(None)
        else:
            invocation.return_dbus_error('org.bluez.Error.Rejected', 'No matching docked pen')

    def close_session(self):
        if self.session:
            self.session.close()
        if self.notify_path:
            self.call(self.notify_path, GATT, 'StopNotify')
        self.session = self.session_path = self.notify_path = None

    def start_session(self, path, device):
        chars = {items[GATT].get('UUID', '').lower(): name for name, items in self.objects.items()
                 if name.startswith(path + '/') and GATT in items}
        if FE11 not in chars or FE12 not in chars:
            return
        adapter = self.objects.get(device.get('Adapter'), {}).get(ADAPTER, {})
        try:
            host = bytes.fromhex(adapter['Address'].replace(':', ''))
        except (KeyError, ValueError):
            return
        self.session_path, self.notify_path = path, chars[FE12]
        def failed():
            print('pen-radio: initialization failed; waiting for a new connection or dock event', flush=True)
            self.failed_path = path
            self.close_session()
        def write(payload, complete):
            self.call(chars[FE11], GATT, 'WriteValue', self.GLib.Variant('(aya{sv})',
                (payload, {'type': self.GLib.Variant('s', 'command')})),
                done=lambda _reply, success: complete(success))
        session = Session(self.p, host, write, self.GLib.timeout_add, self.GLib.source_remove,
                          failed, int(time.time()))
        self.session = session
        session.update(self.docked, self.active)
        def notified(_reply, success):
            if self.session is not session:
                if success:
                    self.call(chars[FE12], GATT, 'StopNotify')
                return
            if success:
                session.start()
            else:
                session.fail()
        self.call(self.notify_path, GATT, 'StartNotify', done=notified)

    def reconcile(self):
        if self.stopping:
            return
        candidates = [(path, items[DEVICE]) for path, items in self.objects.items() if DEVICE in items
                      and items[DEVICE].get('Address', '').upper() == self.address]
        self.target, device = candidates[0] if len(candidates) == 1 else (None, {})
        connected = device.get('Connected') and device.get('ServicesResolved')
        if self.session_path and (self.session_path != self.target or not connected):
            self.close_session()
        if self.sleeping:
            if self.session:
                self.session.update(self.docked, False)
            return
        if connected:
            self.stop_scan()
            if re.fullmatch(r'bluetooth:v0022p5081d[0-9a-fA-F]{4}', device.get('Modalias', ''), re.I):
                if not self.session_path and self.failed_path != self.target:
                    self.start_session(self.target, device)
                if self.session:
                    self.session.update(self.docked, self.active)
            return
        if self.docked is not True or not self.address or not self.dock_verified:
            return
        if self.target and not device.get('Paired') and not self.pair_attempted:
            if not self.agent_ready:
                self.prepare_agent()
            else:
                self.pair_attempted = True
                self.call(self.target, DEVICE, 'Pair', timeout=35000)
        elif self.target and device.get('Paired') and not self.connect_attempted:
            self.connect_attempted = True
            self.call(self.target, DEVICE, 'Connect', timeout=30000)
        if scan_allowed(self.docked, self.address, device, self.scanned):
            adapters = [path for path, items in self.objects.items() if items.get(ADAPTER, {}).get('Powered')]
            if adapters:
                self.start_scan(sorted(adapters)[0])
        elif device.get('Paired'):
            self.stop_scan()

    def stop(self):
        self.close_session()
        self.stop_scan()
        self.stopping = True
        self.loop.quit()
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    args = parser.parse_args()
    app = Controller(profile(args.profile))
    app.GLib.unix_signal_add(app.GLib.PRIORITY_DEFAULT, signal.SIGTERM, app.stop)
    app.GLib.unix_signal_add(app.GLib.PRIORITY_DEFAULT, signal.SIGINT, app.stop)
    app.loop.run()


if __name__ == '__main__':
    main()
