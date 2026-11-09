#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Emit a bounded native-display capture for developer-mode SSH, or judge saved
captures. This tool itself never connects to a device. Legacy console transport
is only supported by an explicitly prepared piano-ram-debug RAM boot; the
normal support package does not provide that console.

script() defaults to a gated native INTF/TOP0 counter probe. An external
legacy scanout probe can still be supplied; its splash colours are historical
data, never native frame proof. cold_boot_script() adds piano8 registration
and sound-card readings for the persistent cold-boot tool.
"""
import argparse
import json
from pathlib import Path
import re

import piano_boot_acceptance as acceptance

HF_AXI_CONSUMERS = {
    'framebuffer', 'ae96000.dsi', 'ae00000.display-subsystem',
    'ae01000.display-controller', 'mdss', 'dpu',
}
HF_AXI_RULE = ('every captured row enable >= 1, hardware Y, consumer framebuffer, ae96000.dsi or MDSS/DPU; '
               'every CBCR CLK_ENABLE=1, CLK_OFF=0; any observed disabled/unheld gap fails; '
               'PASS needs both clk_summary and CBCR evidence')
RAM_TIMING_NOTE = (
    'RAM full-module boot; persistent budgets '
    f'(switch_root ≤{acceptance.BUDGETS["switch_root_s"]:g} s, '
    f'GDM ≤{acceptance.BUDGETS["gdm_active_s"]:g} s) apply only to the cold-boot acceptance'
)
TOP0 = re.compile(r'TOP0 intr_en=(0x[0-9a-fA-F]+) intr_status '
                  r'(0x[0-9a-fA-F]+)->(0x[0-9a-fA-F]+)')
UNDERRUN_RULE = ('TOP0 status bits 26 (INTF1) and 28 (INTF2) clear in both samples; '
                 'both hardware frame counters advance; any observed underrun or frame timeout fails; '
                 'missing TOP0/INTF evidence is UNAVAILABLE')
SPLASH_NOTE = 'splash-memory colour sampling: not applicable (native KMS)'


def native_intfs(text):
    """Reuse the probe grammar, but keep the actual counters as evidence."""
    result = []
    for name, en, first, last, delta, seconds, intr1, intr2 in acceptance.INTF.findall(text):
        if name not in ('INTF1', 'INTF2'):
            continue
        frames = (int(last) - int(first)) & 0xffffffff
        period = float(seconds)
        result.append({'name': name, 'timing_en': int(en),
                       'frame_count': [int(first), int(last)], 'frames': frames,
                       'reported_frames': int(delta), 'sample_s': period,
                       'advancing': frames > 0 and frames == int(delta),
                       'hz': frames / period if period > 0 else None,
                       'intr_status': [intr1, intr2], 'intr_same': int(intr1, 16) == int(intr2, 16)})
    return result


def zero_underrun(text, intfs, encoders):
    top = [{'intr_en': en, 'intr_status': [first, last],
            'underrun': {name: [(int(v, 16) >> bit) & 1 for v in (first, last)]
                         for name, bit in (('INTF1', 26), ('INTF2', 28))}}
           for en, first, last in TOP0.findall(text)]
    bad = (any(any(bits) for sample in top for bits in sample['underrun'].values())
           or any(not i['advancing'] for i in intfs)
           or any(e['underrun'] or e['frame_timeout'] for e in encoders))
    complete = bool(top) and {i['name'] for i in intfs} == {'INTF1', 'INTF2'}
    status = 'FAIL' if bad else 'PASS' if complete else 'UNAVAILABLE'
    value = {'top0': top, 'intf': intfs, 'encoders': encoders}
    shown = ['TOP0 ' + '->'.join(t['intr_status']) + ' ' + ', '.join(
        f'{name} underrun {bits[0]}->{bits[1]}' for name, bits in t['underrun'].items()) for t in top]
    shown += [f'{i["name"]} frames {i["frame_count"][0]}->{i["frame_count"][1]} (+{i["frames"]})'
              for i in intfs]
    shown += [f'encoder INTF{e["intf"]} underrun={e["underrun"]} timeout={e["frame_timeout"]}'
              for e in encoders if e['underrun'] or e['frame_timeout']]
    if not complete:
        shown.append('missing TOP0 or INTF1/INTF2 evidence')
    return status, value, '; '.join(shown)

NATIVE = r'''
import ctypes, fcntl, glob, json, os, re, struct
from pathlib import Path
def read(p):
    try: return Path(p).read_text().strip()
    except OSError: return None
def ioctl(n, size): return (3 << 30) | (size << 16) | (ord('d') << 8) | n
result = {'uptime': float(Path('/proc/uptime').read_text().split()[0]),
          'connectors': {}, 'crtcs': [], 'encoders': [], 'errors': []}
for p in glob.glob('/sys/class/drm/card*-DSI-*'):
    result['connectors'][Path(p).name] = {k: read(p + '/' + k) for k in ('status', 'enabled', 'modes')}
for p in glob.glob('/sys/class/drm/card[0-9]*'):
    if not re.fullmatch(r'card\d+', Path(p).name): continue
    if Path(os.path.realpath(p + '/device/driver')).name != 'msm_dpu': continue
    try:
        fd = os.open('/dev/dri/' + Path(p).name, os.O_RDONLY)
        try:
            res = bytearray(64); fcntl.ioctl(fd, ioctl(0xa0, 64), res)
            count = struct.unpack_from('<I', res, 36)[0]
            if not 0 < count <= 16: raise ValueError('unexpected CRTC count')
            ids = ctypes.create_string_buffer(count * 4)
            struct.pack_into('<Q', res, 8, ctypes.addressof(ids))
            # Only the CRTC array has storage; do not pass nonzero counts for
            # the NULL FB/connector/encoder arrays returned by the first call.
            struct.pack_into('<4I', res, 32, 0, count, 0, 0)
            fcntl.ioctl(fd, ioctl(0xa0, 64), res)
            if struct.unpack_from('<I', res, 36)[0] != count:
                raise ValueError('CRTC list changed during query')
            for cid in struct.unpack('<' + 'I' * count, ids.raw):
                c = bytearray(104); struct.pack_into('<I', c, 12, cid)
                fcntl.ioctl(fd, ioctl(0xa1, 104), c)
                if struct.unpack_from('<I', c, 32)[0]:
                    result['crtcs'].append({'id': cid, 'card': Path(p).name,
                        'width': struct.unpack_from('<H', c, 40)[0],
                        'height': struct.unpack_from('<H', c, 50)[0],
                        'hz': struct.unpack_from('<I', c, 60)[0]})
        finally: os.close(fd)
    except (OSError, ValueError) as e: result['errors'].append(str(e))
for p in glob.glob('/sys/kernel/debug/dri/*/*/status'):
    text = read(p) or ''
    for intf, vsync, underrun, timedout in re.findall(
            r'intf:(\d+)\s+wb:-?\d+\s+vsync:\s*(\d+)\s+underrun:\s*(\d+)\s+frame_done_cnt:(\d+)', text):
        result['encoders'].append({'path': p, 'intf': int(intf), 'vsync': int(vsync),
                                   'underrun': int(underrun), 'frame_timeout': int(timedout)})
result['backlight'] = {Path(p).name: {k: read(p + '/' + k)
                         for k in ('brightness', 'actual_brightness', 'max_brightness', 'bl_power')}
                       for p in glob.glob('/sys/class/backlight/*')}
print('NATIVE_JSON=' + json.dumps(result, sort_keys=True))
'''


# Native KMS needs only frame/interrupt counters, not the old firmware splash.
# Register offsets: dpu_hw_intf.c / dpu_hw_interrupts.c, SM8750 INTF1/2.
# This payload is executed only inside script()'s existing runtime/genpd gate.
NATIVE_SCANOUT = r'''import mmap, os, struct, time
fd = os.open('/dev/mem', os.O_RDONLY | os.O_SYNC)
def read(addr):
    with mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ,
                   offset=addr & ~4095) as page:
        return struct.unpack_from('<I', page, addr & 4095)[0]
try:
    for name, base in (('INTF1', 0x0ae36000), ('INTF2', 0x0ae37000)):
        enabled = read(base)
        first, intr1 = read(base + 0xac), read(base + 0x1c4)
        time.sleep(0.5)
        last, intr2 = read(base + 0xac), read(base + 0x1c4)
        print(f'{name} timing_en={enabled} frame_count {first}->{last} '
              f'(+{(last-first) & 0xffffffff} in 0.5s) '
              f'intr_status {intr1:#x}->{intr2:#x}')
    enabled, first = read(0x0ae01010), read(0x0ae01014)
    time.sleep(0.5)
    last = read(0x0ae01014)
    print(f'TOP0 intr_en={enabled:#010x} intr_status {first:#010x}->{last:#010x}')
finally:
    os.close(fd)
'''

# Extracted from the piano8 runner's integration-check.sh (2026-10-12).
PIANO8_INTEGRATION = r'''echo '== piano8 combined device registration and login'
printf 'PIANO8_RELEASE='; uname -r
cat /proc/bus/input/devices
printf 'PIANO8_KEYBOARD_MODULE='; if test -d /sys/module/hid_nanosic_wn8030; then echo present; else echo missing; fi
printf 'PIANO8_MCA_MODULE='; if test -d /sys/module/xiaomi_mca; then echo present; else echo missing; fi
printf 'PIANO8_MCA_SUPPLY='; if test -d /sys/class/power_supply/xiaomi-mca-usb; then echo present; else echo missing; fi
for name in charge_policy hv_charge charge_current mipps_auth cp_charge; do
    f=/sys/module/xiaomi_mca/parameters/$name
    test -f "$f" && printf '%s=' "$f" && cat "$f"
done
printf 'PIANO8_CLOCK_WARNINGS='
if kernel_log=$(dmesg); then
    printf '%s\n' "$kernel_log" | grep -cE 'WARNING:|already (disabled|unprepared)|DSI.*lock failed|rcg didn.t update|dsi_err_worker' || :
else
    echo UNAVAILABLE
fi
loginctl list-sessions --no-legend --no-pager
wayland_users=0
for sid in $(loginctl list-sessions --no-legend --no-pager | awk '{print $1}'); do
    session_state=$(loginctl show-session "$sid" -p Active -p Class -p Type -p User)
    printf '%s\n' "$session_state"
    if printf '%s\n' "$session_state" | awk -F= '/^Active=/{a=$2} /^Class=/{c=$2} /^Type=/{t=$2} END{exit !(a=="yes" && c=="user" && t=="wayland")}'; then
        wayland_users=$((wayland_users + 1))
    fi
done
printf 'PIANO8_ACTIVE_WAYLAND_USERS=%s\n' "$wayland_users"
echo 'PIANO8_INTEGRATION_DONE'
'''


def cold_boot_script():
    """The shared native capture plus piano8 registration and ALSA evidence."""
    return (script() + PIANO8_INTEGRATION +
            '\necho "== sound"\ncat /proc/asound/cards\n' +
            'echo COLD_BOOT_CAPTURE_DONE\n')


def script(probe=None):
    if probe is None:
        probe = NATIVE_SCANOUT
    if '\nPIANO_NATIVE_PROBE\n' in '\n' + probe + '\n':
        raise ValueError('Probe contains the heredoc delimiter')
    # No expanded register dump: the earlier broad MDSS dump caused a TZ
    # fatal reset. Check the same MDSS/DPU runtime and genpd gates as step 2.
    gate = '''
D=/sys/devices/platform/soc@0/ae00000.display-subsystem
G=$(awk '/^mdss_gdsc /{print $2}' /sys/kernel/debug/pm_genpd/pm_genpd_summary)
echo "NATIVE_MDSS_RT=$(cat $D/power/runtime_status 2>/dev/null)"
echo "NATIVE_DPU_RT=$(cat $D/ae01000.display-controller/power/runtime_status 2>/dev/null)"
if [ "$(cat $D/power/runtime_status 2>/dev/null)" = active ] &&
   [ "$(cat $D/ae01000.display-controller/power/runtime_status 2>/dev/null)" = active ] && [ "$G" = on ]; then
'''
    return (acceptance.device_script() + "\npython3 - <<'PIANO_NATIVE_STATE'\n" + NATIVE +
            "\nPIANO_NATIVE_STATE\n" + gate +
            "python3 - <<'PIANO_NATIVE_PROBE'\n" + probe + '\nPIANO_NATIVE_PROBE\n' +
            'else echo NATIVE_SCANOUT_SKIPPED=power-not-active; fi\n' +
            'echo NATIVE_DSI_ERRORS=$(dmesg | grep -cE "dsi_err_worker|DSI.*(error|timed out)|lock failed|Unhandled context fault")\n' +
            'echo "== native resources"\ncat /sys/kernel/debug/dri/*/state 2>/dev/null\n' +
            'cat /sys/kernel/debug/pm_genpd/pm_genpd_summary\n' +
            'cat /sys/kernel/debug/interconnect/interconnect_summary\n' +
            'echo "== RAM handoff and SSH listener"\n' +
            'findmnt -T /usr/lib/modules -o TARGET,SOURCE,FSTYPE,OPTIONS\n' +
            'grep -c ": OK$" /run/piano-native-display/modules-verified.txt\n' +
            'systemctl show piano-devmode-ssh.socket -p ActiveState -p SubState -p BindsTo -p FragmentPath\n' +
            'ss -ltn\n' +
            'echo "== touch runtime log and IRQ counts"\n' +
            'journalctl -b -u piano-thp.service --no-pager -o short-monotonic -n 40\n' +
            'grep -iE "nt36532|nvt|thp" /proc/interrupts || :\n' +
            'echo "== native boot log"\njournalctl -k -b --no-pager -o short-monotonic\n' +
            'echo NATIVE_CAPTURE_DONE\n')


def judge(directory, release):
    texts = [(p.name, p.read_text(errors='replace')) for p in sorted(directory.glob('root-*.txt'))]
    args = acceptance.parse_args(['evaluate', '--dir', str(directory), '--release', release])
    raw = acceptance.run_evaluate(args)
    # Firmware-only scanout does not describe native KMS. Evaluate clock
    # ownership below using the same rule for the legacy and native row names.
    required = {'simpledrm_init', 'switch_root', 'gdm_active', 'gmu_first_load', 'gpu_errors',
                'copy_256mib', 'thp_first_frame', 'modules_own_release', 'release_in_root',
                'config_dispcc_builtin', 'rpmhpd_icc_synced', 'failed_units', 'slot_attributes'}
    rows = [dict(r, required=r['name'] in required) for r in raw['criteria']]
    mode = next(r for r in rows if r['name'] == 'mode_fast')
    timings = {k: mode['value'][k] for k in ('switch_root_s', 'gdm_active_s', 'fbcon_s')}
    measured = '; '.join(f'{name} {timings[key]:.3f} s' if timings[key] is not None
                         else f'{name} not captured'
                         for name, key in (('switch_root', 'switch_root_s'), ('GDM', 'gdm_active_s')))
    mode.update(status='INFO', value=timings, measured=RAM_TIMING_NOTE + '; ' + measured,
                budget='informational for RAM full-module boot', rule=RAM_TIMING_NOTE, required=False)
    for row in rows:
        if row['name'] in ('store_manifest_release', 'slot_occupant'):
            row.update(status='INFO' if row['value'] is not None else 'UNAVAILABLE',
                       budget='informational for RAM boot from Android',
                       rule='pre-install store/slot state; RAM boot does not install the candidate')

    def add(name, status, value, **details):
        rows.append({'name': name, 'status': status, 'value': value, 'required': True, **details})

    native, scanout, dsi = [], [], []
    for label, text in texts:
        states = re.findall(r'^NATIVE_JSON=(.+)$', text, re.M)
        if not states:
            continue
        state = json.loads(states[-1]); native.append(state)
        modes = state['crtcs']
        modes_ok = (len(modes) == 1 and (modes[0]['width'], modes[0]['height']) == (3200, 2136)
                    and modes[0]['hz'] in (60, 90, 120, 144))
        native_ok = (modes_ok and not state['errors'] and any(
            c['status'] == 'connected' and c['enabled'] == 'enabled' for c in state['connectors'].values()))
        add('native_kms:' + label, 'PASS' if native_ok else 'FAIL', state)
        intfs = native_intfs(text)
        measured = [(i['name'], i['hz']) for i in intfs if i['hz'] is not None]
        expected = modes[0]['hz'] if modes_ok else None
        ok = (expected and {i for i, _ in measured} == {'INTF1', 'INTF2'} and
              all(abs(hz - expected) <= expected * 0.05 for _, hz in measured))
        add('intf_rate:' + label, 'PASS' if ok else 'FAIL' if measured else 'UNAVAILABLE',
            {'expected_hz': expected, 'measured': measured})
        bad = any(not i['advancing'] or i['timing_en'] != 1 or not i['intr_same'] or
                  (expected and i['hz'] is not None and abs(i['hz'] - expected) > expected * 0.05)
                  for i in intfs)
        complete = (expected and {i['name'] for i in intfs} == {'INTF1', 'INTF2'} and
                    all(i['hz'] is not None for i in intfs))
        scanout.append({'label': label, 'expected_hz': expected, 'intf': intfs,
                        'status': 'FAIL' if bad else 'PASS' if complete else 'UNAVAILABLE'})
        enc = [e for e in state['encoders'] if e['intf'] in (1, 2)]
        status, value, shown = zero_underrun(text, intfs, enc)
        add('zero_underrun:' + label, status, value, measured=shown, rule=UNDERRUN_RULE,
            source='scanout probe INTF frame counters and TOP0 status; native encoder error counters',
            budget='0 underrun bits in both TOP0 samples, advancing INTF1/INTF2 counters')
        dsi += [int(x) for x in re.findall(r'^NATIVE_DSI_ERRORS=(\d+)$', text, re.M)]
    row = next(r for r in rows if r['name'] == 'scanout')
    status = ('FAIL' if any(s['status'] == 'FAIL' for s in scanout) else
              'PASS' if scanout and all(s['status'] == 'PASS' for s in scanout) else 'UNAVAILABLE')
    shown = [s['label'] + ': ' + ', '.join(
        f'{i["name"]} {i["hz"]:g} Hz' if i['hz'] is not None else f'{i["name"]} rate unavailable'
        for i in s['intf']) + '; ' +
        ('counters advancing' if s['intf'] and all(i['advancing'] for i in s['intf']) else 'counters not advancing/missing') +
        '; ' + ('intr same' if all(i['intr_same'] for i in s['intf']) else 'intr changed') for s in scanout]
    row.update(status=status, value=scanout, measured='; '.join(shown + [SPLASH_NOTE]),
               source='native KMS CRTC mode and scanout probe INTF1/INTF2 frame/interrupt registers',
               budget='both INTF rates within 5% of the active CRTC mode, counters advancing, intr unchanged',
               rule='every native capture must pass; ' + SPLASH_NOTE)
    add('native_capture', 'PASS' if native else 'UNAVAILABLE', len(native))
    add('dsi_smmu_errors', 'PASS' if dsi and not any(dsi) else 'FAIL' if any(dsi) else 'UNAVAILABLE', dsi)
    # Require every sample to stay held, including the framebuffer -> KMS
    # handoff. A later enabled sample cannot erase an observed gap.
    for phase in ('initrd', 'root'):
        row = next(r for r in rows if r['name'] == 'hf_axi_held_' + phase)
        v = row['value'] or {}; clocks, registers = v.get('rows', []), v.get('cbcr', [])
        bad = (any(c['enable'] < 1 or c['hw'] != 'Y' or c['consumer'] not in HF_AXI_CONSUMERS
                   for c in clocks)
               or any(not int(x, 16) & 1 or int(x, 16) & (1 << 31) for x in registers))
        status = 'FAIL' if bad else 'PASS' if clocks and registers else 'UNAVAILABLE'
        row.update(status=status, rule=HF_AXI_RULE)
        add('native_hf_axi_' + phase, status, v)
    failures = [r['name'] for r in rows if r['required'] and r['status'] == 'FAIL']
    missing = [r['name'] for r in rows if r['required'] and r['status'] == 'UNAVAILABLE']
    return {'profile': 'native-display-ram', 'release': release, 'criteria': rows,
            'failed': failures, 'unavailable': missing,
            'hardware_verdict': 'FAIL' if failures else 'INCOMPLETE' if missing else 'PASS',
            'desktop_visual': 'PENDING_OPERATOR', 'brightness_slider': 'PENDING_OPERATOR',
            'dpms_off_on': 'MUST_RECORD_RESULT_SEPARATELY_D06_GAP',
            'note': 'No splash colour or process-active result counts as native desktop proof.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    emit = sub.add_parser('emit'); emit.add_argument('--scanout-probe', type=Path, required=True)
    check = sub.add_parser('judge'); check.add_argument('--dir', type=Path, required=True)
    check.add_argument('--release', required=True)
    a = p.parse_args()
    if a.action == 'emit': print(script(a.scanout_probe.read_text()))
    else: print(json.dumps(judge(a.dir, a.release), indent=2))


if __name__ == '__main__':
    main()
