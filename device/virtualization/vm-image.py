#!/usr/bin/env python3
"""Unprivileged Ubuntu ARM64 cloud image preparation for a direct-boot VMM.

stdout: one JSON object on success. stderr: diagnostics. Never executes guest code.
The caller must keep the VM stopped and serialize disk/boot operations.
"""
import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import uuid

IMAGE_NAME = "ubuntu-26.04-server-cloudimg-arm64.img"
REQUIRED = (
    "DMA_RESTRICTED_POOL",
    "VIRTIO_PCI",
    "VIRTIO_BLK",
    "VIRTIO_NET",
    "SERIAL_8250",
    "SERIAL_8250_CONSOLE",
    "SERIAL_AMBA_PL011",
    "SERIAL_AMBA_PL011_CONSOLE",
    "ARM_PSCI_FW",
)
QEMU_IMG = os.environ.get("QEMU_IMG", "qemu-img")


def run(*args, input=None):
    p = subprocess.run(
        [str(a) for a in args], input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if p.returncode:
        raise RuntimeError(
            f"{args[0]} exited {p.returncode}: " + p.stderr.decode(errors="replace").strip()
        )
    return p.stdout


def note(message):
    print(message, file=sys.stderr, flush=True)


def download(url, target):
    note("Downloading " + url)
    temporary = target.with_name(target.name + ".part-" + uuid.uuid4().hex)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("xb") as out:
            shutil.copyfileobj(response, out, 1024 * 1024)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def cloud_image(cache, release):
    if not re.fullmatch(r"\d{8}", release):
        raise ValueError("--release must be an immutable YYYYMMDD build date")
    url = f"https://cloud-images.ubuntu.com/releases/resolute/release-{release}/"
    cache = cache / ("release-" + release)
    cache.mkdir(parents=True, exist_ok=True)
    sums, image = cache / "SHA256SUMS", cache / IMAGE_NAME
    download(url + "SHA256SUMS", sums)
    matches = [
        line.split()[0]
        for line in sums.read_text().splitlines()
        if len(line.split()) == 2 and line.split()[1].lstrip("*") == IMAGE_NAME
    ]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{64}", matches[0]):
        raise ValueError("Official SHA256SUMS has no unique valid image entry")
    if not image.exists():
        download(url + IMAGE_NAME, image)
    with image.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if digest != matches[0]:
        raise ValueError("Official SHA-256 mismatch; image was not used")
    note("Official SHA-256: OK")
    return image, url + IMAGE_NAME, digest


def size_bytes(value):
    m = re.fullmatch(r"([1-9][0-9]*)([KMGT]?)", value.upper())
    if not m:
        raise argparse.ArgumentTypeError("size must be bytes or binary K/M/G/T, e.g. 8G")
    return int(m[1]) * 1024 ** ("KMGT".index(m[2]) + 1 if m[2] else 0)


def image_info(disk, fmt=None):
    options = ("-f", fmt) if fmt else ()
    info = json.loads(run(QEMU_IMG, "info", "--output=json", *options, disk))
    if info["format"] not in ("raw", "qcow2"):
        raise ValueError("Only raw and qcow2 disks are supported")
    return info


def gpt_partitions(disk, fmt, work, disk_size):
    header_file = work / "gpt-header.bin"
    run(QEMU_IMG, "dd", "-f", fmt, "if=" + str(disk), "of=" + str(header_file), "bs=512", "count=2")
    data = header_file.read_bytes()
    if len(data) != 1024 or data[512:520] != b"EFI PART":
        raise ValueError("Expected GPT with 512-byte logical sectors")
    lba, count, entry_size = struct.unpack_from("<QII", data, 512 + 72)
    if not (2 <= lba <= 2048 and 1 <= count <= 4096 and 128 <= entry_size <= 4096):
        raise ValueError("Unsupported GPT entry layout")
    if count * entry_size > 16 * 1024 * 1024:
        raise ValueError("GPT table is too large")
    table_file = work / "gpt-entries.bin"
    run(
        QEMU_IMG,
        "dd",
        "-f",
        fmt,
        "if=" + str(disk),
        "of=" + str(table_file),
        "bs=512",
        f"count={lba + (count * entry_size + 511) // 512}",
    )
    # Read a prefix: qemu-img dd count includes skipped input blocks in older QEMU.
    table = table_file.read_bytes()[lba * 512 :]
    parts = []
    for i in range(count):
        entry = table[i * entry_size : (i + 1) * entry_size]
        if len(entry) != entry_size:
            raise ValueError("Truncated GPT table")
        if entry[:16] == bytes(16):
            continue
        kind = str(uuid.UUID(bytes_le=entry[:16]))
        # Linux data, ARM64 root and XBOOTLDR; do not interpret the FAT ESP as ext4.
        if kind not in (
            "0fc63daf-8483-4772-8e79-3d69d8477de4",
            "b921b045-1df0-41c3-af44-4c6f280d3fae",
            "bc13c2ff-59e6-4262-a352-b275fd6f7172",
        ):
            continue
        start, end = struct.unpack_from("<QQ", entry, 32)
        if not (start > 1 and end >= start and (end + 1) * 512 <= disk_size):
            raise ValueError("GPT partition outside disk")
        parts.append((i + 1, start * 512, (end - start + 1) * 512))
    # A separate /boot is normally smaller than the root filesystem.
    return sorted(parts, key=lambda part: part[2])


def debug_quote(value):
    value = str(value)
    if any(c in value for c in "\n\r\0"):
        raise ValueError("Control characters in debugfs path")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def debug_dump(filesystem, guest_path, output):
    command = f"dump {debug_quote(guest_path)} {debug_quote(output)}"
    # debugfs may return zero for a missing file. Check the actual dumped file.
    result = subprocess.run(
        ["debugfs", "-R", command, str(filesystem)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.returncode == 0 and output.is_file() and output.stat().st_size > 0


def grub_boot(grub):
    selected = None
    for raw in grub.splitlines():
        words = shlex.split(raw.strip(), comments=True)
        if not words:
            continue
        if words[0] in ("linux", "linuxefi") and selected is None:
            if len(words) < 3 or "recovery" in words[2:]:
                continue
            kernel, args = words[1], words[2:]
            if any("$" in arg for arg in args):
                raise ValueError("GRUB command line contains unresolved variables")
            selected = (kernel, " ".join(args))
        elif words[0] in ("initrd", "initrdefi") and selected:
            if len(words) != 2:
                raise ValueError("Expected one ARM64 initrd in the selected GRUB entry")
            return selected[0], words[1], selected[1]
        elif words[0] in ("linux", "linuxefi", "menuentry") and selected:
            raise ValueError("Selected GRUB entry has no initrd")
    raise ValueError("No supported default Linux/initrd pair in grub.cfg")


def unwrap_kernel(data):
    # ELF/PE payloads are data, never executed by this host-side tool.
    for _ in range(5):
        if len(data) >= 64 and data[56:60] == b"ARM\x64":
            return data
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
            continue
        if len(data) >= 64 and data[4:8] == b"zimg":
            offset, length = struct.unpack_from("<II", data, 8)
            method = data[24:32].split(b"\0")[0]
            if offset < 64 or not length or offset + length > len(data):
                raise ValueError("Invalid EFI zboot payload bounds")
            payload = data[offset : offset + length]
            if method == b"zstd":
                data = run("zstd", "-d", "-q", "-c", input=payload)
            elif method == b"gzip":
                data = gzip.decompress(payload)
            else:
                raise ValueError("Unsupported EFI zboot compression: " + repr(method))
            continue
        if len(data) >= 64 and data[:2] == b"MZ":
            pe = struct.unpack_from("<I", data, 60)[0]
            if pe + 24 > len(data) or data[pe : pe + 4] != b"PE\0\0":
                raise ValueError("Invalid PE header")
            machine, sections = struct.unpack_from("<HH", data, pe + 4)
            optional_size = struct.unpack_from("<H", data, pe + 20)[0]
            start = pe + 24 + optional_size
            if machine != 0xAA64 or not 1 <= sections <= 512 or start + sections * 40 > len(data):
                raise ValueError("Expected a valid ARM64 PE section table")
            payloads = []
            for i in range(sections):
                entry = data[start + 40 * i : start + 40 * (i + 1)]
                if entry[:8].rstrip(b"\0") != b".linux":
                    continue
                virtual, _, raw_size, offset = struct.unpack_from("<IIII", entry, 8)
                length = virtual or raw_size
                if length > raw_size or not length or offset + length > len(data):
                    raise ValueError("Invalid .linux section bounds")
                payloads.append(data[offset : offset + length])
            if len(payloads) != 1:
                raise ValueError("Expected exactly one EFI .linux section")
            data = payloads[0]
            continue
        raise ValueError("Unsupported kernel format (not ARM64 Image, EFI .linux, or zboot)")
    raise ValueError("Too many kernel wrapper layers")


def extract_boot(disk, directory):
    """Read a stopped guest disk and atomically publish a complete boot generation."""
    if (directory / "boot").exists() and not (directory / "boot").is_symlink():
        raise ValueError("boot exists and is not a managed symlink")
    # Guest-writable bytes must never choose a host backing-file format.
    info = image_info(disk, "raw")
    with tempfile.TemporaryDirectory(prefix=".extract-", dir=directory) as temporary:
        work = Path(temporary)
        found = None
        for number, offset, length in gpt_partitions(
            disk, info["format"], work, info["virtual-size"]
        ):
            filesystem = work / "partition.ext4"
            node = {
                "driver": "raw",
                "offset": offset,
                "size": length,
                "file": {
                    "driver": info["format"],
                    "file": {"driver": "file", "filename": str(disk)},
                },
            }
            run(
                QEMU_IMG, "convert", "json:" + json.dumps(node), "-O", "raw", "-S", "4k", filesystem
            )
            for candidate in ("/grub/grub.cfg", "/boot/grub/grub.cfg"):
                grub_file = work / "grub.cfg"
                grub_file.unlink(missing_ok=True)
                if debug_dump(filesystem, candidate, grub_file):
                    found = (number, candidate, grub_file.read_text())
                    break
            if found:
                break
            filesystem.unlink()
        if not found:
            raise ValueError("No readable ext4 /boot with grub.cfg found")
        number, grub_path, grub = found
        kernel_path, initrd_path, cmdline = grub_boot(grub)
        release = Path(kernel_path).name.removeprefix("vmlinuz-")
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]*", release)
            or release == Path(kernel_path).name
        ):
            raise ValueError("Expected a versioned vmlinuz filename")
        if Path(initrd_path).name != "initrd.img-" + release:
            raise ValueError("Kernel and initrd versions do not match")
        output = work / "generation"
        output.mkdir()
        # GRUB paths are relative to its filesystem, not the eventual root mount.
        for name, source in (
            ("vmlinuz", kernel_path),
            ("initrd.img", initrd_path),
            ("config", str(Path(kernel_path).parent / ("config-" + release))),
        ):
            if not source.startswith("/") or ".." in Path(source).parts:
                raise ValueError("Unsupported GRUB file path: " + source)
            if not debug_dump(filesystem, source, output / name):
                raise ValueError("Cannot extract " + source)
        config = (output / "config").read_text()
        missing = [symbol for symbol in REQUIRED if f"CONFIG_{symbol}=y" not in config.splitlines()]
        if missing:
            raise ValueError("Guest hard requirements not built in: " + ", ".join(missing))
        (output / "Image").write_bytes(unwrap_kernel((output / "vmlinuz").read_bytes()))
        (output / "cmdline").write_text(cmdline + "\n")
        (output / "kernel-release").write_text(release + "\n")
        (output / "grub.cfg").write_text(grub)
        final = directory / ("boot-" + release + "-" + uuid.uuid4().hex[:8])
        output.rename(final)
        link = directory / (".boot-" + uuid.uuid4().hex)
        try:
            link.symlink_to(final.name)
            os.replace(link, directory / "boot")
        finally:
            link.unlink(missing_ok=True)
        note(f"Extracted {release} from partition {number}:{grub_path}")
        return release


def public_key(path):
    lines = path.read_text().splitlines()
    if len(lines) != 1:
        raise ValueError("Provide exactly one SSH public key, no authorized_keys options")
    fields = lines[0].split()
    if len(fields) < 2 or not re.fullmatch(
        r"(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521))", fields[0]
    ):
        raise ValueError("Expected an OpenSSH ed25519/RSA/ECDSA public key")
    blob = base64.b64decode(fields[1], validate=True)
    if len(blob) < 4:
        raise ValueError("Truncated SSH public key")
    size = struct.unpack_from(">I", blob)[0]
    if blob[4 : 4 + size] != fields[0].encode() or len(blob) <= 4 + size:
        raise ValueError("SSH public key type does not match its wire data")
    return lines[0]


def seed(directory, username, key, instance_id):
    data = {
        "users": [
            {
                "name": username,
                "groups": ["adm", "sudo"],
                "shell": "/bin/bash",
                "lock_passwd": True,
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "ssh_authorized_keys": [key],
            }
        ],
        "ssh_pwauth": False,
        "disable_root": True,
    }
    source = directory / "seed"
    source.mkdir()
    (source / "user-data").write_text("#cloud-config\n" + json.dumps(data, indent=2) + "\n")
    (source / "meta-data").write_text(
        json.dumps({"instance-id": instance_id, "local-hostname": "ubuntu-vm"}, indent=2) + "\n"
    )
    run(
        "genisoimage",
        "-quiet",
        "-output",
        directory / "seed.iso",
        "-volid",
        "cidata",
        "-joliet",
        "-rock",
        source / "user-data",
        source / "meta-data",
    )


def describe(directory):
    boot = (directory / "boot").resolve(strict=True)
    return {
        "schema": 1,
        "directory": str(directory),
        "disk": str(directory / "disk.raw"),
        "disk_format": "raw",
        "seed": str(directory / "seed.iso"),
        "boot_directory": str(boot),
        "kernel": str(boot / "Image"),
        "initrd": str(boot / "initrd.img"),
        "kernel_release": (boot / "kernel-release").read_text().strip(),
        "cmdline": (boot / "cmdline").read_text().strip(),
    }


def create(args):
    directory = args.output.resolve()
    if directory.exists():
        raise ValueError("Output already exists; use a new VM directory")
    if args.username == "root" or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", args.username):
        raise ValueError("Provide a valid non-root Ubuntu username")
    if args.instance_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.instance_id):
        raise ValueError("instance-id must contain only letters, digits, dot, underscore or hyphen")
    key = public_key(args.ssh_public_key)
    image, url, digest = cloud_image(args.cache_dir.resolve(), args.release)
    info = image_info(image)
    if args.size < info["virtual-size"]:
        raise ValueError("Requested disk size is smaller than the cloud image")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="." + directory.name + "-", dir=directory.parent
    ) as temporary:
        staging = Path(temporary) / "vm"
        staging.mkdir()
        run(
            QEMU_IMG,
            "convert",
            "-f",
            info["format"],
            "-O",
            "raw",
            "-S",
            "4k",
            image,
            staging / "disk.raw",
        )
        run(QEMU_IMG, "resize", "-f", "raw", staging / "disk.raw", str(args.size))
        seed(staging, args.username, key, args.instance_id or "vm-" + uuid.uuid4().hex)
        extract_boot(staging / "disk.raw", staging)
        staging.rename(directory)
    result = describe(directory)
    result.update(source_url=url, source_sha256=digest)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    c = commands.add_parser(
        "create", help="download/verify, create sparse raw disk, seed and boot files"
    )
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--size", type=size_bytes, required=True)
    c.add_argument("--username", required=True)
    c.add_argument("--ssh-public-key", type=Path, required=True)
    c.add_argument("--cache-dir", type=Path, required=True)
    c.add_argument("--release", default="20261016")
    c.add_argument("--instance-id", help="optional caller-selected stable NoCloud instance-id")
    r = commands.add_parser("refresh", help="re-extract /boot from a stopped guest disk")
    r.add_argument("directory", type=Path, help="VM directory created by create")
    args = parser.parse_args()
    os.umask(0o077)
    # All downloads/conversions and child tools run at nice >= 10 on either architecture.
    os.setpriority(os.PRIO_PROCESS, 0, max(10, os.getpriority(os.PRIO_PROCESS, 0)))
    try:
        if args.command == "create":
            result = create(args)
        else:
            directory = args.directory.resolve(strict=True)
            extract_boot(directory / "disk.raw", directory)
            result = describe(directory)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
