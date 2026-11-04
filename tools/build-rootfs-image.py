#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the release root filesystem image from the pinned Desktop base and the piano packages.

sqfs2tar streams the pristine Desktop SquashFS into upstream
mke2fs -d (libarchive), creating the ext4 at the full partition size.  A
disposable QEMU ARM64 guest, whose own root is that SquashFS mounted
read-only, then runs rootfs-image-guest.sh as root: the steps of
docs/AGENT-GUIDE.en.md section 4.4 in a chroot of the target (policy-rc.d,
SYSTEMD_OFFLINE=1 dpkg --install). Userspace packages are installed first,
then the base's generic kernels and kdump are purged before the piano kernel
runs dracut. As root, the same steps run in a loop-mounted chroot instead: natively on
an aarch64 host, elsewhere through a binfmt_misc qemu-aarch64 handler with the F flag
(qemu-user-static).  Other hosts default to the QEMU guest.  Both emulators use the
implementation-defined pointer authentication algorithm (QEMU_CPU).  The target never boots.

The work before the piano kernel (Desktop base, userspace packages, distribution kernels
purged) depends only on its own inputs.  With --base-cache that state is stored under the
SHA-256 of those inputs and reused when only the kernel and its kernel-bound packages
change; the filesystem UUID derives from the same key.

The image is for any unit: fstab and /etc/piano/layout.json name the fixed
GPT partitions linux_rootfs and linux_data; the boot set is built with
--root-partlabel and --no-serial from a boot profile reduced to the stock
build's fields, without Bluetooth or Wi-Fi addresses.  There is no account
(GNOME Initial Setup creates the first one), no machine ID, no SSH host keys,
no package indexes and no per-unit file in /etc/piano; the hostnames name the
device model.  Outputs: root.ext4 (sparse),
root.sparse.img (sparse_image.py encode-ext4: only used blocks are written,
free blocks are DONT_CARE), SHA256SUMS, manifest.json, verification.json.
The sparse image SHA-256 is the root identity; the raw image records size only.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import shutil
import subprocess
import tarfile
import time
import uuid

import sparse_image

REPO = Path(__file__).resolve().parents[1]
ROOT_PARTLABEL = 'linux_rootfs'
DATA_PARTLABEL = 'linux_data'
FSTAB = (f'PARTLABEL={ROOT_PARTLABEL} / ext4 defaults,noatime 0 1\n'
         f'PARTLABEL={DATA_PARTLABEL} /home ext4 defaults,noatime 0 2\n')
LAYOUT = {'root_partlabel': ROOT_PARTLABEL, 'data_partlabel': DATA_PARTLABEL}
# Device names until the owner picks one: GNOME Initial Setup replaces the static
# hostname with its "Computer" field, the pretty hostname (Bluetooth name) stays.
HOSTNAME = 'xiaomipad-8pro\n'
MACHINE_INFO = 'PRETTY_HOSTNAME="Xiaomi Pad 8 Pro"\n'
# The profile fields build-persistent-boot.py reads.  They come from the stock
# build (header geometry, rollback indices, AVB properties); the serial, slot
# observations and image hashes of the unit they were read from are dropped.
PROFILE_FIELDS = {
    'boot': ('header_version', 'capacity', 'rollback_index', 'avb_properties'),
    'init_boot': ('header_version', 'capacity', 'rollback_index', 'avb_properties'),
    'vendor_boot': ('header_version', 'capacity', 'rollback_index', 'avb_properties',
                    'page_size', 'kernel_addr', 'ramdisk_addr', 'tags_addr', 'dtb_addr'),
}
PIANO_PACKAGES = ('piano-device-support', 'piano-local-firmware', 'piano-mesa', 'piano-ubuntu-desktop', 'piano-iris-vaapi')
UNITS_ENABLED = {'piano-slot-success.service': 'enabled', 'piano-devmode.service': 'disabled',
                 'piano-firefox-snap-remove.service': 'enabled',
                 'ssh.socket': 'disabled', 'ssh.service': 'disabled',
                 'fake-hwclock-load.service': 'enabled', 'fake-hwclock-save.service': 'enabled',
                 'fake-hwclock-save.timer': 'enabled'}
SERIALS = ('piano-helper', 'piano-target', 'piano-inputs', 'piano-result')
# Ubuntu's package enables /dev/zram0 by default; without CONFIG_ZRAM the boot waits for
# that device, so it goes with exactly the kernels that have zram.
ZRAM_GENERATOR = 'systemd-zram-generator'
# Target CPU for emulation: QEMU's default pointer authentication algorithm (QARMA5) is
# several times slower to emulate than its implementation-defined one, and Ubuntu's arm64
# code signs and checks return addresses in nearly every function.  The target sees the
# same CPU features either way.
QEMU_CPU = 'max,pauth-impdef=on'
BASE_SCHEMA = 'piano-rootfs-base-1'
BASE_SCRIPTS = ('rootfs-image-steps.sh', 'rootfs-image-guest.sh', 'rootfs-image-chroot.sh', 'rootfs-native-chroot.sh')
AARCH64_ELF_MAGIC = '7f454c460201010000000000000000000200b700'


def give_back(*paths):
    """Under sudo, return the outputs and the base cache to the invoking user."""
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if os.geteuid() != 0 or not uid or not gid:
        return
    for top in paths:
        if top is not None and top.exists():
            for path in [top, *top.rglob('*')]:
                os.chown(path, int(uid), int(gid), follow_symlinks=False)


def binfmt_aarch64():
    """The enabled binfmt_misc handler for aarch64 ELF that also works inside a chroot (flag F)."""
    root = Path('/proc/sys/fs/binfmt_misc')
    for entry in sorted(root.iterdir()) if root.is_dir() else ():
        if entry.name in ('register', 'status'):
            continue
        try:
            lines = entry.read_text().splitlines()
        except OSError:
            continue
        fields = dict(line.split(' ', 1) for line in lines if ' ' in line)
        if (lines[:1] == ['enabled'] and fields.get('magic') == AARCH64_ELF_MAGIC
                and 'F' in fields.get('flags:', '')):
            return {'name': entry.name, 'interpreter': fields.get('interpreter'), 'flags': fields['flags:']}
    return None


def run_guest(command, out, timeout, env=None):
    """Keep the original console and timestamp milestones with the host monotonic clock.

    Package configuration and trigger messages expose the nested dpkg/dracut
    work without changing the transaction or the installed maintainer scripts.
    """
    started = time.monotonic()
    events = []
    pending = b''
    with (out / 'console.log').open('ab') as log, (out / 'guest-profile.jsonl').open('a') as profile:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, env=env,
                              stderr=subprocess.STDOUT, start_new_session=True) as proc, selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            try:
                while selector.get_map():
                    if time.monotonic() - started > timeout:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _ in selector.select(timeout=1):
                        buf = os.read(key.fd, 65536)
                        if not buf:
                            selector.unregister(key.fileobj)
                            continue
                        log.write(buf)
                        log.flush()
                        pending += buf
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            line = line.decode(errors='replace').strip('\r')
                            if re.match(r'(PIANO_ROOTFS_|Preparing to unpack |Unpacking |'
                                        r'Setting up |Processing triggers for |/etc/kernel/postinst.d/|'
                                        r'update-initramfs:|dracut(?:\[.*?\])?:)', line):
                                event = {'elapsed_s': round(time.monotonic() - started, 3),
                                         'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                         'message': line}
                                events.append(event)
                                profile.write(json.dumps(event) + '\n')
                                profile.flush()
                                if line.startswith('PIANO_ROOTFS_STEP'):
                                    print(f'{event["utc"]} guest +{event["elapsed_s"]}s {line}', flush=True)
                return proc.wait(), events
            finally:
                if proc.poll() is None:
                    # Allow the native helper's EXIT trap to unmount on timeout.
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def portable(value, out):
    """A command argument without host paths: the output directory and inputs below $HOME by name only."""
    text = str(value).replace(str(out), '<output>')
    # Under sudo HOME is root's; the inputs live below the invoking user's home.
    home = pwd.getpwuid(int(os.environ['SUDO_UID'])).pw_dir if os.environ.get('SUDO_UID') else str(Path.home())
    return re.sub(re.escape(home) + r'/[^\s,]*/([^/\s,]+)', r'<input>/\1', text)


def release_profile(unit):
    profile = {name: {k: unit[name][k] for k in fields} for name, fields in PROFILE_FIELDS.items()}
    profile['scope'] = ('Stock boot image fields for build-persistent-boot.py --no-serial; '
                        'no serial or other data of the unit it was read from')
    return profile


def deb_fields(path):
    text = subprocess.check_output(['dpkg-deb', '--field', str(path), 'Package', 'Version', 'Architecture'],
                                   text=True)
    return dict(line.split(': ', 1) for line in text.splitlines())


def extract_member(deb, name, dest):
    """One regular file from a deb's data archive, without unpacking anything else."""
    with subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(deb)], stdout=subprocess.PIPE) as proc:
        with tarfile.open(fileobj=proc.stdout, mode='r|') as archive:
            for member in archive:
                if member.name.removeprefix('./') == name and member.isreg():
                    with archive.extractfile(member) as src, dest.open('wb') as out:
                        shutil.copyfileobj(src, out)
                    break
            else:
                raise ValueError(f'{deb.name} has no {name}')
        proc.stdout.close()
        proc.kill()
    return dest


def scan_base(sqfs2tar, squashfs):
    """Root mtime and the file capabilities of the base, from the same TAR stream mke2fs reads."""
    caps = {}
    root_mtime = None
    with subprocess.Popen([str(sqfs2tar), '--root-becomes', '.', '--no-skip', str(squashfs)],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        with tarfile.open(fileobj=proc.stdout, mode='r|') as archive:
            for member in archive:
                if root_mtime is None:
                    if member.name not in ('.', './') or not member.isdir():
                        raise ValueError('sqfs2tar output must start with the root directory')
                    root_mtime = int(member.mtime)
                value = member.pax_headers.get('SCHILY.xattr.security.capability')
                if value is not None:
                    caps[member.name.removeprefix('./')] = value.encode('utf-8', 'surrogateescape').hex()
        if proc.wait():
            raise RuntimeError('sqfs2tar failed')
    return root_mtime, caps


class Debugfs:
    """Read-only queries of an ext4 file with the pinned debugfs."""

    def __init__(self, debugfs, image, env):
        self.debugfs, self.image, self.env = str(debugfs), str(image), env

    def run(self, command):
        proc = subprocess.run([self.debugfs, '-R', command, self.image], capture_output=True, env=self.env)
        return proc.stdout, proc.stderr.decode(errors='replace')

    def cat(self, path):
        data, err = self.run(f'cat {path}')
        if 'File not found' in err:
            return None
        return data

    def names(self, path):
        data, err = self.run(f'ls -p {path}')
        if 'File not found' in err:
            return None
        return sorted({line.split('/')[5] for line in data.decode().splitlines()
                       if line.count('/') >= 6 and line.split('/')[5] not in ('.', '..')})

    def exists(self, path):
        return 'File not found' not in self.run(f'stat {path}')[1]

    def size(self, path):
        data, _ = self.run(f'stat {path}')
        match = re.search(rb'Size: (\d+)', data)
        return int(match[1]) if match else None

    def dump_sha(self, path, scratch):
        target = scratch / 'dump'
        self.run(f'dump {path} {target}')
        if not target.exists():
            return None
        digest = sha(target)
        target.unlink()
        return digest

    def xattr(self, path, name, scratch):
        target = scratch / 'xattr'
        self.run(f'ea_get -f {target} {path} {name}')
        if not target.exists():
            return None
        value = target.read_bytes().hex()
        target.unlink()
        return value


def dpkg_status(text):
    packages = {}
    for paragraph in text.split('\n\n'):
        fields = dict(re.findall(r'^([A-Za-z-]+): (.*)$', paragraph, re.M))
        if 'Package' in fields:
            packages[fields['Package']] = (fields.get('Status'), fields.get('Version'))
    return packages


def verify(fs, scratch, expected_packages, release, caps, set_hashes, boot_hashes, kernel_zram):
    """The offline release checks of docs/RELEASE.md on the finished image."""
    checks = {}
    status = dpkg_status(fs.cat('/var/lib/dpkg/status').decode())
    checks['packages_installed'] = {p: status.get(p) == ('install ok installed', v)
                                    for p, v in sorted(expected_packages.items())}
    # The piano kernel is the only one: no other image, modules or headers package, no kdump.
    checks['only_piano_kernel'] = sorted(
        p for p, (s, _) in status.items() if s and s.split()[2] != 'not-installed'
        and (re.match(r'linux-(image|modules|headers)-', p) or p == 'kdump-tools')) == [f'linux-image-{release}']
    checks['zram_generator_matches_kernel'] = (
        status.get(ZRAM_GENERATOR, ('',))[0] == 'install ok installed') == kernel_zram
    checks['boot_only_piano_files'] = sorted(set(fs.names('/boot')) - {'initrd.img.old', 'vmlinuz.old'}) == sorted(
        ['initrd.img', 'vmlinuz'] + [f'{n}-{release}' for n in ('System.map', 'config', 'initrd.img', 'vmlinuz')])
    # Fully installed or removed with configuration kept: nothing half-installed or pending triggers.
    checks['no_package_in_other_state'] = sorted(
        p for p, (s, _) in status.items()
        if s.split()[1:] not in (['ok', 'installed'], ['ok', 'config-files'], ['ok', 'not-installed'])) == []
    checks['gnome_initial_setup_installed'] = (status.get('gnome-initial-setup', ('',))[0] == 'install ok installed'
                                               and fs.exists('/usr/libexec/gnome-initial-setup'))
    custom = fs.cat('/etc/gdm3/custom.conf').decode()
    active = [l.strip().replace(' ', '') for l in custom.splitlines() if l.strip() and not l.lstrip().startswith('#')]
    checks['gdm_no_autologin_initial_setup_not_disabled'] = not any(
        l.startswith(('AutomaticLogin', 'TimedLogin')) or l.lower() == 'initialsetupenable=false' for l in active)
    passwd = fs.cat('/etc/passwd').decode()
    users = [l.split(':')[0] for l in passwd.splitlines() if l and 1000 <= int(l.split(':')[2]) < 65534]
    checks['no_user_accounts'] = users == []
    shadow = fs.cat('/etc/shadow').decode()
    root = next(l for l in shadow.splitlines() if l.startswith('root:'))
    checks['root_password_locked'] = root.split(':')[1][:1] in ('!', '*')
    checks['home_empty'] = fs.names('/home') == []
    checks['machine_id_empty'] = fs.size('/etc/machine-id') == 0
    dbus = fs.run('stat /var/lib/dbus/machine-id')[0]
    checks['dbus_machine_id_absent_empty_or_link'] = (not dbus or b'Type: symlink' in dbus
                                                       or fs.size('/var/lib/dbus/machine-id') == 0)
    checks['no_ssh_host_keys'] = [n for n in fs.names('/etc/ssh') if n.startswith('ssh_host_')] == []
    checks['etc_piano_only_layout'] = fs.names('/etc/piano') == ['layout.json']
    mode = fs.run('stat /etc/piano/layout.json')[0]
    checks['layout_root_0600'] = re.search(rb'Mode: +0600', mode) is not None and re.search(rb'User: +0 ', mode) is not None
    checks['layout_by_partlabel'] = json.loads(fs.cat('/etc/piano/layout.json')) == LAYOUT
    checks['fstab_by_partlabel'] = fs.cat('/etc/fstab') == FSTAB.encode()
    checks['device_hostnames'] = (fs.cat('/etc/hostname') == HOSTNAME.encode()
                                  and fs.cat('/etc/machine-info') == (MACHINE_INFO + 'CHASSIS=convertible\n').encode())
    checks['fake_hwclock_data'] = fs.size('/etc/fake-hwclock.data') not in (None, 0)
    checks['no_install_leftovers'] = not any(fs.exists(p) for p in (
        '/var/cache/piano-install', '/usr/sbin/policy-rc.d', '/etc/initramfs-tools/update-initramfs.conf',
        '/var/lib/systemd/random-seed'))
    checks['apt_policy'] = (fs.cat('/etc/apt/sources.list.d/ubuntu.sources') == (REPO / 'ubuntu/26.04.sources').read_bytes()
                            and not fs.exists('/etc/apt/sources.list.d/cdrom.sources')
                            and fs.exists('/etc/cloud/cloud-init.disabled'))
    checks['mozilla_policy'] = all(fs.cat(dest) == (REPO / 'ubuntu/firefox' / source).read_bytes()
        for source, dest in [('mozilla.sources', '/etc/apt/sources.list.d/mozilla.sources'),
                             ('mozilla.pref', '/etc/apt/preferences.d/mozilla'),
                             ('packages.mozilla.org.asc', '/etc/apt/keyrings/packages.mozilla.org.asc')])
    checks['firefox_removal_pending_first_boot'] = not fs.exists('/var/lib/piano/firefox-snap-removed')
    checks['apt_caches_clean'] = not [n for n in fs.names('/var/cache/apt/archives') or [] if n.endswith('.deb')] and not any(
        fs.exists('/var/cache/apt/' + n) for n in ('pkgcache.bin', 'srcpkgcache.bin'))
    checks['apt_lists_empty'] = set(fs.names('/var/lib/apt/lists') or []) <= {'auxfiles', 'lock', 'partial'}
    journal = fs.names('/var/log/journal')
    checks['journal_empty'] = journal in (None, [])
    wants = {
        'piano-firefox-snap-remove.service': '/etc/systemd/system/multi-user.target.wants/piano-firefox-snap-remove.service',
        'piano-slot-success.service': '/etc/systemd/system/multi-user.target.wants/piano-slot-success.service',
        'piano-devmode.service': '/etc/systemd/system/usb-gadget.target.wants/piano-devmode.service',
        'ssh.socket': '/etc/systemd/system/sockets.target.wants/ssh.socket',
        'ssh.service': '/etc/systemd/system/multi-user.target.wants/ssh.service',
        'fake-hwclock-load.service': '/etc/systemd/system/sysinit.target.wants/fake-hwclock-load.service',
        'fake-hwclock-save.service': '/etc/systemd/system/shutdown.target.wants/fake-hwclock-save.service',
        'fake-hwclock-save.timer': '/etc/systemd/system/timers.target.wants/fake-hwclock-save.timer',
    }
    checks['unit_enablement'] = {u: fs.exists(p) == (UNITS_ENABLED[u] == 'enabled') for u, p in wants.items()}
    checks['kernel_files'] = {f'/boot/{n}-{release}': fs.dump_sha(f'/boot/{n}-{release}', scratch) == boot_hashes.get(
        f'/boot/{n}-{release}') for n in ('vmlinuz', 'initrd.img')}
    store = '/var/lib/piano/switch'
    checks['store_only_ubuntu_set'] = fs.names(store) == ['ubuntu']
    sums = fs.cat(f'{store}/ubuntu/SHA256SUMS').decode()
    listed = dict(reversed(line.split()) for line in sums.splitlines())
    checks['store_set_matches_sha256sums'] = sorted(listed) == ['boot.img', 'init_boot.img', 'vendor_boot.img'] and all(
        fs.dump_sha(f'{store}/ubuntu/{n}', scratch) == h == set_hashes[n] for n, h in listed.items())
    checks['file_capabilities_kept'] = {p: fs.xattr('/' + p, 'security.capability', scratch) == v
                                        for p, v in sorted(caps.items())}

    def passed(value):
        return all(value.values()) if isinstance(value, dict) else bool(value)
    return {'passed': all(passed(v) for v in checks.values()), 'checks': checks,
            'failed': sorted(k for k, v in checks.items() if not passed(v))}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--squashfs', type=Path, required=True, help='Pristine Desktop SquashFS (BUILD.md)')
    ap.add_argument('--squashfs-sha256', required=True)
    ap.add_argument('--sqfs2tar', type=Path, required=True, help='squashfs-tools-ng 1.2 sqfs2tar')
    ap.add_argument('--mke2fs', type=Path, required=True, help='mke2fs 1.47.4 with libarchive')
    ap.add_argument('--mke2fs-config', type=Path, required=True)
    ap.add_argument('--e2fsck', type=Path, required=True)
    ap.add_argument('--debugfs', type=Path, required=True)
    ap.add_argument('--dumpe2fs', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, help='qemu-system-aarch64 (guest backend only)')
    ap.add_argument('--kernel-deb', type=Path, required=True)
    ap.add_argument('--deb', type=Path, action='append', required=True,
                    help='piano-device-support, piano-local-firmware, piano-mesa, piano-ubuntu-desktop, piano-iris-vaapi')
    ap.add_argument('--apt-manifest', type=Path, required=True,
                    help='prepare-ubuntu-apt.py --download manifest of the Ubuntu packages to add')
    ap.add_argument('--boot-profile', type=Path, required=True, help='Boot profile read from a stock unit')
    ap.add_argument('--boot-key', type=Path, required=True, help='AVB signing key of the release set')
    ap.add_argument('--release-name', default='v0.1.0-preview')
    ap.add_argument('--size-mib', type=int, default=61440, help='linux_rootfs size (60 GiB)')
    ap.add_argument('--smp', type=int, default=8)
    ap.add_argument('--memory-mib', type=int, default=4096)
    ap.add_argument('--timeout', type=int, default=5400, help='Host budget for the QEMU guest in seconds')
    ap.add_argument('--backend', choices=('guest', 'chroot'),
                    help='Default: chroot as root on an aarch64 host or with a binfmt_misc aarch64 handler, '
                         'guest otherwise')
    ap.add_argument('--base-cache', type=Path,
                    help='Directory of bases keyed by their inputs: reuse the base when only the kernel changed, '
                         'otherwise build it and store it there')
    ap.add_argument('--output', type=Path, required=True, help='New output directory')
    a = ap.parse_args()
    native_root = os.uname().machine == 'aarch64' and os.geteuid() == 0
    binfmt = binfmt_aarch64() if os.geteuid() == 0 and not native_root else None
    a.backend = a.backend or ('chroot' if native_root or binfmt else 'guest')
    if a.backend == 'chroot' and not (native_root or binfmt):
        ap.error('--backend chroot requires euid 0 and an aarch64 host or a binfmt_misc aarch64 handler '
                 'with the F flag (qemu-user-static)')
    if a.backend == 'guest' and a.qemu is None:
        ap.error('--qemu is required for the guest backend')
    if a.timeout <= 0:
        ap.error('--timeout must be positive')
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?', a.release_name):
        ap.error('Release names look like v0.1.0-preview')
    if a.size_mib < 16384:
        ap.error('The root needs at least 16 GiB')
    started = time.monotonic()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, MKE2FS_CONFIG=str(a.mke2fs_config.resolve()), LC_ALL='C.UTF-8', LANGUAGE='C')
    result = {'state': 'BUILDING', 'backend': a.backend, 'release_name': a.release_name, 'tool_sha256': sha(Path(__file__)),
              'guest_script_sha256': sha(Path(__file__).with_name('rootfs-image-guest.sh')),
              'steps_script_sha256': sha(Path(__file__).with_name('rootfs-image-steps.sh')),
              'sparse_encoder_sha256': sha(Path(sparse_image.__file__)),
              'scope': ('Root filesystem image for any piano unit, built offline; not booted on a tablet by this tool'),
              'timings_seconds': {}}

    def save():
        (out / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')

    def lap(name, since):
        result['timings_seconds'][name] = round(time.monotonic() - since, 1)
        print(f'{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())} '
              f'host {name}: {result["timings_seconds"][name]} s', flush=True)
        save()
        return time.monotonic()

    save()
    try:
        # Inputs, all by hash.
        if sha(a.squashfs) != a.squashfs_sha256:
            raise ValueError('Desktop SquashFS hash mismatch')
        unit_profile = json.loads(a.boot_profile.read_text())
        profile = release_profile(unit_profile)
        (out / 'boot-profile.json').write_text(json.dumps(profile, indent=2) + '\n')
        apt = json.loads(a.apt_manifest.read_text())
        if apt['state'] != 'DOWNLOADED_NOT_INSTALLED' or apt['rootfs_sha256'] != a.squashfs_sha256:
            raise ValueError('APT manifest must be a download resolved against this SquashFS')
        firefox = json.loads((REPO / 'ubuntu/firefox/source.json').read_text())
        browser = [d for d in apt['debs'] if d['package'] == 'firefox']
        if len(browser) != 1 or any(browser[0][k] != firefox[k]
                                    for k in ('version', 'architecture', 'sha256', 'bytes')):
            raise ValueError('APT manifest needs --mozilla-firefox with the locked Mozilla package')
        debs = []
        for entry in apt['debs']:
            path = a.apt_manifest.parent / entry['file']
            if sha(path) != entry['sha256']:
                raise ValueError(f'APT package changed after download: {path.name}')
            debs.append((path, {'package': entry['package'], 'version': entry['version'],
                                'architecture': entry['architecture'], 'file': path.name, 'sha256': entry['sha256'],
                                'source': entry.get('source', f'Ubuntu snapshot {apt["snapshot"]}')}))
        kernel = deb_fields(a.kernel_deb)
        match = re.fullmatch(r'linux-image-(.+-piano[0-9]+)', kernel['Package'])
        if not match:
            raise ValueError('The kernel package must be a released linux-image-<version>-piano<ABI>')
        release = match[1]
        config = extract_member(a.kernel_deb, f'boot/config-{release}', out / f'config-{release}')
        kernel_zram = re.search(rb'^CONFIG_ZRAM=[ym]$', config.read_bytes(), re.M) is not None
        config.unlink()
        if kernel_zram != any(d['package'] == ZRAM_GENERATOR for _, d in debs):
            raise ValueError(f'{ZRAM_GENERATOR} belongs in the APT download exactly when the kernel has CONFIG_ZRAM '
                             f'({release}: {"set" if kernel_zram else "not set"})')
        for path in [a.kernel_deb, *a.deb]:
            fields = deb_fields(path)
            debs.append((path, {'package': fields['Package'], 'version': fields['Version'],
                                'architecture': fields['Architecture'], 'file': path.name, 'sha256': sha(path),
                                'source': 'piano'}))
        # Host paths stay in this local file; manifest.json is published with the release.
        (out / 'inputs-local.json').write_text(json.dumps(
            {d['file']: str(path.resolve()) for path, d in debs}, indent=2) + '\n')
        names = [d['package'] for _, d in debs]
        if len(names) != len(set(names)) or not set(PIANO_PACKAGES) <= set(names):
            raise ValueError(f'Need exactly one of each package and all of {PIANO_PACKAGES}')
        result.update(kernel_release=release, kernel_config_zram=kernel_zram, squashfs_sha256=a.squashfs_sha256,
                      apt_manifest_sha256=sha(a.apt_manifest), apt_snapshot=apt['snapshot'],
                      mozilla_firefox=firefox,
                      boot_profile_sha256=sha(out / 'boot-profile.json'),
                      boot_key_public_sha256=hashlib.sha256(subprocess.check_output(
                          ['openssl', 'pkey', '-in', str(a.boot_key), '-pubout', '-outform', 'DER'])).hexdigest(),
                      packages=[d for _, d in debs],
                      tools={n: sha(getattr(a, n)) for n in ('sqfs2tar', 'mke2fs', 'mke2fs_config', 'e2fsck',
                                                             'debugfs', 'dumpe2fs')},
                      root_partlabel=ROOT_PARTLABEL, data_partlabel=DATA_PARTLABEL, fstab=FSTAB, layout=LAYOUT)
        save()

        if a.backend == 'guest':
            result['qemu_version'] = subprocess.check_output([str(a.qemu), '--version'], text=True).splitlines()[0]
            result['tools']['qemu'] = sha(a.qemu)
        else:
            result['chroot_host'] = {'machine': os.uname().machine, 'kernel': os.uname().release,
                                    'euid': os.geteuid(), 'emulation': False}
            for name in ('rootfs-native-chroot.sh', 'rootfs-image-chroot.sh'):
                result['tools'][name] = sha(Path(__file__).with_name(name))
        chroot_env = None
        if a.backend == 'chroot' and binfmt:
            chroot_env = dict(os.environ, QEMU_CPU=os.environ.get('QEMU_CPU', QEMU_CPU))
            result['chroot_host']['emulation'] = {'binfmt_misc': binfmt, 'qemu_cpu': chroot_env['QEMU_CPU']}

        # Inputs of the steps, packed per run into one TAR (a read-only disk for the guest).  It
        # holds the release key and is deleted afterwards.  Read first: they also key the base.
        files = {
            'guest.sh': Path(__file__).with_name('rootfs-image-guest.sh').read_bytes(),
            'steps.sh': Path(__file__).with_name('rootfs-image-steps.sh').read_bytes(),
            'chroot.sh': Path(__file__).with_name('rootfs-image-chroot.sh').read_bytes(),
            'files/etc/apt/sources.list.d/ubuntu.sources': (REPO / 'ubuntu/26.04.sources').read_bytes(),
            'files/etc/piano/layout.json': (json.dumps(LAYOUT, indent=2) + '\n').encode(),
            'files/etc/fstab': FSTAB.encode(),
            'files/etc/hostname': HOSTNAME.encode(),
            'files/etc/machine-info': MACHINE_INFO.encode(),
            'release/boot-profile.json': (out / 'boot-profile.json').read_bytes(),
            'release/boot-signing.pem': a.boot_key.read_bytes(),
        }
        for name in ('mozilla.sources', 'mozilla.pref', 'packages.mozilla.org.asc'):
            files['firefox/' + name] = (REPO / 'ubuntu/firefox' / name).read_bytes()
        # The prebuilt loopback depends on this exact kernel, and camerad depends on
        # loopback. Configure them together after device support is installed and the
        # generic distribution kernels are removed.
        kernel_bound = {kernel['Package'], 'piano-camerad', f'piano-camera-loopback-{release}'}

        # Everything before the piano kernel is installed depends only on these inputs, so a
        # base built from them is reused when only the kernel changes.  The filesystem UUID
        # and directory hash seed follow the same key: equal inputs, equal base.
        base_inputs = {
            'schema': BASE_SCHEMA, 'backend': a.backend, 'size_mib': a.size_mib,
            'squashfs_sha256': a.squashfs_sha256, 'apt_manifest_sha256': result['apt_manifest_sha256'],
            'tools': {n: result['tools'][n] for n in ('sqfs2tar', 'mke2fs', 'mke2fs_config')},
            'scripts': {n: sha(Path(__file__).with_name(n)) for n in BASE_SCRIPTS},
            'files': {n: hashlib.sha256(d).hexdigest() for n, d in files.items() if n.startswith(('files/', 'firefox/'))},
            'packages': sorted([d['package'], d['sha256']] for _, d in debs if d['package'] not in kernel_bound),
        }
        base_key = hashlib.sha256(json.dumps(base_inputs, sort_keys=True).encode()).hexdigest()
        cache = a.base_cache.resolve() / base_key if a.base_cache else None
        hit = cache is not None and (cache / 'base.json').is_file()
        result['base'] = {'key': base_key, 'cache': None if cache is None else ('hit' if hit else 'stored')}
        save()

        # The base ext4 at the full partition size, from the SquashFS TAR stream.
        t = time.monotonic()
        if hit:
            stored = json.loads((cache / 'base.json').read_text())
            epoch, caps = stored['base_epoch'], stored['base_file_capabilities']
        else:
            print('Scanning the base for file capabilities', flush=True)
            epoch, caps = scan_base(a.sqfs2tar, a.squashfs)
            t = lap('scan_base', t)
        result.update(base_epoch=epoch, base_file_capabilities=len(caps))
        fs_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f'piano-rootfs-base:{base_key}')
        image = out / 'root.ext4.partial'
        guest_dir = out / 'guest'
        mke2fs = [str(a.mke2fs.resolve()), '-q', '-t', 'ext4', '-b', '4096', '-m', '0',
                  '-U', str(fs_uuid), '-L', ROOT_PARTLABEL,
                  '-E', f'lazy_itable_init=0,lazy_journal_init=0,root_owner=0:0,hash_seed={fs_uuid}',
                  '-d', '-', str(image)]
        result.update(filesystem_uuid=str(fs_uuid), filesystem_bytes=a.size_mib << 20,
                      mke2fs_command=[portable(w, out) for w in mke2fs])
        save()
        if hit:
            print(f'Reusing the cached base {base_key}', flush=True)
            # Holes stay holes; with --sparse=always cp would also scan every data block for zeros.
            subprocess.run(['cp', '--reflink=auto', str(cache / 'root.ext4'), str(image)], check=True)
            guest_dir.mkdir()
            with tarfile.open(cache / 'base-out.tar') as tar:
                tar.extractall(guest_dir, filter='data')
            t = lap('base_cache_copy', t)
            runs = [['kernel']]
        else:
            with image.open('xb') as f:
                f.truncate(a.size_mib << 20)
            print('Populating the ext4 from the SquashFS TAR stream', flush=True)
            with (out / 'mke2fs.log').open('wb') as log:
                source = subprocess.Popen([str(a.sqfs2tar), '--root-becomes', '.', '--no-skip', str(a.squashfs)],
                                          stdout=subprocess.PIPE, stderr=log)
                sink = subprocess.Popen(mke2fs, stdin=source.stdout, stdout=log, stderr=subprocess.STDOUT,
                                        env=dict(env, E2FSPROGS_FAKE_TIME=str(epoch)))
                source.stdout.close()
                if sink.wait() or source.wait():
                    raise RuntimeError('sqfs2tar | mke2fs failed; see mke2fs.log')
            t = lap('mke2fs', t)
            runs = [['base', 'kernel']] if cache is None else [['base'], ['kernel']]

        guest_kernel = (extract_member(a.kernel_deb, f'boot/vmlinuz-{release}', out / f'vmlinuz-{release}')
                        if a.backend == 'guest' else None)
        result.update(guest_runs=[], guest_profile=[], guest_timeout_seconds=a.timeout)
        deadline = time.monotonic() + a.timeout
        try:
            for phases in runs:
                inputs = out / 'inputs.tar'
                run_files = {'params.sh': (f'RELEASE={release}\nROOT_PARTLABEL={ROOT_PARTLABEL}\n'
                                           f'PHASES="{" ".join(phases)}"\n').encode()}
                run_files.update((n, d) for n, d in files.items() if 'kernel' in phases or not n.startswith('release/'))
                inputs.touch(mode=0o600, exist_ok=False)
                with tarfile.open(inputs, 'w', format=tarfile.PAX_FORMAT) as tar:
                    def add(name, data=None, path=None):
                        info = tarfile.TarInfo(name)
                        info.mtime, info.mode = epoch, 0o600 if name.endswith('.pem') else 0o644
                        if path is not None:
                            info.size = path.stat().st_size
                            with path.open('rb') as src:
                                tar.addfile(info, src)
                        else:
                            info.size = len(data)
                            tar.addfile(info, io.BytesIO(data))
                    for name, data in run_files.items():
                        add(name, data)
                    for path, entry in debs:
                        bound = entry['package'] in kernel_bound
                        if 'kernel' in phases if bound else 'base' in phases:
                            add(('kernel/' if bound else 'debs/') + path.name.replace('%3a', '_'), path=path)
                if a.backend == 'guest':
                    result_disk = out / 'result.raw'
                    with result_disk.open('xb') as f:
                        f.truncate(1 << 30)
                    run = ("/bin/sh -c 'mkdir -p /run/piano-inputs && tar -xf /dev/vdc -C /run/piano-inputs "
                           "&& exec /bin/sh /run/piano-inputs/guest.sh'")
                    # The helper skips AppArmor: compiling the Desktop profiles under TCG takes many minutes, and
                    # nothing in the chroot steps depends on it.  The target's own policy is untouched.
                    cmdline = ('console=ttyAMA0 root=/dev/vda rootfstype=squashfs ro rootwait panic=-1 apparmor=0 '
                               'systemd.unit=kernel-command-line.target systemd.run_success_action=poweroff '
                               f'systemd.run_failure_action=poweroff systemd.run="{run}"')
                    drives = [(a.squashfs.resolve(), 'readonly=on'), (image, 'cache=unsafe,discard=unmap'),
                              (inputs, 'readonly=on'), (result_disk, '')]
                    command = [str(a.qemu), '-machine', 'virt', '-cpu', QEMU_CPU, '-accel', 'tcg,thread=multi',
                               '-smp', str(a.smp), '-m', f'{a.memory_mib}M', '-nodefaults', '-display', 'none',
                               '-monitor', 'none', '-serial', 'stdio', '-nic', 'none', '-no-reboot',
                               '-kernel', str(guest_kernel), '-append', cmdline]
                    for index, ((path, options), serial) in enumerate(zip(drives, SERIALS)):
                        command += ['-drive', f'file={path},format=raw,if=none,id=d{index}' + (',' + options if options else ''),
                                    '-device', f'virtio-blk-device,bus=virtio-mmio-bus.{index},drive=d{index},serial={serial}']
                else:
                    command = ['unshare', '--mount', '--net', '--propagation', 'private', '/bin/bash',
                               str(Path(__file__).with_name('rootfs-native-chroot.sh')), str(image), str(inputs), str(out)]
                result['guest_command'] = [portable(w, out) for w in command]
                save()
                print(f'Running the {" and ".join(phases)} steps with {a.backend}; progress in {out}/console.log', flush=True)
                try:
                    returncode, events = run_guest(command, out, max(1.0, deadline - time.monotonic()), chroot_env)
                except subprocess.TimeoutExpired:
                    returncode, events = None, []
                finally:
                    inputs.unlink()
                result['guest_runs'].append({'phases': phases, 'returncode': returncode})
                result['guest_profile'] += events
                t = lap(f'{a.backend}_{"_".join(phases)}', t)
                if a.backend == 'guest':
                    with tarfile.open(result_disk) as tar:
                        tar.extractall(guest_dir, filter='data')
                    result_disk.unlink()
                if returncode is None:
                    raise RuntimeError(f'{a.backend} exceeded its {a.timeout} s budget; see console.log')
                if returncode != 0:
                    raise RuntimeError(f'{a.backend} failed; see console.log and guest/install.log')
                log = (guest_dir / 'install.log').read_text(errors='replace')
                done = 'PIANO_ROOTFS_GUEST_DONE' if 'kernel' in phases else 'PIANO_ROOTFS_BASE_DONE'
                if done not in log or 'PIANO_ROOTFS_EXIT status=0' not in log:
                    raise RuntimeError('The steps did not complete; see guest/install.log and console.log')
                if 'kernel' not in phases:
                    (guest_dir / 'install.log').replace(guest_dir / 'install-base.log')
                    # Store the base before the kernel run changes it: written aside, then renamed.
                    partial = cache.with_name(cache.name + '.partial')
                    shutil.rmtree(partial, ignore_errors=True)
                    partial.mkdir(parents=True)
                    subprocess.run(['cp', '--reflink=auto', str(image), str(partial / 'root.ext4')], check=True)
                    with tarfile.open(partial / 'base-out.tar', 'w') as tar:
                        for f in sorted(guest_dir.iterdir()):
                            tar.add(f, arcname=f.name)
                    (partial / 'base.json').write_text(json.dumps(
                        {'key': base_key, 'inputs': base_inputs, 'filesystem_uuid': str(fs_uuid), 'base_epoch': epoch,
                         'base_file_capabilities': caps},
                        indent=2) + '\n')
                    partial.rename(cache)
                    t = lap('base_cache_store', t)
        finally:
            if guest_kernel is not None:
                guest_kernel.unlink()
        (out / 'ubuntu-set').mkdir()
        for f in sorted((guest_dir / 'ubuntu-set').iterdir()):
            f.replace(out / 'ubuntu-set' / f.name)
        (guest_dir / 'ubuntu-set').rmdir()
        set_manifest = json.loads((out / 'ubuntu-set/manifest.json').read_text())
        if (set_manifest['kernel_release'] != release or set_manifest['root'] != f'PARTLABEL={ROOT_PARTLABEL}'
                or set_manifest['serial_in_cmdline'] or set_manifest['debug']
                or set_manifest['private_bt_address_injected'] or set_manifest['private_wlan_address_injected']):
            raise RuntimeError('The guest built a boot set that is not the address-free release set')
        set_hashes = {n + '.img': set_manifest['outputs'][n]['sha256'] for n in ('boot', 'init_boot', 'vendor_boot')}
        boot_hashes = {line.split()[1]: line.split()[0]
                       for line in (guest_dir / 'boot-files.sha256').read_text().splitlines()}
        result.update(boot_set={'cmdline': set_manifest['cmdline'], 'images_sha256': set_hashes,
                                'effective_dtb_sha256': set_manifest['effective_dtb_sha256'],
                                'manifest_sha256': sha(out / 'ubuntu-set/manifest.json')},
                      boot_files_sha256=boot_hashes)

        def installed(name):
            return {line.split('|')[1] for line in (guest_dir / name).read_text().splitlines()
                    if line.startswith('ii')}
        result['distribution_kernel_purge'] = {
            'removed_packages': sorted(installed('dpkg-query-base.txt') - installed('dpkg-query.txt')),
            'used_bytes_before': int((guest_dir / 'used-bytes-before-purge.txt').read_text()),
            'used_bytes_after': int((guest_dir / 'used-bytes-after-purge.txt').read_text())}
        save()

        # Seal: whole-filesystem check, hashes, sparse image, offline checks.
        with (out / 'e2fsck.log').open('wb') as log:
            subprocess.run([str(a.e2fsck.resolve()), '-fn', str(image)], stdout=log, stderr=subprocess.STDOUT,
                           env=env, check=True)
        final = out / 'root.ext4'
        image.replace(final)
        t = lap('e2fsck', t)
        expected = {d['package']: d['version'] for _, d in debs}
        scratch = out / 'scratch'
        scratch.mkdir()
        report = verify(Debugfs(a.debugfs.resolve(), final, env), scratch, expected, release, caps,
                        set_hashes, boot_hashes, kernel_zram)
        scratch.rmdir()
        (out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
        if not report['passed']:
            raise RuntimeError(f'Offline checks failed: {report["failed"]}; see verification.json')
        t = lap('verify', t)
        sparse, found = sparse_image.encode_ext4(final, out / 'root.sparse.img', a.dumpe2fs.resolve(),
                                                 sparse_image.DEFAULT_MAX_FILL_MIB << 20)
        result['sparse'] = sparse
        if found:
            raise RuntimeError(f'Sparse image check failed: {found}')
        t = lap('img2simg', t)
        outputs = {
            'root.ext4': {'bytes': final.stat().st_size, 'allocated_bytes': final.stat().st_blocks * 512},
            'root.sparse.img': {'bytes': (out / 'root.sparse.img').stat().st_size,
                                'sha256': sha(out / 'root.sparse.img')}}
        (out / 'SHA256SUMS').write_text(f'{outputs["root.sparse.img"]["sha256"]}  root.sparse.img\n')
        t = lap('hash', t)
        result.update(state='ROOT_IMAGE_BUILT_OFFLINE_CHECKS_PASS_NOT_BOOTED', outputs=outputs,
                      verification_sha256=sha(out / 'verification.json'),
                      total_seconds=round(time.monotonic() - started, 1))
    except Exception as exc:
        result.update(state='FAILED', error=str(exc))
        save()
        give_back(out, a.base_cache)
        raise
    save()
    give_back(out, a.base_cache)
    print(json.dumps({k: result[k] for k in ('state', 'kernel_release', 'outputs')}, indent=2))


if __name__ == '__main__':
    main()
