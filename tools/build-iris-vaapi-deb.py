#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a pinned Iris VA-API driver without installing packages or accessing devices."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from lib.deps import fetch

REPO = Path(__file__).resolve().parents[1]
INPUT = REPO / "device/video/iris-vaapi"


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--fetch-only", action="store_true")
    parser.add_argument("--cross-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=len(os.sched_getaffinity(0)))
    args = parser.parse_args()
    os.umask(0o022)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    lock_fd = (out / ".lock").open("w")
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock = json.loads((INPUT / "source.json").read_text())
    args.source_archive = fetch(lock["archive_url"], args.source_archive or out / "inputs/source.tar.gz", lock["archive_sha256"])
    if args.fetch_only:
        return
    if args.cross_file is None:
        parser.error("--cross-file is required for compilation")
    if sha(args.source_archive) != lock["archive_sha256"]:
        raise ValueError("Source archive does not match source.json")
    fingerprint = {
        "source": lock,
        "cross_file": sha(args.cross_file),
        "meson": subprocess.check_output(["meson", "--version"], text=True).strip(),
        "patches": {p: sha(INPUT / p) for p in lock["patches"]},
    }
    previous = out / "inputs.json"
    if previous.exists() and json.loads(previous.read_text()) != fingerprint:
        raise ValueError("Build inputs changed; select a fresh --output")
    source = out / ("qualcomm-iris-vaapi-" + lock["commit"])
    if not source.exists():
        with tarfile.open(args.source_archive) as archive:
            archive.extractall(out, filter="data")
        for patch in lock["patches"]:
            run("patch", "--batch", "--fuzz=0", "-p1", "-i", INPUT / patch, cwd=source)
    previous.write_text(json.dumps(fingerprint, indent=2) + "\n")
    build = out / "build"
    if not (build / "build.ninja").exists():
        run("meson", "setup", build, source, "--cross-file", args.cross_file.resolve(),
            "--prefix=/usr", "--libdir=lib/aarch64-linux-gnu", "--buildtype=release")
    # Refuse an apparently successful build that silently omitted the GPU copy engine.
    commands = json.loads((build / "compile_commands.json").read_text())
    if not any(c["file"].endswith("/iris/gpu_copy.cc") for c in commands):
        raise ValueError("EGL, GLES and GBM development dependencies are required")
    run("meson", "compile", "-C", build, "-j", args.jobs)
    run("meson", "test", "-C", build, "--print-errorlogs")
    driver = build / "src/v4l2_drv_video.so"
    elf = subprocess.check_output(["readelf", "-h", driver], text=True)
    if "AArch64" not in elf:
        raise ValueError("Driver is not AArch64")
    package = "piano-iris-vaapi"
    stage = out / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    libdir = stage / "usr/lib/aarch64-linux-gnu/dri"
    libdir.mkdir(parents=True)
    shutil.copy2(driver, libdir / driver.name)
    # libva uses the MSM DRM driver name for native discovery. No session-wide
    # LIBVA_DRIVER_NAME override and no dependency on a fixed render node.
    (libdir / "msm_drv_video.so").symlink_to(driver.name)
    doc = stage / "usr/share/doc" / package
    doc.mkdir(parents=True)
    for name in ("COPYING", "COPYING.MIT", "COPYING.LGPL", "AUTHORS", "CREDITS",
                 "source.json", "build-deps.json", "README.md"):
        shutil.copy2(INPUT / name, doc / name)
    shutil.copy2(INPUT / "COPYING", doc / "copyright")
    # Ship corresponding source inside the deb as well as beside it: normal
    # release packaging then carries the LGPL source without a separate step.
    source_archive = out / f"{package}_{lock['version']}.tar.xz"
    with tarfile.open(source_archive, "w:xz") as archive:
        archive.add(source, arcname=source.name)
        archive.add(INPUT, arcname="piano-integration")
        archive.add(Path(__file__).resolve(), arcname="piano-integration/build-iris-vaapi-deb.py")
    shutil.copy2(source_archive, doc / "source.tar.xz")
    control = stage / "DEBIAN"
    control.mkdir()
    (control / "control").write_text(
        f"Package: {package}\nVersion: {lock['version']}\nArchitecture: arm64\n"
        "Maintainer: yzddmr6 <46088090+yzddmr6@users.noreply.github.com>\n"
        "Section: video\nPriority: optional\n"
        "Depends: libc6 (>= 2.38), libstdc++6 (>= 13.1), libgcc-s1, "
        "libva2 (>= 2.23), libdrm2 (>= 2.4.52), libegl1, libgles2, libgbm1\n"
        "Conflicts: libva-v4l2, libva-v4l2-iris\n"
        "Description: VA-API video driver for Qualcomm Iris\n"
        " Stateful V4L2 decoding and encoding with limited RGB-to-NV12 video processing.\n")
    deb = out / f"{package}_{lock['version']}_arm64.deb"
    run("dpkg-deb", "--root-owner-group", "-Zxz", "--build", stage, deb)
    manifest = {
        "status": "OFFLINE_ONLY",
        "source_commit": lock["commit"],
        "inputs": fingerprint,
        "driver_sha256": sha(driver),
        "artifacts": {p.name: sha(p) for p in (deb, source_archive)},
        "hardware_acceptance": "pending",
    }
    compilers = json.loads((build / "meson-info/intro-compilers.json").read_text())
    manifest["compilers"] = {
        language: {key: compiler[key] for key in ("id", "version")}
        for language, compiler in compilers["host"].items()
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(deb)


if __name__ == "__main__":
    main()
