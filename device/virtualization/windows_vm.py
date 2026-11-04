# SPDX-License-Identifier: MIT
"""Windows-specific media, display and boot arguments for the ordinary-user CLI."""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys

ASSETS = Path("/usr/share/piano/vm/windows")


def prepare(vm, cfg, args, bundle):
    script = bundle / "windows/prepare-windows.py"
    if not script.is_file() or not (ASSETS / "firmware.fd").is_file():
        raise ValueError("Install piano-vm-windows before creating a Windows VM")
    if not args.iso or not args.iso.expanduser().is_file():
        raise ValueError("Windows requires --iso with your official ARM64 Windows ISO")
    command = [
        sys.executable,
        str(script),
        "--iso",
        str(args.iso.expanduser().resolve()),
        "--output",
        str(vm / "install"),
        "--instance-id",
        cfg["id"],
    ]
    if args.edition:
        command += ["--edition", args.edition]
    if args.local_user:
        command += ["--local-user", args.local_user]
    # Preserve the terminal for edition selection and getpass; only stdout is JSON.
    prepared = subprocess.run(command, check=True, stdout=subprocess.PIPE, text=True)
    cfg["windows_edition"] = json.loads(prepared.stdout)["edition"]
    shutil.copyfile(ASSETS / "firmware.fd", vm / "firmware.fd")
    with (vm / "disk.raw").open("xb") as stream:
        stream.truncate(cfg["disk_gib"] * 1024**3)


def arguments(vm, cfg, rd, crosvm):
    argv = [
        str(crosvm),
        "--no-syslog",
        "--log-level",
        "info",
        "--extended-status",
        "run",
        "--name",
        "piano-" + cfg["name"],
        "--hypervisor",
        "gunyah",
        "--protected-vm-without-firmware",
        "--cpus",
        str(cfg["cpus"]),
        "--mem",
        str(cfg["memory_mib"]),
        "--swiotlb",
        "256",
        "--hugepages",
        "--no-pmu",
        "--no-smt",
        "--no-usb",
        "--socket",
        str(rd / "control.sock"),
        "--block",
        str(vm / "disk.raw"),
        "--net",
        f'tap-name={cfg["tap"]},mac={cfg["mac"]}',
        "--serial",
        f"type=file,hardware=serial,num=1,console,earlycon,path={rd}/serial.log,input={rd}/console.in",
        "--simplefb",
        "width=1280,height=720",
        "--vnc-server",
        f'host=127.0.0.1,port={cfg["vnc_port"]}',
    ]
    if cfg["install_media"]:
        argv += [
            "--block",
            str(vm / "install/boot.raw"),
            "--block",
            str(vm / "install/install.raw"),
            "--block",
            str(vm / "install/drivers.raw") + ",ro=true",
        ]
    return argv + [str(vm / "firmware.fd")]


def display_available(cfg):
    # Match the VNC listener: SO_REUSEADDR lets a previous run's closing
    # connections be ignored, while an active listener still blocks bind().
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", cfg["vnc_port"]))
        except OSError as error:
            raise ValueError("Windows VNC loopback port is already in use") from error


def deployment_complete(vm, cfg):
    image = str(vm / "install/boot.raw") + "@@1048576"
    result = subprocess.run(
        ["mtype", "-i", image, "::/PIANO_DEPLOYED.TAG"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == cfg["id"]


def save_deployment_logs(vm):
    logs = vm / "deployment-logs"
    logs.mkdir(exist_ok=True)
    for name in (
        "partition.log",
        "dism-apply.log",
        "dism-drivers.log",
        "offline-drivers.log",
        "deployment-config.log",
    ):
        subprocess.run(
            [
                "mcopy",
                "-o",
                "-i",
                str(vm / "install/boot.raw") + "@@1048576",
                "::/" + name,
                str(logs / name),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def view(vm, cfg):
    if not shutil.which("remmina"):
        raise ValueError("Install piano-vm-windows (Remmina and its VNC plugin are required)")
    profile = vm / "display.remmina"
    profile.write_text(
        "[remmina]\nname=Piano " + cfg["name"] + "\nprotocol=VNC\n"
        f'server=127.0.0.1:{cfg["vnc_port"]}\nviewmode=1\n'
    )
    profile.chmod(0o600)
    subprocess.Popen(["remmina", "--connect", str(profile)], start_new_session=True)
