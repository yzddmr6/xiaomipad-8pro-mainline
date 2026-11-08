#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""On-device, root-only s2idle acceptance through logind/systemctl.

Run from SSH in a transient systemd service so a transport disconnect does not
own the test's lifetime. Requires the existing systemd tools, rtcwake, iw, ip,
ping and runuser. No driver loading, unloading, rebinding or service restart.
Only a temporary /run sleep override, an RTC alarm, a private trace instance,
and Mutter's transient PowerSaveMode are changed. No /etc or GNOME settings.
Results contain private device/network identity; do not publish them unredacted.

Automatic mode proves >=80% of the requested RTC interval was spent asleep.
Physical mode instead requires positive measured sleep and a physical wake IRQ;
the longer safety RTC is a deadline, never the minimum physical sleep duration.
SIGINT/TERM/HUP during a sleep request defer cleanup until that attempt returns
or its alarm/deadline passes. Kernel deadlock, SIGKILL or power loss cannot be
made recoverable by Python, an RTC alarm, or a userspace timeout.
"""
import argparse
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

POLICY = Path('/run/systemd/sleep.conf.d/90-piano-suspend-check.conf')
PRODUCT_POLICY = Path('/usr/lib/systemd/sleep.conf.d/50-piano-first-release.conf')
POLICY_TEXT = '[Sleep]\nAllowSuspend=yes\nSuspendState=freeze\n'
TRACE_ROOT = Path('/sys/kernel/tracing/instances')
MUTTER = 'org.gnome.Mutter.DisplayConfig'
WAKE_NAMES = {'pwrkey': r'pwrkey|kpdpwr|power[-_ ]?key',
              # The I2C core also arms the data IRQ (nanosic_wn8030_irq) of a wakeup-source client.
              'keyboard': r'nanosic|keyboard', 'lid': r'\blid\b|lid[_-]|hall'}
RTC_NAME = re.compile(r'rtc', re.I)
ERROR_WORD = r'\b(?:error|failed|failure|fault|timeout|timed out|crash|hang|stalled)\b|\b(?:err|ret)[=: ]+-[0-9]+'
KERNEL_ERROR = re.compile(r'\bWARNING\b|Call trace:|\bBUG:|\bOops:|\btimeout\b|timed out|failed to resume|'
                          r'(?:smmu|iommu).*fault|(?:ath12k|qca\w*|iris|msm|nanosic|wn8030|i2c).*(' + ERROR_WORD + ')', re.I)


def save(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def clock_sample():
    left = time.monotonic_ns()
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    right = time.monotonic_ns()
    return {'boottime_ns': boot, 'monotonic_ns': (left + right) // 2,
            'uncertainty_ns': (right - left + 1) // 2}


def sleep_measurement(before, after, seconds, physical=False):
    boot = after['boottime_ns'] - before['boottime_ns']
    mono = after['monotonic_ns'] - before['monotonic_ns']
    uncertainty = before['uncertainty_ns'] + after['uncertainty_ns']
    slept = boot - mono
    lower = slept - uncertainty
    return {'boottime_delta_s': boot / 1e9, 'monotonic_delta_s': mono / 1e9,
            'sleep_s': slept / 1e9, 'sleep_lower_bound_s': lower / 1e9,
            'measurement_uncertainty_s': uncertainty / 1e9,
            'minimum_s': 0 if physical else seconds * .8,
            'pass': boot >= 0 and mono >= 0 and (lower > 0 if physical else lower >= seconds * .8e9)}


def suspend_delta(before, after):
    keys = {'fail'} | {k for k in before if k.startswith('failed_')}
    available = (keys <= before.keys() and keys <= after.keys() and len(keys) > 1 and
                 {k for k in before if k.startswith('failed_')} == {k for k in after if k.startswith('failed_')})
    delta = {k: int(after[k]) - int(before[k]) for k in sorted(keys)} if available else {}
    success = int(after['success']) - int(before['success'])
    return {'success_delta': success, 'failure_deltas': delta,
            'last_failed_dev': after.get('last_failed_dev'),
            'pass': available and success == 1 and not any(delta.values()) and after.get('last_failed_dev') == ''}


def wake_irq(irq, interrupts):
    result = {'irq': int(irq) if str(irq).strip().isdigit() else None, 'name': None, 'line': None}
    for line in interrupts.splitlines():
        m = re.match(r'\s*(\d+):\s*(.*)', line)
        if m and int(m[1]) == result['irq']:
            fields = m[2].split()
            counts = []
            while fields and fields[0].isdigit():
                counts.append(int(fields.pop(0)))
            result.update(name=' '.join(fields), counts=counts, line=line)
            break
    return result


def physical_wake(source, expected=None):
    # With interrupt deltas several candidates can rise (the keyboard bridge also
    # talks during suspend entry); any RTC increment still means the alarm woke us.
    names = [f['name'] for f in source.get('fired', [])] or [source.get('name') or '']
    if any(RTC_NAME.search(n) for n in names):
        return 'NO_PHYSICAL_WAKE'
    name = ' '.join(names)
    matches = [kind for kind, pattern in WAKE_NAMES.items() if re.search(pattern, name, re.I)]
    if expected:
        return 'PASS' if expected in matches else 'WRONG_WAKE_SOURCE'
    return 'PASS' if matches else 'UNKNOWN_PHYSICAL_WAKE'


# Known, functionally harmless err-level lines. dwc3-qcom logs this on every suspend while the
# USB device link to the test host is up (the HS PHY cannot enter L2 with VBUS and a configured
# gadget); it costs suspend power with the cable attached, not resume correctness.
BENIGN_KERNEL = re.compile(r'dwc3-qcom \S+: port-\d+ HS-PHY not in L2$')


def kernel_errors(records):
    return [r for r in records if not BENIGN_KERNEL.search(str(r.get('MESSAGE', '')).strip()) and
            (KERNEL_ERROR.search(str(r.get('MESSAGE', ''))) or
             (str(r.get('PRIORITY', '')).isdigit() and int(r['PRIORITY']) <= 3))]


def irq_counts(interrupts):
    counts = {}
    for line in interrupts.splitlines():
        m = re.match(r'\s*(\d+):\s*(.*)', line)
        if not m:
            continue
        fields = m[2].split()
        total = 0
        while fields and fields[0].isdigit():
            total += int(fields.pop(0))
        counts[int(m[1])] = (total, ' '.join(fields))
    return counts


def wake_from_counts(before, after):
    """Without CONFIG_PM_SLEEP_DEBUG there is no pm_wakeup_irq: name the candidate
    wake interrupts (RTC alarm, power key, keyboard, lid) whose count rose across the sleep."""
    a, b = irq_counts(before), irq_counts(after)
    pattern = re.compile('|'.join([RTC_NAME.pattern] + list(WAKE_NAMES.values())), re.I)
    fired = [{'irq': irq, 'name': b[irq][1], 'delta': b[irq][0] - a[irq][0]}
             for irq in b if irq in a and b[irq][0] > a[irq][0] and pattern.search(b[irq][1])]
    rtc = [f for f in fired if RTC_NAME.search(f['name'])]
    # An RTC increment means the alarm fired; report it first so a physical round cannot pass.
    first = (rtc or fired or [None])[0]
    return {'irq': first['irq'] if first else None, 'name': first['name'] if first else None,
            'method': 'proc_interrupts_delta', 'fired': fired}


def journal_interval(text, cursor):
    records = [json.loads(line) for line in text.splitlines() if line.startswith('{')]
    covered = bool(records) and records[0].get('__CURSOR') == cursor
    records = records[1:] if covered else records
    gaps = [r for r in records if re.search(r'Missed \d+ kernel messages|Suppressed \d+ messages|\d+ messages suppressed',
                                            str(r.get('MESSAGE', '')), re.I)]
    return {'cursor': cursor, 'coverage': covered and not gaps, 'gaps': gaps,
            'records': records, 'errors': kernel_errors(records)}


def i2c_results(text):
    rows = []
    for line in text.splitlines():
        m = re.search(r'i2c_result: i2c-(\d+) n=(\d+) ret=(-?\d+)', line)
        if m:
            rows.append({'adapter': int(m[1]), 'messages': int(m[2]), 'result': int(m[3]), 'line': line})
    return {'transfers': len(rows), 'errors': [r for r in rows if r['messages'] != r['result']]}


def process_stat(text):
    head, rest = text.rsplit(') ', 1)
    fields = rest.split()
    return {'pid': int(head.split(' ', 1)[0]), 'comm': head.split('(', 1)[1],
            'state': fields[0], 'starttime': int(fields[19])}


def d_tasks(proc=Path('/proc')):
    tasks = []
    for path in proc.glob('[0-9]*/task/[0-9]*/stat'):
        try:
            row = process_stat(path.read_text())
            if row['state'] == 'D':
                row.update(tgid=int(path.parts[-4]), tid=row.pop('pid'))
                tasks.append(row)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return tasks


def input_devices(text):
    rows = []
    for block in text.strip().split('\n\n'):
        name = re.search(r'^N: Name="(.*)"$', block, re.M)
        if name:
            rows.append({'name': name[1], 'events': re.findall(r'\bevent\d+\b', block), 'raw': block})
    return rows


def effective_sleep_config(text):
    section, values = '', {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]
        elif section == 'Sleep' and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


class TemporaryPolicy:
    """Exclusive ownership: never overwrite or remove a pre-existing override."""
    def __init__(self, path=POLICY):
        self.path, self.inode = path, None

    def create(self):
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
        try:
            self._create()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)

    def _create(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            self.inode = os.fstat(fd).st_ino
            with os.fdopen(fd, 'w') as f:
                fd = None
                f.write(POLICY_TEXT)
                f.flush()
        finally:
            if fd is not None:
                os.close(fd)

    def remove(self):
        if self.inode is not None:
            try:
                current = self.path.lstat().st_ino
            except FileNotFoundError:
                self.inode = None
                return
            if current != self.inode:
                raise RuntimeError('sleep override was replaced; refusing to remove another owner')
            self.path.unlink()
            self.inode = None


class StopTest(RuntimeError):
    pass


@contextmanager
def handled_signals(check):
    previous = {}

    def stop(signum, _frame):
        check.result['signal'] = signal.Signals(signum).name
        if not check.sleep_in_flight:
            raise StopTest('interrupted by ' + signal.Signals(signum).name)
        # Leave the already armed fallback in place until this attempt ends.

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[sig] = signal.signal(sig, stop)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def read_snapshot(thp_pid, rtc):
    """Runs in a child: a driver-backed sysfs read must not trap the supervisor."""
    errors = {}

    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError as e:
            errors[str(path)] = str(e)
            return None

    def char_node(path):
        try:
            return stat.S_ISCHR(path.stat().st_mode)
        except OSError:
            return False

    devices = input_devices(read('/proc/bus/input/devices') or '')
    for device in devices:
        device['nodes_present'] = bool(device['events']) and all(
            char_node(Path('/dev/input', event)) for event in device['events'])
    process = read(f'/proc/{thp_pid}/stat') if thp_pid else None
    hinge = Path('/sys/class/misc/nanosic_hinge')
    client = (hinge / 'device').resolve()
    match = re.fullmatch(r'(\d+)-004c', client.name)
    supplies = {}
    for path in sorted(Path('/sys/class/power_supply').glob('*')):
        kind = read(path / 'type')
        if kind == 'Battery':
            supplies[path.name] = {'type': kind, 'present': read(path / 'present'),
                                   'capacity': read(path / 'capacity'),
                                   'status': read(path / 'status'),
                                   'current_now': read(path / 'current_now')}
        elif path.name == 'xiaomi-mca-usb':
            supplies[path.name] = {'type': kind, 'online': read(path / 'online'),
                                   'usb_type': read(path / 'usb_type')}
            if (path / 'status').exists():
                supplies[path.name]['status'] = read(path / 'status')
    return {'boot_id': read('/proc/sys/kernel/random/boot_id'),
            'stats': {p.name: read(p) for p in Path('/sys/power/suspend_stats').glob('*') if p.is_file()},
            'clocks': clock_sample(), 'taint': read('/proc/sys/kernel/tainted'),
            'pm_test': read('/sys/power/pm_test') if Path('/sys/power/pm_test').exists() else None,
            'wakeup_irq': read('/sys/power/pm_wakeup_irq'), 'interrupts': read('/proc/interrupts'),
            'thp_process': process_stat(process) if process else None, 'inputs': devices,
            'keyboard': {'attached': read(hinge / 'keyboard_attached'), 'client': str(client),
                         'driver': (client / 'driver').resolve().name if (client / 'driver').exists() else None,
                         'adapter': int(match[1]) if match else None},
            'cards_raw': read('/proc/asound/cards'),
            'supplies': supplies,
            'video': {p.name: {'name': read(p / 'name'), 'node_present': char_node(Path('/dev', p.name))}
                      for p in Path('/sys/class/video4linux').glob('video*')},
            'display': {p.name: {'enabled': read(p / 'enabled'), 'dpms': read(p / 'dpms')}
                        for p in Path('/sys/class/drm').glob('card*-DSI-1')},
            'wifi_interfaces': [p.name for p in Path('/sys/class/net').glob('*')
                                if (p / 'wireless').exists() or (p / 'phy80211').exists()],
            'rtc': {'name': read(Path('/sys/class/rtc', rtc, 'name')),
                    'wakealarm': read(Path('/sys/class/rtc', rtc, 'wakealarm'))}, 'read_errors': errors}


def trace_action(action, path, adapter=None):
    if path.parent != TRACE_ROOT or not path.name.startswith('piano_suspend_check_'):
        raise ValueError('unexpected trace instance path')
    if action == 'start':
        (path / 'events/i2c/i2c_result/filter').write_text(f'adapter_nr == {int(adapter)}\n')
        (path / 'events/i2c/i2c_result/enable').write_text('1\n')
        (path / 'tracing_on').write_text('1\n')
        return {'instance': str(path)}
    if not path.exists():
        return {'absent': True}
    if action == 'read':
        (path / 'tracing_on').write_text('0\n')
        text = (path / 'trace').read_text()
        stats = {p.parent.name: p.read_text() for p in path.glob('per_cpu/cpu*/stats')}
        lost = sum(int(m[1]) for t in stats.values() for m in
                   re.finditer(r'^(?:overrun|commit overrun|dropped events):\s*(\d+)', t, re.M))
        return {'trace': text, 'per_cpu': stats, 'lost': lost,
                'coverage': bool(stats) and all(re.search(r'^overrun:\s*\d+', t, re.M) for t in stats.values()),
                **i2c_results(text)}
    try:
        (path / 'tracing_on').write_text('0\n')
        enable = path / 'events/i2c/i2c_result/enable'
        if enable.exists():
            enable.write_text('0\n')
    finally:
        path.rmdir()
    return {'removed': True}


class Check:
    def __init__(self, args):
        self.a = args
        self.out = args.output.resolve()
        self.out.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.policy = TemporaryPolicy()
        self.rtc_owned = False
        self.trace = None
        self.sleep_in_flight = False
        self.sequence = 0
        self.result = {'schema': 1, 'status': 'RUNNING', 'started_utc': time.strftime('%FT%TZ', time.gmtime()),
                       'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'options': vars(args) | {'output': str(self.out)}, 'rounds': [], 'cleanup': [],
                       'scope': 's2idle counters, elapsed sleep and peripheral recovery/presence; '
                                'not deep sleep, sleep power, or physical touch/key/audio/camera output acceptance'}

    def persist(self):
        save(self.out / 'result.json', self.result)
        for row in self.result['rounds']:
            save(self.out / f'round-{row["number"]:02}.json', row)

    def guard(self, phase):
        # Busy threads pass through D all the time: the first piano17 run caught its own
        # journalctl, piano22 the THP SPI IRQ thread reading 120 Hz frames. Block only a
        # task that stays in D for ~30 s without a single context switch.
        def switches(task):
            try:
                status = Path(f"/proc/{task['tgid']}/task/{task['tid']}/status").read_text()
            except OSError:
                return None
            return sum(int(v) for v in re.findall(r'^(?:non)?voluntary_ctxt_switches:\s+(\d+)$', status, re.M))
        blocked = [dict(t, switches=switches(t)) for t in d_tasks()]
        for _ in range(30):
            if not blocked:
                break
            time.sleep(1)
            now = {(t['tid'], t['starttime']): t for t in d_tasks()}
            blocked = [t for t in blocked if (t['tid'], t['starttime']) in now and
                       t['switches'] is not None and switches(t) == t['switches']]
        if blocked:
            self.result.setdefault('d_state', []).append({'phase': phase, 'tasks': blocked, 'clocks': clock_sample()})
            self.persist()
            raise StopTest('D-state task detected; no further rounds or hardware probes')

    def run(self, argv, tag, timeout=10, required=True, monitor=True):
        if monitor:
            self.guard(tag)
        self.sequence += 1
        log = self.out / f'{self.sequence:03}-{tag}.log'
        record = {'argv': list(map(str, argv)), 'log': log.name, 'start': clock_sample()}
        env = os.environ | {'LC_ALL': 'C', 'SYSTEMD_COLORS': '0', 'SYSTEMD_PAGER': 'cat'}
        with log.open('wb') as f:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                                     env=env, start_new_session=True)
            record['pid'] = child.pid
            with (self.out / 'commands.jsonl').open('a') as index:
                index.write(json.dumps(record) + '\n')
            deadline = time.monotonic() + timeout
            try:
                while child.poll() is None:
                    if monitor:
                        self.guard(tag)
                    if time.monotonic() >= deadline:
                        raise StopTest(f'{tag} exceeded {timeout}s; timeout does not cancel a kernel wait')
                    time.sleep(.1)
            except BaseException as e:
                # SIGKILL is not a remedy for D-state. Never wait indefinitely.
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    self.result.setdefault('unfinished_commands', []).append(record)
                record.update(returncode=child.poll(), end=clock_sample(), error=str(e))
                with (self.out / 'commands.jsonl').open('a') as index:
                    index.write(json.dumps(record) + '\n')
                raise
        record.update(returncode=child.returncode, end=clock_sample())
        with (self.out / 'commands.jsonl').open('a') as index:
            index.write(json.dumps(record) + '\n')
        text = log.read_text(errors='replace')
        if monitor:
            self.guard(tag + '-returned')
        if required and child.returncode:
            raise RuntimeError(f'{tag} exited {child.returncode}: {text[-1000:]}')
        return child.returncode, text

    def internal(self, mode, *args, monitor=True):
        _, text = self.run([sys.executable, str(Path(__file__).resolve()), '--internal', mode, *map(str, args)],
                           mode, monitor=monitor)
        return json.loads(text)

    def snapshot(self):
        _, text = self.run(['systemctl', 'show', 'piano-thp.service', '-p', 'MainPID', '-p', 'ActiveState'], 'thp')
        service = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        snapshot = self.internal('snapshot', int(service.get('MainPID', 0)), self.a.rtc)
        snapshot['thp_service'] = service
        snapshot['bluetooth'] = self.bluetooth()
        snapshot['wifi'] = {name: self.wifi(name) for name in snapshot['wifi_interfaces']}
        return snapshot

    def bluetooth(self):
        _, text = self.run(['busctl', '--system', '--auto-start=no', '--list', 'tree', 'org.bluez'], 'bluez-tree')
        controllers = re.findall(r'^/org/bluez/hci\d+$', text, re.M)
        result = {}
        for path in controllers:
            code, value = self.run(['busctl', '--system', '--auto-start=no', 'get-property', 'org.bluez', path,
                                    'org.bluez.Adapter1', 'Powered'], 'bluez-powered', required=False)
            result[path] = {'powered': not code and value.strip() == 'b true', 'raw': value}
        return result

    def wifi(self, name):
        code, link = self.run(['iw', 'dev', name, 'link'], 'wifi-link', required=False)
        return {'connected': code == 0 and bool(re.search(r'^Connected to ', link, re.M)),
                'returncode': code, 'raw': link}

    def gateway_ping(self, name):
        for family in ('4', '6'):
            _, text = self.run(['ip', '-j', '-' + family, 'route', 'show', 'default', 'dev', name], 'wifi-route')
            routes = [r for r in json.loads(text) if r.get('gateway')]
            if routes:
                route = min(routes, key=lambda r: r.get('metric', 0))
                gateway = str(ipaddress.ip_address(route['gateway']))
                code, text = self.run(['ping', '-' + family, '-n', '-I', name, '-c', '3', '-W', '2', gateway],
                                      'wifi-ping', timeout=10, required=False)
                return {'route': route, 'returncode': code, 'raw': text, 'pass': code == 0}
        return {'pass': False, 'error': 'connected Wi-Fi has no default gateway'}

    def wake_display(self):
        _, text = self.run(['loginctl', 'list-sessions', '--no-legend', '--no-pager'], 'sessions')
        candidates = []
        for line in text.splitlines():
            sid = line.split()[0]
            _, props = self.run(['loginctl', 'show-session', sid, '-p', 'User', '-p', 'Active', '-p', 'Seat',
                                  '-p', 'Type'], 'session')
            values = dict(line.split('=', 1) for line in props.splitlines() if '=' in line)
            if values.get('Active') == 'yes' and values.get('Seat') == 'seat0' and values.get('Type') in ('wayland', 'x11'):
                candidates.append(values)
        if len(candidates) != 1:
            raise RuntimeError('need exactly one active graphical seat0 session for Mutter')
        uid = int(candidates[0]['User'])
        user = pwd.getpwuid(uid).pw_name
        command = ['runuser', '-u', user, '--', 'env', f'XDG_RUNTIME_DIR=/run/user/{uid}',
                   f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus', 'busctl', '--user', '--auto-start=no']
        self.run(command + ['set-property', MUTTER, '/org/gnome/Mutter/DisplayConfig', MUTTER,
                            'PowerSaveMode', 'i', '0'], 'display-on')
        _, value = self.run(command + ['get-property', MUTTER, '/org/gnome/Mutter/DisplayConfig', MUTTER,
                                       'PowerSaveMode'], 'display-mode')
        return {'uid': uid, 'PowerSaveMode': value.strip(), 'pass': value.strip() == 'i 0'}

    def journal_cursor(self):
        _, text = self.run(['journalctl', '-k', '-b', '-n', '1', '-o', 'json', '--no-pager'], 'kernel-cursor')
        rows = [json.loads(line) for line in text.splitlines() if line.startswith('{')]
        if len(rows) != 1 or not rows[0].get('__CURSOR'):
            raise RuntimeError('no kernel journal cursor; refusing unbounded/ambiguous log comparison')
        return rows[0]['__CURSOR']

    def logs_since(self, cursor):
        self.run(['journalctl', '--sync'], 'journal-sync')
        # Include and verify the boundary. A rotated/missing cursor must not
        # turn an incomplete journal into a clean interval.
        _, text = self.run(['journalctl', '-k', '-b', '--cursor=' + cursor, '-o', 'json', '--no-pager'], 'kernel-round')
        return journal_interval(text, cursor)

    def wait_resume(self, success, deadline):
        while True:
            value = int(Path('/sys/power/suspend_stats/success').read_text())
            if value > success or time.clock_gettime(time.CLOCK_BOOTTIME) >= deadline:
                return {'success_increased': value > success, 'success': value, 'clocks': clock_sample()}
            time.sleep(.2)

    def cleanup(self):
        # A second signal must not interrupt restoration of the sleep prohibition.
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda *_: None)
        actions = [('sleep_override', self.policy.remove)] if self.policy.inode is not None else []
        if self.rtc_owned:
            actions.append(('rtc_alarm', lambda: self.run(['rtcwake', '-d', '/dev/' + self.a.rtc, '-m', 'disable'],
                                                          'rtc-disable', timeout=3, monitor=False)))
        if self.trace:
            actions.append(('trace_instance', lambda: self.internal('trace-stop', self.trace, monitor=False)))
        for name, action in actions:
            try:
                action()
                self.result['cleanup'].append({'resource': name, 'status': 'PASS'})
            except Exception as e:
                self.result['cleanup'].append({'resource': name, 'status': 'FAIL', 'error': str(e)})
                self.result['status'] = 'FAIL'


def peripheral_checks(before, after):
    def has_input(snapshot, name):
        return any(d['name'] == name and d['nodes_present'] for d in snapshot['inputs'])
    def cards(snapshot):
        return set(re.findall(r'^\s*\d+\s+\[([^\]]+)\]', snapshot['cards_raw'] or '', re.M))
    batteries = {n: p for n, p in before['supplies'].items()
                 if p['type'] == 'Battery' and p.get('present') != '0'}
    capacities = [after['supplies'].get(n, {}).get('capacity') for n in batteries]
    charger = after['supplies'].get('xiaomi-mca-usb', {})
    return {
        'thp_same_process': bool(before['thp_process']) and after['thp_process'] is not None and
                            all(before['thp_process'][k] == after['thp_process'][k] for k in ('pid', 'starttime')) and
                            after['thp_process']['state'] not in ('Z', 'X', 'x') and
                            after['thp_service'].get('ActiveState') == 'active',
        'touch_input': has_input(after, 'Piano THP Touchscreen'),
        'bluetooth_powered': bool(after['bluetooth']) and all(c['powered'] for c in after['bluetooth'].values()) and
                             before['bluetooth'].keys() <= after['bluetooth'].keys(),
        'sound_cards': bool(cards(before)) and cards(before) <= cards(after),
        'battery_capacity': bool(batteries) and all(v is not None and v.isdigit() and 0 <= int(v) <= 100 for v in capacities),
        'charger_readable': charger.get('online') in ('0', '1') and bool(charger.get('usb_type')) and
                            ('status' not in charger or bool(charger['status'])),
        'keyboard_bridge': after['keyboard']['attached'] == '1' and after['keyboard']['adapter'] is not None and
                           after['keyboard']['client'] == before['keyboard']['client'] and bool(after['keyboard']['driver']),
        'keyboard_input': has_input(after, 'Xiaomi Keyboard'),
        'video_nodes': all(n in after['video'] and after['video'][n]['node_present'] for n in before['video']),
    }


def charging_recovery_checks(before, after):
    """Opt-in: the charger stays connected and battery protection is off.

    Positive BQ27xxx current means net charging. Full batteries need not
    draw current; a readable offline charger never counts as recovery.
    """
    batteries = {n: p for n, p in before['supplies'].items()
                 if p['type'] == 'Battery' and p.get('present') != '0'}
    checks = {
        'charging_source_before': before['supplies'].get('xiaomi-mca-usb', {}).get('online') == '1',
        'charging_source_restored': after['supplies'].get('xiaomi-mca-usb', {}).get('online') == '1',
    }
    cells = [after['supplies'].get(n, {}) for n in batteries]
    valid = bool(cells) and all(str(p.get('capacity', '')).isdigit() and
                               0 <= int(p['capacity']) <= 100 for p in cells)
    full = valid and all(p.get('status') == 'Full' or p['capacity'] == '100' for p in cells)
    currents = [p.get('current_now') for p in cells]
    readable = all(re.fullmatch(r'-?[0-9]+', v or '') for v in currents)
    checks['battery_net_charging_or_full'] = valid and (full or
        (readable and sum(int(v) for v in currents) > 0))
    return checks


def run_round(check, number):
    a = check.a
    row = {'number': number, 'status': 'RUNNING', 'stage': 'before'}
    check.result['rounds'].append(row)
    check.persist()
    row['kernel_cursor'] = check.journal_cursor()
    before = row['before'] = check.snapshot()
    baseline = row['baseline_checks'] = peripheral_checks(before, before)
    if a.charging_source_connected:
        baseline.update(charging_recovery_checks(before, before))
    baseline['clean_suspend_history'] = before['stats'].get('last_failed_dev') == ''
    baseline['rtc_available'] = bool(before['rtc']['name']) and before['rtc']['wakealarm'] in ('', '0')
    baseline['success_counter'] = str(before['stats'].get('success', '')).isdigit()
    baseline['boot_id'] = bool(re.fullmatch(r'[0-9a-f-]{36}', before['boot_id'] or ''))
    baseline['no_pm_test'] = before.get('pm_test') in (None, '[none]', 'none') or '[none]' in before.get('pm_test', '')
    for name, wifi in before['wifi'].items():
        baseline['wifi_readable_' + name] = wifi['returncode'] == 0
        if wifi['connected']:
            wifi['ping'] = check.gateway_ping(name)
            baseline['wifi_' + name] = wifi['ping']['pass']
    if not all(baseline.values()):
        row.update(status='FAIL', stage='baseline_failed')
        check.persist()
        return
    path = TRACE_ROOT / ('piano_suspend_check_' + uuid.uuid4().hex)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
    try:
        path.mkdir()
        check.trace = path
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    check.internal('trace-start', check.trace, before['keyboard']['adapter'])
    seconds = a.safety_seconds if a.physical else a.seconds
    row['alarm_seconds'] = seconds
    row['stage'] = 'arming'
    check.persist()
    check.guard('before-rtc')
    check.rtc_owned = True  # Also clear if rtcwake is interrupted after setting it.
    arm_code, arm_text = check.run(['rtcwake', '-d', '/dev/' + a.rtc, '-m', 'no', '-s', str(seconds)], 'rtc-arm')
    row['rtc_arm'] = {'returncode': arm_code, 'raw': arm_text}
    row['clock_before'] = clock_sample()
    row['stage'] = 'suspend_requested'
    check.persist()
    if a.physical:
        print('请按电源键/按键盘键/开盖（进入睡眠后操作；RTC 仅作安全兜底）', flush=True)
    irq_before_sleep = Path('/proc/interrupts').read_text()
    check.sleep_in_flight = True
    try:
        # No D-state sampling during the transition: the freezer itself uses
        # uninterruptible waits. Inspect all tasks again immediately on return.
        code, text = check.run(['systemctl', 'suspend'], 'suspend', timeout=seconds + a.resume_timeout,
                               required=False, monitor=False)
        row['suspend_command'] = {'returncode': code, 'raw': text}
        row['resume_wait'] = check.wait_resume(int(before['stats']['success']),
                                              row['clock_before']['boottime_ns'] / 1e9 + seconds + a.resume_timeout)
        row['clock_after'] = row['resume_wait']['clocks']
    finally:
        check.sleep_in_flight = False
    row['stage'] = 'resumed' if row['resume_wait']['success_increased'] else 'resume_deadline_expired'
    check.persist()
    check.guard('after-suspend')
    if check.result.get('signal'):
        raise StopTest('interrupted during sleep; current attempt ended, no further rounds')
    # Capture the wake result before a failing desktop/peripheral check can
    # obscure an RTC fallback. These are PM/proc counters, not bus accesses.
    try:
        row['wake_source'] = wake_irq(Path('/sys/power/pm_wakeup_irq').read_text(),
                                      Path('/proc/interrupts').read_text())
    except OSError as e:
        row['wake_source'] = wake_from_counts(irq_before_sleep, Path('/proc/interrupts').read_text())
        row['wake_source']['pm_wakeup_irq_error'] = str(e)
    row['sleep'] = sleep_measurement(row['clock_before'], row['clock_after'], seconds, a.physical)
    row['physical_wake'] = 'NOT_REQUESTED'
    if a.physical:
        row['physical_wake'] = (physical_wake(row['wake_source'], a.expect_wake)
                                if row['resume_wait']['success_increased'] else 'NO_SUSPEND')
    check.persist()
    row['mutter'] = check.wake_display()
    deadline = time.monotonic() + a.recovery_timeout
    row['recovery_samples'] = []
    while True:
        after = row['after'] = check.snapshot()
        row['recovery_samples'].append(after)
        display_ok = bool(after['display']) and all(d == {'enabled': 'enabled', 'dpms': 'On'} for d in after['display'].values())
        wifi_ok = all(not w['connected'] or after['wifi'].get(n, {}).get('connected') for n, w in before['wifi'].items())
        charging_ok = (not a.charging_source_connected or
                       all(charging_recovery_checks(before, after).values()))
        if (display_ok and wifi_ok and charging_ok) or time.monotonic() >= deadline:
            break
        time.sleep(1)
    row['counters'] = suspend_delta(before['stats'], after['stats'])
    checks = row['checks'] = peripheral_checks(before, after)
    if a.charging_source_connected:
        checks.update(charging_recovery_checks(before, after))
    checks.update(same_boot=before['boot_id'] == after['boot_id'], success_and_failures=row['counters']['pass'],
                  actual_sleep=row['sleep']['pass'], suspend_command=code == 0,
                  resume_before_deadline=row['resume_wait']['success_increased'],
                  wake_irq_resolved=bool(row['wake_source']['name']), display=display_ok and row['mutter']['pass'])
    for name, wifi in before['wifi'].items():
        if wifi['connected']:
            after['wifi'].setdefault(name, {})['ping'] = check.gateway_ping(name)
            checks['wifi_' + name] = bool(after['wifi'][name].get('connected')) and after['wifi'][name]['ping']['pass']
    row['i2c'] = check.internal('trace-read', check.trace)
    checks['keyboard_i2c'] = row['i2c']['transfers'] > 0 and not row['i2c']['errors'] and row['i2c']['lost'] == 0 and row['i2c']['coverage']
    row['kernel_log'] = check.logs_since(row['kernel_cursor'])
    checks['kernel_log'] = row['kernel_log']['coverage'] and not row['kernel_log']['errors']
    check.guard('round-end')
    if a.physical:
        checks['physical_wake'] = row['physical_wake'] == 'PASS'
    row['status'] = 'PASS' if all(checks.values()) else ('NO_PHYSICAL_WAKE' if row['physical_wake'] == 'NO_PHYSICAL_WAKE' else 'FAIL')
    row['stage'] = 'complete'
    check.persist()
    check.run(['rtcwake', '-d', '/dev/' + a.rtc, '-m', 'disable'], 'rtc-disarm', monitor=False)
    check.rtc_owned = False
    check.internal('trace-stop', check.trace)
    check.trace = None
    print(f'round {number}: {row["status"]}, slept {row["sleep"]["sleep_s"]:.3f}s, wake={row["wake_source"]["name"]}', flush=True)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--internal':
        mode, *values = sys.argv[2:]
        if mode == 'snapshot':
            result = read_snapshot(int(values[0]), values[1])
        else:
            result = trace_action(mode.removeprefix('trace-'), Path(values[0]), values[1] if len(values) > 1 else None)
        print(json.dumps(result))
        return 0
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--serial', required=True, help='expected androidboot.serialno; checked locally before any change')
    ap.add_argument('--kernel', help='optional expected uname -r, e.g. the RAM candidate release')
    ap.add_argument('--output', required=True, type=Path, help='new private results directory on the tablet')
    ap.add_argument('--rounds', type=int, default=3)
    ap.add_argument('--seconds', type=int, default=30, help='automatic RTC sleep interval')
    ap.add_argument('--charging-source-connected', action='store_true',
                    help='require source online and net charging after wake (full exempt); '
                         'use with battery protection off and no unplug during sleep')
    ap.add_argument('--physical', action='store_true')
    ap.add_argument('--safety-seconds', type=int, default=180, help='physical mode fallback RTC interval')
    ap.add_argument('--expect-wake', choices=tuple(WAKE_NAMES), help='physical mode IRQ-name check')
    ap.add_argument('--rtc', default='rtc0', help='existing RTC device, default rtc0')
    ap.add_argument('--resume-timeout', type=int, default=30, help='awake grace after RTC deadline before failing')
    ap.add_argument('--recovery-timeout', type=int, default=30, help='bounded Wi-Fi association/display readiness wait')
    a = ap.parse_args()
    if not 1 <= a.rounds <= 100 or not all(1 <= x <= 3600 for x in (a.seconds, a.safety_seconds, a.resume_timeout, a.recovery_timeout)):
        ap.error('rounds must be 1..100 and durations 1..3600 seconds')
    if a.expect_wake and not a.physical:
        ap.error('--expect-wake requires --physical')
    if not re.fullmatch(r'rtc\d+', a.rtc):
        ap.error('--rtc must be an RTC name such as rtc0')
    if os.geteuid() != 0:
        ap.error('run on the tablet as root')
    cmdline = Path('/proc/cmdline').read_text().strip()
    match = re.search(r'(?:^|\s)androidboot.serialno=(\S+)', cmdline)
    if not match or match[1] != a.serial or (a.kernel and a.kernel != os.uname().release):
        ap.error('device serial/kernel mismatch; nothing changed')
    os.umask(0o077)
    check = Check(a)
    check.result['identity'] = {'kernel': os.uname().release, 'cmdline': cmdline,
                                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    check.persist()
    with handled_signals(check):
        try:
            check.guard('start')
            check.result['product_sleep_policy'] = PRODUCT_POLICY.read_text() if PRODUCT_POLICY.exists() else None
            check.policy.create()
            _, text = check.run(['systemd-analyze', 'cat-config', 'systemd/sleep.conf'], 'sleep-config')
            config = check.result['effective_sleep'] = effective_sleep_config(text)
            if config.get('AllowSuspend', '').lower() not in ('yes', 'true', '1') or config.get('SuspendState') != 'freeze':
                raise RuntimeError('effective sleep policy does not exclusively permit freeze')
            _, can = check.run(['busctl', '--system', '--auto-start=no', 'call', 'org.freedesktop.login1',
                                '/org/freedesktop/login1', 'org.freedesktop.login1.Manager', 'CanSuspend'], 'can-suspend')
            if can.strip() != 's "yes"':
                raise RuntimeError('logind CanSuspend is not yes: ' + can.strip())
            for number in range(1, a.rounds + 1):
                run_round(check, number)
                if check.result['rounds'][-1]['status'] != 'PASS':
                    break
            rows = check.result['rounds']
            check.result['status'] = 'PASS' if len(rows) == a.rounds and all(r['status'] == 'PASS' for r in rows) else rows[-1]['status']
        except (Exception, KeyboardInterrupt) as e:
            fallback = bool(check.result['rounds']) and check.result['rounds'][-1].get('physical_wake') == 'NO_PHYSICAL_WAKE'
            status = 'D_STATE' if check.result.get('d_state') else ('NO_PHYSICAL_WAKE' if fallback else 'FAIL')
            check.result.update(status=status, error=str(e))
            if check.result['rounds']:
                check.result['rounds'][-1].update(status=check.result['status'], error=str(e))
        finally:
            check.cleanup()
            check.result['finished_utc'] = time.strftime('%FT%TZ', time.gmtime())
            check.persist()
    print(f'{check.result["status"]}: {check.out / "result.json"}', flush=True)
    return 0 if check.result['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
