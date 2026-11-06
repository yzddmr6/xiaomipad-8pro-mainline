#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build piano's v4 boot triplet and a v2 RAM check from explicit inputs.

--maintenance instead builds only boot-ram-maintenance.img, a v2 RAM image of
the kernel with the maintenance initramfs (build-initramfs.sh MAINTENANCE_DIR):
it stays in the initramfs, mounts no UFS partition, serves a root shell over
the NCM link and reboots to Fastboot when idle (initramfs/piano-maint).

Adapted from erhai build-mainline-gki.py/sign-boot-triplet.py; see NOTICE.
This tool writes regular output files only. It never discovers or flashes disks.
"""
import argparse
import gzip
import hashlib
import json
import lzma
from pathlib import Path
import re
import shutil
import struct
import subprocess
import uuid
import zlib

from initramfs_archive import read_newc, regular_file

# The debug UART (ttyMSM0) is not reachable without opening the tablet, and the
# kernel writes every console record to it at 115200 baud with interrupts off
# on the printing CPU, so a burst of errors stalls that CPU for seconds. No
# console= is passed by default: ABL appends console=ttynull to every command
# line, and console-ramoops (pstore) is always a console. --serial-console
# names the UART again for a debug boot that has it wired.
SERIAL_CONSOLE = 'console=ttyMSM0,115200n8'
# Debug-only observation of a boot that stops before userspace: the pstore copy
# of the log carries every level, each initcall and every clk disable/unprepare
# with its completion.
DEBUG_BOOT_TRACE = ('console=ttynull ignore_loglevel initcall_debug tp_printk trace_event='
                    'clk:clk_disable,clk:clk_disable_complete,clk:clk_unprepare,clk:clk_unprepare_complete')
# The RAM init (initramfs/init) reads piano.diag and piano.maint_idle; no root= at all.
MAINTENANCE_CMDLINE = 'rdinit=/init console=ttyMSM0,115200n8 log_buf_len=2M panic=15 piano.diag=maintenance'
# The v2 header's cmdline field: mkbootimg puts any rest in extra_cmdline, which piano's ABL drops
# (speakers-1, 2026-10-10: a 593-byte line arrived cut at byte 511, losing part of modprobe.blacklist=).
RAM_CMDLINE_MAX = 511


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def kernel_release(path):
    """Release string of an uncompressed arm64 Image, from its linux_banner."""
    match = re.search(rb'Linux version ([0-9][A-Za-z0-9.+-]*) \(', Path(path).read_bytes())
    return match[1].decode() if match else None


def native_kms_enabled(dtb):
    """The board DT, including when called by the distro update hook, owns KMS."""
    nodes = subprocess.check_output(['fdtget', '-l', str(dtb), '/soc@0'], text=True).split()
    if 'display-subsystem@ae00000' not in nodes:
        return False
    node = '/soc@0/display-subsystem@ae00000'
    props = subprocess.check_output(['fdtget', '-p', str(dtb), node], text=True).split()
    if 'status' not in props:
        return True  # Device tree's default status is available.
    status = subprocess.check_output(['fdtget', '-t', 's', str(dtb), node, 'status'], text=True).strip()
    return status in ('okay', 'ok')


def ram_image_size(kernel, initrd, dtb, profile):
    """V2 header and three page-aligned payloads, with no second stage."""
    page = profile['vendor_boot']['page_size']
    size = page + sum((path.stat().st_size + page - 1) // page * page
                      for path in (kernel, initrd, dtb))
    # A successful Fastboot download is not evidence that ABL can boot it.
    # Keep RAM images strictly below this unit's verified boot partition size.
    if size >= profile['boot']['capacity']:
        raise ValueError(f'RAM image {size} bytes must stay under the boot partition size '
                         f'({profile["boot"]["capacity"]} bytes)')
    return size


def empty_ramdisk():
    """gzip of an empty newc archive, byte-identical on every Python version.

    gzip.compress() wrote OS byte 0x03 under Python 3.12 and 0xff under 3.14,
    so init_boot.img depended on the interpreter. The header is written here
    with mtime 0, XFL 2 (level 9) and OS 3, the bytes of the 3.12-built release
    set; the body is zlib's raw deflate at level 9.
    """
    name = b'TRAILER!!!\0'
    blob = b'070701' + b''.join(f'{n:08x}'.encode() for n in
                              (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(name), 0)) + name
    blob += b'\0' * (-len(blob) % 4)
    deflate = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = deflate.compress(blob) + deflate.flush()
    return (b'\x1f\x8b\x08\x00' + struct.pack('<I', 0) + b'\x02\x03' + body +
            struct.pack('<II', zlib.crc32(blob) & 0xffffffff, len(blob)))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('kernel', 'dtb', 'initrd', 'profile', 'mkbootimg', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    for name in ('avbtool', 'key'):
        p.add_argument('--' + name, type=Path, help='Required except with --maintenance')
    root = p.add_mutually_exclusive_group()
    root.add_argument('--root-partuuid', type=uuid.UUID, help='The root partition of this unit')
    root.add_argument('--root-partlabel',
                      help='GPT name of the root partition (linux_rootfs): a set that boots on any unit')
    p.add_argument('--kernel-release', help='Expected release; the Image banner must carry it')
    p.add_argument('--bt-address-file', type=Path)
    p.add_argument('--wlan-address-file', type=Path,
                   help='Private aa:bb:cc:dd:ee:ff of this unit, set as the WCN7860 local-mac-address')
    p.add_argument('--no-serial', action='store_true',
                   help='Leave androidboot.serialno to ABL, which appends it to every command line; '
                        'a set that is not bound to one unit')
    p.add_argument('--debug', action='store_true')
    p.add_argument('--debug-timeout', type=int, default=0)
    p.add_argument('--native-display', action='store_true',
                   help='RAM display experiment: require the complete matching debug initrd and 600 s timeout')
    p.add_argument('--keyboard-transport', action='store_true',
                   help='RAM keyboard experiment: same full-module handoff and 600 s guard')
    p.add_argument('--module-blacklist', help='Explicit comma-separated module control for a debug boot')
    p.add_argument('--modprobe-blacklist',
                   help='Debug boot only: kmod modprobe.blacklist= (stops alias autoload; an explicit insmod still loads)')
    p.add_argument('--debug-boot-trace', action='store_true',
                   help='Debug boot only: add DEBUG_BOOT_TRACE (every level and initcall to pstore)')
    p.add_argument('--serial-console', action='store_true',
                   help='Name the debug UART as a console (console=ttyMSM0,115200n8); it needs a wired UART')
    p.add_argument('--maintenance', action='store_true',
                   help='Build only boot-ram-maintenance.img from a maintenance initramfs; no root, no AVB')
    p.add_argument('--maintenance-idle', type=int, default=600, metavar='SECONDS',
                   help='Maintenance idle limit before the reboot to Fastboot (30-3600, default 600)')
    a = p.parse_args()
    if a.maintenance:
        return maintenance(p, a)
    if a.avbtool is None or a.key is None or (a.root_partuuid is None and a.root_partlabel is None):
        p.error('--avbtool, --key and --root-partuuid or --root-partlabel are required')
    if a.debug_timeout and (not a.debug or not 60 <= a.debug_timeout <= 900):
        p.error('A 60–900 second timeout is only available for explicit debug boots')
    for control in (a.module_blacklist, a.modprobe_blacklist):
        if control and (not a.debug or not re.fullmatch(r'[a-z0-9_]+(?:,[a-z0-9_]+)*', control)):
            p.error('Module controls require an explicit debug boot and valid module names')
    if a.debug_boot_trace and not a.debug:
        p.error('The boot trace console is only available for explicit debug boots')
    if a.serial_console and a.debug_boot_trace:
        p.error('The boot trace prints every level; it must not go to the 115200 serial console')
    if a.root_partlabel is not None and not re.fullmatch(r'[A-Za-z0-9_.-]{1,36}', a.root_partlabel):
        p.error('A GPT partition name of 1-36 letters, digits, "_", "." or "-" is required')
    root_spec = (f'PARTUUID={a.root_partuuid}' if a.root_partuuid is not None else f'PARTLABEL={a.root_partlabel}')
    release = kernel_release(a.kernel)
    if not release:
        p.error('Kernel has no Linux version banner; expected an uncompressed arm64 Image')
    if a.kernel_release and a.kernel_release != release:
        p.error(f'Kernel banner is {release}, not {a.kernel_release}')
    profile = json.loads(a.profile.read_text())
    for name in ('boot', 'init_boot', 'vendor_boot'):
        if profile[name]['header_version'] != 4 or profile[name]['capacity'] <= 0:
            p.error('Expected verified stock v4 geometry')
    ram_image_size(a.kernel, a.initrd, a.dtb, profile)
    if a.native_display and a.keyboard_transport:
        p.error('Select one RAM experiment')
    if a.native_display or a.keyboard_transport:
        if not a.debug or a.debug_timeout != 600 or a.module_blacklist or a.modprobe_blacklist:
            p.error('RAM experiment requires --debug --debug-timeout 600 and no module blacklist')
        compressed = a.initrd.read_bytes()
        if compressed.startswith(b'\xfd7zXZ\0'):
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
            archive = decoder.decompress(compressed)
            if not decoder.eof or decoder.unused_data or decoder.check != lzma.CHECK_CRC32:
                p.error('Native display requires one complete CRC32 XZ stream')
            initrd_decoder = 'RD_XZ'
        else:
            archive = subprocess.check_output(['zstd', '-dc', str(a.initrd)])
            initrd_decoder = 'RD_ZSTD'
        entries = read_newc(archive)
        info = json.loads(regular_file(entries, 'etc/piano-native-display/manifest.json'))
        config = regular_file(entries, 'etc/piano-native-display/kernel.config')
        if (info['kernel_release'] != release or info['kernel_sha256'] != sha(a.kernel) or
                info['config_sha256'] != hashlib.sha256(config).hexdigest() or
                info['modules'].get('selection') != 'all'):
            p.error('Native display initrd/kernel/config/complete-module identity mismatch')
        options = ((initrd_decoder, 'SM_DISPCC_8750') if a.keyboard_transport else
                   (initrd_decoder, 'DRM_MSM', 'DRM_MSM_DPU', 'DRM_MSM_DSI', 'SM_DISPCC_8750',
                    'DRM_PANEL_NOVATEK_NT36532', 'BACKLIGHT_KTZ8866', 'BACKLIGHT_CLASS_DEVICE'))
        for option in options:
            if f'CONFIG_{option}=y'.encode() not in config.splitlines():
                p.error(f'Native display requires built-in {option}')
        for name, digest in info['modules']['files'].items():
            if hashlib.sha256(regular_file(entries, 'usr/' + name)).hexdigest() != digest:
                p.error(f'Native display packed module mismatch: {name}')
        checks = ''.join(f'{digest}  {name.removeprefix("lib/modules/")}\n'
                         for name, digest in sorted(info['modules']['files'].items())).encode()
        if regular_file(entries, 'etc/piano-native-display/modules.sha256') != checks:
            p.error('Native display runtime module checks differ from the packed payload')
        for row in info['firmware']:
            data = regular_file(entries, 'usr/lib/firmware/' + row['path'])
            if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                p.error(f'Native display packed firmware mismatch: {row["path"]}')
        if info.get('purpose') == 'iris':
            iris = [r for r in info['firmware'] if r['path'] == 'qcom/sm8750/vpu35_4v.mbn']
            if len(iris) != 1:
                p.error('Iris RAM check requires the locked VPU35 firmware')
            expected = f'{iris[0]["sha256"]}  {iris[0]["path"]}\n'.encode()
            if regular_file(entries, 'etc/piano-native-display/iris-firmware.sha256') != expected:
                p.error('Iris runtime firmware check differs from the packed payload')
        for name, digest in info['runtime_units'].items():
            if hashlib.sha256(regular_file(entries, 'etc/piano-native-display/systemd/' + name)).hexdigest() != digest:
                p.error(f'Native display runtime unit mismatch: {name}')
        for name, digest in info.get('return_files', {}).items():
            if hashlib.sha256(regular_file(entries, name)).hexdigest() != digest:
                p.error(f'Native display bounded-return file mismatch: {name}')
        hook = regular_file(entries, 'var/lib/dracut/hooks/pre-pivot/50-piano-native-display.sh')
        if hashlib.sha256(hook).hexdigest() != info['pre_pivot_sha256']:
            p.error('Native display handoff hook differs from its manifest')
        if a.keyboard_transport:
            if info.get('purpose') != 'keyboard':
                p.error('Require an explicitly prepared keyboard initrd')
            if b'CONFIG_HID_NANOSIC_WN8030=m' not in config.splitlines():
                p.error('Keyboard transport must be a module')
            if any(line.startswith(b'CONFIG_HID_NANOSIC=') for line in config.splitlines()):
                p.error('Duplicate keyboard transport is enabled')
            keyboard = '/soc@0/geniqup@ac0000/i2c@a98000/keyboard@4c'
            for prop, expected in (('compatible', 'nanosic,wn8030-piano'),
                                   ('status', 'okay'),
                                   ('firmware-name', 'nanosic/piano/MCU_Upgrade.bin')):
                actual = subprocess.check_output(['fdtget', '-t', 's', str(a.dtb), keyboard, prop], text=True).strip()
                if actual != expected:
                    p.error(f'Keyboard DT property mismatch: {prop}')
            subprocess.run(['fdtget', str(a.dtb), keyboard, 'nanosic,no-touchpad'], check=True)
            module_path = f'usr/lib/modules/{release}/kernel/drivers/hid/hid-nanosic-wn8030.ko'
            regular_file(entries, module_path)
        else:
            mdss = '/soc@0/display-subsystem@ae00000'
            for node in (mdss, mdss + '/display-controller@ae01000', mdss + '/dsi@ae94000', mdss + '/dsi@ae96000',
                         mdss + '/phy@ae95000', mdss + '/phy@ae97000'):
                status = subprocess.check_output(['fdtget', '-t', 's', str(a.dtb), node, 'status'], text=True).strip()
                if status != 'okay':
                    p.error(f'Native display node is not enabled: {node}')
            compatible = subprocess.check_output(['fdtget', '-t', 's', str(a.dtb),
                                                  mdss + '/dsi@ae94000/panel@0', 'compatible'], text=True).strip()
            if compatible != 'xiaomi,piano-nt36532 novatek,nt36532':
                p.error('Native display candidate must use the piano NT36532 panel node')
    a.output.mkdir(parents=True, exist_ok=False)
    out = a.output.resolve()
    shutil.copyfile(a.profile, out / 'profile.json')
    dtb = out / 'piano-boot.dtb'
    shutil.copyfile(a.dtb, dtb)
    if a.bt_address_file:
        address = a.bt_address_file.read_text().strip()
        if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address):
            p.error('Invalid local Bluetooth address')
        raw = bytes.fromhex(address.replace(':', ''))
        if raw in (bytes(6), b'\xff' * 6):
            p.error('Bluetooth address is not usable')
        node = '/soc@0/geniqup@8c0000/serial@898000/bluetooth'
        compatible = subprocess.check_output(['fdtget', '-t', 's', str(dtb), node, 'compatible'], text=True).strip()
        if compatible != 'qcom,wcn7860-bt':
            p.error('Expected the piano WCN7860 node')
        # hci_dev_get_bd_addr_from_property() consumes a little-endian bdaddr_t.
        subprocess.run(['fdtput', '-t', 'bx', str(dtb), node, 'local-bd-address',
                        *[f'{b:02x}' for b in reversed(raw)]], check=True)
    if a.wlan_address_file:
        address = a.wlan_address_file.read_text().strip()
        if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address):
            p.error('Invalid local WLAN address')
        raw = bytes.fromhex(address.replace(':', ''))
        if raw == bytes(6) or raw[0] & 1:
            p.error('WLAN address is not a usable unicast address')
        node = '/soc@0/pcie@1c00000/pcie@0/wifi@0'
        compatible = subprocess.check_output(['fdtget', '-t', 's', str(dtb), node, 'compatible'], text=True).strip()
        if compatible != 'pci17cb,110e':
            p.error('Expected the piano WCN7860 WLAN node')
        # ath12k reads it with device_get_mac_address() when the card reports none; byte order as written.
        subprocess.run(['fdtput', '-t', 'bx', str(dtb), node, 'local-mac-address',
                        *[f'{b:02x}' for b in raw]], check=True)
    console = DEBUG_BOOT_TRACE if a.debug_boot_trace else SERIAL_CONSOLE if a.serial_console else ''
    native_kms = native_kms_enabled(dtb)
    gpu_kms = 0 if native_kms else 1
    cmdline = (f'root={root_spec} rootfstype=ext4 rw rootwait '
               f'{console + " " if console else ""}log_buf_len=2M panic=15 '
               f'msm.separate_gpu_kms={gpu_kms} piano.boot=persistent systemd.gpt_auto=0 bootconfig')
    if a.native_display:
        cmdline += ' piano.native_display=1'
    if a.keyboard_transport:
        cmdline += ' piano.ram_modules=1'
    # V4 may expose the firmware serial only in bootconfig. Keep the explicit
    # development gadget bound to the locally verified installation profile.
    # ABL also appends androidboot.serialno to the kernel command line it
    # passes (seen on piano); --no-serial relies on that copy alone.
    if not a.no_serial:
        serial = profile.get('serial')
        if not isinstance(serial, str) or not re.fullmatch('[A-Za-z0-9]+', serial):
            p.error('Profile requires the verified device serial')
        cmdline += ' androidboot.serialno=' + serial
    if a.debug:
        cmdline += ' piano.debug=1'
    if a.debug_timeout:
        cmdline += f' piano.debug_timeout={a.debug_timeout}'
    if a.module_blacklist:
        cmdline += ' module_blacklist=' + a.module_blacklist
    if a.modprobe_blacklist:
        cmdline += ' modprobe.blacklist=' + a.modprobe_blacklist
    if len(cmdline.encode()) > RAM_CMDLINE_MAX:
        raise ValueError(f'command line is {len(cmdline.encode())} bytes; the RAM check keeps only 511')
    ram_bytes = ram_image_size(a.kernel, a.initrd, dtb, profile)
    empty = out / 'empty.cpio.gz'
    empty.write_bytes(empty_ramdisk())
    mk = ['python3', str(a.mkbootimg)]
    common = ['--header_version', '4', '--pagesize', str(profile['vendor_boot']['page_size'])]
    os_version = profile['boot']['avb_properties']['com.android.build.boot.os_version']
    patch = profile['boot']['avb_properties']['com.android.build.boot.security_patch']
    boot_version = ['--os_version', os_version + '.0.0', '--os_patch_level', patch[:7]]
    subprocess.run(mk + common + boot_version + ['--kernel', str(a.kernel), '--cmdline', cmdline,
                                  '--output', str(out / 'boot.img')], check=True)
    subprocess.run(mk + common + boot_version + ['--ramdisk', str(empty), '--output', str(out / 'init_boot.img')], check=True)
    vendor = profile['vendor_boot']
    offsets = ['--base', '0', '--kernel_offset', str(vendor['kernel_addr']),
               '--ramdisk_offset', str(vendor['ramdisk_addr']), '--tags_offset', str(vendor['tags_addr']),
               '--dtb_offset', str(vendor['dtb_addr'])]
    subprocess.run(mk + common + offsets + ['--vendor_ramdisk', str(a.initrd), '--dtb', str(dtb),
                                           '--vendor_boot', str(out / 'vendor_boot.img')], check=True)
    outputs = {}
    for name in ('boot', 'init_boot', 'vendor_boot'):
        image = out / (name + '.img')
        raw_hash = sha(image)
        salt = hashlib.sha256(b'piano-avb\0' + name.encode() + bytes.fromhex(raw_hash)).hexdigest()
        command = ['python3', str(a.avbtool), 'add_hash_footer', '--image', str(image),
                   '--partition_name', name, '--partition_size', str(profile[name]['capacity']),
                   '--rollback_index', str(profile[name]['rollback_index']), '--salt', salt,
                   '--algorithm', 'SHA256_RSA4096', '--key', str(a.key)]
        for prop, value in profile[name].get('avb_properties', {}).items():
            command += ['--prop', prop + ':' + value]
        subprocess.run(command, check=True)
        subprocess.run(['python3', str(a.avbtool), 'verify_image', '--image', str(image),
                        '--key', str(a.key)], check=True)
        if image.stat().st_size != profile[name]['capacity']:
            raise ValueError('Signed image must occupy the complete partition')
        outputs[name] = {'file': image.name, 'bytes': image.stat().st_size, 'sha256': sha(image)}
    ram = out / 'boot-ram-check.img'
    subprocess.run(mk + ['--header_version', '2', '--pagesize', str(vendor['page_size'])] + offsets +
                   ['--kernel', str(a.kernel), '--ramdisk', str(a.initrd), '--dtb', str(dtb),
                    '--cmdline', cmdline, '--output', str(ram)], check=True)
    if ram.stat().st_size != ram_bytes:
        raise ValueError('RAM image size differs from its admitted v2 layout')
    manifest = {'kind': 'piano-persistent-v4', 'kernel_release': release, 'native_kms': native_kms,
                'cmdline': cmdline, 'root': root_spec,
                'root_partuuid': str(a.root_partuuid) if a.root_partuuid is not None else None,
                'serial_in_cmdline': not a.no_serial,
                'debug': a.debug, 'debug_timeout': a.debug_timeout,
                'inputs': {n: sha(getattr(a, n)) for n in ('kernel', 'dtb', 'initrd', 'profile', 'mkbootimg', 'avbtool')},
                'private_bt_address_injected': bool(a.bt_address_file),
                'private_wlan_address_injected': bool(a.wlan_address_file), 'effective_dtb_sha256': sha(dtb),
                'outputs': outputs, 'ram_image': {'file': ram.name, 'sha256': sha(ram),
                                                 'bytes': ram_bytes,
                                                 'boot_partition_bytes': profile['boot']['capacity']},
                'scope': 'Image construction only; cold boot and recovery must be verified on piano'}
    if a.debug_boot_trace:
        manifest['debug_boot_trace'] = DEBUG_BOOT_TRACE
    if a.serial_console:
        manifest['serial_console'] = SERIAL_CONSOLE
    if a.keyboard_transport:
        manifest['keyboard_transport'] = True
    if a.native_display:
        manifest['native_display'] = True
        manifest['scope'] = ('RAM display candidate only; do not install the generated triplet. '
                             'Native KMS/GPU/DPMS and recovery are unverified on this unit.')
    if a.modprobe_blacklist:
        manifest['modprobe_blacklist'] = a.modprobe_blacklist
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(''.join(f'{r["sha256"]}  {r["file"]}\n' for r in outputs.values()))
    print(json.dumps({'output': str(out), 'images': list(outputs)}))


def maintenance(p, a):
    """boot-ram-maintenance.img: kernel, DTB and maintenance initramfs; the kernel stays in the initramfs."""
    unit = [n for n in ('root_partuuid', 'root_partlabel', 'bt_address_file', 'wlan_address_file',
                        'module_blacklist', 'modprobe_blacklist', 'avbtool', 'key') if getattr(a, n) is not None]
    if unit or a.debug or a.debug_timeout or a.debug_boot_trace or a.native_display or a.keyboard_transport:
        p.error('--maintenance takes no root, address, AVB or debug option')
    if not 30 <= a.maintenance_idle <= 3600:
        p.error('--maintenance-idle must be 30-3600 seconds')
    release = kernel_release(a.kernel)
    if not release:
        p.error('Kernel has no Linux version banner; expected an uncompressed arm64 Image')
    if a.kernel_release and a.kernel_release != release:
        p.error(f'Kernel banner is {release}, not {a.kernel_release}')
    entries = read_newc(gzip.decompress(a.initrd.read_bytes()))
    info = json.loads(regular_file(entries, 'etc/piano-maintenance.json'))
    regular_file(entries, 'bin/piano-maint')
    if info['kernel_release'] != release:
        p.error(f'The maintenance initramfs carries modules of {info["kernel_release"]}, not {release}')
    profile = json.loads(a.profile.read_text())
    vendor = profile['vendor_boot']
    ram_bytes = ram_image_size(a.kernel, a.initrd, a.dtb, profile)
    cmdline = f'{MAINTENANCE_CMDLINE} piano.maint_idle={a.maintenance_idle}'
    if not a.no_serial:
        serial = profile.get('serial')
        if not isinstance(serial, str) or not re.fullmatch('[A-Za-z0-9]+', serial):
            p.error('Profile requires the verified device serial (or use --no-serial)')
        cmdline += ' androidboot.serialno=' + serial
    if len(cmdline.encode()) > RAM_CMDLINE_MAX:
        p.error(f'command line is {len(cmdline.encode())} bytes; the RAM image keeps only 511')
    a.output.mkdir(parents=True, exist_ok=False)
    out = a.output.resolve()
    image = out / 'boot-ram-maintenance.img'
    subprocess.run(['python3', str(a.mkbootimg), '--header_version', '2', '--pagesize', str(vendor['page_size']),
                    '--base', '0', '--kernel_offset', str(vendor['kernel_addr']),
                    '--ramdisk_offset', str(vendor['ramdisk_addr']), '--tags_offset', str(vendor['tags_addr']),
                    '--dtb_offset', str(vendor['dtb_addr']), '--kernel', str(a.kernel), '--ramdisk', str(a.initrd),
                    '--dtb', str(a.dtb), '--cmdline', cmdline, '--output', str(image)], check=True)
    if image.stat().st_size != ram_bytes:
        raise ValueError('RAM image size differs from its admitted v2 layout')
    if cmdline.encode() not in image.read_bytes()[:4096]:
        raise ValueError('The command line is not in the v2 header')
    manifest = {'kind': 'piano-maintenance-ram', 'kernel_release': release, 'cmdline': cmdline,
                'serial_in_cmdline': not a.no_serial, 'idle_seconds': a.maintenance_idle,
                'inputs': {n: sha(getattr(a, n)) for n in ('kernel', 'dtb', 'initrd', 'profile', 'mkbootimg')},
                'initramfs_build_id': regular_file(entries, 'etc/build-id').decode().strip(),
                'payload_manifest_sha256': info['payload_manifest_sha256'],
                'image': {'file': image.name, 'bytes': image.stat().st_size, 'sha256': sha(image)},
                'scope': 'Image construction only; the environment must be verified on piano'}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(f'{manifest["image"]["sha256"]}  {image.name}\n')
    print(json.dumps({'output': str(out), 'image': image.name, 'sha256': manifest['image']['sha256']}))


if __name__ == '__main__':
    main()
