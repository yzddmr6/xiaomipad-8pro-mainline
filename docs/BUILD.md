# 构建指南

[English](BUILD.en.md) | [项目概览](../README.md) | [发布产物](RELEASE.md)

本指南说明如何从固定输入构建发布产物：内核 → 可安装构建记录 → 内核包 → 设备包与固件包 →
启动镜像组 → 根镜像 → 发布目录。根镜像 chroot 后端需要 root，其余工具以普通用户运行。
构建工具不访问平板；构建成功不代表硬件验证通过。各阶段预期输出及需记录的身份见
[代理与操作者指南](AGENT-GUIDE.md) §3。

## 主机前提

guest 后端使用 Linux x86_64 主机。chroot 后端以 root 运行，主机为原生 AArch64，或注册了带 F 标志的
qemu-aarch64 binfmt_misc 处理程序的 x86_64（Ubuntu 的 `qemu-user-static` 会注册）。
以下命令均在本仓库根目录执行。`<...>` 为需要替换的输入占位符。

| 阶段 | 工具 |
|---|---|
| 内核 | Git、Python 3、GCC、AArch64 GNU 交叉编译器、make、bc、bison、flex、pkg-config、OpenSSL 和 libelf 开发文件、gzip、cpio、kmod（`depmod`）、flock；ccache 可选 |
| 启动 DTB 与镜像组 | `device-tree-compiler`（`fdtget`、`fdtput`）、`python3-libfdt`、AOSP libufdt 的 `ufdt_apply_overlay`、AOSP `avbtool.py`（[版本锁](../tools/aosp-avb.json)）、`openssl` |
| 软件包 | `dpkg-deb`、`python3-debian`、`g++-13-aarch64-linux-gnu`、`nlohmann-json3-dev`、`qemu-user-static`、`m4`、`alsatplg` |
| Mesa | Meson 1.9、Ninja、Mako、PyYAML、Python packaging、Bison、Flex、原生 `glslangValidator`、ARM64 GCC/G++ 交叉工具链，以及指定 ARM64 sysroot 的 Meson cross file；sysroot 使用 Ubuntu 26.04 的 libdrm、zlib、zstd、expat、Wayland 1.24、wayland-protocols、X11/XCB 开发包 |
| Ubuntu 基础系统 | `7z`、`unsquashfs`、squashfs-tools-ng 1.2（`sqfs2tar`）、`apt`、`python3-apt` |
| 根镜像与发布 | 启用 libarchive 和 NLS 的 e2fsprogs 1.47.4（`mke2fs`、`e2fsck`、`debugfs`、`dumpe2fs`）、`img2simg`、`sgdisk`、`zip`、QEMU `qemu-system-aarch64` |

```bash
sh tools/fetch-aosp-mkbootimg.sh       # 固定版本检出到 tools/local/aosp-mkbootimg
sh tools/fetch-aosp-avb.sh                                # 校验固定提交、SHA-256 和版本
sh tools/fetch-busybox-arm64.sh        # 预期 ".../usr/bin/busybox: OK"
```

## 固定输入

| 输入 | 版本与身份锁 |
|---|---|
| 内核 | [linux-sm8750-piano](https://github.com/yzddmr6/linux-sm8750-piano)，分支 `piano/dev`；提交和配置片段见 [kernel/source.json](../kernel/source.json)，release 见 [kernel/abi.json](../kernel/abi.json) |
| 固件 | [firmware/](../firmware/README.md)，由 `firmware/piano-*.json` 锁定 |
| Mesa | 26.2.6 与 `mesa/piano-fixes.patch`，见 [mesa/source.json](../mesa/source.json) |
| Ubuntu | 26.04 Desktop ARM64 ISO，SHA-256 `c2afd538d66fdd77377d03f1ed2ac76a34f1c116baecc9a8170d68f833121f57`；[ubuntu/26.04.sources](../ubuntu/26.04.sources)；软件包取自指定日期的[快照](https://snapshot.ubuntu.com/) |
| 设备用户态 | `device/*/source.json` |

原厂基础 DTB、DTBO 条目、boot profile 和签名密钥由你提供，隐私边界见 [AGENTS.md](../AGENTS.md)。
原厂输入须来自自己的平板，采集方法见[安装指南](INSTALL.md)。

## 1. 内核

```bash
git clone --branch piano/dev https://github.com/yzddmr6/linux-sm8750-piano ../linux
git -C ../linux checkout "$(python3 -c 'import json; print(json.load(open("kernel/source.json"))["commit"])')"
BUILD_MODULES=1 KERNEL_SOURCE=../linux OUT_DIR="$PWD/out/kernel-release" bash tools/build-kernel.sh
# 预期 "Piano kernel build complete: .../out/kernel-release/build-result.json (boot packaging is separate)"
```

- `build-kernel.sh` 拒绝 HEAD 与 `kernel/source.json` 不符或存在未提交跟踪改动的内核树。
  始终按顺序合并 `release_config_fragments`；`CONFIG_FRAGMENT_EXTRA` 仅供追加片段，路径去重后
  保留首次出现的位置。额外片段必须是内核仓库已跟踪的文件。
- 仅生成 `.config` 时，在上述命令末尾加 `--config-only`；不会编译 Image、DTB 或模块，也不生成
  可打包的 `build-result.json`。
- 上游版本取自内核顶层 Makefile，并核对对应 tag 为 HEAD 的祖先；`kernel/source.json` 的
  `upstream_tag` 和 `kernel_release` 给出发布预期值。
- 增量构建复用 OUT 目录；更换工具链或源码位置时使用新的 OUT。已安装 ccache 时自动使用。
- 产品构建使用 `BUILD_MODULES=1`，交付匹配模块；启动必需依赖须内建或完整纳入发行版 initrd。
- 打包器要求 `build-result.json` 的片段列表及顺序与 `release_config_fragments` 完全一致，并继续
  校验 ABI 记录中的内核提交和最终 `.config` 哈希；缺少、增加或重排片段的构建均拒绝打包。

## 2. 可安装构建记录

复现发布版本时保留 `kernel/source.json` 和 `kernel/abi.json` 原样。
所有可安装打包构建（含候选）的 ABI 分配与 pin 更新见 [RELEASE.md 的维护者打包流程](RELEASE.md#installable-kernel-packaging-maintainers)。

### 本地内核开发

本地修改使用独立内核检出和输出目录，按内核的 Kbuild 流程构建；不要为了试验改发布锁。
从 `defconfig` 开始，用 `scripts/kconfig/merge_config.sh` 按 `kernel/source.json` 的
`release_config_fragments` 顺序合并，再按需追加本地片段，运行 `olddefconfig`。
用 `scripts/config` 将 `CONFIG_LOCALVERSION` 设为独立的开发后缀（如 `-piano-dev`）。
这种构建用于本地开发，不使用发布打包器；打成可安装包（含候选）时，由维护者执行上述登记流程。

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

## 3. 启动 DTB

ABL 会把原厂 DTBO 叠加到启动 DTB。`prepare-boot-dtb.py` 使用本机的原厂基础 DTB
（来自 `vendor_boot_a`）和 DTBO 条目（来自 `dtbo_a`）处理桌面 DTB；这两项需手工从备份中提取。

```bash
python3 tools/prepare-boot-dtb.py \
  --mainline-dtb out/kernel-release/arch/arm64/boot/dts/qcom/sm8750-xiaomi-piano-desktop.dtb \
  --stock-base <stock base DTB> --stock-overlay <stock DTBO entry> \
  --ufdt <ufdt_apply_overlay> --output out/boot-dtb
```

桌面 DTB 变化后须重新生成。

## 4. 内核包

```bash
python3 tools/build-kernel-deb.py --kernel-source ../linux --kernel-out out/kernel-release \
  --boot-dtb out/boot-dtb/piano-boot.dtb --maintainer 'Name <email>' --output out/kernel-deb-<N>
# 预期 JSON 包含 "package": "linux-image-<release>"、"deb" 和 "deb_sha256"
```

包内包含内核、全部 DTB、`piano-boot.dtb` 和匹配模块。维护脚本调用 Ubuntu 的 dracut 钩子及
piano 启动钩子。同一组输入生成逐字节相同的 deb。
`<release>` 从 `kernel/source.json` 的 `kernel_release` 读取；deb 文件名与包版本以输出的 `manifest.json` 为准。

## 5. 设备包与固件包

```bash
# 触控处理（piano-thp-live）：ARM64，动态链接 glibc、静态链接 C++ 运行库、PIE、BIND_NOW
mkdir -p out/json-include && cp -r /usr/include/nlohmann out/json-include/
make -C device/piano-thp OUT="$PWD/out/piano-thp-arm64" CXX=aarch64-linux-gnu-g++-13 \
  JSON_INCLUDE="$PWD/out/json-include" LDFLAGS='-static-libstdc++ -static-libgcc -Wl,-z,relro,-z,now' \
  all test RUNNER='qemu-aarch64-static -L /usr/aarch64-linux-gnu'
# 预期单元测试在 qemu-user 下输出三行 "PASS:"
# Mesa 26.2.6 与 A830 补丁
MESA_SOURCE_ARCHIVE=<official Mesa archive> CROSS_FILE=<arm64 cross file> TARGET_ROOT=<arm64 SDK> \
  OUT_DIR="$PWD/out/mesa" bash tools/build-mesa.sh
# piano-device-support、piano-mesa、piano-ubuntu-desktop
python3 tools/build-device-debs.py --output out/device-debs --version 0.1+<YYYYMMDD>.<n> \
  --thp-live out/piano-thp-arm64/piano-thp-live --mesa-build out/mesa \
  --busybox tools/local/busybox-arm64/usr/bin/busybox --avbtool tools/local/aosp-avb/avbtool.py
# 音频拓扑（AudioReach 检出到 device/audio/source.json 指定提交），然后构建 piano-local-firmware
mkdir -p out/audio
m4 -I <audioreach-topology checkout> device/audio/Piano-Capture-Diag.m4 > out/audio/Piano-Capture-Diag.conf
alsatplg -c out/audio/Piano-Capture-Diag.conf -o out/audio/Piano-Capture-Diag-tplg.bin
m4 -I device/audio -I <audioreach-topology checkout> device/audio/Xiaomi-Pad-8-Pro.m4 > out/audio/Xiaomi-Pad-8-Pro.conf
alsatplg -c out/audio/Xiaomi-Pad-8-Pro.conf -o out/audio/Xiaomi-Pad-8-Pro-tplg.bin
python3 tools/build-firmware-deb.py --topology out/audio/Piano-Capture-Diag-tplg.bin \
  --card-topology out/audio/Xiaomi-Pad-8-Pro-tplg.bin \
  --maintainer 'Name <email>' --output out/firmware-deb
```

- `piano-thp-live` 工具链：Ubuntu 24.04 的 `g++-13-aarch64-linux-gnu` 与 `gcc-13-aarch64-linux-gnu`
  13.3.0-6ubuntu2~24.04.1cross1、同版本 `libstdc++-13-dev-arm64-cross`、`binutils-aarch64-linux-gnu` 2.42、
  `libc6-dev-arm64-cross` 2.39、`nlohmann-json3-dev` 3.11.3，测试用 `qemu-user-static`。交叉 g++ 是解包而非安装时，
  `CXX` 写它的路径，后接 `-B/usr/lib/gcc-cross/aarch64-linux-gnu/13/ -B/usr/aarch64-linux-gnu/bin/
  -B/usr/aarch64-linux-gnu/lib/ -isystem /usr/aarch64-linux-gnu/include`。检查产物：

  ```bash
  B=out/piano-thp-arm64/piano-thp-live
  readelf -d "$B" | grep -E 'NEEDED|FLAGS'  # 只有 libm.so.6、libc.so.6、ld-linux-aarch64.so.1；BIND_NOW；NOW PIE
  objdump -T "$B" | grep -oE 'GLIBC_[0-9.]+' | sort -uV | tail -1  # 最高 GLIBC_2.38；目标系统 glibc 不得低于它
  strings "$B" | grep -c "$PWD"             # 0：二进制中没有构建机路径
  ```

  v0.6.1 的 `piano-thp-live`（SHA-256 `a6d6c50b670333740b4e4cd3a0fd03d469158c00d462fb4ac69321c77b9400ba`）按此方法用解包的 g++ 构建，可从标签 v0.6.1 逐字节重建。
- `build-mesa.sh` 调用共用的 `build-mesa.py`，核对 `mesa/source.json` 中的官方源码包 SHA-256。安装前缀为 `/opt/piano/mesa-26.2.6`，
  不替换 Ubuntu 的 Mesa 包。
- `build-firmware-deb.py` 默认从 `firmware/` 取文件；`--firmware-tree <dir>` 可指定相同布局的本机提取目录。
  每个文件均按身份锁核对。两份音频拓扑的构建与检查说明见[音频文档](../device/audio/README.md#build)。
- 每次重建设备包都提高 `--version`，使 dpkg 将其作为升级安装。

1.2.0 的设备支持包与桌面集成包使用 `--version 1.2.0.1 --release-version 1.2.0`，以更新已安装的同版候选。

## 6. 启动镜像组

`build-persistent-boot.py` 从目标根中的内核、dracut initrd 和 `piano-boot.dtb` 构建供 A 槽使用的
签名 v4 `boot`、`init_boot`、`vendor_boot`，以及供 `fastboot boot` 使用的 `boot-ram-check.img`。
已安装系统由 `piano-boot-update` 在每次内核更新后调用它。

```bash
python3 tools/build-persistent-boot.py \
  --kernel <root>/boot/vmlinuz-<release> --kernel-release <release> \
  --dtb <root>/usr/lib/linux-image-<release>/piano-boot.dtb --initrd <root>/boot/initrd.img-<release> \
  --profile <boot-profile.json> --root-partuuid <linux_rootfs PARTUUID> --bt-address-file <bluetooth-address> \
  --mkbootimg tools/local/aosp-mkbootimg/mkbootimg.py --avbtool tools/local/aosp-avb/avbtool.py --key <boot-signing.pem> \
  --output out/ubuntu-set-<release>
```

- 本机专用镜像组携带地址（`--bt-address-file`、`--wlan-address-file`）和序列号。
- 发布镜像组不绑定单机：使用 `--root-partlabel linux_rootfs --no-serial`，不传入地址文件。
  第 7 步的 `build-rootfs-image.py` 会生成该镜像组。
- `--debug` 等调试选项增加经 USB 提供的无认证 root shell；调试镜像组不得写入 A 槽作日常使用，
  见[代理与操作者指南](AGENT-GUIDE.md) §3.6、§7.2。
- `--maintenance` 从维护 initramfs 只生成 `boot-ram-maintenance.img`，用于分区表维护。
  维护载荷由 `tools/prepare-maintenance-payload.py` 与设置了 `MAINTENANCE_DIR` 的
  `build-initramfs.sh` 构建，命令见[代理与操作者指南](AGENT-GUIDE.md) §4.6。

## 7. Ubuntu 基础系统与根镜像

无需 root，提取官方 Desktop 的两个层并合并为原始 TAR 和 SquashFS：

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

从签名快照下载需额外安装的 Ubuntu 包，再用第 4、5 步的软件包构建 60 GiB 根镜像。
`build-rootfs-image.py` 在一次性 QEMU ARM64 guest 内，或以 root 在 chroot 中安装包，不启动目标系统；
它从镜像中的 initrd 构建发布启动镜像组，执行离线检查并输出 `root.sparse.img`。guest 约需 30 分钟，
chroot 约 20 分钟，只换内核且使用 `--base-cache` 时约 11 分钟（见下文“根镜像构建后端”）。完整参数见
[发布构建说明](RELEASE.md#building-a-release)，下面的根镜像命令以 `...` 省略工具路径及软件包输入：

```bash
python3 tools/prepare-ubuntu-apt.py --rootfs out/ubuntu-desktop-pristine/ubuntu-desktop.squashfs \
  --snapshot <YYYYMMDDTHHMMSSZ> --output out/apt-release \
  --mozilla-firefox --download dracut device-tree-compiler openssh-server fake-hwclock systemd-zram-generator
```

根镜像使用 [RELEASE.md](RELEASE.md#building-a-release) 中的完整命令；完成后预期
`"state": "ROOT_IMAGE_BUILT_OFFLINE_CHECKS_PASS_NOT_BOOTED"`。

Firefox 使用 [Mozilla APT 仓库](https://support.mozilla.org/en-US/kb/install-firefox-linux)中未修改的
ARM64 deb，以使用镜像的 Mesa 栈。`--mozilla-firefox` 按
[ubuntu/firefox/source.json](../ubuntu/firefox/source.json) 校验密钥、签名索引及包版本和哈希；
镜像保留 Mozilla 源和优先级 1000。构建时保留官方预置 Firefox snap 和 snapd 状态。
首次启动 seeding 完成后，桌面包的 `piano-firefox-snap-remove.service` 先确认 Mozilla Firefox 已安装，
并检查 `/home/*/snap/firefox` 和 `/root/snap/firefox`；存在用户数据路径（含符号链接）时保留 Snap 并记录日志。
没有这些路径时才用 `snap remove firefox` 移除，成功或确认未安装后留标记，超时为五分钟，不延迟显示管理器。Dock 使用 `firefox.desktop`；
Thunderbird 保持 snap。Snap 移除和 Firefox GPU 渲染需在设备上单独验证。

`fake-hwclock` 是当前桌面包的依赖。仅为包含 `CONFIG_ZRAM` 的内核下载
`systemd-zram-generator`；缺少模块时，启动会等待 `/dev/zram0` 90 秒，
`build-rootfs-image.py` 会拒绝这种不匹配。guest 还会移除 Ubuntu 通用内核与 `kdump-tools`，
设置主机名 `xiaomipad-8pro`，清理基础系统的 APT 索引，最后执行 `fstrim`，
见[镜像内容](RELEASE.md#what-the-image-contains)。

使用 boot profile 前只保留原厂构建字段。镜像不含账户、machine ID、SSH 主机密钥或逐机文件。
由于 dpkg 和 dracut 记录时间，根镜像不保证逐字节可复现。

### 根镜像构建后端

`build-rootfs-image.py --backend chroot` 要求 EUID 为 0，在 loop 挂载的 ext4 中执行共用安装步骤，
使用私有 proc/sys/dev/pts 绑定及临时 run/tmp。挂载和网络命名空间隔离构建活动，退出时按逆序卸载。
AArch64 主机上目标程序原生运行；其他主机经带 F 标志的 qemu-aarch64 binfmt_misc 处理程序运行，
chroot 内同样有效。由于 qemu-user 不转发 FITRIM ioctl，空闲块改由主机在安装步骤之后裁剪。
省略 `--backend` 时，以 root 在 AArch64 或有该处理程序的主机上选择 chroot，其他情况选择 guest；
`--backend guest --qemu …` 可显式选择 QEMU。两种模拟都以 `max,pauth-impdef=on` 作为 CPU
（chroot 可用 `QEMU_CPU` 覆盖）：QEMU 默认的指针认证算法模拟起来慢数倍，而 Ubuntu 的 arm64 代码
几乎每个函数都校验返回地址。60 GiB raw ext4 在主机上始终保持为稀疏文件。以 `sudo` 运行时，
工具会把输出和缓存交还给调用者。

`--base-cache <目录>` 保存安装项目内核之前的镜像（Desktop 基础、用户态软件包、已移除发行版内核），
以其依赖输入的 SHA-256 命名：SquashFS、APT 下载、除内核及其 loopback 模块和相机服务外的全部软件包、
镜像文件和构建脚本。之后输入相同的构建会复制该基础，只安装这三个包，再照常构建启动镜像组并检查镜像；
其他任何变化都会构建并保存新的基础。文件系统 UUID 由同一键派生，因此相同输入的缓存与非缓存构建
通过同样的检查、软件包相同。

## 8. 发布目录

可先用 `check-rootfs-boot.py` 在 QEMU 中检查根镜像启动。按第 6 步和
[发布构建说明](RELEASE.md#building-a-release)准备同一内核的维护 RAM 镜像，使用
`--maintenance --no-serial` 且不传入地址文件；然后组装发布目录：

```bash
python3 tools/build-release.py --rootfs-build out/rootfs-<version> \
  --boot-check out/rootfs-<version>-boot/result.json --maintenance out/maintenance-ram-<version> \
  --forbid-file <your bluetooth-address> --forbid-file <your wlan-address> --forbid-file <your boot-profile.json> \
  --output out/release-<version>
(cd out/release-<version> && sha256sum -c SHA256SUMS)
```

`--maintenance` 是必需输入；未执行可选 QEMU 启动检查时省略 `--boot-check`。
先按 [RELEASE.md](RELEASE.md#building-a-release) 生成同一内核的维护镜像和变更说明。
若未执行可选的 QEMU 启动检查，省略 `--boot-check`，并按发布验证要求记录未测项。

`--forbid-file` 在全部输出中扫描本机标识。发布目录须与根镜像构建目录处于同一文件系统。
目录布局、内容与安装步骤见[发布说明](RELEASE.md)和[安装指南](INSTALL.md)。

## core24 snap 图形支持

`piano-snap-graphics` 为已安装的 `gpu-2404` 消费者提供 `piano-mesa-2404` content provider，
使其使用 A830 Mesa。Mozilla Firefox deb 策略保持不变，`mesa-2404` 保留供回滚。
provider 使用与桌面相同的 `mesa/source.json` 提交及补丁，针对
`mesa/snap/core24-sdk.json` 锁定的 ABI 编译，并叠加到 `mesa/snap/mesa-2404.json` 锁定的完整
ARM64 provider 上。消费者可能省略由 provider 提供的非 Mesa X11/XML 库，因此必须保留完整内容约定。
打包器拒绝缺失的原始文件及 Mesa overlay、snap 元数据之外的修改；保留上游 wrapper、hook、
组件元数据、运行时文件与许可声明。slot 仅导出 `$SNAP`。
Mesa 使用 release、`-O2`（与 Debian、Ubuntu 相同）、`b_ndebug=true`、关闭 LTO；来源与 GPL-2.0-only 许可见 `mesa/snap/provenance.json`。
GCC 13 以 `-O3` 编译 ARM64 时会误编译 GLSL 编译器的常量处理（`src/compiler/glsl/ir.cpp`）：早先开启断言的
provider 在 `ir_constant::is_value` 中止；关闭断言后 mediump 常量出错而不报错，使用 Impeller 渲染的
Flutter 应用（应用中心）因此不显示文字。

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

在 ARM64 上用 `snap download mesa-2404 --revision=1166` 获取固定 provider，或从锁定的
Desktop SquashFS 提取 `var/lib/snapd/seed/snaps/mesa-2404_1166.snap`，并核对身份锁的 SHA-256。
仅修正打包时，可用 `tools/package-mesa-snap.py --reuse-mesa-build <previous-build> --base-snap <snap>
--output <new-output>` 复用经构建和 snap 收据核验的 Mesa 文件。

打包与验证需要 Python 3、PyYAML、squashfs-tools。构建需要 qemu-user ARM64 binfmt 和非特权
用户命名空间；原生 NIR 生成需要与 SDK 相同的小版本 Python（3.12）、Mako、MarkupSafe、Ninja。
Ninja 不在 PATH 时用 `--ninja /path/to/ninja` 指定。默认的目标编译在固定 ARM64 SDK 内进行。

有 ARM64 交叉工具链时，建议将上述 `--native-nir` 换为 `--cross-file <arm64-core24.ini>`。
使用标准 Meson cross file，将 `sys_root`、目标 pkg-config 搜索目录、C/C++ 头文件及链接目录
指向本次 `--work` 下的 `root/`，不要引用桌面 Ubuntu 的 sysroot。主机需有编译器、Mako、
PyYAML、Ninja、原生 wayland-scanner 和 glslangValidator；构建器复用锁定的 Meson wheel。
此方式仅在准备 SDK 时使用 QEMU，生成器、编译和打包在主机执行；目标仍使用固定 core24 的
头文件与库。构建记录包含 cross file 的 SHA-256 和目标包版本，配置后核对 ARM64 目标。
不要同时传 `--native-nir`；两条路径使用相同的 Mesa 源码、补丁和 profile 选项。

`--resume` 沿用原构建配方和输入。离线像素检查要求 softpipe；验证器检查实际 core24 运行时、
provider ELF，以及可选的 Thunderbird/App Center 和 GNOME 运行库，不替代 GUI、GPU 或沙箱验证。

对最终 snap 执行 GLSL 回归，检查含整数常量及嵌套 min/max 的 shader，以及常量比较须正确折叠的 mediump
shader，能编译、链接并绘制正确像素。
`--old-provider` 可选，用于复现断言失败；检查关闭 shader 缓存，只构建小型 probe，不重建 Mesa 或 snap：

```bash
python3 tools/test-snap-graphics-glsl.py \
  --provider <new-piano-mesa-2404.snap> --old-provider <previous-provider.snap> \
  --core24 <frozen-core24-arm64.snap> --sdk-root out/gpu-snap-build/root \
  --output out/gpu-snap-glsl
```

这是 softpipe/qemu-user 回归，不代表 A830 或应用验收；GCC 13 以 `-O3` 构建的 provider 会在 mediump 用例失败。
Mesa 构建器的 `--keep-build` 可保留未 strip 的对象供排查。

将所得 deb 作为额外的 `--deb` 传给 `build-rootfs-image.py`。包的 postinst 仅启用服务；
首次启动由 `piano-snap-graphics.service` 等待 snapd seeding，安装本地 snap，先断开旧 slot，
再连接 piano，并用 `snap-discard-ns` 清理消费者的旧挂载命名空间。服务只在成功后记录完成，
限时五分钟，不自动重试。更新 provider 后，关闭相关应用，运行 `sudo piano-snap-graphics-connect`，
再重新打开。新安装的消费者不会被自动接管。
消费者改用 `gpu-2604`/core26 时须针对该 ABI 和接口重建 provider。发行版 provider 支持 A830
并通过相同硬件与沙箱检查后，应撤销此覆盖。

`piano-snap-graphics` 仅依赖 Python、snapd 和 systemd 集成，不依赖板级内核包或桌面 Mesa。
它通过标准 `gpu-2404` 接口提供 ARM64/core24 运行库，包名不构成设备型号检查。
提供者装好后，可关闭 Firefox 并运行 `sudo piano-snap-graphics-connect --consumer firefox`，
仅切换该消费者；该选项可重复，不能与首次安装的 `--install` 合用，也不会写首次启动完成标记。
不带 `--consumer` 时仍按原行为处理所有已安装的 `gpu-2404` 消费者。

图形提供者不负责启用严格隔离。`strict` snap 元数据与 Adreno renderer 都不能代替
`snap debug confinement` 的 `strict`、`snap debug sandbox-features` 的 `support-level:full`
和实际访问拒绝。主线内核的 `network_v9` 与 snapd 旧 `network` 判据不匹配时，
系统仍是 partial；不能通过改用 `--devmode` 或关闭 AppArmor 把它当作严格隔离。

## 有时限的 RAM 返回检查

`piano-debug-timeout.service` 使用基于内核 uptime 的 `piano.debug_timeout` 截止时间，
RAM 硬件检查使用 600 秒。到期后停止显示管理器与用户 slice、同步文件系统，并通过 systemd
请求返回 bootloader；关闭过程受阻时，独立子进程在 45 秒后强制返回。
该子进程使用 `DefaultDependencies=no` 和 `SurviveFinalKillSignal=yes`，需要 systemd >=255。
强制返回可能留下脏日志，因此正常卸载流程必须在此之前完成。

`build-native-display-initrd.py` 从本仓库更新 helper 和 unit，经 `/run` 带入已安装根系统，
不依赖根系统预装该修正。调试镜像的构建、硬件检查及恢复操作分开执行。
