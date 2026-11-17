#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Device-side GNOME/DSI mode and DPMS acceptance (root, invoked over SSH).

Python standard library, busctl/loginctl/gsettings and GNOME's existing libgio/glib.
No package/service/driver changes or persistent monitor configuration. The only
kernel write is selecting a DRM debugfs CRC source; data is opened read-only.
Reports contain private device/session information: keep --output private.

A moving pointer must change the layer-mixer CRC on BOTH bonded display halves.
--nightlight N instead keeps the mode and pointer fixed, using encoder CRC to
check N night-light on/off cycles. Prepare a static fullscreen colored image
(no clock, blinking caret, notifications or animations) and leave input idle.
The five changed GSettings are restored, including originally unset values.
CRC is measured before DSI/panel output; PASS is the specified digital-path
acceptance, not proof of panel light output. Run in a fresh recoverable RAM boot:
a D-state DRM call cannot be cancelled by this program's userspace deadlines.

Protocol: Mutter 50.1 DisplayConfig/RemoteDesktop XML; DRM drm_debugfs_crc.c.
The input session lifetime follows piano-desktop-drive.py: its creator must
keep the SAME D-Bus connection for Start/Notify*/Stop. Separate gdbus processes
cannot do that. A short-lived unprivileged ctypes/GIO worker owns that session.
"""
import argparse
import ctypes
import errno
import fcntl
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import pwd
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

DC = 'org.gnome.Mutter.DisplayConfig'
DC_PATH = '/org/gnome/Mutter/DisplayConfig'
RD = 'org.gnome.Mutter.RemoteDesktop'
RATES = (60, 90, 120, 144)
APPLY_SIGNATURE = 'uua(iiduba(ssa{sv}))a{sv}'
COLOR = 'org.gnome.SettingsDaemon.Color'
COLOR_PATH = '/org/gnome/SettingsDaemon/Color'
COLOR_SCHEMA = 'org.gnome.settings-daemon.plugins.color'
NIGHT_KEYS = ('night-light-enabled', 'night-light-temperature',
              'night-light-schedule-automatic', 'night-light-schedule-from',
              'night-light-schedule-to')
NIGHT_TEMPERATURE = 4000
BAD_LOG = re.compile(
    r'dsi_err_worker|dsi.*fifo|underrun|underflow|\bWARNING\b|Call trace|'
    r'Overflow of CRC buffer|'
    r'(?:regdma|lutdma).*?(?:fault|fail|error|time(?:d[ _-]*out|out)|unconfirmed|leak)|'
    r'(?:frame|vblank|vsync|pp_done).*?time(?:d[ _-]*out|out)|'
    r'(?:smmu|iommu).*?(?:fault|translation.*(?:fail|error))|'
    r'(?:pll|clock|clk|rcg).*?(?:stuck|time(?:d out|out)|fail|error|'
    r"didn.t update|already (?:disabled|unprepared))", re.I)


class Unsupported(RuntimeError):
    pass


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def unwrap(value):
    """busctl --json=short variants are {type: signature, data: value}."""
    if isinstance(value, dict):
        if set(value) == {'type', 'data'}:
            return unwrap(value['data'])
        return {k: unwrap(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwrap(v) for v in value]
    return value


def parse_state(reply):
    serial, monitors, logical, properties = unwrap(reply)
    selected = [m for m in monitors if m[0][0] == 'DSI-1']
    # Relative pointer motion has an unambiguous viewport with just this panel.
    # Reject other layouts rather than disabling outputs or moving across them.
    if len(selected) != 1 or len(monitors) != 1 or len(logical) != 1:
        raise Unsupported('requires exactly one connected/active DSI-1 monitor')
    monitor = selected[0]
    group = logical[0]
    if len(group[5]) != 1 or group[5][0] != monitor[0]:
        raise Unsupported('DSI-1 is not the sole logical monitor')
    modes = []
    for ident, width, height, hz, preferred_scale, scales, props in monitor[1]:
        if not math.isfinite(hz) or hz <= 0:
            raise ValueError('invalid refresh rate in Mutter reply')
        modes.append(dict(id=ident, width=width, height=height, hz=hz,
                          scales=scales, properties=props))
    current = [m for m in modes if m['properties'].get('is-current', False)]
    if len(current) != 1:
        raise Unsupported('Mutter must identify exactly one current DSI-1 mode')
    scale = group[2]
    if not math.isfinite(scale) or scale <= 0 or group[3] not in range(8):
        raise ValueError('invalid logical monitor scale/transform')
    if properties.get('layout-mode') not in (1, 2):
        raise Unsupported('unknown Mutter layout-mode')
    return dict(serial=serial, spec=monitor[0], modes=modes, current=current[0],
                x=group[0], y=group[1], scale=scale, transform=group[3], primary=group[4],
                monitor_properties=monitor[2], properties=properties)


def choose_modes(state):
    selected = {}
    current = state['current']
    for rate in RATES:
        candidates = [m for m in state['modes']
                      if (m['width'], m['height']) == (current['width'], current['height'])
                      and abs(m['hz'] - rate) <= 0.5
                      and not m['properties'].get('is-interlaced', False)
                      and m['properties'].get('refresh-rate-mode', 'fixed') == 'fixed'
                      and any(abs(s - state['scale']) < 1e-6 for s in m['scales'])]
        if len(candidates) != 1:
            raise Unsupported(f'{rate} Hz: expected one fixed mode at original size/scale, got {len(candidates)}')
        selected[rate] = candidates[0]
    if current['properties'].get('refresh-rate-mode', 'fixed') != 'fixed':
        raise Unsupported('original mode uses variable refresh rate')
    return selected


def apply_arguments(original, fresh_serial, mode_id):
    """Typed busctl arguments, method=1 (temporary), preserving layout/colour."""
    args = [str(fresh_serial), '1', '1', str(original['x']), str(original['y']),
            str(original['scale']), str(original['transform']),
            str(original['primary']).lower(), '1', 'DSI-1', mode_id]
    props = original['monitor_properties']
    settings = []
    if 'is-underscanning' in props:
        settings += ['underscanning', 'b', str(props['is-underscanning']).lower()]
    if 'color-mode' in props:
        settings += ['color-mode', 'u', str(props['color-mode'])]
    args += [str(len(settings) // 3), *settings]
    if original['properties'].get('supports-changing-layout-mode', False):
        args += ['1', 'layout-mode', 'u', str(original['properties']['layout-mode'])]
    else:
        args += ['0']
    return args


def mode_matches(state, original, mode):
    return (state['current']['id'] == mode['id'] and state['spec'] == original['spec']
            and all(state[key] == original[key] for key in ('x', 'y', 'scale', 'transform', 'primary'))
            and state['properties']['layout-mode'] == original['properties']['layout-mode'])


def parse_crc_line(line):
    fields = line.split()
    if len(fields) < 2:
        raise ValueError(f'incomplete CRC line: {line!r}')
    if fields[0] == 'XXXXXXXXXX':
        raise Unsupported('CRC source has no valid frame counter')
    if any(not re.fullmatch(r'(?:0x)?[0-9a-fA-F]{1,8}', x) for x in fields):
        raise ValueError(f'invalid CRC fields: {line!r}')
    return int(fields[0], 16), tuple(int(x, 16) for x in fields[1:])


def crc_metrics(samples, expected_hz, source='auto', stationary=False):
    """Samples: {time: host monotonic seconds, frame: uint32, values: [CRC]}.

    Rate uses FRAME delta, not line count. Gaps in CRC delivery are recorded;
    counter wrap is allowed, duplicate/backward/reset counters are rejected.
    """
    per_channel_key = 'unique_per_encoder' if source == 'encoder' else 'unique_per_lm'
    result = {'status': 'FAIL', 'samples': len(samples), 'expected_hz': expected_hz,
              'fps': None, 'unique_crc': 0, per_channel_key: [], 'source': source}
    if len(samples) < 2:
        result['reason'] = 'no advancing CRC stream'
        return result
    channels = {len(s['values']) for s in samples}
    if channels != {2}:
        block = 'encoder' if source == 'encoder' else 'LM'
        return dict(result, status='INCOMPLETE', reason=f'bonded DSI requires two {block} CRC columns')
    elapsed = samples[-1]['time'] - samples[0]['time']
    if elapsed < 0.5:
        return dict(result, status='INCOMPLETE', reason='insufficient timed CRC span')
    deltas = [(b['frame'] - a['frame']) & 0xffffffff for a, b in zip(samples, samples[1:])]
    if any(d == 0 or d >= 0x80000000 for d in deltas):
        return dict(result, reason='duplicate, backwards or reset frame counter')
    frames = sum(deltas)
    unique = len({tuple(s['values']) for s in samples})
    per_lm = [len({s['values'][i] for s in samples}) for i in range(2)]
    fps = frames / elapsed
    result.update(seconds=elapsed, frames=frames, fps=fps, unique_crc=unique,
                  max_frame_gap=max(deltas),
                  rate_ok=abs(fps - expected_hz) <= 0.15 * expected_hz + 1e-9,
                  content_ok=unique == 1 if stationary else unique >= 2 and all(n >= 2 for n in per_lm))
    result[per_channel_key] = per_lm
    if stationary:
        result['stable_crc'] = list(samples[0]['values']) if unique == 1 else None
    result['status'] = 'PASS' if result['rate_ok'] and result['content_ok'] else 'FAIL'
    return result


def nightlight_crc_matches(crc, enabled, baseline):
    """Both endpoints must be stable; compare the complete bonded CRC tuple."""
    if baseline is None or crc.get('status') != 'PASS' or crc.get('stable_crc') is None:
        return False
    return (crc['stable_crc'] != baseline) if enabled else (crc['stable_crc'] == baseline)


def nightlight_schedule(hour):
    # Middle of a 12-hour manual window, well clear of GSD's sunrise/sunset
    # transition even across midnight. All settings are GVariant strings.
    return {'night-light-enabled': 'false',
            'night-light-schedule-automatic': 'false',
            'night-light-schedule-from': repr((hour - 6) % 24),
            'night-light-schedule-to': repr((hour + 6) % 24),
            'night-light-temperature': f'uint32 {NIGHT_TEMPERATURE}'}


def settings_snapshot_worker():
    """Read effective AND user values via GSettings, without a python-gi dependency."""
    schema, keys = json.loads(sys.argv[1])
    gio = ctypes.CDLL('libgio-2.0.so.0')
    glib = ctypes.CDLL('libglib-2.0.so.0')
    gio.g_settings_new.argtypes = [ctypes.c_char_p]
    gio.g_settings_new.restype = ctypes.c_void_p
    for name in ('g_settings_get_value', 'g_settings_get_user_value'):
        fn = getattr(gio, name)
        fn.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        fn.restype = ctypes.c_void_p
    gio.g_settings_is_writable.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    gio.g_settings_is_writable.restype = ctypes.c_int
    glib.g_variant_print.argtypes = [ctypes.c_void_p, ctypes.c_int]
    glib.g_variant_print.restype = ctypes.c_void_p
    glib.g_variant_unref.argtypes = [ctypes.c_void_p]
    glib.g_free.argtypes = [ctypes.c_void_p]
    settings = gio.g_settings_new(schema.encode())
    result = {}
    for key in keys:
        values = {'writable': bool(gio.g_settings_is_writable(settings, key.encode()))}
        for label, name in [('effective', 'g_settings_get_value'), ('user', 'g_settings_get_user_value')]:
            variant = getattr(gio, name)(settings, key.encode())
            values[label] = None
            if variant:
                text = glib.g_variant_print(variant, True)
                values[label] = ctypes.string_at(text).decode()
                glib.g_free(text)
                glib.g_variant_unref(variant)
        result[key] = values
    print(json.dumps(result), flush=True)


def filter_kernel(lines):
    return [line for line in lines if BAD_LOG.search(line)]


def active_crtc(state_text):
    blocks = re.split(r'(?=^(?:plane|crtc|connector)\[\d+\]:)', state_text, flags=re.M)
    connector = [b for b in blocks if re.match(r'connector\[\d+\]: DSI-1\s*\n', b)]
    if len(connector) != 1:
        raise Unsupported('cannot resolve DSI-1 in DRM atomic state')
    match = re.search(r'^\s+crtc=(crtc-\d+)\s*$', connector[0], re.M)
    if not match:
        raise Unsupported('DSI-1 has no mapped CRTC')
    name = match[1]
    crtc = [b for b in blocks if re.match(r'crtc\[\d+\]: ' + re.escape(name) + r'\s*\n', b)]
    if len(crtc) != 1 or not re.search(r'^\s+active=1\s*$', crtc[0], re.M):
        raise RuntimeError('DRM DSI CRTC is not active')
    return name


def discover_crc():
    connectors = [p for p in Path('/sys/class/drm').glob('card*-DSI-1')
                  if re.fullmatch(r'card\d+-DSI-1', p.name)
                  and (p / 'status').read_text().strip() == 'connected']
    if len(connectors) != 1:
        raise Unsupported('expected one connected DRM DSI-1')
    card = connectors[0].name.rsplit('-DSI-1', 1)[0]
    device = Path('/sys/class/drm') / card
    if (device / 'device/driver').resolve().name != 'msm_dpu':
        raise Unsupported('DSI-1 is not driven by msm_dpu')
    minor = int((device / 'dev').read_text().strip().split(':')[1])
    debug = Path('/sys/kernel/debug/dri') / str(minor)
    if not (debug / 'state').is_file():
        raise Unsupported('DRM debugfs state is unavailable (not mounted or not configured)')
    crtc = active_crtc((debug / 'state').read_text())
    path = debug / crtc / 'crc'
    if not (path / 'control').is_file() or not (path / 'data').is_file():
        raise Unsupported(f'DRM CRC interface unavailable: {path}')
    return path


class KernelLog:
    def __init__(self):
        self.fd = os.open('/dev/kmsg', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        os.lseek(self.fd, 0, os.SEEK_END)  # no clearing and no historical failures
        self.entries = []
        self.errors = []
        self.last_seq = None

    def drain(self):
        for _ in range(16384):
            try:
                raw = os.read(self.fd, 65536).decode('utf-8', 'replace')
            except BlockingIOError:
                return
            except OSError as error:
                if error.errno == errno.EPIPE:
                    self.errors.append('kernel ring overwritten: log interval incomplete')
                    continue
                raise
            if not raw:
                raise RuntimeError('unexpected /dev/kmsg EOF')
            meta, text = raw.split(';', 1)
            fields = meta.split(',')
            seq = int(fields[1])
            if self.last_seq is not None and seq != self.last_seq + 1:
                self.errors.append(f'kernel sequence gap {self.last_seq}->{seq}')
            self.last_seq = seq
            self.entries.append({'seq': seq, 'uptime_us': int(fields[2]), 'message': text.rstrip()})
        self.errors.append('kernel log flood exceeded bounded read budget')

    def close(self):
        os.close(self.fd)


class CRC:
    def __init__(self, path, source):
        self.path, self.source = path, source
        self.fd = None
        self.samples, self.errors = [], []
        self.done = threading.Event()
        self.thread = None
        self.previous = None

    def __enter__(self):
        # DPU lacks get_crc_sources, so control can be empty. Its supported
        # source is selected explicitly. Opening data enables MISR; close stops it.
        control = (self.path / 'control').read_text().splitlines()
        selected = [line[:-1] for line in control if line.endswith('*')]
        self.previous = selected[0] if len(selected) == 1 else 'none'
        try:
            (self.path / 'control').write_text(self.source + '\n')
            self.fd = os.open(self.path / 'data', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as error:
            # Do not change control again if another reader owns this CRTC.
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            try:
                (self.path / 'control').write_text(self.previous + '\n')
            except OSError as restore_error:
                raise RuntimeError(f'CRC open: {error}; control restore: {restore_error}') from error
            if error.errno in (errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL):
                raise Unsupported(f'CRC source unavailable: {error}') from error
            raise
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()
        return self

    def read(self):
        try:
            while not self.done.is_set():
                if not select.select([self.fd], [], [], 0.05)[0]:
                    continue
                while not self.done.is_set():
                    try:
                        raw = os.read(self.fd, 4096)
                    except BlockingIOError:
                        break
                    if not raw:
                        raise RuntimeError('CRC EOF during active sampling')
                    now = time.monotonic()
                    # Each read returns one line; drm_debugfs_crc's LINE_LEN also copies the NUL.
                    for line in raw.decode('ascii').replace('\x00', '').splitlines():
                        if not line.strip():
                            continue
                        frame, values = parse_crc_line(line)
                        self.samples.append({'time': now, 'frame': frame, 'values': values})
        except Exception as error:
            self.errors.append({'type': type(error).__name__, 'message': str(error)})

    def __exit__(self, *exc):
        self.done.set()
        try:
            self.thread.join(timeout=1)
            if self.thread.is_alive():
                raise RuntimeError('CRC reader did not stop; kernel call may be blocked')
        finally:
            try:
                os.close(self.fd)  # disables MISR before any subsequent modeset/DPMS
            finally:
                self.fd = None
                (self.path / 'control').write_text(self.previous + '\n')


class Pointer:
    def __init__(self, user, env):
        # A persistent unprivileged connection is mandatory for RemoteDesktop.
        # The root-owned script/output may be below /root: send just this one
        # function to the child, without making private directories world-readable.
        source = ('import ctypes, json, math, os, sys\n' + f'RD = {RD!r}\n' + inspect.getsource(pointer_worker) +
                  '\ntry:\n    pointer_worker()\nexcept Exception as error:\n'
                  '    print(json.dumps({"ok": False, "error": str(error)}), flush=True)\n'
                  '    print(str(error), file=sys.stderr)\n    sys.exit(1)\n')
        self.proc = subprocess.Popen([sys.executable, '-c', source],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd='/', env=env, user=user.pw_uid,
                                     group=user.pw_gid, extra_groups=os.getgrouplist(user.pw_name, user.pw_gid))
        try:
            self.reply()
        except Exception:
            self.close()
            raise

    def reply(self):
        if not select.select([self.proc.stdout], [], [], 7)[0]:
            raise TimeoutError('RemoteDesktop worker did not reply')
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('RemoteDesktop worker exited: ' + self.proc.stderr.read().decode(errors='replace'))
        data = json.loads(line)
        if not data.get('ok'):
            raise RuntimeError('RemoteDesktop: ' + data.get('error', 'unknown error'))
        return data

    def move(self, dx, dy):
        self.proc.stdin.write((json.dumps({'move': [dx, dy]}) + '\n').encode())
        self.proc.stdin.flush()
        self.reply()

    def close(self):
        try:
            try:
                self.proc.stdin.close()  # EOF -> Stop on the same connection
            except BrokenPipeError:
                pass
            self.proc.wait(timeout=7)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)
            raise RuntimeError('RemoteDesktop Stop timeout; disconnected worker')
        if self.proc.returncode:
            raise RuntimeError('RemoteDesktop cleanup failed: ' + self.proc.stderr.read().decode(errors='replace'))


def pointer_worker():
    """Only fixed RemoteDesktop calls, using GIO's existing session connection."""
    if os.geteuid() == 0:
        raise RuntimeError('pointer worker must be the desktop user')
    gio = ctypes.CDLL('libgio-2.0.so.0')
    glib = ctypes.CDLL('libglib-2.0.so.0')
    obj = ctypes.CDLL('libgobject-2.0.so.0')
    ptr, char, integer = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
    class GError(ctypes.Structure):
        _fields_ = [('domain', ctypes.c_uint), ('code', integer), ('message', char)]
    def bind(lib, name, result, args):
        fn = getattr(lib, name); fn.restype = result; fn.argtypes = args; return fn
    bus_get = bind(gio, 'g_bus_get_sync', ptr, [integer, ptr, ctypes.POINTER(ptr)])
    call_sync = bind(gio, 'g_dbus_connection_call_sync', ptr,
                     [ptr, char, char, char, char, ptr, ptr, integer, integer, ptr, ctypes.POINTER(ptr)])
    parse = bind(glib, 'g_variant_parse', ptr, [ptr, char, ptr, ptr, ctypes.POINTER(ptr)])
    child = bind(glib, 'g_variant_get_child_value', ptr, [ptr, ctypes.c_size_t])
    string = bind(glib, 'g_variant_get_string', char, [ptr, ptr])
    sink = bind(glib, 'g_variant_ref_sink', ptr, [ptr])
    unref = bind(glib, 'g_variant_unref', None, [ptr])
    error_free = bind(glib, 'g_error_free', None, [ptr])
    object_unref = bind(obj, 'g_object_unref', None, [ptr])
    def checked(value, error):
        if value:
            return value
        message = ctypes.cast(error, ctypes.POINTER(GError)).contents.message.decode() if error else 'GIO failed'
        if error:
            error_free(error)
        raise RuntimeError(message)
    error = ptr()
    connection = checked(bus_get(2, None, ctypes.byref(error)), error)
    session = None
    def call(path, iface, method, params=None, want_path=False):
        error = ptr(); variant = None
        if params is not None:
            variant = checked(parse(None, params.encode(), None, None, ctypes.byref(error)), error)
            sink(variant)
        try:
            response = checked(call_sync(connection, RD.encode(), path.encode(), iface.encode(),
                                          method.encode(), variant, None, 1, 5000, None,
                                          ctypes.byref(error)), error)  # NO_AUTO_START
        finally:
            if variant:
                unref(variant)
        try:
            if want_path:
                value = child(response, 0)
                try:
                    return string(value, None).decode()
                finally:
                    unref(value)
        finally:
            unref(response)
    try:
        session = call('/org/gnome/Mutter/RemoteDesktop', RD, 'CreateSession', want_path=True)
        call(session, RD + '.Session', 'Start')
        print(json.dumps({'ok': True}), flush=True)
        for line in sys.stdin:
            try:
                dx, dy = json.loads(line)['move']
                if not all(math.isfinite(v) for v in (dx, dy)):
                    raise ValueError('non-finite pointer delta')
                call(session, RD + '.Session', 'NotifyPointerMotionRelative', f'({float(dx)}, {float(dy)})')
                print(json.dumps({'ok': True}), flush=True)
            except Exception as error:
                print(json.dumps({'ok': False, 'error': str(error)}), flush=True)
    finally:
        try:
            if session:
                call(session, RD + '.Session', 'Stop')
        finally:
            object_unref(connection)


class Check:
    def __init__(self, args):
        self.a = args
        self.out = args.output.resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        os.chmod(self.out, 0o700)
        shutil.copyfile(__file__, self.out / 'runner.py')
        self.result = {'schema': 1, 'status': 'INCOMPLETE', 'label': args.label,
                       'kernel': os.uname().release, 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'criteria': {'fps_tolerance': 0.15, 'minimum_unique_crc': 2,
                                    'minimum_unique_per_lm': 2, 'sample_seconds': args.sample_seconds,
                                    'settle_seconds': args.settle_seconds, 'source': args.crc_source},
                       'steps': [], 'restore': {}, 'errors': []}
        self.user = self.env = self.original = self.pointer = self.log = None
        self.lock = None
        self.changed = False
        self.settings = None
        self.settings_changed = False
        self.shell_original = None
        self.baseline_crc = None
        if args.nightlight:
            self.result['criteria'].update(minimum_unique_crc=1, endpoint_must_be_constant=True,
                                           every_off_equals_baseline=True, every_on_differs=True,
                                           shell_pid_unchanged=True)
            self.result['criteria'].pop('minimum_unique_per_lm')
            self.result['nightlight'] = {'cycles': args.nightlight, 'temperature': NIGHT_TEMPERATURE}
        elif args.crc_source == 'encoder':
            self.result['criteria']['minimum_unique_per_encoder'] = self.result['criteria'].pop('minimum_unique_per_lm')

    def command(self, argv, user=False):
        with (self.out / 'commands.jsonl').open('a') as f:
            f.write(json.dumps({'monotonic': time.monotonic(), 'argv': argv}) + '\n')
        opts = dict(env=self.env, user=self.user.pw_uid, group=self.user.pw_gid,
                    extra_groups=os.getgrouplist(self.user.pw_name, self.user.pw_gid)) if user else {}
        r = subprocess.run(argv, capture_output=True, text=True, timeout=7, **opts)
        if r.returncode:
            raise RuntimeError(f'{argv[0]}: {r.stderr.strip() or r.stdout.strip()}')
        return r.stdout

    def bus(self, method, signature=None, args=(), dest=DC, path=DC_PATH, iface=DC):
        cmd = ['busctl', '--user', '--json=short', '--no-pager', '--auto-start=no',
               '--allow-interactive-authorization=no', '--timeout=5',
               'call', dest, path, iface, method]
        if signature:
            cmd += [signature, *args]
        value = self.command(cmd, user=True)
        return json.loads(value) if value.strip() else None

    def power(self, value=None):
        cmd = ['busctl', '--user', '--json=short', '--no-pager', '--auto-start=no',
               '--allow-interactive-authorization=no', '--timeout=5']
        if value is None:
            reply = json.loads(self.command(cmd + ['get-property', DC, DC_PATH, DC, 'PowerSaveMode'], user=True))
            return unwrap(reply)  # get-property JSON has a bare scalar, unlike call
        self.command(cmd + ['set-property', DC, DC_PATH, DC, 'PowerSaveMode', 'i', str(value)], user=True)

    def state(self):
        return parse_state(self.bus('GetCurrentState'))

    def shell_identity(self):
        owner = unwrap(self.bus('GetNameOwner', 's', ['org.gnome.Shell'],
                                dest='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                                iface='org.freedesktop.DBus'))[0]
        pid = unwrap(self.bus('GetConnectionUnixProcessID', 's', [owner],
                              dest='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                              iface='org.freedesktop.DBus'))[0]
        return {'pid': pid, 'bus_owner': owner}

    def settings_snapshot(self):
        source = ('import ctypes, json, sys\n' + inspect.getsource(settings_snapshot_worker)
                  + '\nsettings_snapshot_worker()\n')
        return json.loads(self.command([sys.executable, '-c', source,
                                        json.dumps([COLOR_SCHEMA, NIGHT_KEYS])], user=True))

    def setting(self, key, value):
        self.settings_changed = True  # a timeout can still have changed dconf
        argv = ['gsettings', 'reset' if value is None else 'set', COLOR_SCHEMA, key]
        if value is not None:
            argv.append(value)
        self.command(argv, user=True)

    def color_state(self):
        return unwrap(self.bus('GetAll', 's', [COLOR], dest=COLOR, path=COLOR_PATH,
                               iface='org.freedesktop.DBus.Properties'))[0]

    def wait_color(self, enabled):
        # GSD 50.1 smooths transitions over 5 seconds. Wait for its reported
        # endpoint before measuring the downstream CRC; never change debug bits.
        deadline = time.monotonic() + 15
        expected = NIGHT_TEMPERATURE if enabled else 6500
        while True:
            state = self.color_state()
            if (state.get('NightLightActive') is enabled
                    and state.get('Temperature') == expected):
                return state
            if time.monotonic() >= deadline:
                raise RuntimeError(f'GSD did not reach night-light endpoint {enabled}/{expected}K: {state}')
            self.log.drain()
            time.sleep(0.1)

    def restore_settings(self, errors):
        if not self.settings_changed or self.settings is None:
            return
        restored = self.result['restore']['gsettings'] = {}
        # Disable while restoring the schedule, then restore the original
        # enabled value last. Each operation runs even after another failed.
        operations = [('night-light-enabled', 'false')]
        operations += [(key, self.settings[key]['user']) for key in NIGHT_KEYS[1:]]
        operations += [('night-light-enabled', self.settings['night-light-enabled']['user'])]
        for key, value in operations:
            try:
                self.setting(key, value)
                restored[key] = 'reset to default' if value is None else value
            except Exception as error:
                errors.append(f'GSettings {key}: {error}')
        try:
            after = self.settings_snapshot()
            restored['readback'] = after
            restored['matches_original'] = all(
                after[key][field] == self.settings[key][field]
                for key in NIGHT_KEYS for field in ('effective', 'user'))
            if not restored['matches_original']:
                errors.append('GSettings restoration differs from original values')
        except Exception as error:
            errors.append(f'GSettings restore readback: {error}')

    def session(self):
        sessions = []
        for row in self.command(['loginctl', 'list-sessions', '--no-legend', '--no-pager']).splitlines():
            sid = row.split()[0]
            try:
                props = self.command(['loginctl', 'show-session', sid, '-p', 'Seat', '-p', 'Active',
                                      '-p', 'Class', '-p', 'Type', '-p', 'User', '-p', 'LockedHint'])
            except RuntimeError as e:
                if 'No session' in str(e):
                    continue  # it ended between list-sessions and show-session
                raise
            data = dict(line.split('=', 1) for line in props.splitlines() if '=' in line)
            if all(data.get(k) == v for k, v in [('Seat', 'seat0'), ('Active', 'yes'),
                                               ('Class', 'user'), ('Type', 'wayland')]):
                sessions.append((sid, data))
        if len(sessions) != 1 or sessions[0][1].get('LockedHint') != 'no':
            raise Unsupported('requires one unlocked active seat0 Wayland user session')
        sid, data = sessions[0]
        self.user = pwd.getpwuid(int(data['User']))
        runtime = Path(f'/run/user/{self.user.pw_uid}')
        if self.user.pw_uid == 0 or runtime.stat().st_uid != self.user.pw_uid or not stat.S_ISSOCK((runtime / 'bus').stat().st_mode):
            raise Unsupported('invalid desktop user bus')
        self.env = dict(os.environ, HOME=self.user.pw_dir, USER=self.user.pw_name, LOGNAME=self.user.pw_name,
                        XDG_RUNTIME_DIR=str(runtime), DBUS_SESSION_BUS_ADDRESS=f'unix:path={runtime}/bus')
        self.env.pop('DBUS_STARTER_ADDRESS', None)
        owned = unwrap(self.bus('NameHasOwner', 's', ['org.gnome.Shell'], dest='org.freedesktop.DBus',
                                path='/org/freedesktop/DBus', iface='org.freedesktop.DBus'))[0]
        if not owned:
            raise Unsupported('seat0 user bus has no GNOME Shell')
        locked = unwrap(self.bus('GetActive', dest='org.gnome.ScreenSaver', path='/org/gnome/ScreenSaver',
                                 iface='org.gnome.ScreenSaver'))[0]
        if not owned or locked:
            raise Unsupported('seat0 session is not an unlocked GNOME session')
        self.result['session'] = {'id': sid, 'uid': self.user.pw_uid}

    def apply(self, mode):
        state = self.state()
        if state['spec'] != self.original['spec']:
            raise RuntimeError('monitor identity changed during test')
        self.changed = True  # uncertain D-Bus return may still have changed hardware
        self.bus('ApplyMonitorsConfig', APPLY_SIGNATURE,
                 apply_arguments(self.original, state['serial'], mode['id']))

    def wait_mode(self, mode):
        deadline = time.monotonic() + 10
        while True:
            state = self.state()
            if mode_matches(state, self.original, mode) and self.power() == 0:
                return state
            if time.monotonic() >= deadline:
                raise RuntimeError(f'Mutter did not settle on requested mode {mode["id"]}')
            time.sleep(0.1)

    def sample(self, state, step):
        path = discover_crc()
        step['crc_path'] = str(path)
        if self.a.nightlight:
            # gsd-color animates temperature changes and other transitions can follow a
            # GSettings write. Wait (bounded) until the encoder CRC has been unchanged for
            # 1.5 s, then judge only the following sample window.
            with CRC(path, 'encoder') as crc:
                started = time.monotonic()
                while time.monotonic() < started + 12:
                    self.log.drain()
                    time.sleep(0.1)
                    samples = crc.samples
                    if len(samples) < 2:
                        continue
                    last = tuple(samples[-1]['values'])
                    first_same = next((i for i in range(len(samples) - 1, -1, -1)
                                       if tuple(samples[i]['values']) != last), -1) + 1
                    if samples[-1]['time'] - samples[first_same]['time'] >= 1.5:
                        break
                window = time.monotonic()
                end = window + self.a.sample_seconds
                while time.monotonic() < end:
                    self.log.drain()
                    time.sleep(max(0, min(0.1, end - time.monotonic())))
            step['pointer_moves'] = 0
            step['settle_wait_s'] = round(window - started, 3)
            step['crc_samples'] = [x for x in crc.samples if x['time'] >= window]
            step['crc'] = crc_metrics(step['crc_samples'], state['current']['hz'], 'encoder', stationary=True)
            if crc.errors:
                step['crc'].update(status='INCOMPLETE', reader_errors=crc.errors)
            return step['crc']['status']
        width, height = state['current']['width'], state['current']['height']
        if state['transform'] % 2:
            width, height = height, width
        if state['properties']['layout-mode'] == 1:
            width, height = width / state['scale'], height / state['scale']
        # Clamp to the panel's top-left, then cross both axes without clicks.
        self.pointer.move(-2 * width, -2 * height)
        points = [(width * x, height * y) for x, y in
                  ((0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8), (0.5, 0.5))]
        x = y = 0
        with CRC(path, self.a.crc_source) as crc:
            start = time.monotonic()
            end = start + self.a.sample_seconds
            moves = 0
            while time.monotonic() < end:
                tx, ty = points[moves % len(points)]
                self.pointer.move(tx - x, ty - y)
                x, y = tx, ty
                moves += 1
                self.log.drain()
                time.sleep(max(0, min(0.12, end - time.monotonic())))
            step['pointer_moves'] = moves
        # Every CRC open starts a fresh kernel ring after the mode has settled.
        step['crc_samples'] = crc.samples
        step['crc'] = crc_metrics(crc.samples, state['current']['hz'], self.a.crc_source)
        if crc.errors:
            step['crc']['reader_errors'] = crc.errors
            step['crc']['status'] = 'INCOMPLETE'
        return step['crc']['status']

    def step(self, name, mode, action=None, nightlight=None):
        step = {'name': name, 'requested_mode': mode, 'status': 'INCOMPLETE',
                'start_monotonic': time.monotonic(), 'mode_matches': None,
                'crc': {'status': 'INCOMPLETE', 'fps': None, 'unique_crc': 0}}
        self.result['steps'].append(step)
        mark, log_errors = len(self.log.entries), len(self.log.errors)
        try:
            locked = unwrap(self.bus('GetActive', dest='org.gnome.ScreenSaver',
                                     path='/org/gnome/ScreenSaver', iface='org.gnome.ScreenSaver'))[0]
            if locked:
                raise Unsupported('GNOME session locked during test; no input sent')
            if nightlight is not None:
                step['nightlight_enabled'] = nightlight
                step['shell_before'] = self.shell_identity()
                if step['shell_before'] != self.shell_original:
                    raise RuntimeError('GNOME Shell PID or bus owner changed')
            if action:
                action()
            if nightlight is not None:
                step['color_state'] = self.wait_color(nightlight)
            observed = self.wait_mode(mode)
            time.sleep(max(2, self.a.settle_seconds) if nightlight is not None else self.a.settle_seconds)
            step['observed'] = self.wait_mode(mode)
            step['mode_matches'] = True
            step['status'] = self.sample(observed, step)
            after = self.state()
            step['mode_matches'] = mode_matches(after, self.original, mode) and self.power() == 0
            if not step['mode_matches']:
                step['status'] = 'FAIL'
            if nightlight is not None:
                step['shell_after'] = self.shell_identity()
                step['shell_unchanged'] = step['shell_after'] == self.shell_original
                if not step['shell_unchanged']:
                    step['status'] = 'FAIL'
                step['color_after'] = self.color_state()
                if (step['color_after'].get('NightLightActive') is not nightlight
                        or step['color_after'].get('Temperature') != (NIGHT_TEMPERATURE if nightlight else 6500)):
                    step['status'] = 'FAIL'
                if step['crc']['status'] == 'PASS':
                    if self.baseline_crc is None and not nightlight:
                        self.baseline_crc = step['crc']['stable_crc']
                        self.result['nightlight']['baseline_crc'] = self.baseline_crc
                    step['crc_matches_endpoint'] = nightlight_crc_matches(
                        step['crc'], nightlight, self.baseline_crc)
                    if not step['crc_matches_endpoint']:
                        step['status'] = 'FAIL'
        except Unsupported as error:
            step.update(error=str(error), crc={'status': 'UNSUPPORTED', 'fps': None, 'unique_crc': None})
        except Exception as error:
            step.update(status='FAIL', error=str(error))
        finally:
            try:
                self.log.drain()
                step['kernel_log'] = self.log.entries[mark:]
                step['new_kernel_errors'] = filter_kernel([e['message'] for e in step['kernel_log']])
                step['log_gaps'] = self.log.errors[log_errors:]
                if step['new_kernel_errors']:
                    step['status'] = 'FAIL'
                elif step['log_gaps'] and step['status'] == 'PASS':
                    step['status'] = 'INCOMPLETE'
            except Exception as error:
                step['log_error'] = str(error)
                if step['status'] == 'PASS':
                    step['status'] = 'INCOMPLETE'
            step['end_monotonic'] = time.monotonic()
            save(self.out / f'{len(self.result["steps"]):02d}-{name}.json', step)
            save(self.out / 'result.json', self.result)
        return step['status'] == 'PASS'

    def dpms(self):
        try:
            self.changed = True
            self.power(3)
            if self.power() != 3:
                raise RuntimeError('Mutter did not report PowerSaveMode 3')
            time.sleep(self.a.off_seconds)
        finally:
            self.power(0)

    def nightlight_run(self):
        if self.original['current']['properties'].get('refresh-rate-mode', 'fixed') != 'fixed':
            raise Unsupported('night-light CRC requires a fixed refresh rate')
        self.shell_original = self.shell_identity()
        self.result['nightlight']['original_shell'] = self.shell_original
        try:
            self.result['nightlight']['original_color'] = self.color_state()
            self.settings = self.settings_snapshot()
        except Exception as error:
            raise Unsupported(f'GSD Color/GSettings unavailable: {error}') from error
        self.result['nightlight']['original_settings'] = self.settings
        if self.result['nightlight']['original_color'].get('DisabledUntilTomorrow'):
            raise Unsupported('night light is paused until tomorrow; existing pause was preserved')
        if not all(value['writable'] for value in self.settings.values()):
            raise Unsupported('night-light settings are locked by policy')
        # Check CRC availability before changing preferences; there is no LM
        # fallback because its tap is before GC and cannot prove color changes.
        with CRC(discover_crc(), 'encoder'):
            pass
        self.pointer = Pointer(self.user, self.env)
        width, height = self.original['current']['width'], self.original['current']['height']
        if self.original['transform'] % 2:
            width, height = height, width
        if self.original['properties']['layout-mode'] == 1:
            width, height = width / self.original['scale'], height / self.original['scale']
        self.pointer.move(-2 * width, -2 * height)
        self.pointer.move(width / 2, height / 2)
        self.result['nightlight']['pointer_parked_monotonic'] = time.monotonic()
        now = time.localtime()
        schedule = nightlight_schedule(now.tm_hour + now.tm_min / 60 + now.tm_sec / 3600)
        self.result['nightlight']['test_settings'] = schedule

        def configure():
            for key, value in schedule.items():
                self.setting(key, value)

        mode = self.original['current']
        if not self.step('nightlight-baseline-off', mode, configure, nightlight=False):
            return
        for cycle in range(1, self.a.nightlight + 1):
            for enabled in (True, False):
                name = f'nightlight-{cycle:03d}-' + ('on' if enabled else 'off')
                if not self.step(name, mode,
                                 lambda on=enabled: self.setting('night-light-enabled', str(on).lower()),
                                 nightlight=enabled):
                    return
        self.result['status'] = 'PASS'

    def run(self):
        try:
            if os.geteuid() != 0:
                raise Unsupported('run as root on the target tablet')
            if not re.search(r'(?:^|\s)androidboot.serialno=' + re.escape(self.a.serial) + r'(?:\s|$)', Path('/proc/cmdline').read_text()):
                raise Unsupported('target serial mismatch')
            if os.uname().release != self.a.kernel:
                raise Unsupported('running kernel differs from --kernel')
            if b'xiaomi,piano\0' not in Path('/proc/device-tree/compatible').read_bytes():
                raise Unsupported('not a piano device tree')
            self.result['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            # Cross-output-directory single writer; abstract socket/host files unused.
            self.lock = open('/run/piano-display-mode-check.lock', 'a')
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.session()
            self.original = self.state()
            self.result['original'] = self.original
            self.result['original_power_save_mode'] = self.power()
            if self.power() != 0:
                raise Unsupported('start with the desktop awake (PowerSaveMode=0)')
            try:
                self.log = KernelLog()
            except OSError as error:
                raise Unsupported(f'cannot capture new kernel messages: {error}') from error
            if self.a.nightlight:
                self.nightlight_run()
                return
            modes = choose_modes(self.original)
            self.result['selected_modes'] = modes
            self.pointer = Pointer(self.user, self.env)
            if not self.step('original', self.original['current']):
                return
            for index, mode in enumerate([modes[60], modes[120], modes[90], modes[144], modes[120], self.original['current']]):
                if not self.step(f'mode-{index + 1}', mode, lambda m=mode: self.apply(m)):
                    return
            for rate in (120, 144):
                if not self.step(f'dpms-{rate}-before', modes[rate], lambda m=modes[rate]: self.apply(m)):
                    return
                if not self.step(f'dpms-{rate}-after', modes[rate], self.dpms):
                    return
            self.result['status'] = 'PASS'
        except Unsupported as error:
            self.result['errors'].append(str(error))
        except Exception as error:
            self.result['errors'].append(f'{type(error).__name__}: {error}')
            self.result['status'] = 'FAIL'
        finally:
            self.restore()
            statuses = [s['status'] for s in self.result['steps']]
            if 'FAIL' in statuses or self.result['restore'].get('errors'):
                self.result['status'] = 'FAIL'
            elif 'INCOMPLETE' in statuses or 'UNSUPPORTED' in statuses:
                self.result['status'] = 'INCOMPLETE'
            self.result['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            save(self.out / 'result.json', self.result)

    def restore(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        errors = []
        mark = len(self.log.entries) if self.log else 0
        # Each cleanup is independent, so a failed Stop cannot skip screen restore.
        if self.a.nightlight:
            self.restore_settings(errors)
            if self.settings_changed:
                # Include the restored preferences' normal 5-second GSD ramp
                # in the kernel-log window, including when interrupted.
                time.sleep(6)
            if self.shell_original:
                try:
                    self.result['restore']['shell'] = self.shell_identity()
                    if self.result['restore']['shell'] != self.shell_original:
                        errors.append('GNOME Shell PID or bus owner changed')
                except Exception as error:
                    errors.append(f'Shell restore readback: {error}')
        elif self.user and self.original:
            for label, operation in [('power_on_before', lambda: self.power(0)),
                                     ('original_mode', lambda: self.apply(self.original['current'])
                                      if self.changed else 'unchanged; no mode/DPMS request issued'),
                                     ('power_on_after', lambda: self.power(0)),
                                     ('readback', lambda: self.wait_mode(self.original['current']))]:
                try:
                    value = operation()
                    self.result['restore'][label] = value if value is not None else 'requested'
                except Exception as error:
                    errors.append(f'{label}: {error}')
        # The final restoration is another modeset. A healthy test must not
        # return PASS based solely on its software readback if that modeset
        # leaves the LM showing a border again. Do not extend a failed run.
        if self.changed and not errors and self.pointer and self.log and self.result['status'] == 'PASS':
            self.result['restore']['crc_validated'] = self.step('restored-original', self.original['current'])
        if self.pointer:
            try:
                self.pointer.close()
            except Exception as error:
                errors.append(f'pointer: {error}')
        if self.log:
            try:
                self.log.drain()
                self.result['restore']['kernel_errors'] = filter_kernel(
                    [entry['message'] for entry in self.log.entries[mark:]])
                if self.result['restore']['kernel_errors']:
                    errors.append('new kernel errors during final restoration')
                if self.log.errors and self.result['status'] == 'PASS':
                    self.result['status'] = 'INCOMPLETE'
                save(self.out / 'kernel-log.json', {'entries': self.log.entries, 'gaps': self.log.errors})
            except Exception as error:
                errors.append(f'kernel log: {error}')
            finally:
                self.log.close()
        if self.lock:
            self.lock.close()
        self.result['restore']['errors'] = errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', required=True, type=Path, help='new private directory')
    parser.add_argument('--serial', required=True, help='expected piano androidboot.serialno')
    parser.add_argument('--kernel', required=True, help='exact expected uname -r (baseline or candidate)')
    parser.add_argument('--label', default='display-mode-check')
    parser.add_argument('--crc-source', choices=('auto', 'lm', 'encoder'),
                        help='default: encoder for --nightlight, auto for mode checks')
    parser.add_argument('--nightlight', type=int, metavar='N',
                        help='N on/off cycles at 4000 K; static scene, fixed mode, encoder CRC')
    parser.add_argument('--sample-seconds', type=float, default=3.0)
    parser.add_argument('--settle-seconds', type=float, default=1.0)
    parser.add_argument('--off-seconds', type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.nightlight is not None:
        if not 1 <= args.nightlight <= 1000:
            parser.error('--nightlight must be 1..1000 cycles')
        if args.crc_source not in (None, 'encoder'):
            parser.error('--nightlight requires --crc-source encoder (post-GC)')
    if args.crc_source is None:
        args.crc_source = 'encoder' if args.nightlight else 'auto'
    for key, lo, hi in [('sample_seconds', 1, 10), ('settle_seconds', 0.2, 5), ('off_seconds', 1, 5)]:
        value = getattr(args, key)
        if not math.isfinite(value) or not lo <= value <= hi:
            parser.error(f'{key} must be {lo}..{hi} seconds')
    if not re.fullmatch(r'[A-Za-z0-9._-]+', args.serial):
        parser.error('invalid serial')
    check = Check(args)
    def interrupted(signum, frame):
        # Ignore subsequent signals while try/finally restores the display.
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        raise RuntimeError(f'interrupted by signal {signum}')
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    check.run()
    print(json.dumps({'status': check.result['status'], 'output': str(check.out),
                      'steps': [(s['name'], s['status']) for s in check.result['steps']],
                      'restore_errors': check.result['restore'].get('errors', [])}, ensure_ascii=False))
    return {'PASS': 0, 'FAIL': 1, 'INCOMPLETE': 2}[check.result['status']]


if __name__ == '__main__':
    sys.exit(main())
