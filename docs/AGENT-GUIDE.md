# Agent and Operator Guide

[中文](AGENT-GUIDE.zh-CN.md)

How to build, install, update, switch and recover Ubuntu on the Xiaomi Pad 8 Pro (`piano`).
Written for AI agents and engineers; every step is a command and its expected result.

Conventions:

- Host commands run from the root of this repository on a Linux x86_64 host. Commands marked
  *on the tablet* run in a shell on the tablet.
- `<SERIAL>` is the tablet's serial number (`adb devices`, `fastboot devices`), `<USER>` an account
  on the tablet, `<release>` the project kernel package release, `<N>` its ABI number.
- `# expect` comments give the expected output.
- Status words: *verified on the test unit*; *not yet verified on the device*; *not yet available*
  (no tool exists); *manual steps* (standard commands, no project tool yet).

## 1. Scope and safety rules

1. **Bind every device command to the serial:** `adb -s <SERIAL>`, `fastboot -s <SERIAL>`,
   `tools/piano-switch.py --serial <SERIAL>`, and `tools/piano-devmode-ssh.py --serial <SERIAL>` when
   more than one tablet is attached. Never operate a device you have not identified.
2. **One driver at a time.** Before a device command, make sure no other session is driving the
   tablet; never overlap boots, writes or captures.
   ```bash
   pgrep -af 'fastboot|adb'      # expect: no fastboot or adb client of another session (the adb server is shared)
   ```
3. **Battery at 40 % or more before leaving Android**, from a fresh reading. Linux does not charge
   the battery, also with USB connected. A charging indicator or time on the charger is not a reading.
   ```bash
   adb -s <SERIAL> shell dumpsys battery | grep level      # expect "level: 40" or higher
   fastboot -s <SERIAL> getvar battery-soc-ok              # expect "battery-soc-ok: yes"
   ```
4. **Write set.** Switching writes only `init_boot_a`, `vendor_boot_a` and `boot_a`. Installation
   also writes the LUN 0 GPT, `userdata`, `metadata`, `linux_rootfs` and `linux_data`. Never write
   `vbmeta`, `dtbo`, any `_b` partition, `persist` or other calibration, modem or firmware
   partitions, and never run `fastboot --set-active=b`: on the test unit slot B holds an older stock
   chain that does not boot Android.
5. **Keep a way back.**
   - Power + Volume Down enters Fastboot from any state.
   - Keep host copies of the Android set and of the current Ubuntu set (§6.6).
   - Boot new images once as a bounded debug RAM boot (§7.2) before writing them to slot A.
   - Before a release cold boot with nobody at the tablet, enable developer mode (§7.1): release
     images expose no USB function otherwise.
6. **Back up before any partition write** (§4.2). `sdX` names change between boots and backup file
   names are not device names: select targets by UFS controller, LUN, GPT GUID, label and geometry.
7. **On a failure**, stop and locate the earliest failing layer (transfer, device commit, boot,
   handshake). Do not repeat a destructive command after one failed status query, do not re-flash
   filesystems that were written successfully, and do not interrupt a write that is still running;
   monitor its progress separately.
8. **Never commit** to either repository: serial numbers, the Bluetooth and Wi-Fi addresses, touch
   calibration, the boot profile, `/etc/piano/*`, the signing key, backups, stock files other than
   the in-repo `firmware/` set, raw logs and captures. Keep them in the ignored `private/` directory or outside the repository.

## 2. Repositories and pins

| Input | Location | Pin |
|---|---|---|
| Integration | this repository | the checked-out commit |
| Kernel | `https://github.com/yzddmr6/linux-sm8750-piano`, branch `piano/dev` | [`kernel/source.json`](../kernel/source.json) (`commit`, `config`, `release_config_fragments`), [`kernel/abi.json`](../kernel/abi.json) |
| Firmware | [`firmware/`](../firmware/README.md): stock OS3.0.303.0.WPYCNXM files in `/usr/lib/firmware` layout | identity locks `firmware/piano-*.json` (names, sizes, SHA-256) |
| Mesa | upstream Mesa 26.2.0 | [`mesa/source.json`](../mesa/source.json), `mesa/piano-fixes.patch` |
| Ubuntu | 26.04 Desktop ARM64 ISO | SHA-256 `c2afd538d66fdd77377d03f1ed2ac76a34f1c116baecc9a8170d68f833121f57`; `ubuntu/26.04.sources` |
| AOSP mkbootimg | `tools/fetch-aosp-mkbootimg.sh` | commit `954bc3ead5e679005fddf3484d247f2557b3c2c9` |
| BusyBox (ARM64) | `tools/fetch-busybox-arm64.sh` | `busybox-static_1.36.1-6ubuntu3.1_arm64.deb` |
| avbtool | AOSP `external/avb` | fetch script not yet available; pass with `--avbtool` |

```bash
git rev-parse HEAD                                          # integration commit, record it
git clone --branch piano/dev https://github.com/yzddmr6/linux-sm8750-piano ../linux
python3 -c 'import json; print(json.load(open("kernel/source.json"))["commit"])'
git -C ../linux rev-parse HEAD                              # expect the same commit
git -C ../linux status --porcelain --untracked-files=no     # expect no output
python3 tools/kernel_tag.py ../linux                        # expect "v7.2.6" (upstream tag of the Makefile version)
sh tools/fetch-aosp-mkbootimg.sh                            # pinned checkout in tools/local/aosp-mkbootimg
sh tools/fetch-busybox-arm64.sh                             # expect ".../usr/bin/busybox: OK"
```

`tools/build-kernel.sh` refuses a tree whose HEAD differs from `kernel/source.json` ("Kernel HEAD does
not match kernel/source.json") or that has uncommitted tracked changes; `tools/build-mesa.sh`
refuses a Mesa HEAD other than `mesa/source.json` ("Mesa HEAD mismatch").

**Firmware.** `firmware/` holds the stock files the release root loads, unmodified, with the
licence statement in [firmware/README.md](../firmware/README.md); per-unit data is not included.
To use files from your own tablet instead, copy them into a directory with the same layout (rename
the Wi-Fi files as listed in `firmware/piano-wlan.json`) and pass it to `build-firmware-deb.py
--firmware-tree` (§3.5). Every file is checked against its lock; a missing or different file stops
the build, so another stock build needs updated locks. `stage-firmware.py` stages the DSP files
alone:

```bash
python3 -S tools/stage-firmware.py --output private/fw-dsp            # the in-repo ADSP images
# expect '{"state": "LOCAL_PAYLOAD_STAGED_NOT_INSTALLED", "files": 56, ...}'
python3 -S tools/stage-firmware.py --firmware-dir <stock modem_a image/ directory> --output private/fw-dsp-own
# the full DSP lock from your own extraction
```

## 3. Build pipeline

Order: kernel (§3.1) → ABI entry (§3.2) → boot DTB (§3.3) → kernel package (§3.4) → device and
firmware packages (§3.5) → root filesystem (§4.4) → boot image set (§3.6). Host tools are listed in
[build.zh-CN.md](build.zh-CN.md); additionally `dpkg-deb`, `python3-debian`, `device-tree-compiler`
(`fdtget`/`fdtput`), `python3-libfdt`, AOSP libufdt's `ufdt_apply_overlay`, `m4` and `alsatplg`.

### 3.1 Kernel

```bash
CONFIG_FRAGMENT_EXTRA="arch/arm64/configs/piano-desktop-diag.config arch/arm64/configs/piano-persistent.config arch/arm64/configs/piano-release.config" \
BUILD_MODULES=1 KERNEL_SOURCE=../linux OUT_DIR="$PWD/out/kernel-release" bash tools/build-kernel.sh
# expect "Piano kernel build complete: .../out/kernel-release/build-result.json (boot packaging is separate)"
python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["kernel_release"])' out/kernel-release/build-result.json
# expect "7.2.6-piano<N>"
```

- The base fragment (`config` in `kernel/source.json`) is merged first; `CONFIG_FRAGMENT_EXTRA` takes
  the remaining `release_config_fragments` in order, `piano-release.config` last.
- Reuse the same OUT for incremental builds (ccache is used when installed); a different toolchain
  or source location needs a new OUT.
- A build without `piano-release.config` keeps a diagnostic suffix such as `7.2.6-piano-diag` and
  cannot be packaged.

### 3.2 ABI bump (every packaged kernel)

The release is `<version>-piano<ABI>`. Take the next integer whenever the commit or the resulting
`.config` of a packaged kernel changes, including DTS-only and toolchain changes; never reuse or
decrease it. `CONFIG_MODVERSIONS` is off, so a module ABI break is not detected otherwise.

1. In the kernel repository, set `CONFIG_LOCALVERSION="-piano<N+1>"` in
   `arch/arm64/configs/piano-release.config` and commit.
2. Set `commit` in `kernel/source.json` to the new kernel HEAD.
3. Build (§3.1) and run the packager (§3.4). For an unrecorded release it stops and prints the
   entry to add:
   ```
   # expect "7.2.6-piano<N+1> is not recorded in .../kernel/abi.json; after review add and commit"
   #        '{"kernel_release": "7.2.6-piano<N+1>", "kernel_commit": "...", "config_sha256": "..."}'
   ```
4. Append that object with `"upload": 1` to `releases` in `kernel/abi.json`, commit
   `kernel/source.json` and `kernel/abi.json` together, and run the packager again.

"`<release>` already belongs to `<commit>` ... raise the ABI" means the release was allocated to
another build. Raise `upload` only to repackage the identical build with different package content.

### 3.3 Boot DTB

ABL applies the stock DTBO to the boot DTB; `prepare-boot-dtb.py` prepares the desktop DTB for it.
It needs your tablet's stock base DTB (from `vendor_boot_a`) and DTBO entry (from `dtbo_a`);
extracting these two from the backup images is a manual step.

```bash
python3 tools/prepare-boot-dtb.py \
  --mainline-dtb out/kernel-release/arch/arm64/boot/dts/qcom/sm8750-xiaomi-piano-desktop.dtb \
  --stock-base <stock base DTB> --stock-overlay <stock DTBO entry> \
  --ufdt <ufdt_apply_overlay> --output out/boot-dtb
# expect out/boot-dtb/piano-boot.dtb and out/boot-dtb/manifest.json
```

Prepare a new one whenever the desktop DTB changes. `build-kernel-deb.py --boot-dtb-build <older
build-result.json>` accepts a DTB prepared for an older build only when the kernel sources between
the two commits differ in `arch/arm64/boot/dts/`, `Documentation/devicetree/bindings/` and
`arch/arm64/configs/` alone; the package then carries that older DTB.

### 3.4 Kernel package

```bash
python3 tools/build-kernel-deb.py --kernel-source ../linux --kernel-out out/kernel-release \
  --boot-dtb out/boot-dtb/piano-boot.dtb --maintainer 'Name <email>' --output out/kernel-deb-<N>
# expect JSON: "package": "linux-image-7.2.6-piano<N>", "version": "7.2.6-<N>.1", "deb", "deb_sha256"
```

The package holds `/boot/vmlinuz-<release>` (uncompressed Image), `System.map`, `config`, all DTBs
and `piano-boot.dtb` in `/usr/lib/linux-image-<release>/`, and the matching modules; its maintainer
scripts run the distribution hooks (dracut, then `zz-piano-boot`). Identical inputs give a
byte-identical deb. Without `--boot-dtb` the package has no `piano-boot.dtb` and `piano-boot-update`
cannot build an image set.

### 3.5 Device and firmware packages

```bash
# Touch processing, ARM64 with dynamic glibc and a static C++ runtime (toolchain and checks:
# BUILD.md §5); the JSON headers get their own include directory so that host headers do not
# enter the cross build
mkdir -p out/json-include && cp -r /usr/include/nlohmann out/json-include/
make -C device/piano-thp OUT="$PWD/out/piano-thp-arm64" CXX=aarch64-linux-gnu-g++-13 \
  JSON_INCLUDE="$PWD/out/json-include" LDFLAGS='-static-libstdc++ -static-libgcc -Wl,-z,relro,-z,now' \
  all test RUNNER='qemu-aarch64-static -L /usr/aarch64-linux-gnu'
file out/piano-thp-arm64/piano-thp-live      # expect "ARM aarch64 ... dynamically linked, interpreter /lib/ld-linux-aarch64.so.1"
# Mesa 26.2.0 with the A830 patch (cross file and sysroot: build.zh-CN.md)
MESA_SOURCE=<Mesa checkout at mesa/source.json> CROSS_FILE=<arm64 cross file> \
  OUT_DIR="$PWD/out/mesa" bash tools/build-mesa.sh

python3 tools/build-device-debs.py --output out/device-debs --version 0.1+<YYYYMMDD>.<n> \
  --thp-live out/piano-thp-arm64/piano-thp-live --mesa-build out/mesa \
  --busybox tools/local/busybox-arm64/usr/bin/busybox --avbtool <avbtool.py>
# expect "Local packages created; no packages installed or device accessed" and the debs
# piano-device-support, piano-mesa, piano-ubuntu-desktop plus manifest.json

# Capture topology (AudioReach checkout at the commit in device/audio/source.json), then firmware
mkdir -p out/audio
m4 -I <audioreach-topology checkout> device/audio/Piano-Capture-Diag.m4 > out/audio/Piano-Capture-Diag.conf
alsatplg -c out/audio/Piano-Capture-Diag.conf -o out/audio/Piano-Capture-Diag-tplg.bin
python3 tools/build-firmware-deb.py --topology out/audio/Piano-Capture-Diag-tplg.bin \
  --card-topology out/audio/Xiaomi-Pad-8-Pro-tplg.bin \
  --maintainer 'Name <email>' --output out/firmware-deb
# expect JSON with "deb": "piano-local-firmware_0.6+os3.0.303.0_all.deb" and "deb_sha256"
```

- `build-firmware-deb.py` takes `firmware/` by default; add `--firmware-tree <tree>` for your own
  extraction (§2). It includes every locked file plus the capture topology.
- `--support-only` rebuilds only `piano-device-support`; the other options must still be present
  (any path).
- Raise `--version` on every device package rebuild so that dpkg installs it as an upgrade.

### 3.6 Boot image set

```bash
python3 tools/build-persistent-boot.py \
  --kernel <root>/boot/vmlinuz-<release> --kernel-release <release> \
  --dtb <root>/usr/lib/linux-image-<release>/piano-boot.dtb --initrd <root>/boot/initrd.img-<release> \
  --profile <boot-profile.json> --root-partuuid <linux_rootfs PARTUUID> --bt-address-file <bluetooth-address> \
  --mkbootimg tools/local/aosp-mkbootimg/mkbootimg.py --avbtool <avbtool.py> --key <boot-signing.pem> \
  --output out/ubuntu-set-<release>
# expect '{"output": ".../out/ubuntu-set-<release>", "images": ["boot", "init_boot", "vendor_boot"]}'
(cd out/ubuntu-set-<release> && sha256sum -c SHA256SUMS)    # expect three "OK" lines
```

- `--wlan-address-file <wlan-address>` (optional) writes this unit's Wi-Fi MAC address into the
  packaging DTB, as `--bt-address-file` does for Bluetooth.
- `--root-partlabel linux_rootfs` (instead of `--root-partuuid`) selects the root by GPT name and
  `--no-serial` leaves `androidboot.serialno` to ABL, which appends it on every boot; with neither
  address file this is the unit-independent set of a release ([RELEASE.md](RELEASE.md)).
- `<root>` is the target root filesystem: the initrd is the distribution dracut initrd that the
  kernel package generated there (§4.4). On an installed tablet `piano-boot-update` runs this tool
  itself (§5).
- Outputs: `boot.img`, `init_boot.img`, `vendor_boot.img` (100,663,296, 8,388,608 and 100,663,296
  bytes, AVB footers signed with your key), `SHA256SUMS`, `manifest.json` (input and output SHA-256,
  command line, release) and `boot-ram-check.img` (v2 image for `fastboot boot` with the same
  kernel, initrd, DTB and command line). The tool refuses a command line over 511 bytes: the v2
  header field keeps 511, and piano's ABL drops the rest.
- `--maintenance` builds only `boot-ram-maintenance.img` from a maintenance initramfs (§4.6).
- Release command line: `root=PARTUUID=<...> rootfstype=ext4 rw rootwait console=ttyMSM0,115200n8
  log_buf_len=2M panic=15 msm.separate_gpu_kms=1 piano.boot=persistent systemd.gpt_auto=0 bootconfig
  androidboot.serialno=<SERIAL>`. It has no debug entry.
- Debug options (refused without `--debug`):

| Option | Effect |
|---|---|
| `--debug` | adds `piano.debug=1`: unauthenticated root shell over USB NCM in the initrd and the root (§7.2) |
| `--debug-timeout 60..900` | adds `piano.debug_timeout=<s>`: forced reboot to Fastboot at that uptime |
| `--modprobe-blacklist a,b` | kmod `modprobe.blacklist=`: stops alias autoloading; explicit loads still work |
| `--module-blacklist a,b` | kernel `module_blacklist=` |
| `--debug-boot-trace` | replaces the serial console with a pstore trace console (`initcall_debug`, clock trace events) for boots that stop before userspace |

Module blacklists are for single diagnostic boots. A module subset that leaves consumers of a
power-domain, clock or interconnect provider unprobed keeps that provider's `sync_state` pending;
such subsets have caused resets without logs on the test unit. Return to the full module tree
afterwards. Never write a debug set to slot A for normal use.

### 3.7 Identities to record

For each build that reaches the tablet: the integration and kernel commits; `kernel_release`;
SHA-256 of `build-result.json` and of `.config` (the `kernel/abi.json` entry); the kernel package
`manifest.json` (`deb_sha256`, `build_result_sha256`, `abi_ledger_sha256`, `boot_dtb`);
`out/boot-dtb/manifest.json`; the device and firmware package manifests; the firmware locks; the Mesa
build manifest; the image set's `manifest.json` and `SHA256SUMS`; the SHA-256 of the initrd. dracut
initrds are not reproducible, so `vendor_boot.img` differs between builds with otherwise equal inputs.

## 4. Fresh install (manual steps)

The partitioning and root filesystem installer is not yet available. These steps reproduce the
installation of the test unit (512 GB UFS) with standard commands; §4.5 gives the 256 GB values. With a release directory, the
release root image and boot set replace §4.4 and the set of §3.6; the changes are listed in
[RELEASE.md](RELEASE.md#installing-the-release).

### 4.1 Prerequisites

```bash
adb -s <SERIAL> shell getprop ro.product.device              # expect "piano"
adb -s <SERIAL> shell getprop ro.build.version.incremental   # expect "OS3.0.303.0.WPYCNXM"
adb -s <SERIAL> shell getprop ro.boot.slot_suffix            # expect "_a"
adb -s <SERIAL> shell getprop ro.boot.flash.locked           # expect "0"
adb -s <SERIAL> shell su -c id                               # expect "uid=0(root) ..."
adb -s <SERIAL> reboot bootloader
fastboot -s <SERIAL> getvar unlocked                         # expect "unlocked: yes"
fastboot -s <SERIAL> getvar current-slot                     # expect "current-slot: a"
fastboot -s <SERIAL> reboot
```

Android root (Magisk or KernelSU-Next in a patched `init_boot_a`) is needed to read partitions.

### 4.2 Back up (manual steps)

Back up every partition except `userdata` and both GPT copies of each UFS LUN. `adb exec-out` is
binary-safe; `adb shell` output is not.

```bash
adb -s <SERIAL> shell su -c 'for d in /sys/block/sd?; do echo ${d##*/} $(cat $d/size) $(cat $d/queue/logical_block_size); done'
# expect six LUNs with logical block size 4096; size is counted in 512-byte units
adb -s <SERIAL> shell su -c 'ls -l /dev/block/by-name/'     # label -> /dev/block/sdXN
# each partition except userdata
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/by-name/<label> bs=1048576 2>/dev/null' > backup/<label>.img
adb -s <SERIAL> shell su -c 'sha256sum /dev/block/by-name/<label>'    # must equal the next line
sha256sum backup/<label>.img
# MBR, primary and backup GPT of each LUN; <blocks> = size / 8
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/sdX bs=4096 count=34 2>/dev/null' > backup/gpt/sdX-head.bin
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/sdX bs=4096 skip=<blocks-34> 2>/dev/null' > backup/gpt/sdX-tail.bin
```

- Record a manifest: label, current node, LUN, size and SHA-256 of each image, and each LUN's disk
  GUID. Only this manifest maps files to partitions.
- Filesystems that Android mounts read-write (for example `persist`, `metadata`) can change during
  the read: hash twice, or freeze the filesystem (`fsfreeze`) or remount it read-only for the read.
- Slot A's `boot`, `init_boot` (the root-patched image that is in the slot) and `vendor_boot` form
  the Android image set (§6.6). Keep the backup outside both repositories.

### 4.3 Device-specific inputs

| File | Source | Command |
|---|---|---|
| `bluetooth-address` | stock Android | `adb -s <SERIAL> shell settings get secure bluetooth_address > bluetooth-address` (one `XX:XX:XX:XX:XX:XX` line; `piano-boot-update` requires it) |
| `wlan-address` (optional) | `wlan/wlan_mac.bin` on the stock `persist` partition | manual steps: one unicast `XX:XX:XX:XX:XX:XX` line; `piano-boot-update` uses it when present |
| `thp-calibration.json` | `piano_nova_csot_thp_config.ini` or `piano_nova_boe_thp_config.ini` from the stock ODM partition, the file the unit's stock driver loads (`dmesg \| grep config_file_name` in stock Android; the test unit has the CSOT panel). The kernel detects the panel and loads its touch firmware; `piano-thp-live` picks the orientation from the kernel's `panel_vendor` and logs a `THP calibration error` line if the calibration belongs to the other panel | `python3 tools/extract-thp-calibration.py --input <ini> --output thp-calibration.json` |
| `boot-signing.pem` | new owner key | `openssl genrsa -out boot-signing.pem 4096` |
| `boot-profile.json` | stock `boot_a`, `init_boot_a`, `vendor_boot_a` | manual steps, below |
| `boot-partitions.json` | `sfdisk --json` of the boot LUN (LUN 4: the one whose GPT holds `boot_a` and `boot_b`) | `python3 tools/extract-boot-partitions.py --input <sfdisk json> --profile boot-profile.json --output boot-partitions.json` (§4.8; INSTALL step 8a reads the table in the maintenance image) |
| stock base DTB, DTBO entry | `vendor_boot_a`, `dtbo_a` | manual steps (§3.3) |

The boot profile is read by `build-persistent-boot.py`; its generator is not yet available:

```
{
  "serial": "<SERIAL>",
  "boot":        {"header_version": 4, "capacity": 100663296, "rollback_index": <int>, "avb_properties": {<props>}},
  "init_boot":   {"header_version": 4, "capacity": 8388608,   "rollback_index": <int>, "avb_properties": {<props>}},
  "vendor_boot": {"header_version": 4, "capacity": 100663296, "rollback_index": <int>, "avb_properties": {<props>},
                  "page_size": <int>, "kernel_addr": <int>, "ramdisk_addr": <int>, "tags_addr": <int>, "dtb_addr": <int>}
}
```

Fill in the values of your stock images:

```bash
python3 tools/local/aosp-mkbootimg/unpack_bootimg.py --boot_img backup/vendor_boot_a.img --out /tmp/vb
# header version, page size, kernel, ramdisk and tags load addresses, dtb address
python3 <avbtool.py> info_image --image backup/boot_a.img
# "Rollback Index:" and every "Prop: com.android.build.boot.*" line; repeat for init_boot_a and vendor_boot_a
fastboot -s <SERIAL> getvar partition-size:boot_a           # expect "partition-size:boot_a: 0x6000000"
```

`capacity` is the partition size in bytes. Copy every `Prop:` of each image into its
`avb_properties`; `boot` needs `com.android.build.boot.os_version` and
`com.android.build.boot.security_patch`. Without the stock header fields, rollback indices and AVB
properties, ABL marks the slot unbootable.

### 4.4 Root and data filesystems (host)

Build the pristine Desktop TAR as in [build.zh-CN.md](build.zh-CN.md) (`prepare-ubuntu-desktop.sh`,
`sqfs2tar`, `merge-desktop-tars.py`), then:

```bash
ROOT_UUID=$(uuidgen); DATA_UUID=$(uuidgen)      # also the partition GUIDs in §4.5
python3 tools/build-ubuntu-ext4.py --tar out/ubuntu-desktop-pristine/ubuntu-desktop.tar --tar-sha256 <sha256> \
  --mke2fs <mke2fs 1.47.4 with libarchive> --mke2fs-config <mke2fs.conf> --e2fsck <e2fsck> \
  --uuid "$ROOT_UUID" --size-mib 12288 --output out/ubuntu-ext4
mkdir -p out/install && cp out/ubuntu-ext4/ubuntu-root.ext4 out/install/root.ext4
e2fsck -f out/install/root.ext4 && resize2fs out/install/root.ext4 60G
# expect "The filesystem on out/install/root.ext4 is now 15728640 (4k) blocks long."
```

Install the packages in an ARM64 chroot (manual steps; an x86_64 host needs `qemu-user-static`
with binfmt):

```bash
R=/mnt/piano-root
sudo mkdir -p $R && sudo mount -o loop out/install/root.ext4 $R
sudo mount -t proc proc $R/proc && sudo mount -t sysfs -o ro sysfs $R/sys
sudo mount --bind /dev $R/dev && sudo mount --bind /dev/pts $R/dev/pts
printf '#!/bin/sh\nexit 101\n' | sudo tee $R/usr/sbin/policy-rc.d >/dev/null && sudo chmod 755 $R/usr/sbin/policy-rc.d

# device configuration, root:root 0600; layout.json must exist before the kernel package is
# installed (it enables the dracut module 99piano and the zz-piano-boot hooks)
sudo install -d -m 755 $R/etc/piano
sudo install -m 600 bluetooth-address boot-profile.json boot-signing.pem thp-calibration.json $R/etc/piano/
sudo install -m 600 wlan-address $R/etc/piano/            # only if you have it
printf '{"root_partuuid": "%s"}\n' "$ROOT_UUID" | sudo tee $R/etc/piano/layout.json >/dev/null
sudo chmod 600 $R/etc/piano/layout.json
printf 'UUID=%s / ext4 defaults,noatime 0 1\nUUID=%s /home ext4 defaults,noatime 0 2\n' \
  "$ROOT_UUID" "$DATA_UUID" | sudo tee -a $R/etc/fstab

# Android set from the backup (init_boot = the root-patched image in slot A)
A=$R/var/lib/piano/switch/android
sudo install -d $A
sudo cp backup/boot_a.img $A/boot.img && sudo cp backup/init_boot_a.img $A/init_boot.img
sudo cp backup/vendor_boot_a.img $A/vendor_boot.img
(cd $A && sudo sh -c 'sha256sum boot.img init_boot.img vendor_boot.img > SHA256SUMS')

sudo chroot $R dpkg-query -W dracut python3 openssl device-tree-compiler gdm3 linux-base
# every name must be listed; fetch missing packages with their dependencies, plus openssh-server
# for developer mode (§7.1), fake-hwclock (a dependency of the current desktop package) and,
# only for a kernel with CONFIG_ZRAM, systemd-zram-generator, from the
# signed snapshot:
python3 tools/prepare-ubuntu-apt.py --rootfs out/ubuntu-desktop-pristine/ubuntu-desktop.squashfs \
  --snapshot <YYYYMMDDTHHMMSSZ> --output out/apt-extra \
  --download device-tree-compiler openssh-server fake-hwclock systemd-zram-generator
sudo install -d $R/var/cache/piano-install
sudo cp out/apt-extra/cache/archives/*.deb out/kernel-deb-<N>/linux-image-*.deb out/device-debs/*.deb \
  out/firmware-deb/*.deb $R/var/cache/piano-install/
sudo chroot $R env SYSTEMD_OFFLINE=1 DEBIAN_FRONTEND=noninteractive dpkg --install /var/cache/piano-install/*.deb
# expect dracut to create /boot/initrd.img-<release>, then "Piano boot image set prepared for <release>"
# and "Slot A not inspected: not a running persistent piano boot"
sudo chroot $R dpkg --audit                    # expect no output
```

Without `systemd-zram-generator` on a kernel with zram there is no swap; with it on a kernel without
`CONFIG_ZRAM` the boot waits 90 s for `/dev/zram0` (QEMU). Then, as
[tools/rootfs-image-guest.sh](../tools/rootfs-image-guest.sh) does for the release root, name the
tablet, purge the base's generic kernels and kdump, whose packages would be updated and get initrds
without ever booting (the names of the 26.04 Desktop base; the guest selects them by pattern), and
drop the base's package indexes, which belong to the build machine's mirror:

```bash
printf 'xiaomipad-8pro\n' | sudo tee $R/etc/hostname >/dev/null
printf 'PRETTY_HOSTNAME="Xiaomi Pad 8 Pro"\n' | sudo tee $R/etc/machine-info >/dev/null
printf 'update_initramfs=no\n' | sudo tee $R/etc/initramfs-tools/update-initramfs.conf >/dev/null
sudo chroot $R apt-mark manual linux-firmware
sudo chroot $R env SYSTEMD_OFFLINE=1 DEBIAN_FRONTEND=noninteractive apt-get --yes --purge --autoremove remove \
  kdump-tools linux-generic-hwe-24.04 linux-generic-hwe-26.04 linux-headers-7.0.0-14-generic \
  linux-headers-generic-hwe-26.04 linux-image-7.0.0-14-generic linux-image-generic-hwe-26.04 \
  linux-main-modules-zfs-7.0.0-14-generic linux-modules-7.0.0-14-generic linux-tools-7.0.0-14-generic
sudo rm $R/etc/initramfs-tools/update-initramfs.conf
sudo chroot $R dpkg --audit                    # expect no output
ls $R/usr/lib/modules                          # expect only <release>
sudo find $R/var/lib/apt/lists -mindepth 1 -maxdepth 1 -type f ! -name lock -delete
```

On the release root the purge removed 47 packages (with `--autoremove`, including `libc6-dev` and
`flash-kernel`; users install `build-essential` to compile).

`openssh-server` enables Ubuntu's `ssh.socket`, which listens on every address. For developer mode
from the first boot (§7.1; turn it off after the installation):

```bash
sudo systemctl --root=$R disable ssh.socket ssh.service
sudo systemctl --root=$R enable piano-devmode.service
```

Copy out what later steps need and seal the image:

```bash
sudo cp -r $R/var/lib/piano/switch/ubuntu out/install/ubuntu-set        # written to slot A in §4.9
sudo cp $R/boot/vmlinuz-<release> $R/boot/initrd.img-<release> \
  $R/usr/lib/linux-image-<release>/piano-boot.dtb out/install/
sudo rm -r $R/usr/sbin/policy-rc.d $R/var/cache/piano-install
sudo fstrim -v $R      # freed blocks keep their content; the loop device must pass discards to the file
sudo umount $R/dev/pts $R/dev $R/sys $R/proc $R
e2fsck -f out/install/root.ext4 && python3 tools/sparse_image.py encode-ext4 out/install/root.ext4 out/install/root.sparse.img

truncate -s 216115687424 out/install/data.ext4     # linux_data size on the 512 GB unit (§4.5); 65418539008 on the 256 GB unit
mkfs.ext4 -U "$DATA_UUID" out/install/data.ext4 && img2simg -s out/install/data.ext4 out/install/data.sparse.img
```

Plain `img2simg` stores zero blocks as FILL chunks, which Fastboot writes in full: about 55 GB for
the root, committed by the bootloader at about 30 MB/s after it has answered `OKAY` (1839 s on the
test unit). `encode-ext4` makes only the free blocks DONT_CARE and keeps zero used blocks
(inode tables, journal) as FILL: about 9.4 GiB written for the release root. `-s` leaves the unused
blocks of the data image out. Check the totals with `python3 tools/sparse_image.py summary` before
flashing; keep the root image as built (its bytes are the release's). `encode-ext4` stores only zero
free blocks as DONT_CARE, so without the `fstrim` the purged kernels stay RAW (a sample
build of the release root: 10.14 GB written without it, 8.73 GB with it).

### 4.5 New LUN 0 GPT (host, manual steps)

Entry 34 `userdata` keeps its start, type GUID, unique GUID and attributes and shrinks to 200 GiB
(110 GiB on 256 GB units); entries 35 `linux_rootfs` (60 GiB) and 36 `linux_data` (the remainder) are
new. All other entries and LUNs stay unchanged. LUN 0 has 124,552,192 blocks of 4096 bytes on the
512 GB unit and 64,167,936 on 256 GB units (`size / 8` from §4.2; one GiB is 262,144 blocks). A
256 GB unit measured `userdata` 28,835,840, `linux_rootfs` 15,728,640 and `linux_data` 15,971,323
blocks, the last ending at the last usable LBA 64167930.

```bash
BLOCKS=<LUN 0 size / 8>                         # from §4.2
truncate -s $((BLOCKS * 4096)) lun0.img
dd if=backup/gpt/<lun0>-head.bin of=lun0.img bs=4096 conv=notrunc
dd if=backup/gpt/<lun0>-tail.bin of=lun0.img bs=4096 seek=$((BLOCKS - 34)) conv=notrunc
LOOP=$(sudo losetup --find --show --sector-size 4096 lun0.img)
sudo sgdisk --print "$LOOP"       # expect "Sector size (logical): 4096 bytes" and the device's table
sudo sgdisk --info=34 "$LOOP"     # expect "Partition name: 'userdata'"; note first sector, type, unique GUID, attributes
sudo sfdisk --json "$LOOP" > gpt-before.json

S=<first sector of userdata>      # 3632128 on the test unit
U=<unique GUID of userdata>; T=<type GUID of userdata>
A=52428800                        # userdata blocks: 200 GiB x 262144 on 512 GB units; 28835840 (110 GiB) on 256 GB units
R=15728640                        # linux_rootfs blocks: 60 GiB x 262144
L=0FC63DAF-8483-4772-8E79-3D69D8477DE4      # Linux filesystem
sudo sgdisk --set-alignment=256 --delete=34 \
  --new=34:$S:$((S + A - 1)) --typecode=34:$T --partition-guid=34:$U --change-name=34:userdata \
  --new=35:$((S + A)):$((S + A + R - 1)) --typecode=35:$L --partition-guid=35:$ROOT_UUID --change-name=35:linux_rootfs \
  --new=36:$((S + A + R)):0 --typecode=36:$L --partition-guid=36:$DATA_UUID --change-name=36:linux_data \
  "$LOOP"
sudo sgdisk --verify "$LOOP"      # expect "No problems found."
sudo sfdisk --json "$LOOP" > gpt-after.json
```

Compare `gpt-before.json` and `gpt-after.json`: only entries 34–36 may differ, and they must start
at `S`, `S + A` and `S + A + R`; `linux_data` is `LAST - S - A - R + 1` blocks. The disk GUID, first
and last usable LBA and table length must not change. Copy non-zero userdata attributes with
`--attributes=34:=:<hex>`. Then extract the two GPT regions (first usable LBA is 6 and last usable
LBA 124552186 on the test unit, 64167930 on 256 GB units):

```bash
FIRST=<first usable LBA>; LAST=<last usable LBA>        # from sgdisk --print
sudo dd if="$LOOP" of=new-primary.bin bs=4096 count=$FIRST
sudo dd if="$LOOP" of=new-backup.bin bs=4096 skip=$((LAST + 1))
dd if=backup/gpt/<lun0>-head.bin bs=4096 count=$FIRST 2>/dev/null | sha256sum    # current primary region
sha256sum new-primary.bin new-backup.bin
sudo losetup -d "$LOOP"
```

### 4.6 Write the GPT (tablet, manual steps)

Run it in the maintenance RAM image: the release's `boot/boot-ram-maintenance.img`, or one built
for another kernel package. It stays in the initramfs (`piano.diag=maintenance` of
[initramfs/init](../initramfs/init)), mounts no UFS partition, serves a root shell on
`192.168.88.2:2323` over the NCM link of `initramfs/network.json`, and reboots into Fastboot after
`--maintenance-idle` seconds (default 600) with no open session and no
`piano-maint keepalive` ([initramfs/piano-maint](../initramfs/piano-maint)).

```bash
# Ubuntu tools from the pinned desktop SquashFS, the reboot-mode modules of the kernel package,
# restart2 against the kernel's nolibc
python3 tools/prepare-maintenance-payload.py build \
  --squashfs out/ubuntu-desktop-pristine/ubuntu-desktop.squashfs --squashfs-sha256 <sha256> \
  --kernel-deb out/kernel-deb-<N>/linux-image-<release>_*.deb --nolibc <kernel tree>/tools/include/nolibc \
  --output out/maintenance-payload
BUSYBOX=<pinned busybox> GEN_INIT_CPIO=<kernel OUT>/usr/gen_init_cpio OUT_DIR=out/maintenance-initramfs \
  MAINTENANCE_DIR=out/maintenance-payload bash tools/build-initramfs.sh
dpkg-deb --fsys-tarfile out/kernel-deb-<N>/linux-image-<release>_*.deb | tar -x -C out/maintenance-kernel \
  ./boot/vmlinuz-<release> ./usr/lib/linux-image-<release>/piano-boot.dtb
python3 tools/build-persistent-boot.py --maintenance --no-serial \
  --kernel out/maintenance-kernel/boot/vmlinuz-<release> --kernel-release <release> \
  --dtb out/maintenance-kernel/usr/lib/linux-image-<release>/piano-boot.dtb \
  --initrd out/maintenance-initramfs/initramfs.cpio.gz --profile boot-profile.json \
  --mkbootimg tools/local/aosp-mkbootimg/mkbootimg.py --output out/maintenance-ram
MAINTENANCE_INITRAMFS=out/maintenance-initramfs/initramfs.cpio.gz python3 tools/test-maintenance-initramfs.py
```

`build-release.py --maintenance out/maintenance-ram` copies the image into the release after
checking that it has the set's kernel and address-free DTB and no serial. Writing the GPT from
running Android, with `userdata` mounted, is not verified.

For a script driving the session: do not send `stty -echo`. BusyBox ash then reads the typed-ahead
lines through stdio, and an applet that reads standard input at the end of a pipeline (`cut`,
`head`) receives the following lines instead of its pipe (seen on the test unit).

*On the tablet* (`fastboot boot` the image, `telnet 192.168.88.2 2323`; the login prints the LUN
table and LUN 0's `sgdisk -p`; serve the two files with `python3 -m http.server --bind 192.168.88.10 8000`):

```sh
FIRST=<first usable LBA>; LAST=<last usable LBA>; BLOCKS=<LUN 0 blocks>    # the values of §4.5
disk=/dev/sdX                                           # "LUN 0: /dev/sdX" of the login table or piano-maint map
wget -O /run/new-primary.bin http://192.168.88.10:8000/new-primary.bin
wget -O /run/new-backup.bin http://192.168.88.10:8000/new-backup.bin
sha256sum /run/new-primary.bin /run/new-backup.bin      # expect the hashes of §4.5
dd if=$disk bs=4096 count=$FIRST | sha256sum            # expect the current primary region hash from §4.5
dd if=/run/new-backup.bin of=$disk bs=4096 seek=$((LAST + 1)) conv=fsync
dd if=$disk bs=4096 skip=$((LAST + 1)) count=$((BLOCKS - LAST - 1)) | sha256sum   # expect new-backup.bin
dd if=/run/new-primary.bin of=$disk bs=4096 conv=fsync
dd if=$disk bs=4096 count=$FIRST | sha256sum            # expect new-primary.bin
sync
sgdisk --verify $disk                                   # expect "No problems found."
piano-maint fastboot
```

Write the backup copy first, then the primary, and compare each readback before continuing.

### 4.7 Erase and write the filesystems (Fastboot)

```bash
fastboot -s <SERIAL> getvar partition-size:userdata       # expect "0x3200000000"; 256 GB units: 102 GiB (8 GiB below the 110 GiB GPT entry, as on stock)
fastboot -s <SERIAL> getvar partition-size:linux_rootfs   # expect "0xf00000000"
fastboot -s <SERIAL> getvar partition-size:linux_data     # linux_data blocks x 4096: "0x32517fb000" on the 512 GB unit
fastboot -s <SERIAL> erase userdata
fastboot -s <SERIAL> erase metadata
fastboot -s <SERIAL> flash linux_rootfs out/install/root.sparse.img
fastboot -s <SERIAL> flash linux_data out/install/data.sparse.img
# expect "OKAY" on every Sending and Writing line, then "Finished."
# OKAY can come while the bootloader still writes FILL blocks; the next command waits for it.
# Never unplug, reset or power off the tablet while a command waits.
```

Android re-creates `userdata` and `metadata` on its next boot; all Android data, including the
root manager's configuration, is gone. Do not repeat these writes once they have succeeded.

### 4.8 Check boot and partition map

```bash
python3 tools/build-persistent-boot.py --kernel out/install/vmlinuz-<release> --kernel-release <release> \
  --dtb out/install/piano-boot.dtb --initrd out/install/initrd.img-<release> \
  --profile boot-profile.json --root-partuuid "$ROOT_UUID" --bt-address-file bluetooth-address \
  --mkbootimg tools/local/aosp-mkbootimg/mkbootimg.py --avbtool <avbtool.py> --key boot-signing.pem \
  --debug --debug-timeout 900 --output out/install/debug-set
fastboot -s <SERIAL> boot out/install/debug-set/boot-ram-check.img     # expect "Booting OKAY"
```

Connect to the debug console (§7.2). *On the tablet*, in the root phase:

```sh
findmnt -no SOURCE,UUID /        # expect the linux_rootfs partition and ROOT_UUID
findmnt -no UUID /home           # expect DATA_UUID
uname -r                         # expect <release>
d=$(lsblk -no PKNAME "$(readlink -f /dev/disk/by-partlabel/boot_a)"); readlink -f /sys/block/$d/device
# expect a path ending in ":4" (LUN 4)
sfdisk --json /dev/$d            # save the output on the host as boot-lun.json
```

On the host, make the map that `piano-boot-switch` requires and install it on the tablet as
`/etc/piano/boot-partitions.json` (root, mode 0600):

```bash
python3 tools/extract-boot-partitions.py --input boot-lun.json --profile boot-profile.json --output boot-partitions.json
# expect init_boot_a 8388608 B, vendor_boot_a and boot_a 100663296 B each
```

The tool refuses a disk without `boot_a` and `boot_b` and sizes other than the profile's
capacities; the map records LUN 4, which `piano-boot-switch` compares with the disk. It replaces
the hand-written map of earlier versions and has not yet been run against the tablet.

```sh
piano-boot-switch status         # expect "occupant": "android" and "backup_gpt": "valid"
piano-boot-switch verify         # expect exit status 0, "android": "verified", "ubuntu": "verified"
```

### 4.9 Write slot A and first boot

Either in the same debug console, which keeps the slot attributes:

```sh
piano-boot-switch to-ubuntu --dry-run   # expect "result": "would-write" and "rollback": "android"
piano-boot-switch to-ubuntu             # expect "result": "written"
systemctl reboot                        # cold boot from slot A
```

or from Fastboot on the host, which resets the retry counter (§6.4):

```bash
python3 tools/piano-switch.py --serial <SERIAL> --images out/install/ubuntu-set --rollback <android-set>
# expect "Active A boot triplet written; remain in Fastboot for a RAM readback before reboot."
fastboot -s <SERIAL> reboot
```

- First boot: GNOME Initial Setup creates the first account. There is no network; skip that page.
  Enter passwords with the magnetic keyboard: the on-screen keyboard fails on the lock screen.
- The clock is wrong after every boot (the PMIC RTC cannot be written): `sudo date -s 'YYYY-MM-DD HH:MM'`.
- `piano-slot-success.service` marks slot A after the boot completes:
  ```bash
  sudo piano-slot-success --check                 # on the tablet; expect "state": "already-successful"
  fastboot -s <SERIAL> getvar slot-successful:a   # at the next Fastboot visit; expect "yes"
  ```
  If the slot holds a set that is not in the store, the marker refuses (`slot-set-unregistered`);
  run `sudo piano-boot-switch import-ubuntu` once in that boot.

## 5. Updating the kernel on the device

### 5.1 Prepare

- Build the new package with the next ABI (§3.2–§3.4).
- Keep a host copy of the current Ubuntu set (§6.6) and of the Android set.
- For a reboot with nobody at the tablet, developer mode must be enabled (§7.1).

```bash
scp out/kernel-deb-<N>/linux-image-7.2.6-piano<N>_7.2.6-<N>.1_arm64.deb <USER>@192.168.88.2:
```

### 5.2 Install (on the tablet)

Run dpkg as a transient unit so that a dropped SSH session does not stop it. It takes about 10 to 13
minutes (dracut about 8, the kdump-tools initrd about 4).

```bash
sudo systemd-run --unit=piano-kernel-update --setenv=DEBIAN_FRONTEND=noninteractive \
  dpkg -i /home/<USER>/linux-image-7.2.6-piano<N>_7.2.6-<N>.1_arm64.deb
journalctl -f -u piano-kernel-update
# expect "Piano boot image set prepared for 7.2.6-piano<N>", then either
#   "Slot A now holds store/ubuntu"                                (slot held Ubuntu: rewritten in place)
#   "Slot A holds Android; the new Ubuntu set stays in the store"  (slot held Android: "result": "skipped")
# and finally "piano-kernel-update.service: Deactivated successfully."
dpkg -l linux-image-7.2.6-piano<N> | tail -n 1    # expect "ii"
sudo dpkg --audit                                 # expect no output
sudo python3 -c 'import json; print(json.load(open("/var/lib/piano/switch/ubuntu/manifest.json"))["kernel_release"])'
# expect "7.2.6-piano<N>"
sudo piano-boot-switch verify                     # expect exit status 0
```

The hook rebuilds `/var/lib/piano/switch/ubuntu/`, moves the previous set to `ubuntu.prev/` and,
when slot A holds an Ubuntu set, rewrites slot A in place, so the next boot uses the new kernel.
Without `boot-profile.json` and `boot-signing.pem`, and on a persistent boot also without
`boot-partitions.json`, it changes neither the store
nor slot A, exits 0 and prints which file to install before running `piano-boot-update <release>`.
When slot A holds Android, write the new set later with `sudo piano-boot-switch to-ubuntu` from a
running Ubuntu (§5.4), from Android with the KernelSU module (§6.2), or from the host (§6.3).

### 5.3 Reboot and verify

```bash
sudo systemctl reboot
uname -r                              # expect "7.2.6-piano<N>"
ls -d /lib/modules/$(uname -r)        # expect the directory
sudo piano-boot-switch status         # expect "occupant": "ubuntu"
sudo piano-slot-success --check       # expect "state": "already-successful"
```

In-OS writes keep the slot attributes; only host Fastboot writes reset them (§6.4). Check the
release cold boot (§8.4).

### 5.4 Validate before writing the slot

Recommended for kernels with boot-relevant changes; this sequence is device-verified.

1. Install the package while slot A holds Android, so that the hook reports `skipped` (for example
   in a debug RAM boot of the current kernel).
2. Copy `/boot/vmlinuz-<new>`, `/boot/initrd.img-<new>` (the initrd generated on the tablet) and
   `/usr/lib/linux-image-<new>/piano-boot.dtb` to the host and build a debug set from them (§3.6 with
   `--debug --debug-timeout 900`).
3. `fastboot boot` its `boot-ram-check.img` and accept it with the debug profile (§8.3).
4. In that session run `piano-boot-switch to-ubuntu`, then `systemctl reboot` for the cold boot, or
   `piano-reboot-bootloader` first to read the slot attributes in Fastboot.

### 5.5 Remove the superseded kernel

Only after the new kernel has booted from slot A. Removal deletes its modules and drops
`ubuntu.prev/` when that set boots the removed kernel.

```bash
sudo apt-get remove linux-image-<old release>   # expect "Dropped the rollback set of removed kernel <old release>"
sudo apt-get purge linux-image-<old release>    # removes the leftover depmod index files
```

- `ubuntu.prev/` is kept while its kernel is installed; booting it is not yet verified on the device.
- Only unique `-piano<N>` releases own the store, and only the newest installed one is written. If a
  distribution kernel with a higher version than the piano release is installed,
  `update-initramfs -u` from package triggers rebuilds only that kernel's initrd; check with
  `linux-version list`.
- If the new kernel does not boot: Power + Volume Down, then write the Android set or your host copy
  of the previous Ubuntu set with `piano-switch.py` (§6.3).

## 6. Switching systems and recovery

### 6.1 From Ubuntu: `piano-boot-switch`

| Command | Writes | Expected result |
|---|---|---|
| `sudo piano-boot-switch status` | nothing | JSON: `occupant` (`android`, `ubuntu`, `ubuntu.prev` or `unknown`), `boot_a` flags (`retry`, `successful`, `unbootable`), `backup_gpt`, per-set `equals_slot`, `stray` entries |
| `sudo piano-boot-switch verify` | nothing | exit status 0, every set `verified` |
| `sudo piano-boot-switch to-android --dry-run` | nothing | `"result": "would-write"` |
| `sudo piano-boot-switch to-android --reboot` | slot A | `"result": "written"`, then reboot into Android |
| `sudo piano-boot-switch to-ubuntu [--dry-run] [--reboot]` | slot A | `written`, or `unchanged` when slot A already holds it |
| `sudo piano-boot-switch import-android [--from DIR]` | store | `"result": "imported"`; replaces `android/` |
| `sudo piano-boot-switch import-ubuntu [--from DIR]` | store | `imported`; the old `ubuntu/` becomes `ubuntu.prev/` |

- It runs only on piano with the installed profile serial and partition map, slot A active and B
  inactive. It writes only when slot A holds a known store set, which serves as the rollback; each
  of `init_boot_a`, `vendor_boot_a`, `boot_a` is written and read back in full.
- `"result": "refused"` (exit status 1) means nothing was written; `code` and `message` say why
  (for `occupant-unknown`, register the slot content with `import-android` or `import-ubuntu`).
  `rolled-back`: the previous set is back in the slot. `rollback-failed` or `final-mismatch`: do not
  reboot into slot A; recover from Fastboot (§6.3).
- Logs: `journalctl -t piano-boot-switch`, `/var/lib/piano/switch/switch.log` and `state.json`
  (device time, not real time).
- Refresh `android/` whenever the Android root patch in slot A changes (for example Magisk to
  KernelSU): run `sudo piano-boot-switch import-android` in a debug RAM boot started while Android is
  in the slot. Otherwise `to-android` writes the old `init_boot` back.
- Verified on the test unit: `status`, `verify`, both imports, `to-ubuntu`, `to-android --reboot`.
  Not yet verified on the device: the app-grid entry **Switch to Android** (`pkexec
  /usr/sbin/piano-boot-switch to-android --desktop --force`) and the rollback after a failed write.
  The current desktop entry explicitly ignores reboot inhibitors;
  save work first. The CLI respects them unless `--force` is given with `--reboot`.
  See [reboot failures and the next boot's OS](FLASHING.md#from-ubuntu-piano-boot-switch).

### 6.2 From Android: KernelSU module (not yet verified on the device)

Needs KernelSU-Next with shell root, an `android/` set equal to slot A, and a clean Ubuntu shutdown
(the module refuses an ext4 journal that needs recovery). Details:
[device/android/piano-switch-ksu](../device/android/piano-switch-ksu/README.md).

```bash
sh device/android/piano-switch-ksu/build.sh "$PWD/out/ksu"    # expect out/ksu/piano-switch-ksu-v0.1.0.zip and .sha256
adb -s <SERIAL> push out/ksu/piano-switch-ksu-v0.1.0.zip /data/local/tmp/
adb -s <SERIAL> shell su -c '/data/adb/ksud module install /data/local/tmp/piano-switch-ksu-v0.1.0.zip'
adb -s <SERIAL> reboot
adb -s <SERIAL> shell su -c 'sh /data/adb/modules/piano-switch/piano-switch.sh --check'
# expect "CHECK OK: slot A holds the android set and the ubuntu set is ready; nothing written"
adb -s <SERIAL> shell su -c 'ksud module action piano-switch'   # writes slot A and reboots
```

Exit status 0: switched or nothing to do; 1: refused before any write; 2: write failed, Android set
restored; 3: write and restore failed, do not reboot and restore the Android set with Fastboot.

### 6.3 From the host: `piano-switch.py`

```bash
# Ubuntu -> Android: power off Ubuntu, then hold Power + Volume Down
python3 tools/piano-switch.py --serial <SERIAL> --images <android-set> --rollback <ubuntu-set>
fastboot -s <SERIAL> reboot
# Android -> Ubuntu
adb -s <SERIAL> reboot bootloader
python3 tools/piano-switch.py --serial <SERIAL> --images <ubuntu-set> --rollback <android-set>
fastboot -s <SERIAL> reboot
# expect "Active A boot triplet written; remain in Fastboot for a RAM readback before reboot."
```

- Before the first write it checks `product: piano`, `unlocked: yes`, `current-slot: a`,
  `is-userspace: no`, the three partition sizes and both `SHA256SUMS`. It writes `init_boot_a`,
  `vendor_boot_a`, `boot_a`; if a write fails it prints "Write failed; restoring the checked rollback
  triplet before returning the error." and writes the rollback set.
- Each set directory holds `boot.img`, `init_boot.img`, `vendor_boot.img` (full partition size) and
  `SHA256SUMS`. The three writes are not atomic: a power loss during a switch needs host recovery.
- Verified on the test unit in both directions.

### 6.4 Slot attributes

```bash
fastboot -s <SERIAL> getvar current-slot          # expect "current-slot: a"
fastboot -s <SERIAL> getvar slot-successful:a     # "yes" once marked
fastboot -s <SERIAL> getvar slot-unbootable:a     # expect "no"
fastboot -s <SERIAL> getvar slot-retry-count:a    # 7 after a host write, one less after each unmarked boot
```

| Event | Effect on slot A |
|---|---|
| Host `fastboot flash boot_a` (also through `piano-switch.py`) | successful cleared, retry count set to 7 |
| `fastboot flash init_boot_a` alone | unchanged |
| Boot of an unsuccessful slot | ABL decrements the retry count; at 0 the slot becomes unbootable |
| Persistent Ubuntu boot | `piano-slot-success.service` sets bit 54 (successful) after `boot-complete.target` |
| Android boot | the Android boot HAL marks the slot |
| In-OS switching (`piano-boot-switch`, KernelSU module) | unchanged |

`piano-slot-success --check` and `--dry-run` never write. `--mark` refuses debug RAM boots, a
retry count of 0, a damaged backup GPT and slot content that is neither `ubuntu/` nor `ubuntu.prev/`.

Stuck in Fastboot with `slot-unbootable:a: yes`: `fastboot -s <SERIAL> --set-active=a` resets slot
A's counter (on the test unit it also raised slot B's priority; slot B stays unused), then switch
(§6.3) or `fastboot -s <SERIAL> reboot`.

### 6.5 When the tablet is unreachable

1. Developer mode enabled: about 22 s after power-on,
   `python3 tools/piano-devmode-ssh.py --check` should print
   `enx020000880001 piano-<SERIAL> 192.168.88.10 -> 192.168.88.2 ready`. Then
   `sudo piano-reboot-bootloader` (to Fastboot) or `sudo piano-boot-switch to-android --reboot`.
2. A debug RAM boot with `--debug-timeout` returns to Fastboot by itself at the timeout; wait for
   `fastboot devices` to list `<SERIAL>`.
3. Otherwise a person at the tablet holds Power + Volume Down until Fastboot appears; then §6.3 to
   Android. A boot that hangs before userspace has no timer.

Do not reset the USB device from the host and do not repeat commands against a device that does not
answer; report the state and wait for the key combination.

### 6.6 Host copies of the image sets

- Android set: the slot A backup images renamed to `boot.img`, `init_boot.img`, `vendor_boot.img`,
  plus `sha256sum boot.img init_boot.img vendor_boot.img > SHA256SUMS`. The `init_boot` must be the
  root-patched image currently in use.
- Ubuntu set: after every kernel update, copy the store's `ubuntu/` to the host:
  ```bash
  # on the tablet
  sudo cp -r /var/lib/piano/switch/ubuntu ~/ubuntu-set && sudo chown -R <USER>: ~/ubuntu-set
  # on the host
  scp -r <USER>@192.168.88.2:ubuntu-set out/ubuntu-set-current
  (cd out/ubuntu-set-current && sha256sum -c SHA256SUMS)       # expect three "OK" lines
  ```

## 7. Developer mode and debug boots

### 7.1 Developer mode (release boots)

Off by default. It adds the USB NCM link (tablet `192.168.88.2`, host `192.168.88.10` by DHCP) and
SSH on it to the normal boot. Details: [device/devmode](../device/devmode/README.md).

*On the tablet*:

```bash
sudo systemctl disable --now ssh.socket ssh.service   # keep SSH on the USB link only
sudo piano-devmode on
piano-devmode status
# expect "developer mode: enabled, active", "USB gadget: bound to a600000.usb",
#        "SSH socket: active (192.168.88.2 port 22)"
sudo piano-devmode off
```

Host:

```bash
python3 tools/piano-devmode-ssh.py --check              # expect "... 192.168.88.10 -> 192.168.88.2 ready"
python3 tools/piano-devmode-ssh.py <USER>               # login shell
python3 tools/piano-devmode-ssh.py -t <USER> sudo piano-reboot-bootloader
```

- Needs `openssh-server`; `piano-devmode on` refuses without `/usr/sbin/sshd`. Root cannot log in;
  every local account with a password can. Harden with `~/.ssh/authorized_keys` and
  `PasswordAuthentication no` in `/etc/ssh/sshd_config.d/`.
- If the host did not take the DHCP lease, the tool prints the `ip` commands, for example
  `sudo ip address replace 192.168.88.10/24 dev enx020000880001`.
- Developer mode does not start in debug boots (`piano.debug=1`), which own the same link.
- `piano-reboot-bootloader` loads `nvmem_qcom-spmi-sdam` and `nvmem-reboot-mode` and refuses to
  reboot when no reboot-mode device is bound, since the tablet would restart into slot A instead.
- Verified on the test unit in a release cold boot.
- Default: off in the release image. The installation turns it on ([INSTALL.md](INSTALL.md) step 8a
  or 10) and step 10 ends with `sudo piano-devmode off`, the release default.
- The current device support package socket has `BindToDevice=usb0` (the NCM function's
  network device) and starts after `sys-subsystem-net-devices-usb0.device`: the address alone also
  accepted connections that reached 192.168.88.2 over Wi-Fi. In QEMU a peer routing to that address
  over another interface is refused; not yet checked on the tablet. If the NCM device gets another
  name, the socket does not start (`piano-devmode status` shows it inactive).

### 7.2 Debug RAM boot

Build a debug set from the same kernel, initrd and DTB as the release set (§3.6 with `--debug` and
usually `--debug-timeout 900`) and boot its v2 image without writing partitions:

```bash
fastboot -s <SERIAL> boot <debug-set>/boot-ram-check.img       # expect "Booting OKAY"
ip -brief address show dev enx020000880001                     # expect 192.168.88.10/24 within about 10 s
script -q -c 'telnet 192.168.88.2 2323' private/<run>/console.log
# expect the banner "PIANO_PERSISTENT_CONSOLE phase=initrd" (or "phase=root") and a "# " prompt
```

- The console is an unauthenticated root BusyBox shell. The initrd console ends at switch-root;
  reconnect for the root console (`phase=root`).
- With `--debug-timeout <s>` the tablet syncs, loads the reboot-mode modules and forcibly reboots
  into Fastboot at that uptime; unsaved work is lost. `systemctl stop piano-debug-timeout.service`
  removes the timer, after which you must return manually (`piano-reboot-bootloader`). Without
  `--debug-timeout` there is no timer.
- `piano-boot-switch` refuses to write when fewer than 60 s of the session remain.
  `piano-slot-success --mark` refuses debug boots.
- Send short command lines. Transfer longer scripts as files and check their SHA-256 on the tablet
  before running them.
- Debug and release sets have the same `vendor_boot.img` and `init_boot.img`; `boot.img` differs only
  in the command line.

### 7.3 Console and kernel log capture

- Capture the session on the host (`script`, as above) into the ignored `private/<run>/`; record host
  time, because the tablet clock is wrong after every boot.
- Kernel log: `dmesg` (timestamps from boot), `journalctl -k -b` in the root phase. The journal
  stamps messages from before switch-root with the time journald received them (simpledrm at about
  4.3 s in the journal against 0.2 s in `dmesg`); use `dmesg` for early timings.
- Boots that stop before userspace: build with `--debug-boot-trace` and read
  `/sys/fs/pstore/` on the next boot (in Android: `adb -s <SERIAL> shell su -c 'ls /sys/fs/pstore'`).
  Logs across a reboot can be incomplete.

## 8. Acceptance

### 8.1 Rules

- A process being active (`gdm`, `gnome-shell`), `/dev/fb0` content or a framebuffer screenshot is
  not a display pass. Hardware criteria (DPU frame counters, interrupt status, scanout memory
  samples) and a person's visual confirmation are recorded separately and do not replace each other.
- The same software can boot into a fast or a slow state; a check at a single moment does not reveal
  the slow state. Every acceptance boot checks the time and bandwidth budgets below.
- Accept a new kernel or image set first in a debug RAM boot of exactly its kernel, initrd and DTB
  (profile `debug`, §8.3), then on the release cold boot over developer mode (profile `cold`, §8.4).
- `tools/piano_boot_acceptance.py` holds the criteria and budgets as data and evaluates saved
  captures. Each criterion is PASS, FAIL or UNAVAILABLE with its measured value; missing input is
  never FAIL. The verdict is PASS, FAIL or INCOMPLETE (exit status 0, 1 or 3).

### 8.2 Budgets

`python3 tools/piano_boot_acceptance.py criteria` prints the current list:

| Criterion | Budget |
|---|---|
| Classification | FAST: switch-root ≤ 18 s, simpledrm to fb0 ≤ 0.5 s, gdm active by 40 s |
| simpledrm | initialised < 1 s (kernel log time, not journal receive time) |
| Scanout clock | `gcc_disp_hf_axi_clk` held by the framebuffer (clk_summary and CBCR) from the first initrd console and in the root |
| Switch-root | `UserspaceTimestampMonotonic` ≤ 10 s |
| GDM | active ≤ 20 s and still active |
| GMU | first `Loaded GMU firmware` ≤ 20 s |
| GPU errors | no GMU initialisation timeout, OOB, hangcheck or GMU/Adreno error line, observed to ≥ 180 s |
| Memory | every 256 MiB RAM copy ≥ 2000 MB/s |
| Scanout | INTF1 and INTF2 at 100–140 Hz, interrupt status unchanged, ≥ 5 colours, most common `222222` (GDM greeter) |
| Touch, Bluetooth | THP first frame without SPI/GPI errors; `hci0` UP RUNNING, Powered yes |
| System | no failed units; rpmhpd and every interconnect provider synced |
| Identity | expected release running the root, only its own modules loaded, `CONFIG_SM_DISPCC_8750=y` |
| `debug` only | slot attributes unchanged between Fastboot readings before and after |
| `cold` only | store manifest release equals `uname -r`; `piano-boot-switch status` occupant `ubuntu` |

### 8.3 Debug validation boot (profile `debug`)

Evidence directory layout: `initrd-*.txt` (first initrd console), `root-*.txt` (root transcripts,
name order = time order), optional `kmsg*.txt` / `journal*.txt`, `getvar-before.txt` and
`getvar-after.txt`.

```bash
R=private/<run>; mkdir -p $R
python3 tools/piano_boot_acceptance.py script --initrd > $R/initrd.sh
python3 tools/piano_boot_acceptance.py script > $R/root.sh
getvars() { date -Iseconds; for v in product unlocked current-slot is-userspace slot-successful:a \
  slot-retry-count:a slot-unbootable:a slot-successful:b slot-retry-count:b slot-unbootable:b \
  battery-voltage battery-soc-ok; do fastboot -s <SERIAL> getvar $v 2>&1 | grep -v '^Finished'; done; }
getvars > $R/getvar-before.txt
fastboot -s <SERIAL> boot <debug-set>/boot-ram-check.img
ip -brief address show dev enx020000880001                           # wait for 192.168.88.10/24
python3 -m http.server --bind 192.168.88.10 --directory $R 8000 &     # serves the two scripts
script -q -c 'telnet 192.168.88.2 2323' $R/initrd-0006s.txt          # first initrd console
```

*On the tablet*, in that console (and likewise with `root.sh` in root consoles recorded as
`$R/root-0041s.txt` at about 40 s and `$R/root-0190s.txt` after 180 s):

```sh
B=/usr/libexec/piano/busybox
$B wget -q -O /run/initrd.sh http://192.168.88.10:8000/initrd.sh && $B sha256sum /run/initrd.sh && sh /run/initrd.sh
# compare the hash with sha256sum on the host before trusting the output
```

After the session has returned to Fastboot:

```bash
kill %1                                                               # stop the HTTP server
getvars > $R/getvar-after.txt
python3 tools/piano_boot_acceptance.py evaluate --dir $R --release <release> --profile debug --json $R/verdict.json
# expect one PASS line per criterion and exit status 0
```

The scanout criterion needs the DPU scanout probe, which is maintained outside this repository
(`live --scanout-probe`, `script --scanout-probe`); without it that criterion is UNAVAILABLE and
the verdict at best INCOMPLETE. Record the manual frame counter check (§8.5) instead, as a separate
hardware result.

### 8.4 Release cold boot (profile `cold`)

```bash
python3 tools/piano_boot_acceptance.py live --user <USER> --sudo --profile cold --release <release> --save private/<run>
# expect one PASS line per criterion, private/<run>/verdict-*.json and exit status 0
```

`live` checks the link with `piano-devmode-ssh.py --check`, runs the read once and evaluates it.
With `--sudo` the password typed there is the first input line of `sudo -S`; a wrong password ends
the read.

### 8.5 Manual frame counter check (as root on the tablet)

```bash
bb=/usr/libexec/piano/busybox
a=$($bb devmem 0x0ae360ac 32); b=$($bb devmem 0x0ae370ac 32); i=$($bb devmem 0x0ae01014 32); sleep 0.5
echo "INTF1 $a $($bb devmem 0x0ae360ac 32)  INTF2 $b $($bb devmem 0x0ae370ac 32)  TOP0 $i $($bb devmem 0x0ae01014 32)"
# expect each INTF counter about 0x3c higher in the second value and the two TOP0 values equal
```

The registers are INTF1 and INTF2 `FRAME_COUNT` and the MDP TOP0 interrupt status of the firmware
scanout; reading them changes nothing. A latched underrun bit in TOP0 from early boot is history,
not a rate; only a change between the two reads counts. This covers the refresh rate only, not the
image content.

### 8.6 Flash torch check (as root on the tablet)

```bash
python3 tools/check-piano-flash.py            # the limits and the current torch of every flash device
python3 tools/check-piano-flash.py --torch 64 # write a torch value and read it back
```

The flash of the camera is a standard LED class device, so a program of the distribution can use it
and no helper of this repository is needed at runtime. The script reads the state of the controller;
the light itself is only visible on the tablet.

## 9. Evidence and documentation

- Status levels: *to be researched*, *source lead*, *offline pass*, *device pass*, *device fail*.
  Untested is not failed. Source code, a probe or an active service, QEMU and a successful build are
  not a device pass.
- Bind every device result to the device model and stock build, the kernel commit and release, the
  integration commit, the image set's `SHA256SUMS`, the scenario (debug RAM boot or release cold
  boot) and the location of the evidence. Results from another tablet or another candidate do not
  carry over.
- Keep earlier failures; a later pass states its own scope instead of replacing them.
- Public status lives in the [README support matrix](../README.md#hardware-support) (one line per
  feature) and [STATUS.md](STATUS.md) (test configuration, known issues, roadmap). Raw logs,
  captures and device records stay in ignored `private/` directories and are never committed.
- Hardware reports name the device model, stock build, kernel release and image set identity and
  omit serial numbers, Bluetooth addresses, calibration and personal data.
- Project kernel commits carry no `Signed-off-by`, `Fixes:` or `Tested-by` tags; a human contributor
  adds them before any upstream submission. Agents do not add human sign-offs.
