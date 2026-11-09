# Ubuntu for Xiaomi Pad 8 Pro

Native Ubuntu 26.04 LTS with the GNOME desktop on the Xiaomi Pad 8 Pro (codename `piano`, Snapdragon 8 Elite), dual-booting with Android, on a device-adapted kernel based on upstream Linux 7.2. Not a container, not a remote desktop. [中文](README.md)

**Latest release: see [Releases](https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/latest). Official builds are published only in this repository's Releases, free of charge.** What works is in [Hardware Support](#hardware-support).

## 🧭 Our Approach

There are roughly two ways to bring a Linux desktop to an Android tablet.

One is **make it work first, then adapt**: keep the vendor's downstream drivers and patch the desktop and applications wherever they do not cooperate. Features arrive quickly, but the device's quirks end up spread across the kernel, the desktop and the applications, tied together, and every system upgrade means reworking those changes.

The other is **stay close to upstream**: solve hardware problems in the kernel and drivers the way mainline does, keep Ubuntu, GNOME and the rest as unmodified as possible, and let the system recognise the device through standard interfaces. It is slower and harder; many problems can only be fixed once their root cause is found.

We chose the second.

| | Make it work, then adapt | Stay close to upstream (this project) |
|---|---|---|
| Kernel and drivers | Vendor downstream drivers plus temporary workarounds | Mainline kernel and upstream drivers; missing pieces added the upstream way |
| Desktop and apps | Desktop components modified to suit this device | Stock distribution software wherever possible; device differences stay out of the desktop |
| When something breaks | Add delays, retries and compatibility layers until it works | Find the root cause and fix it in the layer where it lives |
| System upgrades | Every change has to be reworked, upgrades can get stuck | Normal upgrades with upstream and the distribution |
| Reuse by others | Depends on this whole set of specific changes | Low cost to move to another distribution or a device with the same SoC |

**Why:**

- **Decoupling.** Differences in the display, touch and charging hardware are described by the kernel and the device tree; the desktop just sees a standard display, touchscreen and battery and does not need to know which tablet it runs on.
- **Upgrades are not held hostage.** The fewer the changes, the smoother the upgrades: the kernel can follow new upstream releases and Ubuntu updates install as usual. The few desktop components that must be changed ship as separate packages that track upstream and are dropped once upstream supports the feature.
- **Fix once, everyone benefits.** A problem solved the upstream way can go back upstream for other devices and distributions; upstream fixes, in turn, apply here directly.
- **Maintainable in the long run.** Few changes with clear boundaries make problems easy to locate instead of piling up.

The price is that features arrive more slowly, and some problems take a long time to trace to their root cause. The occasional screen flash while touching, for example, was not avoided by lowering the refresh rate; we traced it to a timing conflict between touch data transfers and system power saving, and fixed it in the driver. We accept that price, because this is the route that holds up over time.

## 📮 Follow & Discuss

Long-form technical articles, write-ups and progress updates are published on the WeChat official account; the community group is on Xiaohongshu — scan to join. Both are in Chinese, and installation feedback is welcome in either place.

<div align="center">
<table>
<tr><th>WeChat: 熵减矩阵</th><th>Xiaohongshu: yzddmr6</th></tr>
<tr>
<td align="center"><img src="docs/assets/gzh-qr.png" width="300" alt="WeChat: 熵减矩阵"></td>
<td align="center"><img src="docs/assets/xhs-qr.jpg" width="300" alt="Xiaohongshu: yzddmr6"></td>
</tr>
<tr><td align="center">Long-form articles · Write-ups · Updates</td><td align="center">Community group · Feedback</td></tr>
</table>
</div>

## ⚠️ Read Before Installing

- **This is an experimental project, not a daily driver.** Installing unlocks, repartitions and erases the tablet; a mistake can brick it, and recovery requires `adb`, `fastboot` and partition backups. Make sure you have the skills, decide deliberately, and accept the risk.
- **All Android data on the tablet is erased.** Installation re-creates `userdata` and clears `metadata`.
- **The bootloader must be unlocked** through Xiaomi's official process, and Android must be rooted (to read partitions for the backup).
- **Only stock build OS3.0.303.0.WPYCNXM on slot A is supported.**
- **Back up first.** Before any write, back up every partition except `userdata` and the partition table of every UFS LUN. Slot A's stock `boot`, `init_boot` and `vendor_boot` are your way back to Android.
- **Keep at least 40 % before switching systems.**
- **No speaker protection.** The speakers play, but with a capped volume; do not raise the gain yourself.
- **Verified on one unit only:** model 25091RP04C (China), 16 GB RAM, 512 GB storage. Other models and capacities are unverified.
- **Recovery:** with no picture or no response, hold Power + Volume Down for Fastboot and write Android back with `tools/piano-switch.py`, see [Usage and Maintenance](#usage-and-maintenance).

Partition layout: on the 512 GB model the original `userdata` range becomes `userdata` (200 GiB, Android), `linux_rootfs` (60 GiB, Ubuntu `/`) and `linux_data` (about 201 GiB, `/home`); on the 256 GB model `userdata` is 110 GiB and `linux_data` about 61 GiB. Nothing else changes.
**Never flash `vbmeta`, never write or activate slot B.** Calibration and per-unit configuration must come from your own tablet.

## 🚀 Installation

You need a Linux x86_64 host, a USB data cable, and an hour or two next to the tablet.

### Recommended: let an AI assistant install it

The procedure is long and order-sensitive. The easiest way is to hand it to an AI coding assistant that can run commands (Claude Code, Codex CLI, Gemini CLI, …). Start the assistant on the Linux host and paste the block below in full. It reads the documentation, checks the environment, makes the backup and asks for your confirmation before every write; you only press buttons on the tablet and make the choices.

```text
I want to install Ubuntu on a Xiaomi Pad 8 Pro (codename piano), dual-booting with Android. Act as my operator and follow these rules.

Project: https://github.com/yzddmr6/xiaomipad-8pro-mainline , latest release (the one marked Latest at https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/latest).

1. First git clone the repository and check out the tag of the latest release. Read README.en.md "Read Before Installing", docs/INSTALL.en.md, docs/FLASHING.en.md and the safety rules in section 1 of docs/AGENT-GUIDE.en.md before doing anything.
2. Download every asset of that GitHub release into one empty directory, run sha256sum -c SHA256SUMS.assets first, then restore the release directory as the release notes describe and verify it again.
3. Check the host tools (adb, fastboot, python3, zstd, sgdisk, sfdisk, img2simg and the rest of the "Prerequisites" table in INSTALL) and the tablet (product piano, stock build OS3.0.303.0.WPYCNXM, current slot a, unlocked, rooted, battery at least 40 %). If anything does not match, stop and tell me; do not work around it.
4. Follow docs/INSTALL.en.md strictly in order and explicitly select my tablet with -s or --serial in every device command. Before each step, tell me in a sentence or two what it does and where it writes. For every operation that writes to the tablet (partition table, erase, flash, slot A), list the exact commands and wait for me to reply "confirm" before running them.
5. Never write vbmeta, dtbo, any _b partition, persist or any other calibration, modem or firmware partition; never run fastboot --set-active=b; never use calibration, per-unit configuration or images from another tablet.
6. No write step before the backup is complete and its hashes verified. Keep the backup and my per-unit files outside the repository; never commit or upload them anywhere.
7. Stop on any error: if a command fails or its output differs from the documented "expect" line, stop and show me the full output. Do not retry write commands on your own and do not interrupt a write in progress.
8. I will stay next to the tablet. When I need to act on it (key combination for Fastboot, creating the account in Initial Setup, typing a password), tell me exactly what to do and what to wait for.
9. After the installation, finish the last step as documented (install the per-unit files, store the Android image set, rebuild the boot set, turn developer mode off), then explain how to switch between the two systems and how to recover Android from Fastboot.
```

### Manual installation

1. Download every asset of the [latest release](https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/latest) into one empty directory (only that one release), verify it and restore the release directory:

   ```bash
   sha256sum -c SHA256SUMS.assets
   unzip piano-ubuntu-*-boot.zip && unzip piano-ubuntu-*-tools.zip && unzip piano-ubuntu-*-packages.zip
   mkdir -p rootfs && cat piano-ubuntu-*-rootfs.img.zst.part* | zstd -d > rootfs/root.sparse.img
   sha256sum -c SHA256SUMS
   ```

2. Follow [docs/INSTALL.en.md](docs/INSTALL.en.md) step by step. Switching, Fastboot recovery and the partitions that must never be written are in [docs/FLASHING.en.md](docs/FLASHING.en.md).

### Upgrading an existing installation

Follow [docs/UPGRADE.en.md](docs/UPGRADE.en.md) to upgrade the kernel and the device packages from the release's `packages.zip`; the upgrade keeps a rollback boot set.

### Building from source

The kernel, Mesa, the device packages, the root image and the signed boot set are all built on the host without root privileges, from inputs pinned in `kernel/source.json`, `mesa/source.json`, `ubuntu/` and `device/*/source.json`. See [docs/BUILD.en.md](docs/BUILD.en.md).

<a id="support-matrix"></a>

## Hardware Support

Support in the latest release. ✅ works · 🟡 partial · ❌ unsupported.
Current support and improvements by release are in [docs/STATUS.md](docs/STATUS.md).

### Platform, Display and Input

| Function | Hardware / implementation | Status | Notes |
|---|---|---|---|
| SoC / CPU | Qualcomm Snapdragon 8 Elite (SM8750P) | ✅ works | All eight cores with frequency scaling |
| Android / Linux dual boot | Slot-A image-set rotation | ✅ works | Switching from Ubuntu (one command), from Android (KernelSU module) and from the host (Fastboot) all verified |
| Internal storage | KIOXIA UFS / ext4 | ✅ works | 60 GiB root, about 201 GiB `/home` on the 512 GB model; UFS multi-circular queues enabled |
| Internal display | Native DSI KMS (`msm_dpu`) | ✅ works | 3200×2136; 60/90/120/144 Hz changes and screen off/on verified |
| Night Light | GNOME / DPU color temperature | ✅ works | Color temperature adjustment with settings preserved across screen off/on and suspend |
| GNOME desktop | Ubuntu 26.04 GNOME / Wayland / GDM | ✅ works | Cold boot to the login screen in about 13 s |
| GPU | Adreno 830 / Mesa 26.2 | ✅ works | GPU rendering for the desktop and for snap applications including Firefox |
| System monitor | GNOME Resources | ✅ works | The task manager shows per-core CPU frequency and temperature, the GPU model, frequency, temperature and utilisation, per-application GPU usage, and the temperature of each battery; a Temperature page lists every temperature sensor by device with history graphs |
| Touchscreen | Novatek NT36532E (THP over SPI) | ✅ works | Single- and multi-finger touch and gestures at 60/90/120/144 Hz and after screen off/on and suspend; the panel type (CSOT or BOE) is detected automatically and loads its own touch firmware and orientation |
| Magnetic keyboard | Nanosic WN8030 | ✅ works | Typing, key wake with the screen off and wake from suspend |

Not yet supported: stylus, fingerprint.

### Wireless and USB

| Function | Hardware / implementation | Status | Notes |
|---|---|---|---|
| Wi-Fi | Qualcomm WCN7860 / ath12k | ✅ works | Stable 2.4 GHz and 5 GHz 80 MHz links |
| Bluetooth | Qualcomm WCN7860 / `hci_qca` | ✅ works | Pairing, file transfer and audio work |
| USB device mode | USB 3 / 5 Gbit/s networking (NCM) | ✅ works | SSH over the USB cable in developer mode, off by default |
| USB host / OTG | Type-C role switching / USB 3 / 5 Gbit/s | 🟡 partial | USB sticks, keyboards, mice and docks work at 5 Gbit/s in both orientations; a plain OTG adapter may not be detected when re-plugged right after an OTG device is removed |
| USB-C external display | DisplayPort Alt Mode | ✅ works | Both orientations and hot-plug; tested at 4K 60 Hz as an extended desktop |

### Audio, Video and Sensors

| Function | Hardware / implementation | Status | Notes |
|---|---|---|---|
| Speakers | FS19xx quad amplifiers | ✅ works | Playback on all four speakers; maximum volume increased; keep the default volume cap |
| Internal microphones | DMIC / ADSP / PipeWire | ✅ works | Recording verified |
| Video decoding | Qualcomm Iris / V4L2 | ✅ works | Automatic hardware decoding in the system player (GStreamer), Firefox (H.264/HEVC/VP9) and Chrome (H.264/VP9); AV1 uses software decoding |
| Video encoding | Qualcomm Iris / V4L2 | ✅ works | H.264/H.265 at 1080p and 4K verified |
| Cameras | Rear S5KJN1SQ03, front OV32D40 | ✅ works | Front and rear cameras available in camera apps; fixed rear focus, suited to subjects beyond close range |
| Accelerometer, gyro, light, proximity | SSC / hexagonrpcd / libssc | ✅ works | SensorProxy reports orientation and ambient light |
| Auto-rotation | SensorProxy / Mutter | ✅ works | Rotates with the keyboard detached; locked by GNOME's rule while the keyboard is attached |

Some HEVC videos may drop a small number of frames in Firefox. Chromium snap is not covered by this acceleration integration.

### Power and Time

| Function | Hardware / implementation | Status | Notes |
|---|---|---|---|
| Power / volume keys | Qualcomm PMIC | ✅ works | Power key locks the screen by default and wakes the lock screen; action configurable in Settings → Power; volume keys change the volume |
| Power modes | GNOME / TuneD | ✅ works | Power Saver, Balanced and Performance in quick settings or Settings → Power; Balanced by default |
| Battery gauge / temperature | 2 × TI BQ27Z561 / thermal zones | ✅ works | Charge level, voltage, current and temperatures |
| Board temperatures | PMIC ADC thermistors | ✅ works | 11 on-board temperature readings, shown on the task manager Temperature page |
| Charging | `xiaomi_mca` / SC8541 | 🟡 partial | The original adapter supports PPS fast charging, while other USB PD chargers retain 9 V fast charging; Qualcomm QC and other proprietary protocols charge at the normal 5 V rate. Actual power varies with charge level, temperature, adapter and cable. Settings → Power offers “Preserve Battery Health”: stop at 80% and resume at 75%; while it is on, the tablet stays awake when plugged in below the limit, as on Android |
| Suspend | suspend-to-idle | ✅ works | Timer, keyboard, power-button and cover-open wake; default system idle policy; closing the keyboard cover on external power only locks the screen |

Not yet supported: disk hibernation, hardware clock writes (time is set from the network after boot).

### Virtualization

| Feature | Implementation | Status | Notes |
|---|---|---|---|
| Command-line VMs | Gunyah / crosvm | ✅ optional | Ubuntu and Windows ARM guests, SSH/VNC, NAT and persistent disks; [user guide](docs/VIRTUALIZATION.md) |
| Android apps | Waydroid | 🟡 partial | Optional install; runs Android apps with networking; software rendering only, no GPU acceleration; [installation and setup](docs/WAYDROID.en.md) |

## Usage and Maintenance

Checks GitHub anonymously once a day for the latest release and notifies you when a newer version is available; this can be disabled in notification settings.

Host commands use the REPO, RELEASE and WORK variables from [INSTALL.en.md "What you need"](docs/INSTALL.en.md#what-you-need) and run in WORK. Details are in [docs/FLASHING.en.md](docs/FLASHING.en.md).

**Switching.** In Ubuntu, `sudo piano-boot-switch to-android --reboot` writes the stored Android image set to slot A, reads it back and reboots. In Android, the [KernelSU switch module](device/android/piano-switch-ksu/README.md) switches to Ubuntu. Switching from within either system does not change the slot attributes.

**Emergency return to Android.** If Ubuntu is unresponsive, open the keyboard cover and hold both volume keys for 10 seconds; after checking the image set, the tablet switches to Android and restarts, losing unsaved work. The shortcut is ignored with the cover closed; if it does not respond, use Fastboot recovery below.

**Recovery from the host.** With the tablet in Fastboot (Power + Volume Down), `tools/piano-switch.py` checks the device and both image sets, writes slot A and restores the previous set if any write fails:

```bash
python3 "$REPO/tools/piano-switch.py" --serial <serial> --images <android-set> --rollback <ubuntu-set>
fastboot -s <serial> reboot
```

A host write resets slot A's retry counter; the next Ubuntu boot marks the slot successful again. If Fastboot reports `slot-unbootable:a` as `yes`, run `fastboot -s <serial> --set-active=a` to reset it; slot B stays unused.

**Developer mode.** `sudo piano-devmode on` enables SSH over the USB cable; it is off by default. Connect from the host with `python3 "$REPO/tools/piano-devmode-ssh.py" --serial <serial> <user>`, and turn it off afterwards with `sudo piano-devmode off`. See [device/devmode](device/devmode/README.md).

**Upgrades.** See [docs/UPGRADE.en.md](docs/UPGRADE.en.md). Installing a kernel package rebuilds the boot set and rewrites slot A; the previous set is kept for rollback.

**Time.** The hardware clock is not writable; the time is set from the network after boot. Without a network, set it with `sudo date -s 'YYYY-MM-DD HH:MM'`.

**Sensor calibration.** The image ships default calibration only. Installing your own tablet's stock calibration is recommended: prepare it from your own backup as described in [INSTALL](docs/INSTALL.en.md#recommended-prepare-your-sensor-registry) or the [sensor instructions](device/sensors/README.zh-CN.md) (Chinese); never use another tablet's.

**After Initial Setup.** Initial Setup sets the hostname from its *Computer* field; change it later with `sudo hostnamectl hostname --static <name>`. Run `sudo apt update` before the first `apt install`: the image ships without package indexes.

**App Center.** Once online, run `sudo snap refresh snap-store gnome-46-2404` to update App Center.

**Self-check.** Run `piano-check` for local status, or `piano-check --markdown` for an issue-ready summary. It needs no root privileges and omits device serials, radio addresses, network names and account names.

## Development and Contributions

| Repository | Content |
|---|---|
| xiaomipad-8pro-mainline | Device userspace, Ubuntu integration, build/packaging/switching tools, firmware, documentation |
| [linux-sm8750-piano](https://github.com/yzddmr6/linux-sm8750-piano) | Linux kernel: drivers, devicetree and configuration maintained on top of upstream Linux 7.2 stable, release commit pinned in the input lock |

See [docs/BUILD.en.md](docs/BUILD.en.md) to build the kernel, Mesa, device packages and images; [kernel/source.json](kernel/source.json) pins the kernel commit and configuration fragments.

Before operating on a device, read [docs/AGENT-GUIDE.en.md](docs/AGENT-GUIDE.en.md), written for human operators and AI assistants; contribution rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

Report problems through [GitHub Issues](https://github.com/yzddmr6/xiaomipad-8pro-mainline/issues) with the output of `piano-check --markdown` and your stock build; strip serial numbers, Bluetooth addresses, calibration data and personal information first.

## Sponsors

This project is maintained by one person. Porting, debugging and testing every release on real hardware take a lot of time and effort, and development also needs a steady supply of AI coding tool tokens.

The project is free and open source, and every feature works without sponsoring. If it helps you, sponsorship is welcome; the funds go to tokens and ongoing development and maintenance. Sponsorship is voluntary support and does not come with any service or commitment.

- Xiaohongshu: the 「致谢名单」 (acknowledgment list) item in the shop (scan the Xiaohongshu QR code above to open the profile)

When sponsoring, leave your GitHub ID or the name you want shown, and it will be added to the list below; without one, nothing is published.

Thanks to these sponsors (sorted by total amount; amounts shown from ¥50):

| Sponsor | Amount |
|---|---|
| [@pidbid](https://github.com/pidbid) | ¥100 |

## Acknowledgments and Licensing

This project reuses and adapts the following work:
- the OnePlus Pad 2 Pro (`erhai`) mainline integration project (GPL-2.0-only);
- the Xiaomi Pad 6 Pro (`liuqin`) mainline project: [integration](https://github.com/yzddmr6/xiaomipad-6pro-mainline) (MIT) and [kernel](https://github.com/yzddmr6/linux-sm8450-liuqin) (GPL-2.0);
- [BigfootACA/linux](https://github.com/BigfootACA/linux/tree/full-v7.1.3) (GPL-2.0);
- [ROCKNIX](https://github.com/ROCKNIX/distribution/tree/92a6fca6afda5ab7e1874af8985095beee19b6a8) and Teguh Sobirin's [SM8750 kernel patches](https://github.com/ROCKNIX/distribution/tree/92a6fca6afda5ab7e1874af8985095beee19b6a8/projects/ROCKNIX/devices/SM8750/patches/linux) (GPL-2.0; DTS BSD-3-Clause);
- Qualcomm (Jie Zhang, Akhil P Oommen, Gaurav Kohli): the [Adreno 830 series](https://patchew.org/linux/20260809-pakala-gpu-v1-0-207ad1fac1c7%40oss.qualcomm.com/) (GPL-2.0);
- Junjie Cao: the [NT36532 panel driver](https://patch.msgid.link/20260804074233.85629-1-junjie.cao@linux.dev) (GPL-2.0-only);
- Alexander Koskovich and Jonathan Marek: the [DSC slice-per-packet patch](https://patch.msgid.link/20260318-dsi-dsc-slice-per-pkt-v2-1-0a1b316f8250@pm.me) (GPL-2.0);
- Xiaomi's [vendor_xiaomi_proprietary_touch-driver](https://github.com/MiCode/vendor_xiaomi_proprietary_touch-driver/tree/piano-w-oss) (GPL-2.0);
- ianchb's [xiaomi-sheng-thp](https://github.com/ianchb/xiaomi-sheng-thp) (Apache-2.0);
- map220v's [hid-nanosic-wn8030](https://github.com/ianchb/sm8550-mainline/blob/42f3b40c702abbc37c3a04c95af008cd283aeaf3/drivers/hid/hid-nanosic-wn8030.c) (GPL-2.0-only);
- AOSP [mkbootimg](https://android.googlesource.com/platform/system/tools/mkbootimg) (Apache-2.0) and
  [avbtool](https://android.googlesource.com/platform/external/avb/) (MIT);
- [Mesa](https://gitlab.freedesktop.org/mesa/mesa) (MIT);
- linux-msm [hexagonrpc](https://github.com/linux-msm/hexagonrpc) (GPL-3.0, LGPL-3.0) and
  [qrtr](https://github.com/linux-msm/qrtr) (BSD-3-Clause);
- [libssc](https://codeberg.org/DylanVanAssche/libssc) (GPL-3.0);
- [iio-sensor-proxy](https://gitlab.freedesktop.org/hadess/iio-sensor-proxy) (GPL-3.0);
- [AudioReach topology](https://github.com/linux-msm/audioreach-topology) (BSD-3-Clause);
- [TinyALSA](https://github.com/tinyalsa/tinyalsa) (BSD-3-Clause);
- [systemd](https://github.com/systemd/systemd) (LGPL-2.1-or-later, GPL-2.0-or-later);
- BusyBox (GPL-2.0);
- [Ubuntu](https://ubuntu.com/download/desktop).

Related projects (used as references):
- [Project-SunUEFI](https://github.com/slwyts/Project-SunUEFI) (per-file licences);
- [blu-sharky/linux-piano](https://github.com/blu-sharky/linux-piano) (GPL-2.0);
- [blu-sharky/debian-piano](https://github.com/blu-sharky/debian-piano) (MIT).

Per-file origins, commits and changes are recorded in [NOTICE](NOTICE). Unless a file's SPDX header says otherwise, original integration code is [MIT](LICENSE); a few tools adapted from GPL-2.0-only sources stay GPL-2.0-only. The kernel is GPL-2.0. Third-party components keep their own licences (see `LICENSES/`). Stock firmware and static sensor configuration follow the [firmware distribution policy](firmware/README.md#distribution-policy), which also describes using your own extraction.
