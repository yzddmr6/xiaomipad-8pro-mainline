# Building

[中文](BUILD.md) | [Project overview](../README.en.md) | [Release artefacts](RELEASE.md)

How the release is built from pinned inputs: kernel → installable build record → kernel package → device and
firmware packages → boot image set → root image → release directory. The root-image chroot backend
requires root; the other tools run unprivileged. No build tool accesses a tablet. A successful build is not a hardware result. Each step's expected output and
the identities to record are in [AGENT-GUIDE.en.md](AGENT-GUIDE.en.md) §3.

## Host

Linux x86_64 for the guest backend. The chroot backend runs as root, on native AArch64 or on x86_64
with a qemu-aarch64 binfmt_misc handler that has the F flag (Ubuntu's `qemu-user-static` registers one).
Commands run from the root of this repository.

| Stage | Tools |
|---|---|
| Kernel | Git, Python 3, GCC, an AArch64 GNU cross compiler, make, bc, bison, flex, pkg-config, OpenSSL and libelf development files, gzip, cpio, kmod (`depmod`), flock; ccache optional |
| Boot DTB and boot set | `device-tree-compiler` (`fdtget`, `fdtput`), `python3-libfdt`, AOSP libufdt `ufdt_apply_overlay`, AOSP `avbtool.py` ([lock](../tools/aosp-avb.json)), `openssl` |
| Packages | `dpkg-deb`, `python3-debian`, `g++-13-aarch64-linux-gnu`, `nlohmann-json3-dev`, `qemu-user-static`, `m4`, `alsatplg` |
| Mesa | Meson 1.9, Ninja, Mako, PyYAML, Python packaging, Bison, Flex, a native `glslangValidator`, an ARM64 GCC/G++ cross toolchain, and a Meson cross file with an explicit ARM64 sysroot (libdrm, zlib, zstd, expat, Wayland 1.24 and wayland-protocols, X11/XCB development packages from Ubuntu 26.04) |
| Ubuntu base | `7z`, `unsquashfs`, squashfs-tools-ng 1.2 (`sqfs2tar`), `apt`, `python3-apt` |
| Root image and release | e2fsprogs 1.47.4 built with libarchive and NLS (`mke2fs`, `e2fsck`, `debugfs`, `dumpe2fs`), `img2simg`, `sgdisk`, `zip`, QEMU `qemu-system-aarch64` |

```bash
sh tools/fetch-aosp-mkbootimg.sh       # pinned checkout in tools/local/aosp-mkbootimg
sh tools/fetch-aosp-avb.sh                                # verify the pinned commit, SHA-256 and version
sh tools/fetch-busybox-arm64.sh        # expect ".../usr/bin/busybox: OK"
```

## Pinned inputs

| Input | Pin |
|---|---|
| Kernel | [linux-sm8750-piano](https://github.com/yzddmr6/linux-sm8750-piano), branch `piano/dev`; commit and configuration fragments in [kernel/source.json](../kernel/source.json), releases in [kernel/abi.json](../kernel/abi.json) |
| Firmware | [firmware/](../firmware/README.md), locked by `firmware/piano-*.json` |
| Mesa | 26.2.6 and `mesa/piano-fixes.patch`, [mesa/source.json](../mesa/source.json) |
| Ubuntu | 26.04 Desktop ARM64 ISO, SHA-256 `c2afd538d66fdd77377d03f1ed2ac76a34f1c116baecc9a8170d68f833121f57`; [ubuntu/26.04.sources](../ubuntu/26.04.sources); packages from a dated [snapshot](https://snapshot.ubuntu.com/) |
| Device userspace | `device/*/source.json` |

Private build inputs come from your own tablet: the stock base DTB and DTBO entry,
the boot profile and the signing key ([INSTALL.en.md](INSTALL.en.md#3-collect-your-units-files)).

## 1. Kernel

```bash
git clone --branch piano/dev https://github.com/yzddmr6/linux-sm8750-piano ../linux
git -C ../linux checkout "$(python3 -c 'import json; print(json.load(open("kernel/source.json"))["commit"])')"
BUILD_MODULES=1 KERNEL_SOURCE=../linux OUT_DIR="$PWD/out/kernel-release" bash tools/build-kernel.sh
# expect "Piano kernel build complete: .../out/kernel-release/build-result.json (boot packaging is separate)"
```

- `build-kernel.sh` refuses a HEAD different from `kernel/source.json` or uncommitted tracked
  kernel changes. It always merges `release_config_fragments` in order. `CONFIG_FRAGMENT_EXTRA`
  appends tracked kernel files; duplicate paths are merged once at their first occurrence.
- Append `--config-only` to the command above to generate only `.config`. It does not compile
  Image, DTBs or modules and does not produce a packageable `build-result.json`.
- The upstream version comes from the kernel's top-level Makefile and its tag must be an ancestor
  of HEAD. `upstream_tag` and `kernel_release` in `kernel/source.json` give the release expectations.
- Reuse OUT for incremental builds; a different toolchain or source location needs a new OUT.
  ccache is used when installed.
- Product builds use `BUILD_MODULES=1` and ship matching modules. Boot-critical dependencies must
  be built in or fully available in the distribution initrd.
- The packager requires the `build-result.json` fragment list and order to match
  `release_config_fragments` exactly, as well as the ABI ledger's commit and final `.config` hash.
  Missing, additional or reordered fragments are rejected.

## 2. Installable build record

Rebuilding a release leaves `kernel/source.json` and `kernel/abi.json` unchanged.
ABI allocation and pin updates for all installable packaged builds, including candidates, are in
the [maintainer packaging procedure](RELEASE.md#installable-kernel-packaging-maintainers).

### Local kernel development

Use a separate kernel checkout and output directory with the kernel's Kbuild workflow for local
changes; do not edit release locks to run an experiment. Start from `defconfig`, merge
`release_config_fragments` from `kernel/source.json` in order with
`scripts/kconfig/merge_config.sh`, append any local fragments, and run `olddefconfig`.
Use `scripts/config` to set `CONFIG_LOCALVERSION` to a distinct development suffix such as
`-piano-dev`. These builds are for local development and do not use the release packager;
maintainers register any installable package, including a candidate, through the packaging procedure above.

```bash
DEV_SRC=/path/to/your/kernel-checkout
DEV_OUT="$PWD/out/kernel-local"
mkdir -p "$DEV_OUT"
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
make -C "$DEV_SRC" O="$DEV_OUT" defconfig
mapfile -t DEV_FRAGMENTS < <(python3 - "$DEV_SRC" <<'CONFIG_PATHS'
import json, pathlib, sys
for name in json.load(open('kernel/source.json'))['release_config_fragments']:
    print(pathlib.Path(sys.argv[1]) / name)
CONFIG_PATHS
)
"$DEV_SRC/scripts/kconfig/merge_config.sh" -m -O "$DEV_OUT" "$DEV_OUT/.config" "${DEV_FRAGMENTS[@]}"
"$DEV_SRC/scripts/config" --file "$DEV_OUT/.config" --set-str LOCALVERSION -piano-dev --disable LOCALVERSION_AUTO
make -C "$DEV_SRC" O="$DEV_OUT" LOCALVERSION= olddefconfig
make -C "$DEV_SRC" O="$DEV_OUT" LOCALVERSION= -j"$(nproc)" Image dtbs modules
```

## 3. Boot DTB

ABL applies the stock DTBO to the boot DTB. `prepare-boot-dtb.py` prepares the desktop DTB against
your stock base DTB (from `vendor_boot_a`) and DTBO entry (from `dtbo_a`); extracting these two from
the backup is manual.

```bash
python3 tools/prepare-boot-dtb.py \
  --mainline-dtb out/kernel-release/arch/arm64/boot/dts/qcom/sm8750-xiaomi-piano-desktop.dtb \
  --stock-base <stock base DTB> --stock-overlay <stock DTBO entry> \
  --ufdt <ufdt_apply_overlay> --output out/boot-dtb
```

Prepare a new one whenever the desktop DTB changes.

## 4. Kernel package

```bash
python3 tools/build-kernel-deb.py --kernel-source ../linux --kernel-out out/kernel-release \
  --boot-dtb out/boot-dtb/piano-boot.dtb --maintainer 'Name <email>' --output out/kernel-deb-<N>
# expect JSON with "package": "linux-image-<release>", "deb" and "deb_sha256"
```

The package holds the kernel, all DTBs, `piano-boot.dtb` and the matching modules. Its maintainer
scripts run Ubuntu's dracut hook and the piano boot hook. Identical inputs give a byte-identical deb.
Read `<release>` from `kernel_release` in `kernel/source.json`; use the output `manifest.json`
for the deb filename and package version.

## 5. Device and firmware packages

```bash
# touch processing (piano-thp-live): ARM64, dynamic glibc, static C++ runtime, PIE, BIND_NOW
mkdir -p out/json-include && cp -r /usr/include/nlohmann out/json-include/
make -C device/piano-thp OUT="$PWD/out/piano-thp-arm64" CXX=aarch64-linux-gnu-g++-13 \
  JSON_INCLUDE="$PWD/out/json-include" LDFLAGS='-static-libstdc++ -static-libgcc -Wl,-z,relro,-z,now' \
  all test RUNNER='qemu-aarch64-static -L /usr/aarch64-linux-gnu'
# expect three "PASS:" lines from the unit tests run under qemu-user
# Mesa 26.2.6 with the A830 patch
MESA_SOURCE_ARCHIVE=<official Mesa archive> CROSS_FILE=<arm64 cross file> TARGET_ROOT=<arm64 SDK> \
  OUT_DIR="$PWD/out/mesa" bash tools/build-mesa.sh
# piano-device-support, piano-mesa, piano-ubuntu-desktop
python3 tools/build-device-debs.py --output out/device-debs --version 0.1+<YYYYMMDD>.<n> \
  --thp-live out/piano-thp-arm64/piano-thp-live --mesa-build out/mesa \
  --busybox tools/local/busybox-arm64/usr/bin/busybox --avbtool tools/local/aosp-avb/avbtool.py
# capture topology (AudioReach checkout at device/audio/source.json), then piano-local-firmware
mkdir -p out/audio
m4 -I <audioreach-topology checkout> device/audio/Piano-Capture-Diag.m4 > out/audio/Piano-Capture-Diag.conf
alsatplg -c out/audio/Piano-Capture-Diag.conf -o out/audio/Piano-Capture-Diag-tplg.bin
m4 -I device/audio -I <audioreach-topology checkout> device/audio/Xiaomi-Pad-8-Pro.m4 > out/audio/Xiaomi-Pad-8-Pro.conf
alsatplg -c out/audio/Xiaomi-Pad-8-Pro.conf -o out/audio/Xiaomi-Pad-8-Pro-tplg.bin
python3 tools/build-firmware-deb.py --topology out/audio/Piano-Capture-Diag-tplg.bin \
  --card-topology out/audio/Xiaomi-Pad-8-Pro-tplg.bin \
  --maintainer 'Name <email>' --output out/firmware-deb
```

- `piano-thp-live` toolchain: Ubuntu 24.04 `g++-13-aarch64-linux-gnu` and `gcc-13-aarch64-linux-gnu`
  13.3.0-6ubuntu2~24.04.1cross1, `libstdc++-13-dev-arm64-cross` of the same version,
  `binutils-aarch64-linux-gnu` 2.42, `libc6-dev-arm64-cross` 2.39, `nlohmann-json3-dev` 3.11.3 and
  `qemu-user-static` for the tests. With an unpacked rather than installed cross g++, put its path in
  `CXX` followed by `-B/usr/lib/gcc-cross/aarch64-linux-gnu/13/ -B/usr/aarch64-linux-gnu/bin/
  -B/usr/aarch64-linux-gnu/lib/ -isystem /usr/aarch64-linux-gnu/include`. Check the binary:

  ```bash
  B=out/piano-thp-arm64/piano-thp-live
  readelf -d "$B" | grep -E 'NEEDED|FLAGS'  # only libm.so.6, libc.so.6, ld-linux-aarch64.so.1; BIND_NOW; NOW PIE
  objdump -T "$B" | grep -oE 'GLIBC_[0-9.]+' | sort -uV | tail -1  # at most GLIBC_2.38; the target glibc must be newer or equal
  strings "$B" | grep -c "$PWD"             # 0: no build-host path in the binary
  ```

  The v0.6.1 `piano-thp-live` (SHA-256 `a6d6c50b670333740b4e4cd3a0fd03d469158c00d462fb4ac69321c77b9400ba`) was built this way, with the unpacked g++, and
  rebuilds byte-identically from tag v0.6.1.
- `build-mesa.sh` calls the shared `build-mesa.py` recipe and verifies the official archive against `mesa/source.json`. Mesa installs under
  `/opt/piano/mesa-26.2.6` and does not replace Ubuntu's Mesa packages.
- `build-firmware-deb.py` takes `firmware/` by default; `--firmware-tree <dir>` uses your own
  extraction in the same layout. Every file is checked against its lock.
- Raise `--version` on every device package rebuild so that dpkg installs it as an upgrade.

For 1.2.0, build the device support and desktop integration packages with `--version 1.2.0.1 --release-version 1.2.0`.

## 6. Boot image set

`build-persistent-boot.py` builds the signed v4 `boot`, `init_boot` and `vendor_boot` images for
slot A from a root's kernel, its dracut initrd and `piano-boot.dtb`, plus `boot-ram-check.img` for
`fastboot boot`. On an installed tablet `piano-boot-update` runs it after each kernel update.

```bash
python3 tools/build-persistent-boot.py \
  --kernel <root>/boot/vmlinuz-<release> --kernel-release <release> \
  --dtb <root>/usr/lib/linux-image-<release>/piano-boot.dtb --initrd <root>/boot/initrd.img-<release> \
  --profile <boot-profile.json> --root-partuuid <linux_rootfs PARTUUID> --bt-address-file <bluetooth-address> \
  --mkbootimg tools/local/aosp-mkbootimg/mkbootimg.py --avbtool tools/local/aosp-avb/avbtool.py --key <boot-signing.pem> \
  --output out/ubuntu-set-<release>
```

- A set for your unit carries your addresses (`--bt-address-file`, `--wlan-address-file`) and serial.
- The release set is unit-independent: `--root-partlabel linux_rootfs --no-serial` and no address
  files. `build-rootfs-image.py` builds it in step 7.
- `--debug` and the other debug options add an unauthenticated root shell over USB; never write a
  debug set to slot A for normal use ([AGENT-GUIDE.en.md](AGENT-GUIDE.en.md) §3.6, §7.2).
- `--maintenance` builds only `boot-ram-maintenance.img`, the RAM image for writing the partition
  table, from a maintenance initramfs (`tools/prepare-maintenance-payload.py` and
  `build-initramfs.sh` with `MAINTENANCE_DIR`); the commands are in [AGENT-GUIDE.en.md](AGENT-GUIDE.en.md) §4.6.

## 7. Ubuntu base and root image

Extract the two official Desktop layers and merge them into a pristine TAR and SquashFS, without
root:

```bash
UBUNTU_ISO=<ubuntu-26.04-desktop-arm64.iso> OUT_DIR="$PWD/out/ubuntu-desktop-layers" \
  bash tools/prepare-ubuntu-desktop.sh
mkdir -p out/desktop-tars
sqfs2tar --root-becomes . --no-skip out/ubuntu-desktop-layers/minimal.squashfs > out/desktop-tars/minimal.tar
sqfs2tar --root-becomes . --no-skip out/ubuntu-desktop-layers/minimal.standard.squashfs \
  > out/desktop-tars/minimal.standard.tar
python3 tools/merge-desktop-tars.py \
  --base out/desktop-tars/minimal.tar --desktop out/desktop-tars/minimal.standard.tar \
  --output out/ubuntu-desktop-pristine
```

Then fetch the Ubuntu packages added to the base from a signed snapshot, and build the 60 GiB root
image with the packages of steps 4 and 5. `build-rootfs-image.py` installs them in a disposable QEMU
ARM64 guest or, as root, in a chroot (the target never boots), builds the release boot set from the
image's initrd, checks the result offline and writes `root.sparse.img`; it takes about 30 minutes in the
guest and 20 in the chroot, and 11 with `--base-cache` when only the kernel changed (see
[Root-image backends](#root-image-backends)). The full commands are in
[RELEASE.md](RELEASE.md#building-a-release):

```bash
python3 tools/prepare-ubuntu-apt.py --rootfs out/ubuntu-desktop-pristine/ubuntu-desktop.squashfs \
  --snapshot <YYYYMMDDTHHMMSSZ> --output out/apt-release \
  --mozilla-firefox --download dracut device-tree-compiler openssh-server fake-hwclock systemd-zram-generator
```

Use the complete root-image command in [RELEASE.md](RELEASE.md#building-a-release); expect
`"state": "ROOT_IMAGE_BUILT_OFFLINE_CHECKS_PASS_NOT_BOOTED"` when it finishes.

Firefox is the unmodified ARM64 deb from [Mozilla's APT repository](https://support.mozilla.org/en-US/kb/install-firefox-linux),
so it can use the image's Mesa stack. `--mozilla-firefox` authenticates the key, archived signed index
and exact package version/hash in [ubuntu/firefox/source.json](../ubuntu/firefox/source.json); the image
retains the Mozilla source and priority 1000 for updates. The official preseeded Firefox snap and
snapd state remain untouched during construction. After first-boot seeding, the desktop package's
`piano-firefox-snap-remove.service` first checks that Mozilla Firefox is installed and checks
`/home/*/snap/firefox` and `/root/snap/firefox`. Existing data paths, including symlinks, retain
the snap and produce a journal message. Only when these paths are absent does it run
`snap remove firefox`; it marks completion after removal or confirmation that the snap is absent.
The service has a five-minute timeout. It does not delay the display manager. The dock uses `firefox.desktop`;
Thunderbird remains a snap. Snap removal and Firefox GPU rendering require a device test.

`fake-hwclock` is a dependency of the current piano-ubuntu-desktop package. Download
`systemd-zram-generator` only for a kernel with `CONFIG_ZRAM`: without the
module the boot waits 90 s for `/dev/zram0` (QEMU), and `build-rootfs-image.py`
refuses the mismatch. The guest also purges Ubuntu's generic kernels and `kdump-tools`, sets the
hostname `xiaomipad-8pro`, deletes the base's APT indexes and ends with `fstrim`
([RELEASE.md](RELEASE.md#what-the-image-contains)).

The boot profile is reduced to the stock-build fields before use. The image has no account, no
machine ID, no SSH host keys and no file of any unit; it is not byte-reproducible because dpkg and
dracut record times.

### Root-image backends

`build-rootfs-image.py --backend chroot` requires EUID 0 and runs the shared package steps in a
loop-mounted ext4, with private proc/sys/dev/pts binds and temporary run/tmp. Mount and network
namespaces keep build activity separate from the host; an exit trap unmounts in reverse order. On an
AArch64 host the target's programs run natively; elsewhere they run through a qemu-aarch64
binfmt_misc handler with the F flag, which also works inside the chroot. The host then discards the
image's free blocks after the steps, because qemu-user does not pass the FITRIM ioctl through.
Omitting `--backend` selects chroot as root on AArch64 or with such a handler, and guest otherwise;
`--backend guest --qemu …` selects the QEMU guest explicitly. Both emulators use
`max,pauth-impdef=on` as the CPU (for the chroot, `QEMU_CPU` overrides it): emulating QEMU's default
pointer authentication algorithm is several times slower, and Ubuntu's arm64 code authenticates
return addresses in nearly every function. The 60 GiB raw ext4 remains a sparse host file in all
cases. Run with `sudo`, the tool returns its outputs and the cache to the invoking user.

`--base-cache <dir>` keeps the image as it is before the project kernel is installed (the Desktop
base with the userspace packages, the distribution kernels removed), named by the SHA-256 of the
inputs it depends on: the SquashFS, the APT download, every package except the kernel, its loopback
module and the camera service, the image files and the build scripts. A later build with the same
inputs copies that base, installs only those three packages, then builds the boot set and checks the
image as usual; any other change builds and stores a new base. The filesystem UUID derives from the
same key, so cached and uncached builds of the same inputs pass the same checks with the same
packages.

## 8. Release directory

Optionally boot the image in QEMU with `check-rootfs-boot.py`, then assemble the release:

```bash
python3 tools/build-release.py --rootfs-build out/rootfs-<version> \
  --boot-check out/rootfs-<version>-boot/result.json \
  --maintenance out/maintenance-ram-<version> --changes packaging/release/changes/<version>.md \
  --forbid-file <your bluetooth-address> --forbid-file <your wlan-address> --forbid-file <your boot-profile.json> \
  --output out/release-<version>
(cd out/release-<version> && sha256sum -c SHA256SUMS)
```

First prepare the matching maintenance image and change list as in
[RELEASE.md](RELEASE.md#building-a-release). Omit `--boot-check` if the optional QEMU boot was not
run, and record that untested path in release validation.

`--forbid-file` scans every output for identifiers of your unit. The release must be on the same
filesystem as the root image build. Layout, contents and installation: [RELEASE.md](RELEASE.md) and
[INSTALL.en.md](INSTALL.en.md).

## Graphics for core24 snaps

`piano-snap-graphics` supplies one `piano-mesa-2404` content provider for installed
`gpu-2404` consumers. This gives those strict snaps the A830 Mesa without replacing
each application with a deb. The existing Mozilla Firefox deb policy is unchanged;
`mesa-2404` stays installed for rollback. The provider uses the desktop's exact
`mesa/source.json` commit and patch, compiled against the separately locked
`mesa/snap/core24-sdk.json` ABI and overlaid on the complete ARM64 mesa-2404 rev 1166
locked in `mesa/snap/mesa-2404.json`. This full content contract matters: consumers
run Canonical's gpu-2404 cleanup and omit libraries supplied by mesa-2404, including
non-Mesa X11/XML dependencies. A Mesa-only provider passed its own pixel test but
broke Thunderbird and App Center on the device. The packager now
rejects missing stock files or changes outside the Mesa overlay and snap metadata.
All stock wrapper/hooks, component metadata, runtime files and notices are retained.
The slot exports only `$SNAP`; the optional kernel-content directory is not a
second export on piano. Mesa uses release / `-O2` / `b_ndebug=true` / LTO off,
the optimization level Debian and Ubuntu use. GCC 13 at `-O3` miscompiles the
GLSL compiler's constant handling (`src/compiler/glsl/ir.cpp`) on ARM64: the
earlier assertion-enabled provider aborted in `ir_constant::is_value`, and with
assertions disabled mediump constants were silently wrong, so Flutter apps that
render with Impeller (App Center) drew no text.
Imported tooling provenance and its GPL-2.0-only licence are recorded in `mesa/snap/provenance.json`.

```bash
python3 tools/build-mesa-root.py --target gpu-2404 --native-nir \
  --base-snap <mesa-2404_1166.snap> \
  --source-archive <official Mesa archive> --cache out/gpu-snap-inputs \
  --work out/gpu-snap-build --output out/gpu-snap-package --jobs 6
python3 tools/validate-mesa-snap.py --build out/gpu-snap-package \
  --sdk-root out/gpu-snap-build/root --stock-provider <mesa-2404_1166.snap> \
  --base out/gpu-snap-inputs/ubuntu-base-24.04.4-base-arm64.tar.gz \
  --output out/gpu-snap-validation --core24 <frozen-core24-arm64.snap> \
  --consumer <thunderbird_1045.snap> --consumer <snap-store_1368.snap> \
  --gnome-runtime <gnome-46-2404_154.snap>
python3 tools/build-device-debs.py --snap-graphics-only \
  --snap-graphics-build out/gpu-snap-package --output out/gpu-snap-deb \
  --version 0.1+<YYYYMMDD>.<n>
```

Obtain the locked ARM64 provider with `snap download mesa-2404 --revision=1166`
on ARM64, or extract `var/lib/snapd/seed/snaps/mesa-2404_1166.snap` from the frozen
Desktop SquashFS; always check the lock's SHA-256. For a packaging-only correction,
`tools/package-mesa-snap.py --reuse-mesa-build <previous-build> --base-snap <snap>
--output <new-output>` reuses only Mesa files verified against the previous build
and snap receipts, preserving the same source/patch without recompilation. The
stock component monitor and hooks remain upstream; the install hook disables
that monitor when its optional kernel content plug is disconnected.

Packaging/validation need Python 3, PyYAML and squashfs-tools. The build needs
qemu-user ARM64 binfmt and unprivileged user namespaces; native NIR generation needs the SDK Python minor version (3.12), Mako, MarkupSafe and
Ninja on the host (`--ninja /path/to/ninja` if it is not on PATH). By default, target compilation runs in the pinned ARM64 SDK.

With an ARM64 cross-toolchain, replace `--native-nir` above with
`--cross-file <arm64-core24.ini>` to run generators, compilation and packaging
on the build host. The standard Meson cross file must point its sysroot,
target pkg-config directories, C/C++ headers and library paths at `root/`
under this build's `--work`, rather than a desktop Ubuntu sysroot. The host
needs the compiler, Mako, PyYAML, Ninja, native wayland-scanner and
glslangValidator; the builder reuses the pinned Meson wheel. QEMU remains
necessary for SDK preparation. The build records the cross-file SHA-256 and
target package versions and checks that Meson configured an ARM64 target.
Do not combine this option with `--native-nir`. Both paths use the same Mesa
source, patch and profile options, with core24 target headers and libraries.

`--resume`
continues that attempt's frozen recipe, without changing its inputs. The offline
pixel check explicitly requires softpipe and is not a GPU or sandbox test.
Validation uses the actual seeded core24 runtime, checks every provider ELF,
and can check Thunderbird/App Center binaries plus their dlopened graphics/proxy
libraries with the GNOME runtime. `--old-provider <previous.snap>` first reproduces
the missing-library failure. These checks do not launch the GUI or prove confinement.

Also run the GLSL regression against the final snap. It compiles a shader with
integer constants and nested min/max, and a mediump shader whose constant
comparison must fold correctly, through the provider's mapped libgallium, then
checks the rendered pixels. `--old-provider` is optional; when supplied it
must reproduce exit 134 at `ir.cpp:1603`, while the new provider must compile,
link and draw without an assertion. Shader caching is disabled. Only the small
probe is built; neither Mesa nor the provider snap is rebuilt:

```bash
python3 tools/test-snap-graphics-glsl.py \
  --provider <new-piano-mesa-2404.snap> --old-provider <previous-provider.snap> \
  --core24 <frozen-core24-arm64.snap> --sdk-root out/gpu-snap-build/root \
  --output out/gpu-snap-glsl
```

This is a softpipe/qemu-user regression, not application, confinement or A830
acceptance. A provider built at `-O3` with GCC 13 fails the mediump case.
`--keep-build` on the Mesa
builder retains unstripped objects for later crash analysis.

Pass the resulting deb as one additional `--deb` to `build-rootfs-image.py` along
with the existing device packages. Its postinst only enables the unit. On the
first boot, `piano-snap-graphics.service` waits for `snapd.seeded.service`, installs
the local snap with `--dangerous`, then processes each installed `gpu-2404` plug:
disconnect previous slots first, connect piano if needed, then run
`/usr/lib/snapd/snap-discard-ns` for that consumer. Content connections are additive;
the previous connect-first order could leave a stale `gpu-2404-2` mount. Discard
also runs for an already connected consumer so its next launch recreates the
namespace with one content mount. The previous provider snaps stay installed.
It records completion only after success, has a five-minute bound and no retries.
After installing a new snap, close affected applications, run
`sudo piano-snap-graphics-connect`, then reopen them. Consumer refreshes retain manual connections;
new consumers are not automatically intercepted. When consumers move to
`gpu-2604`/core26, rebuild a provider for that ABI/interface before connecting them;
this core24 provider does not serve `gpu-2604`. Retire it when the distribution
provider supports A830 and passes the same hardware/confinement checks.

`piano-snap-graphics` depends only on Python, snapd and systemd integration;
it does not depend on a board kernel package or native desktop Mesa. It supplies
an ARM64/core24 runtime through the standard `gpu-2404` interface. Its package
name is not a device-model check. With the provider already installed, close
Firefox and run `sudo piano-snap-graphics-connect --consumer firefox` to switch
only that consumer. Repeat the option to select more consumers. It cannot be
combined with first-boot `--install` and does not mark first boot complete.
Omitting `--consumer` retains the existing behavior for all installed consumers.

Graphics content does not enable strict confinement. Neither `strict` snap
metadata nor an Adreno renderer replaces `strict` from `snap debug confinement`,
`support-level:full` from `snap debug sandbox-features`, and actual access denial.
When mainline `network_v9` does not satisfy snapd's legacy `network` assessment,
confinement remains partial. Using `--devmode` or disabling AppArmor does not
satisfy strict confinement.

## Bounded RAM return regression

`piano-debug-timeout.service` uses the kernel-uptime `piano.debug_timeout`
deadline (600 seconds for RAM hardware checks). Its helper stops the display
manager and user slice, syncs, and requests a normal systemd reboot with
`--reboot-argument=bootloader`. PID1 stops services and systemd-shutdown
unmounts/remounts the ext4 root read-only. The host still waits for Fastboot
and performs its existing recovery; capture and image-size limits are unchanged.

A child armed before any blocking shutdown work forces the same bootloader
reboot after 45 seconds. `DefaultDependencies=no` and
`SurviveFinalKillSignal=yes` (systemd >=255; Ubuntu 26.04 supplies 259) keep it
alive through shutdown. Explicitly stopping the timeout unit still cancels its
whole control group. A forced fallback can leave a dirty journal if filesystem
shutdown was blocked. The clean path must finish before that fallback.

New `build-native-display-initrd.py` builds refresh the helper/unit from this
source and carry them through `/run` into the installed root; they do not depend
on that root already containing the fix. Existing frozen RAM images stay unchanged.

Run the ARM64 exit/journal regression on the host, with a clean disposable-input
Ubuntu ext4 image and a kernel with built-in VirtIO block/ext4 support:

```sh
python3 tools/test-debug-timeout-qemu.py \
  --kernel /path/to/Image --rootfs /path/to/ubuntu.ext4 \
  --qemu /path/to/qemu-system-aarch64 --busybox /path/to/arm64/busybox \
  --gen-init-cpio /path/to/linux-out/usr/gen_init_cpio \
  --debugfs /path/to/debugfs --e2fsck /path/to/e2fsck \
  --output /path/to/new-test-output
```

This uses the same virt/TCG, serial and `-no-reboot` exit method as the Ubuntu
handoff harness. It pivots from RAM into a writable ext4 copy and exercises real
Ubuntu PID1 with test display-manager/user-slice services. It retains guest disk
writes and asserts `e2fsck -n` reports clean **and** `needs_recovery` is clear;
it never replays the journal to make a test pass. It also checks the unchanged
`bootloader` reboot reason and both fixture stop markers. This is not a GDM,
Fastboot or tablet hardware test.

`--scenario old-force` is a negative control that must detect a dirty journal.
`--scenario blocked-stop` holds a stop job; `--scenario blocked-shutdown` holds
an executable systemd-shutdown hook after the final kill and filesystem cleanup.
Both must force a bootloader reboot at the 45-second bound; the latter must
still have a clean journal. `--seconds 60` can shorten these failure fixtures
without changing the production 600-second budget.
