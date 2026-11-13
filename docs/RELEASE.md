# Release artefacts

How a release is built from pinned inputs, what it contains, and how it is published. The tools
write local files only; publishing is a separate, manual step.

## What a release contains

A release directory (built by `tools/build-release.py`) has this layout; the published assets are
a repackaging of exactly these files (see [Publishing](#publishing)).

| Path | Content |
|---|---|
| `boot/boot.img`, `boot/init_boot.img`, `boot/vendor_boot.img` | Ubuntu boot image set for slot A, AVB-signed with a release key; `boot/SHA256SUMS` lists them |
| `boot/boot-ram-check.img` | same kernel, initrd, DTB and command line as the set, for `fastboot boot` before writing slot A |
| `boot/boot-ram-maintenance.img` | RAM-only maintenance system: a root shell over USB with nothing mounted, for writing the partition table ([INSTALL.en.md step 6](INSTALL.en.md#6-write-the-partition-table-on-the-tablet)) |
| `boot/manifest.json`, `boot/boot-profile.json`, `boot/piano-boot.dtb`, `boot/maintenance-manifest.json` | inputs and outputs of the set; the boot profile holds only stock-build fields; the DTB the set carries |
| `rootfs/root.sparse.img` | the 60 GiB `linux_rootfs` ext4 as an Android sparse image, for `fastboot flash linux_rootfs` |
| `rootfs/manifest.json`, `rootfs/verification.json` | every input hash and package version; the offline checks |
| `debs/` | release-supplied packages (including rebuilt Ubuntu power-button packages; [inputs and update policy](../ubuntu/README.md)): project packages installed in the image: kernel, device support, firmware, Mesa, desktop defaults, snap graphics provider, MiPPS authentication, sensors |
| `tools/piano-switch.py`, `tools/piano-switch-ksu-<version>.zip` | host Fastboot switching and recovery; the Android KernelSU switch module |
| `RELEASE-NOTES.md`, `release-manifest.json`, `SHA256SUMS` | the release notes (also the GitHub release body); identity and checks of every input; every file's hash |

The `linux_data` (`/home`) filesystem is not shipped; it is created at install time
([INSTALL.en.md step 7](INSTALL.en.md#7-write-the-filesystems-fastboot)).

## What the image contains

- Ubuntu 26.04 Desktop ARM64 (the two official ISO layers merged unchanged, [BUILD.en.md](BUILD.en.md)),
  with the APT sources of [ubuntu/26.04.sources](../ubuntu/26.04.sources), cloud-init disabled and
  the CD-ROM source removed.
- The project packages above, `openssh-server` for developer mode, and the updated `dracut`,
  installed in a chroot ([AGENT-GUIDE.en.md](AGENT-GUIDE.en.md) §4.4). Only the project kernel is
  installed: Ubuntu's generic kernel, its headers and `kdump-tools` are purged so that updates do
  not fetch kernels that cannot boot (this also removes `libc6-dev`; install `build-essential`
  before compiling). `linux-firmware` stays.
- `/etc/fstab` and `/etc/piano/layout.json` name the partitions by GPT label, so the image fits any
  unit:
  ```
  PARTLABEL=linux_rootfs / ext4 defaults,noatime 0 1
  PARTLABEL=linux_data /home ext4 defaults,noatime 0 2
  ```
- The boot set in `/var/lib/piano/switch/ubuntu/`, the same files as `boot/`.
- Hostname `xiaomipad-8pro` (GNOME Initial Setup replaces it with its *Computer* field), zram swap
  (half the memory up to 8 GiB), `fake-hwclock` for the unwritable RTC, no APT indexes (run
  `apt update` first), Chinese locale default.

It has no user account (Initial Setup creates the first one), an empty `/etc/machine-id`, no SSH
host keys, developer mode off, no journal or APT caches, and no per-unit file in `/etc/piano`.

The boot set is address-free: its command line is

```
root=PARTLABEL=linux_rootfs rootfstype=ext4 rw rootwait log_buf_len=2M panic=15
msm.separate_gpu_kms=0 piano.boot=persistent systemd.gpt_auto=0 bootconfig
```

ABL appends `androidboot.serialno` and its other parameters itself. The DTB has no
`local-bd-address` or `local-mac-address` property. The boot profile keeps only the fields of the
stock build (header version, partition sizes, rollback indices, AVB properties, `vendor_boot` page
size and load addresses).

## Per-unit and per-build inputs

| Input | Scope | Where it goes | Without it |
|---|---|---|---|
| `bluetooth-address` | this unit | `/etc/piano/` | the Bluetooth controller stays unconfigured |
| `wlan-address` | this unit (`wlan/wlan_mac.bin` on `persist`) | `/etc/piano/` | a random Wi-Fi MAC address on every boot |
| `thp-calibration.json` | this unit's panel | `/etc/piano/` | no touch input |
| `boot-profile.json` | this unit (`serial`) and its stock build (the rest) | `/etc/piano/` | `piano-boot-update` keeps the prebuilt set; `piano-boot-switch` refuses |
| `boot-signing.pem` | local signing key | `/etc/piano/` | `piano-boot-update` keeps the prebuilt set |
| `boot-partitions.json` | this unit's GPT (`tools/extract-boot-partitions.py`) | `/etc/piano/` | `piano-boot-switch` refuses; kernel and initrd updates leave the set and slot A unchanged |
| Android set (`boot_a`, root-patched `init_boot_a`, `vendor_boot_a`) | this unit | `/var/lib/piano/switch/android/` | switching to Android from Ubuntu is refused; use Fastboot |
| stock base DTB (`vendor_boot_a`) and DTBO entry (`dtbo_a`) | the stock build | `prepare-boot-dtb.py`, then the kernel package | needed only to build a kernel package |

[AGENT-GUIDE.en.md](AGENT-GUIDE.en.md) §4.3 shows how to obtain each one. The prebuilt set and the
`piano-boot.dtb` in the kernel package were made from the stock build OS3.0.303.0.WPYCNXM; with
another stock build, prepare the DTB and the set from your own images.

## Installable kernel packaging (maintainers)

[kernel/abi.json](../kernel/abi.json) allocates releases for **all installable packaged kernel
builds**, including candidates such as `piano33` and `piano34`. Maintainers allocate an ABI when
packaging a build, before it becomes an official release. Ordinary PRs do not reserve numbers or
edit this ledger or release locks; coordinate packaging changes through an issue as described in
[CONTRIBUTING.md](../CONTRIBUTING.md#where-to-send-changes).

Use `<version>-piano<N>` with the next unused integer. Never reuse, decrease or reset the number
on an upstream update. Allocate a new ABI when the kernel commit, resulting `.config` or
toolchain changes, including DTS-only changes; `CONFIG_MODVERSIONS` is off.

1. Select the reviewed kernel commit and ordered `release_config_fragments` in
   [kernel/source.json](../kernel/source.json). Set `kernel_release` to the new release name;
   the builder applies its suffix to `CONFIG_LOCALVERSION` after merging the kernel fragments.
2. Build with [BUILD.en.md](BUILD.en.md#1-kernel). Record the commit, compiler and final `.config`
   SHA-256 from `build-result.json`.
3. Append the release, `kernel_commit`, `config_sha256` and `"upload": 1` to `kernel/abi.json`.
   Commit both locks together, then package the recorded build. An existing release with a
   different commit or configuration is refused. Raise `upload` only to repackage the same
   build with changed package content.

Rebuilding an allocated build leaves its committed locks unchanged. Local development builds
are described in [BUILD.en.md](BUILD.en.md#local-kernel-development).

## Release preparation (maintainers)

Select the already allocated kernel build and prepare the bilingual change list and release
notes. Publishing does not itself allocate another ABI. Record completed checks and untested
paths under [Release validation](#release-validation).

## Building a release

Host tools (the chroot image backend requires root; other stages run unprivileged):
`dpkg-deb`, `python3-debian`, `img2simg`, `sgdisk`, `openssl`, `zip`,
`zstd`, QEMU `qemu-system-aarch64` (TCG), squashfs-tools-ng 1.2 `sqfs2tar`, and e2fsprogs 1.47.4
`mke2fs`/`e2fsck`/`debugfs`/`dumpe2fs` built with libarchive (the identity is recorded in the
manifests). A full root image build takes about 30 minutes in the QEMU guest on an eight-core
host; as root, the chroot backend and `--base-cache` shorten it ([BUILD.en.md](BUILD.en.md#root-image-backends)).

```bash
# 1. Ubuntu packages added to the base, from a signed snapshot
python3 tools/prepare-ubuntu-apt.py --rootfs out/ubuntu-desktop-pristine/ubuntu-desktop.squashfs \
  --snapshot <YYYYMMDDT000000Z> --output out/apt-release --mozilla-firefox \
  --download dracut device-tree-compiler openssh-server fake-hwclock systemd-zram-generator \
  tuned-ppd showtime gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-libav
# 2. The project packages (AGENT-GUIDE.en.md §3.4, §3.5)
#    Verify the four patched Ubuntu debs with ubuntu/power-button-lock.SHA256SUMS
#    (ubuntu/README.md) and the Mutter archives in ubuntu/mutter/SHA256SUMS.
#    Include both sets and the Ubuntu wireplumber/libwireplumber-0.5-0 update as --deb inputs.
# 3. The root image and the boot set
python3 tools/build-rootfs-image.py \
  --squashfs out/ubuntu-desktop-pristine/ubuntu-desktop.squashfs --squashfs-sha256 <sha256> \
  --sqfs2tar <sqfs2tar> --mke2fs <mke2fs> --mke2fs-config <mke2fs.conf> --e2fsck <e2fsck> --debugfs <debugfs> \
  --dumpe2fs <dumpe2fs> --qemu <qemu-system-aarch64> --kernel-deb <linux-image deb> \
  --deb <each project deb> ... \
  --apt-manifest out/apt-release/manifest.json \
  --boot-profile <boot-profile.json read from a stock unit> --boot-key <release signing key> \
  --output out/rootfs-<version>
# expect "state": "ROOT_IMAGE_BUILT_OFFLINE_CHECKS_PASS_NOT_BOOTED"
# 4. Boot the image in QEMU towards multi-user.target (optional; slow under TCG)
python3 tools/check-rootfs-boot.py --rootfs-build out/rootfs-<version> --qemu <qemu-system-aarch64> \
  --debugfs <debugfs> --mke2fs <mke2fs> --output out/rootfs-<version>-boot
# 5. The maintenance RAM image of the same kernel package (AGENT-GUIDE.en.md §4.6), address-free
python3 tools/build-persistent-boot.py --maintenance --no-serial --kernel <vmlinuz-<release>> \
  --kernel-release <release> --dtb <piano-boot.dtb of the package> --initrd out/maintenance-initramfs/initramfs.cpio.gz \
  --profile out/rootfs-<version>/boot-profile.json --mkbootimg tools/local/aosp-mkbootimg/mkbootimg.py \
  --output out/maintenance-ram-<version>
# 6. The release directory, with the hand-written change list for the notes
python3 tools/build-release.py --rootfs-build out/rootfs-<version> \
  --boot-check out/rootfs-<version>-boot/result.json --maintenance out/maintenance-ram-<version> \
  --changes packaging/release/changes/<version>.md \
  --forbid-file <your bluetooth-address> --forbid-file <your wlan-address> --forbid-file <your boot-profile.json> \
  --output out/release-<version>
(cd out/release-<version> && sha256sum -c SHA256SUMS)
```

Omit `--boot-check` when step 4 was not run, and list QEMU boot as untested.

- Request `tuned-ppd` from the signed snapshot for the desktop image. The resolver permits
  removal only of `power-profiles-daemon` when replacing that provider. Image
  construction applies the supplied debs in one offline APT transaction and
  also permits replacing `iio-sensor-proxy` when the release supplies
  `piano-iio-sensor-proxy`. All other removals and downloads are forbidden. Profiles and configuration
  ownership are described in [device/power/tuned](../device/power/tuned/README.md).
- `build-rootfs-image.py` streams the SquashFS into `mke2fs -d -` at the full partition size,
  then installs packages with the selected chroot or QEMU guest backend
  ([BUILD.en.md](BUILD.en.md#root-image-backends)). In guest mode the release key reaches the
  guest only on a temporary input disk that is deleted afterwards. The boot profile is reduced to
  the stock-build fields; the unit's serial and observations are dropped.
- The tool runs `e2fsck -fn`, checks the finished image with `debugfs` (`verification.json`:
  packages, accounts, machine ID, SSH host keys, `/etc/piano`, fstab, units, kernel files, the store
  set, only the project kernel, no APT indexes), trims the free blocks, and writes `root.sparse.img`
  with [tools/sparse_image.py](../tools/sparse_image.py): free blocks become DONT_CARE, so Fastboot
  writes only the used blocks (about 9 GB) instead of 60 GiB. The build stops if a DONT_CARE block
  is in use or more than 64 MiB of FILL lies on free blocks. The image is not byte-reproducible
  (dpkg and dracut record times).
- `build-release.py` checks that the boot set carries no unit identity (no serial on the command
  line, no address properties in the DTB, stock-only profile), prints what Fastboot writes for every
  image, and with `--forbid-file` scans every output for your unit's serial and addresses (the
  files are read, never copied or recorded; the scan does not see inside compressed files). It
  hard-links the root image, so the release directory must be on the same filesystem as the build.

## Release validation

List the installation, upgrade and cold-boot checks actually performed, their results and
untested paths. Bind each result to the candidate image/integration commit, kernel release and
commit, device/panel variant and stock build, following
[CONTRIBUTING.md](../CONTRIBUTING.md#evidence). Keep the evidence summary in
[STATUS.md](STATUS.md); a previous release's result does not validate a new candidate.
Use the current cold-boot procedure in [AGENT-GUIDE.en.md §8.4](AGENT-GUIDE.en.md#84-release-cold-boot).

## Publishing

Release notes describe completed work, supported features and usage: retain safety guidance, summarize unsupported hardware briefly, limit known issues to at most three user-visible notes, and omit internal plans, debugging narratives, debt identifiers and internal paths.

GitHub limits a release asset to 2 GiB, so the release directory is repackaged into a fixed set of
assets by `tools/package-release-assets.py`:

| Asset | Content |
|---|---|
| `piano-ubuntu-<version>-boot.zip` | `boot/` (the slot-A set, the RAM check and maintenance images, manifests) and the directory's `SHA256SUMS` |
| `piano-ubuntu-<version>-tools.zip` | `tools/` (`piano-switch.py`, the KernelSU module) |
| `piano-ubuntu-<version>-packages.zip` | `debs/` (the upgrade package, see [UPGRADE.en.md](UPGRADE.en.md)) |
| `piano-ubuntu-<version>-rootfs.img.zst.part0`, `.part1`, … | `rootfs/root.sparse.img`, zstd-compressed and split below 2 GiB |
| `SHA256SUMS.assets` | hashes of the assets above |

```bash
python3 tools/package-release-assets.py --release out/release-<version> --output out/release-<version>-assets
python3 tools/package-release-assets.py verify --assets out/release-<version>-assets --output out/release-<version>-verify   # restores and checks the directory
git tag -a <version> -m "<version>"; git push origin <version>
gh release create <version> --title "Ubuntu for Xiaomi Pad 8 Pro <version>" \
  --notes-file out/release-<version>/RELEASE-NOTES.md out/release-<version>-assets/*
```

Users restore the directory with the commands in the release notes and verify it with
`sha256sum -c SHA256SUMS`. Nothing else is uploaded: the packages are inside `packages.zip`, and
the BusyBox source for the maintenance image is referenced by its Ubuntu source package in the
notes. Tag the kernel repository with the same version at the commit recorded in
`kernel/source.json`.
