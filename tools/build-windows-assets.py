#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build locked Gunyah RELEASE firmware/helper and select signed Windows drivers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, **kwargs)


def sha(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def archive(cache, commit, output):
    output.mkdir(parents=True, exist_ok=True)
    producer = subprocess.Popen(
        ["git", "-C", str(cache), "archive", commit], stdout=subprocess.PIPE
    )
    try:
        run(["tar", "-xf", "-", "-C", output], stdin=producer.stdout)
    finally:
        producer.stdout.close()
    if producer.wait():
        raise RuntimeError("Could not export locked source: " + str(cache))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--firmware-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    source = repo / "device/virtualization/windows"
    lock = json.loads((repo / "tools/windows.lock.json").read_text())
    os.setpriority(os.PRIO_PROCESS, 0, max(10, os.getpriority(os.PRIO_PROCESS, 0)))
    os.umask(0o022)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for package, version in lock["host_packages"].items():
        if (
            subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", package], text=True)
            != version
        ):
            raise ValueError("Locked host tool differs: " + package)
    host = out / "host"
    host.mkdir()
    for item in lock["host_debs"]:
        package = args.inputs / item["filename"]
        if sha(package) != item["sha256"]:
            raise ValueError("Input checksum: " + package.name)
        run(["dpkg-deb", "-x", package, host])
    tree = out / "src"
    for item in lock["firmware_repositories"]:
        archive(args.firmware_cache / item["path"], item["commit"], tree / item["path"])
    run(["git", "init", "-q", tree / "edk2"])
    for patch in sorted((tree / "patches/edk2").glob("*.patch")):
        run(["git", "-C", tree / "edk2", "apply", patch])
    patch = repo / lock["firmware_patch"]["path"]
    if sha(patch) != lock["firmware_patch"]["sha256"]:
        raise ValueError("Firmware patch differs")
    run(["git", "-C", tree / "edk2", "apply", patch])
    # BaseTools only generates cookies when these files are absent. Seed the
    # locked build inputs without patching EDK2 or changing runtime protection.
    build_dir = tree / "build/Build/GunyahKernel-AARCH64/RELEASE_GCC"
    build_dir.mkdir(parents=True)
    for bits in (32, 64):
        values = lock["stack_cookies"]["values" + str(bits)]
        if len(values) != 100 or any(
            type(value) is not int or not 0 <= value < (1 << bits) - 1
            for value in values
        ):
            raise ValueError("Invalid locked stack cookie table: " + str(bits))
        (build_dir / f"StackCookieValues{bits}.json").write_text(json.dumps(values) + "\n")
    # A private mount/user namespace gives compiler paths one neutral spelling.
    build_script = out / "firmware-build.sh"
    build_script.write_text(
        '''#!/bin/bash
set -euo pipefail
cd /build/piano-edk2
export EDK_TOOLS_PATH=$PWD/edk2/BaseTools
export PACKAGES_PATH=$PWD/edk2:$PWD
export WORKSPACE=$PWD/build
export GCC_AARCH64_PREFIX=aarch64-linux-gnu-
export PATH=/toolchain/usr/bin:/usr/bin:/bin
export CPATH=/toolchain/usr/include
export LIBRARY_PATH=/toolchain/usr/lib/x86_64-linux-gnu
export LD_LIBRARY_PATH=/toolchain/usr/lib/x86_64-linux-gnu
mkdir -p "$WORKSPACE"
set +u
source edk2/edksetup.sh
set -u
make -C "$EDK_TOOLS_PATH" -j1
build -t GCC -a AARCH64 -b RELEASE -D DISABLE_NEW_DEPRECATED_INTERFACES=TRUE \\
  -D FIRMWARE_VER="'''
        + lock["firmware_version"]
        + """" -D PCI_CAM_MODE=FALSE \\
  -p GunyahPkg/GunyahKernel.dsc -n "$(nproc)"
"""
    )
    sandbox = [
        "bwrap",
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        "/etc",
        "/etc",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/build",
        "--bind",
        str(tree),
        "/build/piano-edk2",
        "--ro-bind",
        str(host),
        "/toolchain",
        "--ro-bind",
        str(build_script),
        "/build-firmware.sh",
        "--chdir",
        "/build/piano-edk2",
        "env",
        "LC_ALL=C",
        "TZ=UTC",
        "PYTHONHASHSEED=0",
        "SOURCE_DATE_EPOCH=" + str(lock["source_date_epoch"]),
        "bash",
        "/build-firmware.sh",
    ]
    run(sandbox)
    assets = out / "assets"
    assets.mkdir()
    firmware = tree / "build/Build/GunyahKernel-AARCH64/RELEASE_GCC/FV/GUNYAH_EFI.fd"
    shutil.copyfile(firmware, assets / "firmware.fd")
    data = firmware.read_bytes()
    if b"/private/" in data or str(out).encode() in data or b"/home/" in data:
        raise ValueError("Firmware contains a non-neutral build path")
    # Original, freestanding ARM64 PE helper; no Windows SDK or signing operation.
    helper = out / "helper"
    helper.mkdir()
    env = dict(
        os.environ,
        LD_LIBRARY_PATH=str(host / "usr/lib/x86_64-linux-gnu"),
        SOURCE_DATE_EPOCH=str(lock["source_date_epoch"]),
    )
    exports = {
        "kernel32": [
            "ExitProcess",
            "GetLastError",
            "GetStdHandle",
            "WriteFile",
            "CreateFileW",
            "ReadFile",
            "CloseHandle",
            "GetCommandLineW",
            "LoadLibraryW",
            "GetProcAddress",
        ],
        "crypt32": ["CertOpenStore", "CertAddEncodedCertificateToStore", "CertCloseStore"],
        "setupapi": [
            "SetupDiCreateDeviceInfoList",
            "SetupDiCreateDeviceInfoW",
            "SetupDiSetDeviceRegistryPropertyW",
            "SetupDiCallClassInstaller",
            "SetupDiDestroyDeviceInfoList",
            "SetupDiGetClassDevsW",
            "SetupDiEnumDeviceInterfaces",
        ],
    }
    for dll, names in exports.items():
        definition = helper / (dll + ".def")
        definition.write_text("LIBRARY " + dll + ".dll\nEXPORTS\n" + "\n".join(names) + "\n")
        run(
            [
                host / "usr/bin/llvm-dlltool-18",
                "-m",
                "arm64",
                "-d",
                definition,
                "-l",
                helper / (dll + ".lib"),
            ],
            env=env,
        )
    run(
        [
            host / "usr/bin/clang-18",
            "--target=aarch64-pc-windows-msvc",
            "-O2",
            "-fno-stack-protector",
            "-ffreestanding",
            "-fshort-wchar",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-c",
            source / "winpe-helper.c",
            "-o",
            helper / "winpe-helper.obj",
        ],
        env=env,
    )
    run(
        [
            host / "usr/bin/lld-link-18",
            "/machine:arm64",
            "/subsystem:console",
            "/entry:mainCRTStartup",
            "/nodefaultlib",
            "/timestamp:0",
            "/out:" + str(assets / "winpe-helper.exe"),
            helper / "winpe-helper.obj",
            *[helper / (name + ".lib") for name in exports],
        ],
        env=env,
    )
    driver_archive = args.inputs / lock["drivers"]["filename"]
    if sha(driver_archive) != lock["drivers"]["sha256"]:
        raise ValueError("Driver release attachment differs")
    families = {"rdmapool", "pvmpower", "viostor", "NetKVM", "vioinput"}
    with zipfile.ZipFile(driver_archive) as package:
        for name in package.namelist():
            path = Path(name)
            if name == "DroidVM_Test.cer" or (
                len(path.parts) == 3
                and path.parts[0] == "drivers"
                and path.parts[1] in families
                and path.suffix.lower() in (".sys", ".cat", ".inf", ".exe")
            ):
                target = assets / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.read(name))
    if {p.name for p in (assets / "drivers").iterdir()} != families:
        raise ValueError("Incomplete driver release")
    shutil.copytree(source / "licenses", assets / "licenses")
    shutil.copyfile(tree / "edk2/License.txt", assets / "licenses/edk2.txt")
    for item in lock["firmware_repositories"]:
        directory = tree / item["path"]
        label = item["path"].replace("/", "-") if item["path"] != "." else "edk2-gunyah"
        for filename in directory.iterdir():
            if filename.is_file() and filename.name.upper().startswith(
                ("LICENSE", "LICENCE", "COPYING", "BSD-", "GPL")
            ):
                dest = assets / "licenses" / label / filename.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(filename, dest)
    notices = {}
    for path in sorted((assets / "drivers").rglob("*")):
        if path.is_file():
            family = path.relative_to(assets / "drivers").parts[0]
            notices[str(path.relative_to(assets))] = {
                "license": (
                    "BSD-2-Clause-Patent"
                    if family in ("rdmapool", "pvmpower")
                    else "BSD-3-Clause AND BSD-2-Clause-Patent"
                ),
                "source": lock["drivers"]["source_url"]
                + "/tree/"
                + lock["drivers"]["source_commit"],
                "sha256": sha(path),
            }
    notices["DroidVM_Test.cer"] = {
        "license": "BSD-3-Clause",
        "source": lock["drivers"]["source_url"],
        "sha256": sha(assets / "DroidVM_Test.cer"),
    }
    (assets / "driver-files.json").write_text(json.dumps(notices, indent=2) + "\n")
    for name, expected in lock["artifacts"].items():
        if sha(assets / name) != expected:
            raise ValueError("Locked Windows artifact differs: " + name)
    (out / "SHA256SUMS").write_text(
        "".join(
            sha(p) + "  " + str(p.relative_to(assets)) + "\n"
            for p in sorted(assets.rglob("*"))
            if p.is_file()
        )
    )
    print("firmware SHA256", sha(assets / "firmware.fd"))
    print("helper SHA256", sha(assets / "winpe-helper.exe"))


if __name__ == "__main__":
    main()
