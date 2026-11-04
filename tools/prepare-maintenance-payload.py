#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Collect the maintenance RAM environment's payload from pinned inputs (no device I/O).

build: unmodified Ubuntu ARM64 binaries (gdisk's sgdisk, util-linux's sfdisk,
lsblk, partx, blockdev) with their interpreter and libraries, taken from the
pinned Ubuntu desktop SquashFS by checksum; the two reboot-mode modules of
the kernel package; restart2 (initramfs/restart2.c) built against the
kernel's nolibc. The output is DIR/root (the tree) and DIR/manifest.json.

stage: verify a payload against its manifest and print gen_init_cpio lines
for build-initramfs.sh (MAINTENANCE_DIR); the payload owns /usr and the
/lib -> usr/lib link, BusyBox keeps /bin.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
TOOLS = ('usr/sbin/sgdisk', 'usr/sbin/sfdisk', 'usr/bin/lsblk', 'usr/bin/partx', 'usr/sbin/blockdev')
LIBRARY_DIRS = ('usr/lib/aarch64-linux-gnu', 'usr/lib')
MODULES = ('kernel/drivers/nvmem/nvmem_qcom-spmi-sdam.ko', 'kernel/drivers/power/reset/nvmem-reboot-mode.ko')
# Built-in drivers the environment depends on: UFS, the NCM gadget, the GPT
# parser; the reboot-mode pair stays modular as in the release kernel.
CONFIG = ('CONFIG_SCSI_UFSHCD=y', 'CONFIG_SCSI_UFSHCD_PLATFORM=y', 'CONFIG_SCSI_UFS_QCOM=y',
          'CONFIG_PHY_QCOM_QMP_UFS=y', 'CONFIG_BLK_DEV_SD=y', 'CONFIG_EFI_PARTITION=y',
          'CONFIG_USB_DWC3=y', 'CONFIG_USB_CONFIGFS_NCM=y', 'CONFIG_DEVTMPFS=y', 'CONFIG_RD_GZIP=y',
          'CONFIG_NVMEM_SPMI_SDAM=m', 'CONFIG_NVMEM_REBOOT_MODE=m', '# CONFIG_MODULE_COMPRESS is not set')
SAFE = re.compile(r'[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*')
LISTING = re.compile(r'^([-dlcbps])\S+ \S+\s+\d+ \S+ \S+ squashfs-root/(\S+?)(?: -> (\S+))?$')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def elf_info(data):
    """(PT_INTERP, DT_NEEDED names, DT_RUNPATH/RPATH) of a little-endian ELF64 aarch64 file."""
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise ValueError('Not a little-endian ELF64 file')
    machine, = struct.unpack_from('<H', data, 18)
    if machine != 183:
        raise ValueError('Not an aarch64 ELF file')
    phoff, = struct.unpack_from('<Q', data, 32)
    phentsize, phnum = struct.unpack_from('<HH', data, 54)
    interp, dynamic, loads = None, None, []
    for i in range(phnum):
        p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from('<IIQQQQQQ', data, phoff + i * phentsize)
        if p_type == 3:
            interp = data[p_offset:p_offset + p_filesz].rstrip(b'\0').decode()
        elif p_type == 2:
            dynamic = (p_offset, p_filesz)
        elif p_type == 1:
            loads.append((p_vaddr, p_offset, p_filesz))
    needed, runpath = [], None
    if dynamic:
        entries, strtab = [], None
        for off in range(dynamic[0], dynamic[0] + dynamic[1], 16):
            tag, value = struct.unpack_from('<qQ', data, off)
            if tag == 0:
                break
            if tag == 5:
                strtab = value
            entries.append((tag, value))
        # DT_STRTAB is a virtual address; map it through the loadable segments.
        base = next((o + strtab - v for v, o, s in loads if strtab is not None and v <= strtab < v + s), None)
        if base is None:
            raise ValueError('DT_STRTAB outside the loadable segments')
        name = lambda value: data[base + value:data.index(b'\0', base + value)].decode()
        needed = [name(v) for t, v in entries if t == 1]
        runpath = next((name(v) for t, v in entries if t in (15, 29)), None)
    return interp, needed, runpath


def parse_listing(text):
    """Map path -> (type, symlink target) from `unsquashfs -lls` output."""
    tree = {}
    for line in text.splitlines():
        m = LISTING.match(line)
        if m:
            tree[m[2]] = (m[1], m[3])
    return tree


def resolve(tree, path, links, depth=0):
    """Final regular-file path of path inside tree, recording each symlink passed in links."""
    if depth > 40:
        raise ValueError(f'Symlink loop at {path}')
    parts = PurePosixPath(path.lstrip('/')).parts
    current = PurePosixPath()
    for i, part in enumerate(parts):
        if part == '..':
            current = current.parent
            continue
        current = current / part
        kind, target = tree.get(str(current), (None, None))
        if kind is None:
            raise FileNotFoundError(str(current))
        if kind == 'l':
            links[str(current)] = target
            base = PurePosixPath('/') if target.startswith('/') else current.parent
            rest = PurePosixPath(*parts[i + 1:]) if i + 1 < len(parts) else PurePosixPath()
            return resolve(tree, os.path.normpath(str(base / target / rest)), links, depth + 1)
        if i + 1 < len(parts) and kind != 'd':
            raise ValueError(f'{current} is not a directory')
    if tree[str(current)][0] != '-':
        raise ValueError(f'{current} is not a regular file')
    return str(current)


def closure(tree, roots, read):
    """Regular files and symlinks needed to run roots: interpreters and DT_NEEDED libraries."""
    files, links, pending = {}, {}, list(roots)
    while pending:
        path = pending.pop()
        real = resolve(tree, path, links)
        if real in files:
            continue
        interp, needed, runpath = elf_info(read(real))
        if runpath:
            raise ValueError(f'{real}: RUNPATH/RPATH is not supported')
        files[real] = needed
        if interp:
            pending.append(interp)
        for soname in needed:
            for d in LIBRARY_DIRS:
                if f'{d}/{soname}' in tree:
                    pending.append(f'{d}/{soname}')
                    break
            else:
                raise FileNotFoundError(f'{real}: library {soname} not found')
    return files, links


def owners(info_dir, paths):
    """Debian package of each path, from the extracted var/lib/dpkg/info/*.list files."""
    found = {}
    for listing in sorted(Path(info_dir).glob('*.list')):
        package = listing.name.removesuffix('.list').split(':')[0]
        for line in listing.read_text(errors='replace').splitlines():
            found.setdefault(line.lstrip('/'), package)
    return {p: found.get(p) for p in paths}


def deb_member(deb, names, dest):
    """Extract regular files from a .deb's data archive into dest."""
    tar = subprocess.run(['dpkg-deb', '--fsys-tarfile', str(deb)], check=True, capture_output=True).stdout
    subprocess.run(['tar', '-x', '-C', str(dest), *('./' + n for n in names)], input=tar, check=True)


def build(a):
    out = a.output.resolve()
    if sha(a.squashfs) != a.squashfs_sha256:
        raise SystemExit('The Ubuntu SquashFS does not match --squashfs-sha256')
    out.mkdir(parents=True, exist_ok=False)
    root = out / 'root'
    root.mkdir()
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        tmp = Path(tmp)
        listing = subprocess.run(['unsquashfs', '-lls', '-n', str(a.squashfs)], check=True,
                                 capture_output=True, text=True).stdout
        tree = parse_listing(listing)
        extracted = tmp / 'x'

        def extract(paths):
            subprocess.run(['unsquashfs', '-n', '-q', '-f', '-no-wildcards', '-no-xattrs', '-d', str(extracted),
                            str(a.squashfs), *paths], check=True, stdout=subprocess.DEVNULL)

        # Pass 1: read ELF headers of every candidate as it is reached.
        cache = {}

        def read(path):
            if path not in cache:
                extract([path])
                cache[path] = (extracted / path).read_bytes()
            return cache[path]

        files, links = closure(tree, TOOLS, read)
        info = [p for p in tree if p.startswith('var/lib/dpkg/info/') and p.endswith('.list')]
        extract(info)
        packages = owners(extracted / 'var/lib/dpkg/info', files)
        if None in packages.values():
            raise SystemExit(f'No Debian package owns {[p for p, v in packages.items() if v is None]}')
        copyrights = sorted({f'usr/share/doc/{p}/copyright' for p in packages.values()})
        doc_files = {}
        for c in copyrights:
            doc_files[c] = resolve(tree, c, links)
        extract(sorted(set(doc_files.values())))
        for path in sorted(set(files) | set(doc_files.values())):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(extracted / path, target)
            os.chmod(target, 0o755 if path in files else 0o644)
        for link, target in links.items():
            if link in ('bin', 'sbin'):
                raise SystemExit(f'A tool path passes /{link}, which BusyBox owns in the initramfs')
            (root / link).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, root / link)

        # The reboot-mode modules of the kernel package, and its configuration.
        deb = tmp / 'deb'
        deb.mkdir()
        control = subprocess.run(['dpkg-deb', '--field', str(a.kernel_deb), 'Package', 'Version'],
                                 check=True, capture_output=True, text=True).stdout
        release = re.search(r'^Package: linux-image-(\S+)$', control, re.M)[1]
        modroot = f'usr/lib/modules/{release}'
        deb_member(a.kernel_deb, [f'boot/config-{release}', *(f'{modroot}/{m}' for m in MODULES)], deb)
        config = (deb / f'boot/config-{release}').read_text().splitlines()
        missing = [c for c in CONFIG if c not in config]
        if missing:
            raise SystemExit(f'Kernel configuration lacks {missing}')
        for m in MODULES:
            data = (deb / modroot / m).read_bytes()
            if f'vermagic={release} '.encode() not in data:
                raise SystemExit(f'{m} is not built for {release}')
            (root / modroot / m).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(deb / modroot / m, root / modroot / m)

        # restart2 against the kernel's nolibc; static, stripped, no build ID.
        (root / 'bin').mkdir()
        source = REPO / 'initramfs/restart2.c'
        nolibc = a.nolibc.resolve()
        compiler_include = subprocess.run([a.cc, '-print-file-name=include'], check=True,
                                          capture_output=True, text=True).stdout.strip()
        cc = [a.cc, '-Os', '-static', '-nostdlib', '-nostdinc', '-fno-asynchronous-unwind-tables', '-fno-ident',
              '-include', str(nolibc / 'nolibc.h'), '-I', str(nolibc), '-isystem', str(a.uapi_include),
              '-isystem', compiler_include, '-Wl,--build-id=none', '-s', '-o', str(root / 'bin/restart2'),
              str(source), '-lgcc']
        subprocess.run(cc, check=True)
        if elf_info((root / 'bin/restart2').read_bytes())[0] is not None:
            raise SystemExit('restart2 must be static')
        cc_version = subprocess.run([a.cc, '--version'], check=True, capture_output=True,
                                    text=True).stdout.splitlines()[0]
        git = subprocess.run(['git', '-C', str(nolibc), 'rev-parse', 'HEAD'], capture_output=True, text=True)
        nolibc_commit = git.stdout.strip() if git.returncode == 0 else None
        dpkg = subprocess.run(['dpkg-query', '-S', str(a.uapi_include / 'linux/reboot.h')],
                              capture_output=True, text=True)
        uapi = dpkg.stdout.split(':')[0] if dpkg.returncode == 0 else str(a.uapi_include)
        if dpkg.returncode == 0:
            uapi += ' ' + subprocess.run(['dpkg-query', '-W', '-f', '${Version}', uapi], capture_output=True,
                                         text=True).stdout

    entries, symlinks = {}, {}
    for path in sorted(root.rglob('*')):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            symlinks[rel] = os.readlink(path)
        elif path.is_file():
            entries[rel] = sha(path)
    manifest = {
        'kind': 'piano-maintenance-payload', 'kernel_release': release,
        'kernel_package': {'file': a.kernel_deb.name, 'sha256': sha(a.kernel_deb), 'control': control.split()},
        'ubuntu_squashfs_sha256': a.squashfs_sha256, 'tools': list(TOOLS),
        'packages': dict(sorted(packages.items())), 'licences': copyrights, 'modules': list(MODULES),
        'config_checked': list(CONFIG),
        'restart2': {'source_sha256': sha(source), 'nolibc_tree_commit': nolibc_commit, 'compiler': cc_version,
                     'uapi_headers': uapi},
        'files': entries, 'symlinks': symlinks,
        'scope': 'Unmodified Ubuntu binaries and kernel-package modules; not booted by this tool',
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'output': str(out), 'files': len(entries), 'symlinks': len(symlinks),
                      'kernel_release': release}))


def stage(a):
    """Check a payload and print gen_init_cpio lines; no copy, the files are read in place."""
    payload = a.payload.resolve()
    manifest = json.loads((payload / 'manifest.json').read_text())
    if manifest.get('kind') != 'piano-maintenance-payload':
        raise SystemExit('Not a maintenance payload')
    root = payload / 'root'
    seen_files, seen_links, dirs = {}, {}, set()
    for path in sorted(root.rglob('*')):
        rel = str(path.relative_to(root))
        if not SAFE.fullmatch(rel):
            raise SystemExit(f'Unsafe payload path: {rel}')
        if path.is_symlink():
            seen_links[rel] = os.readlink(path)
        elif path.is_file():
            seen_files[rel] = sha(path)
        elif path.is_dir():
            dirs.add(rel)
        else:
            raise SystemExit(f'Special file in the payload: {rel}')
    if seen_files != manifest['files'] or seen_links != manifest['symlinks']:
        raise SystemExit('Payload differs from its manifest')
    for rel in [*seen_files, *seen_links]:
        if rel.split('/')[0] not in ('usr', 'lib', 'bin') or (rel.startswith('bin/') and rel != 'bin/restart2'):
            raise SystemExit(f'Payload path outside /usr, /lib and /bin/restart2: {rel}')
    for rel, target in seen_links.items():
        resolved = os.path.normpath(str(PurePosixPath(rel).parent / target))
        if target.startswith('/') or resolved.split('/')[0] in ('..', 'bin'):
            raise SystemExit(f'Payload symlink leaves the tree or enters /bin: {rel} -> {target}')
    a.stage_dir.mkdir(parents=True, exist_ok=False)
    (a.stage_dir / 'piano-maintenance.json').write_text(json.dumps(
        {k: manifest[k] for k in manifest if k not in ('files', 'symlinks')} |
        {'payload_manifest_sha256': sha(payload / 'manifest.json')}, indent=2) + '\n')
    print(f'file /etc/piano-maintenance.json {a.stage_dir / "piano-maintenance.json"} 0644 0 0')
    for d in sorted(dirs, key=lambda d: (d.count('/'), d)):
        if d != 'bin':  # emitted by build-initramfs.sh
            print(f'dir /{d} 0755 0 0')
    for rel in sorted(seen_files):
        mode = '0755' if os.access(root / rel, os.X_OK) else '0644'
        print(f'file /{rel} {root / rel} {mode} 0 0')
    for rel, target in sorted(seen_links.items()):
        print(f'slink /{rel} {target} 0777 0 0')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--squashfs', type=Path, required=True, help='Ubuntu 26.04 desktop SquashFS (BUILD.md)')
    b.add_argument('--squashfs-sha256', required=True)
    b.add_argument('--kernel-deb', type=Path, required=True, help='linux-image-<release> package')
    b.add_argument('--nolibc', type=Path, required=True, help='tools/include/nolibc of the pinned kernel tree')
    b.add_argument('--cc', default='aarch64-linux-gnu-gcc')
    b.add_argument('--uapi-include', type=Path, default=Path('/usr/aarch64-linux-gnu/include'),
                   help='arm64 Linux UAPI headers (linux-libc-dev-arm64-cross)')
    b.add_argument('--output', type=Path, required=True)
    s = sub.add_parser('stage')
    s.add_argument('--payload', type=Path, required=True)
    s.add_argument('--stage-dir', type=Path, required=True)
    a = ap.parse_args()
    build(a) if a.command == 'build' else stage(a)


if __name__ == '__main__':
    main()
