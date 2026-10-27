#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Initialize a connected Piano Focus Pen Pro through its original BLE protocol.

The supplied board profile contains radio parameters, never pairing keys or
device identities. This client does not read THP frames or generate input.

The pen in the dock is identified over the magnetic charging link, so the
client never pairs an unrelated nearby device. It owns one agent for that
pen and never stops the discovery session of another client.
"""
import argparse
import ctypes
import fcntl
import json
import os
import re
from pathlib import Path
import signal
import socket
import struct
import time

FE11 = '0000fe11-aa6c-462a-964a-7f2ed5b3e512'
FE12 = '0000fe12-aa6c-462a-964a-7f2ed5b3e512'

AGENT_XML = """
<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="Cancel"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
  <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>
"""


def read_dock():
    events = []
    for event in Path('/sys/class/input').glob('event*'):
        try:
            if (event / 'device/name').read_text().strip() == 'Xiaomi Piano Pen Dock':
                events.append(event)
        except FileNotFoundError:
            continue
    if len(events) != 1:
        raise RuntimeError('Piano pen-dock switch is unavailable')
    fd = os.open('/dev/input/' + events[0].name,
                 os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        bits = bytearray(8)
        fcntl.ioctl(fd, 0x8008451b, bits, True)  # EVIOCGSW(8)
        return bool(int.from_bytes(bits, 'little') & (1 << 15))
    finally:
        os.close(fd)


def dock_address():
    """Identity learned by the charger over the magnetic charging link."""
    try:
        address = Path('/sys/class/power_supply/sc96231-pen/serial_number').read_text().strip()
    except OSError:
        return None
    return address.upper() if re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', address) else None


def screen_on():
    """Use the internal connector's real DRM power state."""
    for node in Path('/sys/class/drm').glob('card*-DSI-*/dpms'):
        try:
            value = node.read_text().strip()
        except OSError:
            continue
        if value in ('On', 'Standby', 'Suspend', 'Off'):
            return value == 'On'
    return None


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


class LeScan:
    """Bound discovery while disconnected and reconnect an already paired pen.

    Discovery only finds an advertising radio; it does not wake the pen.
    Own one BlueZ discovery session and never stop another client's session.
    """

    def __init__(self):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.adapter = None
        self.until = 0
        self.retry_at = 0
        self.connect_at = {}
        self.connecting = set()
        self.last_dock = None
        self.last_error = None
        self.activity = False
        self.address = None
        self.repair = set()
        self.repaired = set()
        self.pairing = None
        self.pair_retry = 0
        self.agent = None
        self.agent_path = '/org/piano/PenPairing'
        self.auth_monitor = None
        self.auth_watch = None
        # Older BlueZ versions collapse a missing LE key into Error.Failed
        # and lack Device1.Disconnected's typed authentication reason.
        # The kernel management API reports MGMT_EV_AUTH_FAILED directly.
        try:
            self.auth_monitor = socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_RAW | socket.SOCK_CLOEXEC,
                socket.BTPROTO_HCI)
            # Python's HCI bind tuple does not expose hci_channel.
            endpoint = struct.pack('HHH', socket.AF_BLUETOOTH, 0xffff, 3)
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.bind(self.auth_monitor.fileno(), endpoint, len(endpoint)):
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            self.auth_monitor.setblocking(False)
            self.auth_watch = GLib.io_add_watch(
                self.auth_monitor.fileno(), GLib.IO_IN, self.auth_failed)
        except OSError:
            if self.auth_monitor is not None:
                self.auth_monitor.close()
            self.auth_monitor = None
        self.signals = [
            self.bus.signal_subscribe(
                'org.bluez', 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                None, 'org.bluez.Device1', self.Gio.DBusSignalFlags.NONE,
                self.device_changed, None),
            self.bus.signal_subscribe(
                'org.bluez', 'org.freedesktop.DBus.ObjectManager', 'InterfacesAdded',
                '/', None, self.Gio.DBusSignalFlags.NONE, self.interfaces_added, None),
            self.bus.signal_subscribe(
                'org.bluez', 'org.bluez.Device1', 'Disconnected',
                None, None, self.Gio.DBusSignalFlags.NONE, self.disconnected, None),
        ]

    def auth_failed(self, _fd, _condition):
        while True:
            try:
                packet = self.auth_monitor.recv(4096)
            except BlockingIOError:
                break
            if len(packet) < 14:
                continue
            event, index, length = struct.unpack_from('<HHH', packet)
            if length != 8:
                continue
            # Some kernels deliver only DEVICE_DISCONNECTED reason 4 after
            # encryption fails; both events explicitly identify authentication.
            if not ((event == 0x0011 and packet[13] == 0x05) or
                    (event == 0x000c and packet[13] == 0x04)):
                continue
            address = ':'.join(f'{byte:02X}' for byte in packet[6:12][::-1])
            # Only repair a genuine key/authentication failure for the pen
            # independently identified over the magnetic charging link.
            if address != dock_address():
                continue
            path = f'/org/bluez/hci{index}/dev_' + address.replace(':', '_')
            self.repair.add(path)
            self.activity = True
        return True

    def disconnected(self, _bus, _sender, path, _interface, _member, args, _data):
        reason, _ = args.unpack()
        if reason == 'org.bluez.Reason.Authentication':
            self.repair.add(path)
            self.activity = True

    def agent_call(self, _bus, _sender, _path, _iface, method, args, invocation):
        values = args.unpack()
        if method in ('Release', 'Cancel'):
            invocation.return_value(None)
        elif (values and values[0] == self.pairing and
              method in ('RequestAuthorization', 'RequestConfirmation',
                         'AuthorizeService', 'DisplayPinCode', 'DisplayPasskey')):
            invocation.return_value(None)
        else:
            invocation.return_dbus_error('org.bluez.Error.Rejected',
                                         'This agent handles only the magnetically identified pen')

    def prepare_agent(self):
        if self.agent is None:
            info = self.Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
            self.agent = self.bus.register_object(self.agent_path, info, self.agent_call, None, None)
        try:
            self.bus.call_sync('org.bluez', '/org/bluez', 'org.bluez.AgentManager1',
                               'RegisterAgent', self.GLib.Variant('(os)',
                               (self.agent_path, 'NoInputNoOutput')), None,
                               self.Gio.DBusCallFlags.NONE, 5000, None)
        except self.GLib.Error as error:
            if self.Gio.DBusError.get_remote_error(error) != 'org.bluez.Error.AlreadyExists':
                raise

    def pair_done(self, connection, result, path):
        self.pairing = None
        try:
            connection.call_finish(result)
            # The retry guard belongs to a failed bond, not this process's
            # lifetime. A later key mismatch must be recoverable too.
            self.repair.discard(path)
            self.repaired.discard(path)
            self.pair_retry = 0
            # Trust only after Pair has completed, and only this physical pen.
            connection.call_sync('org.bluez', path, 'org.freedesktop.DBus.Properties',
                'Set', self.GLib.Variant('(ssv)', ('org.bluez.Device1', 'Trusted',
                                                 self.GLib.Variant('b', True))),
                None, self.Gio.DBusCallFlags.NONE, 5000, None)
            self.connect_at.pop(path, None)
            print(json.dumps({'kind': 'pen_pairing_restored'}), flush=True)
        except self.GLib.Error as error:
            self.error(error)
        self.activity = True

    def device_changed(self, _bus, _sender, _path, _interface, _member, args, _data):
        _, fields, _ = args.unpack()
        if any(name in fields for name in ('Connected', 'ServicesResolved', 'Paired', 'Trusted')):
            self.activity = True

    def interfaces_added(self, _bus, _sender, _path, _interface, _member, args, _data):
        _, interfaces = args.unpack()
        if any(name in interfaces for name in ('org.bluez.Device1', 'org.bluez.GattCharacteristic1')):
            self.activity = True

    def wait(self, stopped):
        """Wake for BlueZ readiness; two seconds is only the fallback retry."""
        context = self.GLib.MainContext.default()
        until = time.monotonic() + 2
        while not stopped() and time.monotonic() < until:
            while context.pending():
                context.iteration(False)
            if self.activity:
                self.activity = False
                return
            time.sleep(.02)

    def connect_done(self, connection, result, path):
        self.connecting.discard(path)
        try:
            connection.call_finish(result)
        except self.GLib.Error as error:
            if self.Gio.DBusError.get_remote_error(error) in (
                    'org.bluez.Error.AuthenticationFailed',
                    'org.bluez.Error.AuthenticationRejected'):
                self.repair.add(path)
            # Another BlueZ client may already be establishing this link.
            if not self.Gio.DBusError.is_remote_error(error) or \
                    self.Gio.DBusError.get_remote_error(error) != 'org.bluez.Error.InProgress':
                self.error(error)
        self.activity = True

    def tick(self, address):
        now = time.monotonic()
        # Only the charger supplies automatic-pairing authority. An explicit
        # address alone must not silently pair an unrelated nearby device.
        magnetic_address = dock_address()
        self.address = address
        try:
            attached = read_dock()
        except (RuntimeError, OSError):
            attached = None
        if attached is not None and attached != self.last_dock:
            self.last_dock = attached
            self.retry_at = 0
            self.connect_at.clear()
        if self.adapter and now >= self.until:
            self.stop()
            self.retry_at = now + 90
        objects = self.bus.call_sync(
            'org.bluez', '/', 'org.freedesktop.DBus.ObjectManager',
            'GetManagedObjects', None, None,
            self.Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        powered = {path for path, interfaces in objects.items()
                   if interfaces.get('org.bluez.Adapter1', {}).get('Powered')}
        if self.adapter and self.adapter not in powered:
            self.stop()
        for path, interfaces in objects.items():
            device = interfaces.get('org.bluez.Device1', {})
            if (not magnetic_address or
                    device.get('Address', '').upper() != magnetic_address or
                    (address and address.upper() != magnetic_address) or
                    device.get('Adapter') not in powered):
                continue
            if path in self.repair and path not in self.repaired and device.get('Paired'):
                self.bus.call_sync('org.bluez', device['Adapter'], 'org.bluez.Adapter1',
                                   'RemoveDevice', self.GLib.Variant('(o)', (path,)),
                                   None, self.Gio.DBusCallFlags.NONE, 5000, None)
                self.repaired.add(path)
                self.repair.discard(path)
                self.connecting.discard(path)
                self.connect_at.pop(path, None)
                self.retry_at = 0
                print(json.dumps({'kind': 'pen_expired_pairing_removed'}), flush=True)
                return
            if not device.get('Paired') and self.pairing is None and now >= self.pair_retry:
                self.prepare_agent()
                self.pairing = path
                self.pair_retry = now + 30
                self.bus.call('org.bluez', path, 'org.bluez.Device1', 'Pair',
                              None, None, self.Gio.DBusCallFlags.NONE, 35000, None,
                              self.pair_done, path)
                print(json.dumps({'kind': 'pen_pairing_started'}), flush=True)
                return
        if any(
                device.get('Connected') and
                (not address or device.get('Address', '').upper() == address.upper()) and
                re.fullmatch(r'bluetooth:v0022p5081d[0-9a-fA-F]{4}',
                             device.get('Modalias', ''), re.IGNORECASE)
                for interfaces in objects.values()
                for device in [interfaces.get('org.bluez.Device1', {})]):
            self.stop()
            return
        for path, interfaces in objects.items():
            device = interfaces.get('org.bluez.Device1', {})
            if (device.get('Adapter') not in powered or
                    not device.get('Paired') or not device.get('Trusted') or
                    device.get('Connected') or
                    path in self.connecting or
                    (address and device.get('Address', '').upper() != address.upper()) or
                    (not address and
                     not re.fullmatch(r'bluetooth:v0022p5081d[0-9a-fA-F]{4}',
                                      device.get('Modalias', ''), re.IGNORECASE)) or
                    now < self.connect_at.get(path, 0)):
                continue
            self.connect_at[path] = now + 30
            self.connecting.add(path)
            self.bus.call('org.bluez', path, 'org.bluez.Device1',
                          'Connect', None, None, self.Gio.DBusCallFlags.NONE,
                          30000, None, self.connect_done, path)
        if self.adapter or now < self.retry_at or not powered:
            return
        adapter = sorted(powered)[0]
        self.retry_at = now + 10
        try:
            self.bus.call_sync(
                'org.bluez', adapter, 'org.bluez.Adapter1',
                'SetDiscoveryFilter', self.GLib.Variant('(a{sv})', ({
                    'Transport': self.GLib.Variant('s', 'le'),
                    'DuplicateData': self.GLib.Variant('b', False),
                },)), None, self.Gio.DBusCallFlags.NONE, 5000, None)
            self.bus.call_sync(
                'org.bluez', adapter, 'org.bluez.Adapter1',
                'StartDiscovery', None, None,
                self.Gio.DBusCallFlags.NONE, 5000, None)
        except self.GLib.Error as error:
            self.error(error)
            return
        self.adapter = adapter
        self.until = time.monotonic() + 30
        self.last_error = None

    def error(self, error):
        message = str(error)
        if message != self.last_error:
            print(json.dumps({'kind': 'pen_discovery_error', 'message': message}), flush=True)
            self.last_error = message

    def stop(self):
        if self.adapter is None:
            return
        adapter, self.adapter = self.adapter, None
        try:
            self.bus.call_sync(
                'org.bluez', adapter, 'org.bluez.Adapter1',
                'StopDiscovery', None, None,
                self.Gio.DBusCallFlags.NONE, 5000, None)
        except self.GLib.Error as error:
            self.error(error)


class Pen:
    def __init__(self, address, stationary):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        objects = self.bus.call_sync('org.bluez', '/', 'org.freedesktop.DBus.ObjectManager',
                                     'GetManagedObjects', None, None,
                                     Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        devices = [(p, o['org.bluez.Device1']) for p, o in objects.items()
                   if 'org.bluez.Device1' in o and
                   (address is None or o['org.bluez.Device1'].get('Address', '').upper() == address.upper()) and
                   o['org.bluez.Device1'].get('Connected') and
                   o['org.bluez.Device1'].get('ServicesResolved') and
                   re.fullmatch(r'bluetooth:v0022p5081d[0-9a-fA-F]{4}',
                                o['org.bluez.Device1'].get('Modalias', ''), re.IGNORECASE)]
        if len(devices) != 1:
            raise RuntimeError('expected one connected Focus Pen Pro')
        self.base, device = devices[0]
        self.address = device['Address']
        adapter = objects[device['Adapter']]['org.bluez.Adapter1']
        self.host_address = bytes.fromhex(adapter['Address'].replace(':', ''))
        if len(self.host_address) != 6:
            raise RuntimeError('invalid actual adapter address')
        chars = {o['org.bluez.GattCharacteristic1']['UUID'].lower(): p for p, o in objects.items()
                 if p.startswith(self.base + '/') and 'org.bluez.GattCharacteristic1' in o}
        if FE11 not in chars or FE12 not in chars:
            raise RuntimeError('connected pen has not exposed its control services')
        self.write, self.notify = chars[FE11], chars[FE12]
        self.events = []
        self.initialized = False
        self.serial = 0
        self.stationary = stationary if stationary is not None and stationary.exists() else None
        self.last_stationary = None
        self.last_dock = None
        self.last_screen = None
        self.stop = False
        self.failure = None
        self.subscription = None
        self.notifying = False

    def log(self, kind, **fields):
        print(json.dumps({'kind': kind, 'boottime_ns': time.clock_gettime_ns(time.CLOCK_BOOTTIME),
                          **fields}), flush=True)

    def call(self, path, method, args=None):
        return self.bus.call_sync('org.bluez', path, 'org.bluez.GattCharacteristic1', method,
                                 args, None, self.Gio.DBusCallFlags.NONE, 5000, None)

    def changed(self, connection, sender, path, interface, member, args, userdata):
        _, fields, _ = args.unpack()
        if 'Value' not in fields:
            return
        value = bytes(fields['Value'])
        self.serial += 1
        self.events.append((self.serial, value))
        self.events = self.events[-64:]
        self.log('reply', hex=value.hex())
        if (self.stationary and len(value) == 3 and value[:2] in (b'\xd2\x01', b'\x53\x01')
                and value[2] in (0, 1) and value[2] != self.last_stationary):
            try:
                self.stationary.write_text(str(value[2]) + '\n')
                self.last_stationary = value[2]
                self.log('stationary_forwarded', value=value[2])
            except OSError as error:
                self.failure = error

    def pump(self):
        context = self.GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        if self.failure:
            raise self.failure
        if self.stop:
            raise InterruptedError('pen initialization interrupted')

    def request(self, payload, response, size=None):
        self.pump()
        before = self.serial
        self.call(self.write, 'WriteValue', self.GLib.Variant('(aya{sv})',
                  (payload, {'type': self.GLib.Variant('s', 'command')})))
        self.log('request', hex=payload.hex())
        until = time.monotonic() + 3
        while time.monotonic() < until:
            self.pump()
            for serial, value in self.events:
                if serial > before and value.startswith(response) and (size is None or len(value) == size):
                    return value
            time.sleep(.01)
        raise TimeoutError('no response to opcode ' + payload[:1].hex())

    def connected(self):
        properties = self.bus.call_sync(
            'org.bluez', self.base, 'org.freedesktop.DBus.Properties', 'GetAll',
            self.GLib.Variant('(s)', ('org.bluez.Device1',)), None,
            self.Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        return bool(properties.get('Connected') and properties.get('ServicesResolved'))

    def follow_dock(self):
        try:
            attached = read_dock()
        except (RuntimeError, OSError):
            return  # Optional dock driver may not yet be installed.
        if attached != self.last_dock:
            self.request(b'\x51\x01' + bytes([0 if attached else 1]), b'\xd1\x00', 2)
            self.last_dock = attached
            self.log('dock_updated', attached=attached)

    def follow_screen(self):
        active = screen_on()
        if active is not None and active != self.last_screen:
            self.request(b'\x61\x01' + bytes([int(active)]), b'\xe1\x00', 2)
            self.last_screen = active
            self.log('screen_updated', active=active)

    def open(self):
        self.subscription = self.bus.signal_subscribe(
            'org.bluez', 'org.freedesktop.DBus.Properties', 'PropertiesChanged', self.notify,
            None, self.Gio.DBusSignalFlags.NONE, self.changed, None)
        self.call(self.notify, 'StartNotify')
        self.notifying = True

    def initialize(self, p):
        started = time.monotonic()
        self.log('initialization_started')
        # Send actual wake/dock state before radio setup. The pen can retain
        # the previous OS's screen state across a host reboot.
        if screen_on():
            self.follow_screen()
        self.request(b'\x52\x00', b'\xd2\x01', 3)
        self.request(b'\x00\x00', b'\x80\x01', 3)
        self.request(b'\x01\x00', b'\x81\x02', 4)
        self.request(b'\x02\x00', b'\x82\x02', 4)
        self.request(b'\x03\x00', b'\x83\x03', 5)
        assigned = []
        for cycle in ('first_id_cycle', 'second_id_cycle'):
            pen_id = self.request(b'\x04\x00', b'\x84\x01', 3)[2:3]
            if pen_id == b'\xff':
                # Reopening only the helper while the existing BLE session
                # still owns both slots returns the unassigned sentinel.
                # Start a fresh session instead of programming slot 255.
                self.bus.call_sync('org.bluez', self.base, 'org.bluez.Device1',
                                   'Disconnect', None, None,
                                   self.Gio.DBusCallFlags.NONE, 5000, None)
                raise ConnectionError('pen radio slots need a fresh connection')
            assigned.append(pen_id[0])
            parameters = (self.host_address + p['parameter_prefix'] + pen_id + p['timing'] +
                          p[cycle] + p['intervals'] + p['parameter_tail'])
            self.request(b'\x30' + bytes([len(parameters)]) + parameters, b'\xb0\x00', 2)
            self.request(b'\x31\x08' + pen_id + b'\x03' + p['frequency'] +
                         p['frequency_interval'], b'\xb1\x00', 2)
            self.request(b'\x32\x03' + pen_id + p['voltage'], b'\xb2\x00', 2)
        # The protocol uses Unix seconds; report/frame timing uses BOOTTIME.
        self.request(b'\x33\x04' + struct.pack('>I', int(time.time()) & 0xffffffff), b'\xb3\x00', 2)
        self.follow_dock()
        self.log('radio_configured', elapsed_ms=round((time.monotonic() - started) * 1000))
        self.request(b'\x5f\x01' + bytes([int(p['double_tap'])]), b'\xdf\x01\x01', 3)
        self.request(b'\x5a\x01' + bytes([p['squeeze_motor_level']]), b'\xda\x01\x01', 3)
        self.request(b'\x5c\x04' + struct.pack('>HH', p['squeeze_threshold_down'],
                     p['squeeze_threshold_up']), b'\xdc\x01\x01', 3)
        self.follow_screen()
        self.initialized = True
        self.log('initialized', assigned_ids=assigned, attached=self.last_dock,
                 haptic_level=p['squeeze_motor_level'], haptic_setting_accepted=True)

    def close(self):
        try:
            if self.notifying:
                try:
                    self.call(self.notify, 'StopNotify')
                except self.GLib.Error as error:
                    self.log('notification_cleanup_error', message=str(error))
        finally:
            if self.subscription is not None:
                self.bus.signal_unsubscribe(self.subscription)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', help='optional paired pen address; otherwise discover one connected P81C')
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--stationary-device', type=Path)
    parser.add_argument('--observe-seconds', type=float, default=10)
    parser.add_argument('--follow', action='store_true',
                        help='reinitialize after real BlueZ reconnects and follow the pen-dock switch')
    args = parser.parse_args()
    if not 0 <= args.observe_seconds <= 300:
        parser.error('observe-seconds must be between 0 and 300')
    p = profile(args.profile)
    stopped = False
    pen = None
    last_error = None

    def stop(signum, frame):
        nonlocal stopped
        stopped = True
        if pen is not None:
            pen.stop = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    scan = LeScan()
    try:
        while not stopped:
            address = args.address or dock_address()
            try:
                pen = Pen(address, args.stationary_device)
                scan.stop()
                pen.open()
                pen.initialize(p)
                last_error = None
                until = time.monotonic() + args.observe_seconds
                next_state_check = time.monotonic()
                while not stopped and (args.follow or time.monotonic() < until):
                    pen.pump()
                    if args.follow and time.monotonic() >= next_state_check:
                        next_state_check = time.monotonic() + 1
                        if not pen.connected():
                            pen.log('disconnected')
                            break
                        pen.follow_dock()
                        pen.follow_screen()
                    time.sleep(.01)
            except InterruptedError:
                if not stopped:
                    raise
            except Exception as error:
                if not args.follow:
                    raise
                # A missing/docked pen is normal. Reconnect only after BlueZ
                # exposes the actual peer again; never replay stale IDs.
                message = str(error)
                if message != last_error:
                    print(json.dumps({'kind': 'waiting_for_pen', 'message': message}), flush=True)
                    last_error = message
                try:
                    scan.tick(address)
                except scan.GLib.Error as scan_error:
                    scan.error(scan_error)
            finally:
                if pen is not None:
                    pen.close()
                    pen = None
            if not args.follow or stopped:
                break
            scan.wait(lambda: stopped)
    finally:
        scan.stop()
        for subscription in scan.signals:
            scan.bus.signal_unsubscribe(subscription)
        if scan.agent is not None:
            scan.bus.unregister_object(scan.agent)
        if scan.auth_watch is not None:
            scan.GLib.source_remove(scan.auth_watch)
        if scan.auth_monitor is not None:
            scan.auth_monitor.close()


if __name__ == '__main__':
    main()
