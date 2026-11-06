#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Boot a built release root image in QEMU to multi-user.target, without host root.

The root image goes into a temporary GPT disk as partition linux_rootfs next to
a small new linux_data, so the image's own dracut initrd and fstab find both by
PARTLABEL as on the tablet.  Kernel and initrd are read from the image; the
command line is the release boot set's with the QEMU console.  Disk writes go
to a QEMU snapshot.  This is QEMU virt, not piano hardware: no display, GPU,
UFS, touch, firmware loading or ABL.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

REACHED = b'Reached target multi-user.target'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def qmp(path, command):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(10)
        s.connect(str(path))
        stream = s.makefile('rwb')
        stream.readline()
        for message in ({'execute': 'qmp_capabilities'}, {'execute': command}):
            stream.write(json.dumps(message).encode() + b'\n')
            stream.flush()
            stream.readline()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--rootfs-build', type=Path, required=True, help='Output directory of build-rootfs-image.py')
    ap.add_argument('--qemu', type=Path, required=True)
    ap.add_argument('--debugfs', type=Path, required=True)
    ap.add_argument('--mke2fs', type=Path, required=True)
    ap.add_argument('--timeout', type=int, default=1200, help='Seconds to wait for multi-user.target')
    ap.add_argument('--kernel-arg', action='append', default=[],
                    help='Extra QEMU-only kernel parameter, e.g. apparmor=0 (recorded in result.json)')
    ap.add_argument('--smp', type=int, default=8)
    ap.add_argument('--memory-mib', type=int, default=4096)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    build = a.rootfs_build.resolve()
    manifest = json.loads((build / 'manifest.json').read_text())
    release = manifest['kernel_release']
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    image = build / 'root.ext4'
    result = {'state': 'RUNNING', 'tool_sha256': sha(Path(__file__)), 'rootfs_manifest_sha256': sha(build / 'manifest.json'),
              'rootfs_sparse_sha256': manifest['outputs']['root.sparse.img']['sha256'],
              'scope': 'QEMU virt with the image kernel, initrd and fstab; not piano hardware'}

    def save():
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')

    files = {}
    for name in ('vmlinuz', 'initrd.img'):
        target = out / f'{name}-{release}'
        subprocess.run([str(a.debugfs), '-R', f'dump /boot/{name}-{release} {target}', str(image)],
                       capture_output=True, check=True)
        if sha(target) != manifest['boot_files_sha256'][f'/boot/{name}-{release}']:
            raise SystemExit(f'{name} read from the image differs from the build manifest')
        files[name] = target
    # GPT disk: 1 MiB, linux_rootfs (the image), linux_data (1 GiB, new), backup GPT.
    root_bytes = image.stat().st_size
    start = 1 << 20
    disk = out / 'disk.img'
    with disk.open('xb') as f:
        f.truncate(start + root_bytes + (1 << 30) + (1 << 20))
    sectors = root_bytes // 512
    subprocess.run(['sgdisk', '-o', '-n', f'1:2048:{2048 + sectors - 1}', '-c', '1:linux_rootfs', '-t', '1:8300',
                    '-n', '2:0:+1G', '-c', '2:linux_data', '-t', '2:8300', str(disk)],
                   capture_output=True, check=True)
    layout = subprocess.check_output(['sgdisk', '-p', str(disk)], text=True)
    first, last = map(int, re.search(r'^\s+1\s+(\d+)\s+(\d+)', layout, re.M).groups())
    if (last - first + 1) * 512 != root_bytes:
        raise SystemExit('linux_rootfs partition size differs from the image')
    data_start = int(re.search(r'^\s+2\s+(\d+)\s+(\d+)', layout, re.M)[1]) * 512
    subprocess.run(['dd', f'if={image}', f'of={disk}', 'bs=4M', f'seek={start}', 'oflag=seek_bytes',
                    'conv=sparse,notrunc', 'status=none'], check=True)
    subprocess.run([str(a.mke2fs), '-q', '-F', '-t', 'ext4', '-b', '4096', '-L', 'linux_data', '-E', f'offset={data_start}',
                    str(disk), str((1 << 30) // 4096)], check=True)
    set_cmdline = manifest['boot_set']['cmdline']
    # QEMU's PL011 replaces whatever console the set names (by default none: ABL adds console=ttynull).
    cmdline = (' '.join(w for w in set_cmdline.split() if not w.startswith('console='))
               + ' console=ttyAMA0 systemd.unit=multi-user.target' + ''.join(' ' + arg for arg in a.kernel_arg))
    sock = out / 'qmp.sock'
    command = [str(a.qemu), '-machine', 'virt', '-cpu', 'max', '-accel', 'tcg,thread=multi',
               '-smp', str(a.smp), '-m', f'{a.memory_mib}M', '-nodefaults', '-display', 'none',
               '-monitor', 'none', '-serial', 'stdio', '-nic', 'none', '-no-reboot',
               '-qmp', f'unix:{sock},server=on,wait=off',
               '-drive', f'file={disk},format=raw,if=none,id=disk,snapshot=on',
               '-device', 'virtio-blk-device,drive=disk,serial=piano-disk',
               '-device', 'virtio-rng-device',
               '-kernel', str(files['vmlinuz']), '-initrd', str(files['initrd.img']), '-append', cmdline]
    home = re.escape(str(Path.home()))
    result.update(command=[re.sub(home + r'/[^\s,]*/([^/\s,]+)', r'<input>/\1', w.replace(str(out), '<output>'))
                           for w in command], cmdline=cmdline,
                  qemu_only_kernel_args=a.kernel_arg, timeout_seconds=a.timeout)
    save()
    log = out / 'console.log'
    started = time.monotonic()
    with log.open('wb') as stream:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        reached = None
        try:
            while time.monotonic() - started < a.timeout and proc.poll() is None:
                time.sleep(5)
                if REACHED in log.read_bytes():
                    reached = round(time.monotonic() - started, 1)
                    break
            if reached:
                time.sleep(30)  # late failures after the target
            result['powerdown_requested'] = proc.poll() is None
            if proc.poll() is None:
                qmp(sock, 'system_powerdown')
                try:
                    proc.wait(timeout=240)
                except subprocess.TimeoutExpired:
                    qmp(sock, 'quit')
                    proc.wait(timeout=30)
                    result['forced_quit'] = True
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for path in [disk, *files.values()]:
                path.unlink(missing_ok=True)
    text = log.read_bytes()
    # systemd colours its status lines; match on the text without the escape sequences.
    lines = [re.sub(r'\x1b\[[0-9;:]*[A-Za-z]', '', l.decode(errors='replace')) for l in re.split(rb'[\r\n]+', text)]
    result.update(
        elapsed_seconds=round(time.monotonic() - started, 1), qemu_returncode=proc.returncode,
        multi_user_reached_seconds=reached,
        root_by_partlabel=any('by-partlabel/linux_rootfs' in l or 'Reached target initrd-root-fs.target' in l
                              for l in lines),
        home_mounted=any(re.search(r'Mounted home\.mount', l) for l in lines),
        failed=sorted({l.strip() for l in lines if l.lstrip().startswith('[FAILED]')}),
        pending_jobs=sorted({m[1] for l in lines[-200:] if (m := re.search(r'Job (\S+)/start running', l))}),
        kernel_panic='Kernel panic' in text.decode(errors='replace'),
        clean_power_down=b'reboot: Power down' in text)
    result['passed'] = bool(reached and result['home_mounted'] and not result['kernel_panic']
                            and result['clean_power_down'])
    result['state'] = 'QEMU_MULTI_USER_PASS_NOT_PIANO' if result['passed'] else 'FAILED'
    save()
    print(json.dumps({k: result[k] for k in ('state', 'multi_user_reached_seconds', 'home_mounted', 'failed')},
                     indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
