# 代理与操作者指南

[English](AGENT-GUIDE.md)

说明如何在小米平板 8 Pro（`piano`）上构建、安装、更新、切换和恢复 Ubuntu。
面向 AI 代理和工程师；每一步给出命令及其预期结果。

约定：

- 主机命令在 Linux x86_64 主机上、于本仓库根目录执行。标为*平板上*的命令在平板的 shell 中执行。
- `<SERIAL>` 为平板序列号（`adb devices`、`fastboot devices`），`<USER>` 为平板上的账户，
  `<release>` 为项目内核包的 release，`<N>` 为其 ABI 编号。
- `# 预期` 注释给出预期输出。
- 状态用语：*已在测试机上验证*；*尚未在设备上验证*；*尚不可用*（没有相应工具）；
  *手工步骤*（使用标准命令，尚无项目工具）。

## 1. 范围与安全规则

1. **每条设备命令都绑定序列号：** `adb -s <SERIAL>`、`fastboot -s <SERIAL>`、
   `tools/piano-switch.py --serial <SERIAL>`；连接多台平板时 `tools/piano-devmode-ssh.py --serial <SERIAL>`。
   不操作未经识别的设备。
2. **同一时刻只有一个操作者。** 执行设备命令前确认没有其他会话在操作这台平板；启动、写入和采集不得重叠。
   ```bash
   pgrep -af 'fastboot|adb'      # 预期：没有其他会话的 fastboot 或 adb 客户端（adb server 为共享进程）
   ```
3. **离开 Android 前电量不低于 40 %**，以新读数为准。Linux 下不充电，接 USB 也一样。充电标志或充电时长不是读数。
   ```bash
   adb -s <SERIAL> shell dumpsys battery | grep level      # 预期 "level: 40" 或更高
   fastboot -s <SERIAL> getvar battery-soc-ok              # 预期 "battery-soc-ok: yes"
   ```
4. **写入范围。** 切换只写 `init_boot_a`、`vendor_boot_a` 和 `boot_a`。安装另写 LUN 0 的 GPT、`userdata`、
   `metadata`、`linux_rootfs` 和 `linux_data`。不得写 `vbmeta`、`dtbo`、任何 `_b` 分区、`persist`
   及其他标定、基带或固件分区，不得执行 `fastboot --set-active=b`：测试机的 B 槽是无法启动 Android 的旧版原厂链。
5. **保留退路。**
   - 任何状态下按住电源 + 音量下都可进入 Fastboot。
   - 在主机上保存 Android 镜像组和当前 Ubuntu 镜像组的副本（§6.6）。
   - 新镜像先以有时限的调试 RAM 启动（§7.2）运行一次，再写入 A 槽。
   - 无人在平板旁时冷启动正式镜像组之前，先开启开发者模式（§7.1）：否则正式镜像不提供任何 USB 功能。
6. **任何分区写入前先备份**（§4.2）。`sdX` 盘符在不同启动之间会变化，备份文件名不是设备名：
   按 UFS 控制器、LUN、GPT GUID、label 和几何选择目标。
7. **出现失败时**停下，定位最早失败的层（传输、设备提交、启动、握手）。一次状态查询失败后不重复破坏性命令；
   已成功写入的文件系统不重刷；不中断仍在进行的写入，另行监控其进度。
8. **不得提交**到任一仓库：序列号、蓝牙与 Wi-Fi 地址、触控标定、启动配置（boot profile）、`/etc/piano/*`、
   签名密钥、备份、仓库内 `firmware/` 以外的原厂文件、原始日志与采集数据。它们放在被忽略的 `private/` 目录或仓库之外。

## 2. 仓库与版本锁定

| 输入 | 位置 | 锁定 |
|---|---|---|
| 集成 | 本仓库 | 当前检出的提交 |
| 内核 | `https://github.com/yzddmr6/linux-sm8750-piano`，分支 `piano/dev` | [`kernel/source.json`](../kernel/source.json)（`commit`、`config`、`release_config_fragments`）、[`kernel/abi.json`](../kernel/abi.json) |
| 固件 | [`firmware/`](../firmware/README.md)：原厂 OS3.0.303.0.WPYCNXM 文件，按 `/usr/lib/firmware` 布局 | 身份锁 `firmware/piano-*.json`（文件名、大小、SHA-256） |
| Mesa | 上游 Mesa 26.2.0 | [`mesa/source.json`](../mesa/source.json)、`mesa/piano-fixes.patch` |
| Ubuntu | 26.04 Desktop ARM64 ISO | SHA-256 `c2afd538d66fdd77377d03f1ed2ac76a34f1c116baecc9a8170d68f833121f57`；`ubuntu/26.04.sources` |
| AOSP mkbootimg | `tools/fetch-aosp-mkbootimg.sh` | 提交 `954bc3ead5e679005fddf3484d247f2557b3c2c9` |
| BusyBox（ARM64） | `tools/fetch-busybox-arm64.sh` | `busybox-static_1.36.1-6ubuntu3.1_arm64.deb` |
| avbtool | AOSP `external/avb` | 获取脚本尚不可用；用 `--avbtool` 传入 |

```bash
git rev-parse HEAD                                          # 集成提交，记录下来
git clone --branch piano/dev https://github.com/yzddmr6/linux-sm8750-piano ../linux
python3 -c 'import json; print(json.load(open("kernel/source.json"))["commit"])'
git -C ../linux rev-parse HEAD                              # 预期与上一行相同
git -C ../linux status --porcelain --untracked-files=no     # 预期无输出
python3 tools/kernel_tag.py ../linux                        # 预期 "v7.2.6"（Makefile 版本对应的上游 tag）
sh tools/fetch-aosp-mkbootimg.sh                            # 固定版本检出到 tools/local/aosp-mkbootimg
sh tools/fetch-busybox-arm64.sh                             # 预期 ".../usr/bin/busybox: OK"
```

`tools/build-kernel.sh` 拒绝 HEAD 与 `kernel/source.json` 不符（"Kernel HEAD does not match
kernel/source.json"）或含未提交跟踪改动的源码树；`tools/build-mesa.sh` 拒绝与 `mesa/source.json`
不符的 Mesa HEAD（"Mesa HEAD mismatch"）。

**固件。** `firmware/` 存放正式根系统加载的原厂文件，未经修改，许可说明见
[firmware/README.md](../firmware/README.md)；不含逐机数据。改用自己平板的文件时，把它们复制到布局相同的目录
（Wi-Fi 文件按 `firmware/piano-wlan.json` 改名），作为 `build-firmware-deb.py --firmware-tree` 传入（§3.5）。
每个文件都按身份锁核对；缺失或不同的文件会中止构建，因此其他原厂版本需要更新身份锁。`stage-firmware.py`
只暂存 DSP 文件：

```bash
python3 -S tools/stage-firmware.py --output private/fw-dsp            # 仓库内的 ADSP 镜像
# 预期 '{"state": "LOCAL_PAYLOAD_STAGED_NOT_INSTALLED", "files": 56, ...}'
python3 -S tools/stage-firmware.py --firmware-dir <stock modem_a image/ directory> --output private/fw-dsp-own
# 从自己的提取结果暂存完整 DSP 锁
```

## 3. 构建流程

顺序：内核（§3.1）→ ABI 条目（§3.2）→ 启动 DTB（§3.3）→ 内核包（§3.4）→ 设备包与固件包（§3.5）→
根文件系统（§4.4）→ 启动镜像组（§3.6）。主机工具见 [build.zh-CN.md](build.zh-CN.md)；另需 `dpkg-deb`、
`python3-debian`、`device-tree-compiler`（`fdtget`/`fdtput`）、`python3-libfdt`、AOSP libufdt 的
`ufdt_apply_overlay`、`m4` 和 `alsatplg`。

### 3.1 内核

```bash
CONFIG_FRAGMENT_EXTRA="arch/arm64/configs/piano-desktop-diag.config arch/arm64/configs/piano-persistent.config arch/arm64/configs/piano-release.config" \
BUILD_MODULES=1 KERNEL_SOURCE=../linux OUT_DIR="$PWD/out/kernel-release" bash tools/build-kernel.sh
# 预期 "Piano kernel build complete: .../out/kernel-release/build-result.json (boot packaging is separate)"
python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["kernel_release"])' out/kernel-release/build-result.json
# 预期 "7.2.6-piano<N>"
```

- 基础片段（`kernel/source.json` 的 `config`）最先合并；`CONFIG_FRAGMENT_EXTRA` 按顺序给出其余
  `release_config_fragments`，`piano-release.config` 在最后。
- 增量构建沿用同一 OUT（已安装 ccache 时自动使用）；更换工具链或源码位置需要新的 OUT。
- 不含 `piano-release.config` 的构建保留诊断后缀（如 `7.2.6-piano-diag`），不能打包。

### 3.2 升 ABI（每个打包的内核）

release 为 `<version>-piano<ABI>`。被打包内核的提交或最终 `.config` 一变（含只改 DTS、更换工具链），
就取下一个整数；不复用、不回退。`CONFIG_MODVERSIONS` 关闭，否则无法发现模块 ABI 破坏。

1. 在内核仓库把 `arch/arm64/configs/piano-release.config` 中的 `CONFIG_LOCALVERSION` 改为 `"-piano<N+1>"` 并提交。
2. 把 `kernel/source.json` 的 `commit` 改为新的内核 HEAD。
3. 构建（§3.1）并运行打包器（§3.4）。release 未登记时，打包器停止并打印应追加的条目：
   ```
   # 预期 "7.2.6-piano<N+1> is not recorded in .../kernel/abi.json; after review add and commit"
   #      '{"kernel_release": "7.2.6-piano<N+1>", "kernel_commit": "...", "config_sha256": "..."}'
   ```
4. 把该对象加上 `"upload": 1` 追加到 `kernel/abi.json` 的 `releases`，`kernel/source.json` 与
   `kernel/abi.json` 一起提交，再运行打包器。

"`<release>` already belongs to `<commit>` ... raise the ABI" 表示该 release 已分配给另一构建。
只有以不同包内容重新打包同一构建时才增加 `upload`。

### 3.3 启动 DTB

ABL 把原厂 DTBO 叠加到启动 DTB 上；`prepare-boot-dtb.py` 为此处理桌面 DTB。它需要本机的原厂基础 DTB
（来自 `vendor_boot_a`）和 DTBO 条目（来自 `dtbo_a`）；从备份镜像中提取这两项是 手工步骤。

```bash
python3 tools/prepare-boot-dtb.py \
  --mainline-dtb out/kernel-release/arch/arm64/boot/dts/qcom/sm8750-xiaomi-piano-desktop.dtb \
  --stock-base <stock base DTB> --stock-overlay <stock DTBO entry> \
  --ufdt <ufdt_apply_overlay> --output out/boot-dtb
# 预期生成 out/boot-dtb/piano-boot.dtb 和 out/boot-dtb/manifest.json
```

桌面 DTB 一变就重新生成。`build-kernel-deb.py --boot-dtb-build <较早的 build-result.json>` 只在两个提交之间的
内核源码仅在 `arch/arm64/boot/dts/`、`Documentation/devicetree/bindings/` 和 `arch/arm64/configs/` 中有差异时，
接受为较早构建准备的 DTB；此时包内带的是那份较早的 DTB。

### 3.4 内核包

```bash
python3 tools/build-kernel-deb.py --kernel-source ../linux --kernel-out out/kernel-release \
  --boot-dtb out/boot-dtb/piano-boot.dtb --maintainer 'Name <email>' --output out/kernel-deb-<N>
# 预期 JSON："package": "linux-image-7.2.6-piano<N>"、"version": "7.2.6-<N>.1"、"deb"、"deb_sha256"
```

包内有 `/boot/vmlinuz-<release>`（未压缩 Image）、`System.map`、`config`，`/usr/lib/linux-image-<release>/`
下的全部 DTB 与 `piano-boot.dtb`，以及匹配的模块；维护脚本调用发行版钩子（dracut，然后 `zz-piano-boot`）。
相同输入得到逐字节相同的 deb。不带 `--boot-dtb` 时包内没有 `piano-boot.dtb`，`piano-boot-update` 无法生成镜像组。

### 3.5 设备包与固件包

```bash
# 触控处理，ARM64，动态链接 glibc、静态链接 C++ 运行库（工具链与检查见 build.zh-CN.md 第 5 节）；
# JSON 头文件单独放一个 include 目录，避免主机头文件混入交叉编译
mkdir -p out/json-include && cp -r /usr/include/nlohmann out/json-include/
make -C device/piano-thp OUT="$PWD/out/piano-thp-arm64" CXX=aarch64-linux-gnu-g++-13 \
  JSON_INCLUDE="$PWD/out/json-include" LDFLAGS='-static-libstdc++ -static-libgcc -Wl,-z,relro,-z,now' \
  all test RUNNER='qemu-aarch64-static -L /usr/aarch64-linux-gnu'
file out/piano-thp-arm64/piano-thp-live      # 预期 "ARM aarch64 ... dynamically linked, interpreter /lib/ld-linux-aarch64.so.1"
# Mesa 26.2.0 加 A830 补丁（cross file 与 sysroot 见 build.zh-CN.md）
MESA_SOURCE=<Mesa checkout at mesa/source.json> CROSS_FILE=<arm64 cross file> \
  OUT_DIR="$PWD/out/mesa" bash tools/build-mesa.sh

python3 tools/build-device-debs.py --output out/device-debs --version 0.1+<YYYYMMDD>.<n> \
  --thp-live out/piano-thp-arm64/piano-thp-live --mesa-build out/mesa \
  --busybox tools/local/busybox-arm64/usr/bin/busybox --avbtool <avbtool.py>
# 预期 "Local packages created; no packages installed or device accessed"，以及
# piano-device-support、piano-mesa、piano-ubuntu-desktop 三个 deb 和 manifest.json

# 录音拓扑（AudioReach 检出到 device/audio/source.json 中的提交），然后构建固件包
mkdir -p out/audio
m4 -I <audioreach-topology checkout> device/audio/Piano-Capture-Diag.m4 > out/audio/Piano-Capture-Diag.conf
alsatplg -c out/audio/Piano-Capture-Diag.conf -o out/audio/Piano-Capture-Diag-tplg.bin
python3 tools/build-firmware-deb.py --topology out/audio/Piano-Capture-Diag-tplg.bin \
  --card-topology out/audio/Xiaomi-Pad-8-Pro-tplg.bin \
  --maintainer 'Name <email>' --output out/firmware-deb
# 预期 JSON 中 "deb": "piano-local-firmware_0.6+os3.0.303.0_all.deb" 与 "deb_sha256"
```

- `build-firmware-deb.py` 默认使用 `firmware/`；使用自己的提取结果时加 `--firmware-tree <tree>`（§2）。
  包内含全部锁定文件和录音拓扑。
- `--support-only` 只重建 `piano-device-support`；其余参数仍须给出（任意路径）。
- 每次重建设备包都提高 `--version`，dpkg 才会把它作为升级安装。

### 3.6 启动镜像组

```bash
python3 tools/build-persistent-boot.py \
  --kernel <root>/boot/vmlinuz-<release> --kernel-release <release> \
  --dtb <root>/usr/lib/linux-image-<release>/piano-boot.dtb --initrd <root>/boot/initrd.img-<release> \
  --profile <boot-profile.json> --root-partuuid <linux_rootfs PARTUUID> --bt-address-file <bluetooth-address> \
  --mkbootimg tools/local/aosp-mkbootimg/mkbootimg.py --avbtool <avbtool.py> --key <boot-signing.pem> \
  --output out/ubuntu-set-<release>
# 预期 '{"output": ".../out/ubuntu-set-<release>", "images": ["boot", "init_boot", "vendor_boot"]}'
(cd out/ubuntu-set-<release> && sha256sum -c SHA256SUMS)    # 预期三行 "OK"
```

- `--wlan-address-file <wlan-address>`（可选）把本机 Wi-Fi MAC 地址写入打包 DTB，与 `--bt-address-file`
  处理蓝牙的方式相同。
- `--root-partlabel linux_rootfs`（代替 `--root-partuuid`）按 GPT 分区名选根，`--no-serial` 不写
  `androidboot.serialno`，交由每次启动都会追加它的 ABL；两个地址文件都不给时即发布用的、不绑定单机的镜像组
  （[RELEASE.md](RELEASE.md)）。
- `<root>` 为目标根文件系统：initrd 是内核包在其中生成的发行版 dracut initrd（§4.4）。已安装的平板上由
  `piano-boot-update` 自行调用本工具（§5）。
- 输出：`boot.img`、`init_boot.img`、`vendor_boot.img`（100,663,296、8,388,608 和 100,663,296 字节，
  AVB footer 用你的密钥签名），`SHA256SUMS`，`manifest.json`（输入与输出 SHA-256、命令行、release），
  以及 `boot-ram-check.img`（供 `fastboot boot` 的 v2 镜像，内核、initrd、DTB 与命令行相同）。命令行超过 511 字节时工具拒绝构建：
  v2 头字段只容 511 字节，piano 的 ABL 丢弃其余部分。
- `--maintenance` 只用维护 initramfs 构建 `boot-ram-maintenance.img`（§4.6）。
- 正式命令行：`root=PARTUUID=<...> rootfstype=ext4 rw rootwait console=ttyMSM0,115200n8
  log_buf_len=2M panic=15 msm.separate_gpu_kms=1 piano.boot=persistent systemd.gpt_auto=0 bootconfig
  androidboot.serialno=<SERIAL>`，不含调试入口。
- 调试选项（不带 `--debug` 时拒绝）：

| 选项 | 作用 |
|---|---|
| `--debug` | 加 `piano.debug=1`：initrd 与根阶段经 USB NCM 提供无认证的 root shell（§7.2） |
| `--debug-timeout 60..900` | 加 `piano.debug_timeout=<s>`：运行到该 uptime 时强制重启进入 Fastboot |
| `--modprobe-blacklist a,b` | kmod 的 `modprobe.blacklist=`：阻止按别名自动加载，显式加载仍有效 |
| `--module-blacklist a,b` | 内核的 `module_blacklist=` |
| `--debug-boot-trace` | 把串口控制台换成 pstore 跟踪控制台（`initcall_debug`、时钟跟踪事件），用于在用户态之前停止的启动 |

模块黑名单只用于单次诊断启动。若模块子集让电源域、时钟或互连提供者的消费者没有完成 probe，
该提供者的 `sync_state` 会一直挂起；这类子集曾在测试机上造成无日志的复位。用后恢复完整模块树。
调试镜像组不得写入 A 槽作日常使用。

### 3.7 需记录的身份

每个送到平板的构建记录：集成与内核提交；`kernel_release`；`build-result.json` 与 `.config` 的 SHA-256
（即 `kernel/abi.json` 条目）；内核包 `manifest.json`（`deb_sha256`、`build_result_sha256`、
`abi_ledger_sha256`、`boot_dtb`）；`out/boot-dtb/manifest.json`；设备包与固件包的 manifest；固件身份锁；
Mesa 构建 manifest；镜像组的 `manifest.json` 与 `SHA256SUMS`；initrd 的 SHA-256。dracut initrd 不可复现，
因此其余输入相同时 `vendor_boot.img` 在不同构建间也不同。

## 4. 全新安装（手工步骤）

分区与根文件系统安装器尚不可用。以下步骤用标准命令复现测试机（512 GB UFS）的安装，256 GB 对应值见 §4.5。使用发布目录时，发布的根文件系统镜像与启动镜像组代替 §4.4 和 §3.6 的镜像组，改动见 [RELEASE.md](RELEASE.md#installing-the-release)。

### 4.1 前提

```bash
adb -s <SERIAL> shell getprop ro.product.device              # 预期 "piano"
adb -s <SERIAL> shell getprop ro.build.version.incremental   # 预期 "OS3.0.303.0.WPYCNXM"
adb -s <SERIAL> shell getprop ro.boot.slot_suffix            # 预期 "_a"
adb -s <SERIAL> shell getprop ro.boot.flash.locked           # 预期 "0"
adb -s <SERIAL> shell su -c id                               # 预期 "uid=0(root) ..."
adb -s <SERIAL> reboot bootloader
fastboot -s <SERIAL> getvar unlocked                         # 预期 "unlocked: yes"
fastboot -s <SERIAL> getvar current-slot                     # 预期 "current-slot: a"
fastboot -s <SERIAL> reboot
```

读取分区需要 Android root（Magisk 或 KernelSU-Next，位于已修补的 `init_boot_a`）。

### 4.2 备份（手工步骤）

备份除 `userdata` 外的全部分区，以及每个 UFS LUN 的两份 GPT。`adb exec-out` 可安全传输二进制，`adb shell` 不行。

```bash
adb -s <SERIAL> shell su -c 'for d in /sys/block/sd?; do echo ${d##*/} $(cat $d/size) $(cat $d/queue/logical_block_size); done'
# 预期六个 LUN，逻辑块大小 4096；size 以 512 字节为单位
adb -s <SERIAL> shell su -c 'ls -l /dev/block/by-name/'     # label -> /dev/block/sdXN
# 除 userdata 外的每个分区
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/by-name/<label> bs=1048576 2>/dev/null' > backup/<label>.img
adb -s <SERIAL> shell su -c 'sha256sum /dev/block/by-name/<label>'    # 须与下一行相同
sha256sum backup/<label>.img
# 每个 LUN 的 MBR、主 GPT 与备份 GPT；<blocks> = size / 8
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/sdX bs=4096 count=34 2>/dev/null' > backup/gpt/sdX-head.bin
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/sdX bs=4096 skip=<blocks-34> 2>/dev/null' > backup/gpt/sdX-tail.bin
```

- 记录清单：每个镜像的 label、当前节点、LUN、大小和 SHA-256，以及每个 LUN 的磁盘 GUID。只有该清单把文件对应到分区。
- Android 读写挂载的文件系统（如 `persist`、`metadata`）在读取期间可能变化：读两次比对哈希，或在读取时冻结
  （`fsfreeze`）或只读重挂。
- A 槽的 `boot`、`init_boot`（槽内已打 root 补丁的镜像）和 `vendor_boot` 构成 Android 镜像组（§6.6）。
  备份存放在两个仓库之外。

### 4.3 本机输入

| 文件 | 来源 | 命令 |
|---|---|---|
| `bluetooth-address` | 原厂 Android | `adb -s <SERIAL> shell settings get secure bluetooth_address > bluetooth-address`（一行 `XX:XX:XX:XX:XX:XX`；`piano-boot-update` 必需） |
| `wlan-address`（可选） | 原厂 `persist` 分区中的 `wlan/wlan_mac.bin` | 手工步骤：一行单播 `XX:XX:XX:XX:XX:XX`；存在时 `piano-boot-update` 使用它 |
| `thp-calibration.json` | 原厂 ODM 分区中的 `piano_nova_csot_thp_config.ini` 或 `piano_nova_boe_thp_config.ini`，取本机原厂驱动加载的那份（原厂 Android 中 `dmesg \| grep config_file_name`；测试机为 CSOT 面板）。内核识别屏型并加载对应触控固件；`piano-thp-live` 按内核 `panel_vendor` 选方向，标定属于另一种屏时记一行 `THP calibration error` | `python3 tools/extract-thp-calibration.py --input <ini> --output thp-calibration.json` |
| `boot-signing.pem` | 所有者新生成的密钥 | `openssl genrsa -out boot-signing.pem 4096` |
| `boot-profile.json` | 原厂 `boot_a`、`init_boot_a`、`vendor_boot_a` | 手工步骤，见下 |
| `boot-partitions.json` | 启动 LUN（LUN 4，即 GPT 含 `boot_a` 与 `boot_b` 的 LUN）的 `sfdisk --json` | `python3 tools/extract-boot-partitions.py --input <sfdisk json> --profile boot-profile.json --output boot-partitions.json`（§4.8；INSTALL 第 8a 步在维护镜像中读取分区表） |
| 原厂基础 DTB、DTBO 条目 | `vendor_boot_a`、`dtbo_a` | 手工步骤（§3.3） |

启动配置由 `build-persistent-boot.py` 读取；其生成工具尚不可用：

```
{
  "serial": "<SERIAL>",
  "boot":        {"header_version": 4, "capacity": 100663296, "rollback_index": <int>, "avb_properties": {<props>}},
  "init_boot":   {"header_version": 4, "capacity": 8388608,   "rollback_index": <int>, "avb_properties": {<props>}},
  "vendor_boot": {"header_version": 4, "capacity": 100663296, "rollback_index": <int>, "avb_properties": {<props>},
                  "page_size": <int>, "kernel_addr": <int>, "ramdisk_addr": <int>, "tags_addr": <int>, "dtb_addr": <int>}
}
```

按本机原厂镜像填写：

```bash
python3 tools/local/aosp-mkbootimg/unpack_bootimg.py --boot_img backup/vendor_boot_a.img --out /tmp/vb
# 头版本、页大小、kernel/ramdisk/tags 加载地址、dtb 地址
python3 <avbtool.py> info_image --image backup/boot_a.img
# "Rollback Index:" 与所有 "Prop: com.android.build.boot.*" 行；init_boot_a、vendor_boot_a 同样处理
fastboot -s <SERIAL> getvar partition-size:boot_a           # 预期 "partition-size:boot_a: 0x6000000"
```

`capacity` 为分区字节数。把每个镜像的全部 `Prop:` 写入其 `avb_properties`；`boot` 必须有
`com.android.build.boot.os_version` 与 `com.android.build.boot.security_patch`。缺少原厂头字段、rollback
index 与 AVB 属性时，ABL 会把该槽标为不可启动。

### 4.4 根与数据文件系统（主机）

按 [build.zh-CN.md](build.zh-CN.md) 构建原始 Desktop TAR（`prepare-ubuntu-desktop.sh`、`sqfs2tar`、
`merge-desktop-tars.py`），然后：

```bash
ROOT_UUID=$(uuidgen); DATA_UUID=$(uuidgen)      # 同时用作 §4.5 的分区 GUID
python3 tools/build-ubuntu-ext4.py --tar out/ubuntu-desktop-pristine/ubuntu-desktop.tar --tar-sha256 <sha256> \
  --mke2fs <mke2fs 1.47.4 with libarchive> --mke2fs-config <mke2fs.conf> --e2fsck <e2fsck> \
  --uuid "$ROOT_UUID" --size-mib 12288 --output out/ubuntu-ext4
mkdir -p out/install && cp out/ubuntu-ext4/ubuntu-root.ext4 out/install/root.ext4
e2fsck -f out/install/root.ext4 && resize2fs out/install/root.ext4 60G
# 预期 "The filesystem on out/install/root.ext4 is now 15728640 (4k) blocks long."
```

在 ARM64 chroot 中安装软件包（手工步骤；
x86_64 主机需要带 binfmt 的 `qemu-user-static`）：

```bash
R=/mnt/piano-root
sudo mkdir -p $R && sudo mount -o loop out/install/root.ext4 $R
sudo mount -t proc proc $R/proc && sudo mount -t sysfs -o ro sysfs $R/sys
sudo mount --bind /dev $R/dev && sudo mount --bind /dev/pts $R/dev/pts
printf '#!/bin/sh\nexit 101\n' | sudo tee $R/usr/sbin/policy-rc.d >/dev/null && sudo chmod 755 $R/usr/sbin/policy-rc.d

# 设备配置，root:root 0600；layout.json 必须在安装内核包之前存在
# （它启用 dracut 模块 99piano 和 zz-piano-boot 钩子）
sudo install -d -m 755 $R/etc/piano
sudo install -m 600 bluetooth-address boot-profile.json boot-signing.pem thp-calibration.json $R/etc/piano/
sudo install -m 600 wlan-address $R/etc/piano/            # 仅在有该文件时
printf '{"root_partuuid": "%s"}\n' "$ROOT_UUID" | sudo tee $R/etc/piano/layout.json >/dev/null
sudo chmod 600 $R/etc/piano/layout.json
printf 'UUID=%s / ext4 defaults,noatime 0 1\nUUID=%s /home ext4 defaults,noatime 0 2\n' \
  "$ROOT_UUID" "$DATA_UUID" | sudo tee -a $R/etc/fstab

# 取自备份的 Android 镜像组（init_boot 为 A 槽内已打 root 补丁的镜像）
A=$R/var/lib/piano/switch/android
sudo install -d $A
sudo cp backup/boot_a.img $A/boot.img && sudo cp backup/init_boot_a.img $A/init_boot.img
sudo cp backup/vendor_boot_a.img $A/vendor_boot.img
(cd $A && sudo sh -c 'sha256sum boot.img init_boot.img vendor_boot.img > SHA256SUMS')

sudo chroot $R dpkg-query -W dracut python3 openssl device-tree-compiler gdm3 linux-base
# 须列出全部包名；缺少的包及其依赖，连同开发者模式（§7.1）所需的 openssh-server、
# 当前桌面包依赖的 fake-hwclock，以及仅在内核含 CONFIG_ZRAM 时
# 需要的 systemd-zram-generator，从签名快照下载：
python3 tools/prepare-ubuntu-apt.py --rootfs out/ubuntu-desktop-pristine/ubuntu-desktop.squashfs \
  --snapshot <YYYYMMDDTHHMMSSZ> --output out/apt-extra \
  --download device-tree-compiler openssh-server fake-hwclock systemd-zram-generator
sudo install -d $R/var/cache/piano-install
sudo cp out/apt-extra/cache/archives/*.deb out/kernel-deb-<N>/linux-image-*.deb out/device-debs/*.deb \
  out/firmware-deb/*.deb $R/var/cache/piano-install/
sudo chroot $R env SYSTEMD_OFFLINE=1 DEBIAN_FRONTEND=noninteractive dpkg --install /var/cache/piano-install/*.deb
# 预期 dracut 生成 /boot/initrd.img-<release>，随后输出 "Piano boot image set prepared for <release>"
# 与 "Slot A not inspected: not a running persistent piano boot"
sudo chroot $R dpkg --audit                    # 预期无输出
```

内核有 zram 而未装 `systemd-zram-generator` 时没有交换；内核不含 `CONFIG_ZRAM` 却装了它时，
启动为等待 `/dev/zram0` 多用 90 s（QEMU）。随后按 [tools/rootfs-image-guest.sh](../tools/rootfs-image-guest.sh) 对发布根的做法，
设置平板主机名，清除底层的通用内核与 kdump（这些包会被更新并生成 initrd，却永远不会启动；下列为 26.04 Desktop 底层的包名，
guest 按模式选取），并删除属于构建机镜像源的底层软件包索引：

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
sudo chroot $R dpkg --audit                    # 预期无输出
ls $R/usr/lib/modules                          # 预期只有 <release>
sudo find $R/var/lib/apt/lists -mindepth 1 -maxdepth 1 -type f ! -name lock -delete
```

发布根上此步共移除 47 个包（`--autoremove` 连带 `libc6-dev` 与 `flash-kernel`；用户编译软件需先装 `build-essential`）。

`openssh-server` 会启用 Ubuntu 自带、监听所有地址的 `ssh.socket`。要从首次启动起使用开发者模式（§7.1；安装完成后关闭）：

```bash
sudo systemctl --root=$R disable ssh.socket ssh.service
sudo systemctl --root=$R enable piano-devmode.service
```

取出后续步骤所需文件并封存镜像：

```bash
sudo cp -r $R/var/lib/piano/switch/ubuntu out/install/ubuntu-set        # §4.9 写入 A 槽
sudo cp $R/boot/vmlinuz-<release> $R/boot/initrd.img-<release> \
  $R/usr/lib/linux-image-<release>/piano-boot.dtb out/install/
sudo rm -r $R/usr/sbin/policy-rc.d $R/var/cache/piano-install
sudo fstrim -v $R      # 释放的块保留旧内容；loop 设备须把 discard 传到镜像文件
sudo umount $R/dev/pts $R/dev $R/sys $R/proc $R
e2fsck -f out/install/root.ext4 && python3 tools/sparse_image.py encode-ext4 out/install/root.ext4 out/install/root.sparse.img

truncate -s 216115687424 out/install/data.ext4     # 512 GB 机型的 linux_data 大小（§4.5）；256 GB 机器为 65418539008
mkfs.ext4 -U "$DATA_UUID" out/install/data.ext4 && img2simg -s out/install/data.ext4 out/install/data.sparse.img
```

不带参数的 `img2simg` 把全零块存为 FILL 块，Fastboot 仍会完整写入：根约 55 GB，引导程序回 `OKAY` 后在后台以约 30 MB/s
写入（测试机耗时 1839 s）。`encode-ext4` 只把空闲块设为 DONT_CARE，已用的全零块（inode 表、日志）保留为 FILL：
发布根约写 9.4 GiB。`-s` 不写数据盘镜像的未用块。刷写前用 `python3 tools/sparse_image.py summary` 核对合计；根镜像保持构建原样（其字节即发布物）。
`encode-ext4` 只把全零的空闲块设为 DONT_CARE，不做 `fstrim` 时清除的内核仍按 RAW 写入（发布根构建样本：
不 trim 写 10.14 GB，trim 后 8.73 GB）。

### 4.5 新的 LUN 0 GPT（主机，手工步骤）

34 号条目 `userdata` 保留起点、类型 GUID、唯一 GUID 和属性，缩小到 200 GiB（256 GB 机型为 110 GiB）；
35 号 `linux_rootfs`（60 GiB）和 36 号 `linux_data`（其余空间）为新条目。其他条目和 LUN 不变。
LUN 0 在 512 GB 机型有 124,552,192 个 4096 字节块，256 GB 机型有 64,167,936 个（§4.2 的 `size / 8`；
1 GiB 为 262,144 块）。一台 256 GB 机器实测 `userdata` 28,835,840 块、`linux_rootfs` 15,728,640 块、
`linux_data` 15,971,323 块，最后一个结束于最后可用 LBA 64167930。

```bash
BLOCKS=<LUN 0 size / 8>                         # 取自 §4.2
truncate -s $((BLOCKS * 4096)) lun0.img
dd if=backup/gpt/<lun0>-head.bin of=lun0.img bs=4096 conv=notrunc
dd if=backup/gpt/<lun0>-tail.bin of=lun0.img bs=4096 seek=$((BLOCKS - 34)) conv=notrunc
LOOP=$(sudo losetup --find --show --sector-size 4096 lun0.img)
sudo sgdisk --print "$LOOP"       # 预期 "Sector size (logical): 4096 bytes"，分区表与设备一致
sudo sgdisk --info=34 "$LOOP"     # 预期 "Partition name: 'userdata'"；记下起始扇区、类型、唯一 GUID 与属性
sudo sfdisk --json "$LOOP" > gpt-before.json

S=<first sector of userdata>      # 测试机为 3632128
U=<unique GUID of userdata>; T=<type GUID of userdata>
A=52428800                        # userdata 块数：512 GB 机型 200 GiB x 262144；256 GB 机型 28835840（110 GiB）
R=15728640                        # linux_rootfs 块数：60 GiB x 262144
L=0FC63DAF-8483-4772-8E79-3D69D8477DE4      # Linux 文件系统
sudo sgdisk --set-alignment=256 --delete=34 \
  --new=34:$S:$((S + A - 1)) --typecode=34:$T --partition-guid=34:$U --change-name=34:userdata \
  --new=35:$((S + A)):$((S + A + R - 1)) --typecode=35:$L --partition-guid=35:$ROOT_UUID --change-name=35:linux_rootfs \
  --new=36:$((S + A + R)):0 --typecode=36:$L --partition-guid=36:$DATA_UUID --change-name=36:linux_data \
  "$LOOP"
sudo sgdisk --verify "$LOOP"      # 预期 "No problems found."
sudo sfdisk --json "$LOOP" > gpt-after.json
```

对比 `gpt-before.json` 与 `gpt-after.json`：只允许 34–36 号条目不同，且三者起点分别为 `S`、`S + A`
和 `S + A + R`；`linux_data` 为 `LAST - S - A - R + 1` 块。磁盘 GUID、首末可用 LBA 和表长度不得变化。userdata 属性非零时用
`--attributes=34:=:<hex>` 复制。然后取出两个 GPT 区域（测试机首个可用 LBA 为 6，最后可用 LBA 为 124552186；256 GB 机型为 64167930）：

```bash
FIRST=<first usable LBA>; LAST=<last usable LBA>        # 取自 sgdisk --print
sudo dd if="$LOOP" of=new-primary.bin bs=4096 count=$FIRST
sudo dd if="$LOOP" of=new-backup.bin bs=4096 skip=$((LAST + 1))
dd if=backup/gpt/<lun0>-head.bin bs=4096 count=$FIRST 2>/dev/null | sha256sum    # 当前主 GPT 区域
sha256sum new-primary.bin new-backup.bin
sudo losetup -d "$LOOP"
```

### 4.6 写入 GPT（平板，手工步骤）

在维护 RAM 镜像中执行：发布目录的 `boot/boot-ram-maintenance.img`，或为其他内核包构建的同类镜像。它停留在
initramfs 中（[initramfs/init](../initramfs/init) 的 `piano.diag=maintenance`），不挂载任何 UFS 分区，经
`initramfs/network.json` 的 NCM 链路在 `192.168.88.2:2323` 提供 root shell；没有打开的会话、也没有
`piano-maint keepalive` 达 `--maintenance-idle` 秒（默认 600）后重启进入 Fastboot（[initramfs/piano-maint](../initramfs/piano-maint)）。

```bash
# 取自锁定桌面 SquashFS 的 Ubuntu 工具、内核包中的 reboot-mode 模块、基于内核 nolibc 的 restart2
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

`build-release.py --maintenance out/maintenance-ram` 先核对镜像使用镜像组的内核与无地址 DTB、命令行无序列号，再复制进发布目录。
在运行中、`userdata` 已挂载的 Android 里写 GPT 尚未验证。

脚本驱动该会话时不要发送 `stty -echo`：此后 BusyBox ash 经 stdio 读取预先送入的各行，管道末尾读标准输入的 applet
（`cut`、`head`）会读到后续命令行而不是管道内容（测试机上可复现）。

*平板上*（`fastboot boot` 该镜像后 `telnet 192.168.88.2 2323`；登录时打印 LUN 表和 LUN 0 的 `sgdisk -p`；
主机用 `python3 -m http.server --bind 192.168.88.10 8000` 提供两个文件）：

```sh
FIRST=<first usable LBA>; LAST=<last usable LBA>; BLOCKS=<LUN 0 blocks>    # §4.5 中的值
disk=/dev/sdX                                           # 登录表或 piano-maint map 中的 "LUN 0: /dev/sdX"
wget -O /run/new-primary.bin http://192.168.88.10:8000/new-primary.bin
wget -O /run/new-backup.bin http://192.168.88.10:8000/new-backup.bin
sha256sum /run/new-primary.bin /run/new-backup.bin      # 预期等于 §4.5 的哈希
dd if=$disk bs=4096 count=$FIRST | sha256sum            # 预期等于 §4.5 中当前主 GPT 区域的哈希
dd if=/run/new-backup.bin of=$disk bs=4096 seek=$((LAST + 1)) conv=fsync
dd if=$disk bs=4096 skip=$((LAST + 1)) count=$((BLOCKS - LAST - 1)) | sha256sum   # 预期等于 new-backup.bin
dd if=/run/new-primary.bin of=$disk bs=4096 conv=fsync
dd if=$disk bs=4096 count=$FIRST | sha256sum            # 预期等于 new-primary.bin
sync
sgdisk --verify $disk                                   # 预期 "No problems found."
piano-maint fastboot
```

先写备份 GPT，再写主 GPT，每次读回比对通过后再继续。

### 4.7 擦除并写入文件系统（Fastboot）

```bash
fastboot -s <SERIAL> getvar partition-size:userdata       # 预期 "0x3200000000"；256 GB 机型为 102 GiB（比 GPT 的 110 GiB 小 8 GiB，与原厂一致）
fastboot -s <SERIAL> getvar partition-size:linux_rootfs   # 预期 "0xf00000000"
fastboot -s <SERIAL> getvar partition-size:linux_data     # linux_data 块数 x 4096：512 GB 机型预期 "0x32517fb000"
fastboot -s <SERIAL> erase userdata
fastboot -s <SERIAL> erase metadata
fastboot -s <SERIAL> flash linux_rootfs out/install/root.sparse.img
fastboot -s <SERIAL> flash linux_data out/install/data.sparse.img
# 预期每个 Sending 与 Writing 行均为 "OKAY"，最后 "Finished."
# 返回 OKAY 时引导程序可能仍在写 FILL 块，下一条命令会等它写完；等待期间不要拔线、复位或关机。
```

Android 下次启动时重建 `userdata` 和 `metadata`；全部 Android 数据（包括 root 管理器的配置）都会丢失。
这些写入成功后不再重复。

### 4.8 检查启动与分区映射

```bash
python3 tools/build-persistent-boot.py --kernel out/install/vmlinuz-<release> --kernel-release <release> \
  --dtb out/install/piano-boot.dtb --initrd out/install/initrd.img-<release> \
  --profile boot-profile.json --root-partuuid "$ROOT_UUID" --bt-address-file bluetooth-address \
  --mkbootimg tools/local/aosp-mkbootimg/mkbootimg.py --avbtool <avbtool.py> --key boot-signing.pem \
  --debug --debug-timeout 900 --output out/install/debug-set
fastboot -s <SERIAL> boot out/install/debug-set/boot-ram-check.img     # 预期 "Booting OKAY"
```

连接调试控制台（§7.2）。*平板上*，根阶段：

```sh
findmnt -no SOURCE,UUID /        # 预期 linux_rootfs 分区与 ROOT_UUID
findmnt -no UUID /home           # 预期 DATA_UUID
uname -r                         # 预期 <release>
d=$(lsblk -no PKNAME "$(readlink -f /dev/disk/by-partlabel/boot_a)"); readlink -f /sys/block/$d/device
# 预期路径以 ":4" 结尾（LUN 4）
sfdisk --json /dev/$d            # 把输出在主机上存为 boot-lun.json
```

在主机上生成 `piano-boot-switch` 所需的映射，再装到平板的 `/etc/piano/boot-partitions.json`（root，权限 0600）：

```bash
python3 tools/extract-boot-partitions.py --input boot-lun.json --profile boot-profile.json --output boot-partitions.json
# 预期 init_boot_a 8388608 B，vendor_boot_a 与 boot_a 各 100663296 B
```

磁盘没有 `boot_a` 与 `boot_b`、大小与配置中的容量不符时工具拒绝；映射记录 LUN 4，由 `piano-boot-switch` 与磁盘比对。
它取代旧版的手写映射，尚未对平板运行。

```sh
piano-boot-switch status         # 预期 "occupant": "android" 与 "backup_gpt": "valid"
piano-boot-switch verify         # 预期退出码 0，"android": "verified"、"ubuntu": "verified"
```

### 4.9 写入 A 槽并首次启动

可以在同一调试控制台中写入，这样不改变槽属性：

```sh
piano-boot-switch to-ubuntu --dry-run   # 预期 "result": "would-write" 与 "rollback": "android"
piano-boot-switch to-ubuntu             # 预期 "result": "written"
systemctl reboot                        # 从 A 槽冷启动
```

也可以在主机上经 Fastboot 写入，这会重置重试计数（§6.4）：

```bash
python3 tools/piano-switch.py --serial <SERIAL> --images out/install/ubuntu-set --rollback <android-set>
# 预期 "Active A boot triplet written; remain in Fastboot for a RAM readback before reboot."
fastboot -s <SERIAL> reboot
```

- 首次启动：GNOME 首次设置创建第一个账户。没有网络，跳过网络页。用磁吸键盘输入密码：锁屏时屏幕键盘失效。
- 每次启动后时间都不对（PMIC RTC 不可写）：`sudo date -s 'YYYY-MM-DD HH:MM'`。
- 启动完成后 `piano-slot-success.service` 标记 A 槽：
  ```bash
  sudo piano-slot-success --check                 # 平板上；预期 "state": "already-successful"
  fastboot -s <SERIAL> getvar slot-successful:a   # 下次进入 Fastboot 时；预期 "yes"
  ```
  槽内镜像组不在存储中时，标记工具拒绝（`slot-set-unregistered`）；在该次启动中执行一次
  `sudo piano-boot-switch import-ubuntu`。

## 5. 在设备上更新内核

### 5.1 准备

- 以下一个 ABI 构建新包（§3.2–§3.4）。
- 在主机上保存当前 Ubuntu 镜像组（§6.6）和 Android 镜像组的副本。
- 无人在平板旁时重启，须已开启开发者模式（§7.1）。

```bash
scp out/kernel-deb-<N>/linux-image-7.2.6-piano<N>_7.2.6-<N>.1_arm64.deb <USER>@192.168.88.2:
```

### 5.2 安装（平板上）

用临时单元运行 dpkg，SSH 断开时它不会被中止。耗时约 10 到 13 分钟（dracut 约 8 分钟，kdump-tools 的 initrd 约 4 分钟）。

```bash
sudo systemd-run --unit=piano-kernel-update --setenv=DEBIAN_FRONTEND=noninteractive \
  dpkg -i /home/<USER>/linux-image-7.2.6-piano<N>_7.2.6-<N>.1_arm64.deb
journalctl -f -u piano-kernel-update
# 预期 "Piano boot image set prepared for 7.2.6-piano<N>"，随后为以下之一：
#   "Slot A now holds store/ubuntu"                                （槽内为 Ubuntu：原位重写）
#   "Slot A holds Android; the new Ubuntu set stays in the store"  （槽内为 Android："result": "skipped"）
# 最后 "piano-kernel-update.service: Deactivated successfully."
dpkg -l linux-image-7.2.6-piano<N> | tail -n 1    # 预期 "ii"
sudo dpkg --audit                                 # 预期无输出
sudo python3 -c 'import json; print(json.load(open("/var/lib/piano/switch/ubuntu/manifest.json"))["kernel_release"])'
# 预期 "7.2.6-piano<N>"
sudo piano-boot-switch verify                     # 预期退出码 0
```

钩子重建 `/var/lib/piano/switch/ubuntu/`，把旧组移到 `ubuntu.prev/`；A 槽为 Ubuntu 镜像组时原位重写 A 槽，
下次启动即使用新内核。缺 `boot-profile.json` 或 `boot-signing.pem`，以及持久启动中缺 `boot-partitions.json` 时，
钩子不改存储和 A 槽，退出码 0，并提示装好哪个文件后运行 `piano-boot-update <release>`。
A 槽为 Android 时，之后用 `sudo piano-boot-switch to-ubuntu` 写入新组：在运行中的 Ubuntu 中（§5.4）、在 Android 中用 KernelSU 模块（§6.2），或从主机写入（§6.3）。

### 5.3 重启并核对

```bash
sudo systemctl reboot
uname -r                              # 预期 "7.2.6-piano<N>"
ls -d /lib/modules/$(uname -r)        # 预期该目录存在
sudo piano-boot-switch status         # 预期 "occupant": "ubuntu"
sudo piano-slot-success --check       # 预期 "state": "already-successful"
```

系统内写入不改变槽属性；只有主机 Fastboot 写入会重置（§6.4）。按正式冷启动验收（§8.4）。

### 5.4 写槽前先验证

内核含启动相关改动时推荐；这是经真机验证的顺序。

1. 在 A 槽为 Android 时安装新包，钩子报告 `skipped`（例如在当前内核的调试 RAM 启动中安装）。
2. 把 `/boot/vmlinuz-<new>`、`/boot/initrd.img-<new>`（平板上生成的 initrd）和
   `/usr/lib/linux-image-<new>/piano-boot.dtb` 复制到主机，据此构建调试镜像组（§3.6，带
   `--debug --debug-timeout 900`）。
3. `fastboot boot` 其 `boot-ram-check.img`，按 debug 配置验收（§8.3）。
4. 在该会话中运行 `piano-boot-switch to-ubuntu`，再 `systemctl reboot` 冷启动；或先用
   `piano-reboot-bootloader` 进入 Fastboot 读取槽属性。

### 5.5 移除被取代的内核

只在新内核已从 A 槽启动之后进行。移除会删除其模块；若 `ubuntu.prev/` 启动的是被移除的内核，该组也被删除。

```bash
sudo apt-get remove linux-image-<old release>   # 预期 "Dropped the rollback set of removed kernel <old release>"
sudo apt-get purge linux-image-<old release>    # 删除残留的 depmod 索引文件
```

- 其内核仍安装时 `ubuntu.prev/` 保留；从它启动尚未在设备上验证。
- 只有唯一 release `-piano<N>` 拥有存储，且只写入最新安装的那个。若安装了版本高于 piano release 的发行版内核，
  软件包触发的 `update-initramfs -u` 只重建该内核的 initrd；用 `linux-version list` 检查。
- 新内核无法启动时：电源 + 音量下，然后用 `piano-switch.py` 写入 Android 镜像组或主机上旧 Ubuntu 镜像组的副本（§6.3）。

## 6. 切换系统与恢复

### 6.1 在 Ubuntu 中：`piano-boot-switch`

| 命令 | 写入 | 预期结果 |
|---|---|---|
| `sudo piano-boot-switch status` | 无 | JSON：`occupant`（`android`、`ubuntu`、`ubuntu.prev` 或 `unknown`）、`boot_a` 标志（`retry`、`successful`、`unbootable`）、`backup_gpt`、每组 `equals_slot`、`stray` 条目 |
| `sudo piano-boot-switch verify` | 无 | 退出码 0，每组 `verified` |
| `sudo piano-boot-switch to-android --dry-run` | 无 | `"result": "would-write"` |
| `sudo piano-boot-switch to-android --reboot` | A 槽 | `"result": "written"`，随后重启进入 Android |
| `sudo piano-boot-switch to-ubuntu [--dry-run] [--reboot]` | A 槽 | `written`；A 槽已是该组时为 `unchanged` |
| `sudo piano-boot-switch import-android [--from DIR]` | 存储 | `"result": "imported"`，替换 `android/` |
| `sudo piano-boot-switch import-ubuntu [--from DIR]` | 存储 | `imported`；原 `ubuntu/` 变为 `ubuntu.prev/` |

- 只在 piano 上、已安装的启动配置序列号和分区映射相符、A 槽活动且 B 槽非活动时运行。只有 A 槽为存储中已知镜像组时
  才写入，该组即回退组；`init_boot_a`、`vendor_boot_a`、`boot_a` 逐个写入并全量读回。
- `"result": "refused"`（退出码 1）表示未写入；`code` 与 `message` 说明原因（`occupant-unknown` 时用
  `import-android` 或 `import-ubuntu` 登记槽内内容）。`rolled-back`：原组已写回。`rollback-failed` 或
  `final-mismatch`：不要重启进入 A 槽，从 Fastboot 恢复（§6.3）。
- 日志：`journalctl -t piano-boot-switch`、`/var/lib/piano/switch/switch.log` 与 `state.json`（设备时间，不是实际时间）。
- A 槽内 Android 的 root 补丁变化时（例如 Magisk 改为 KernelSU）更新 `android/`：在 Android 位于槽内时启动的调试
  RAM 会话中执行 `sudo piano-boot-switch import-android`。否则 `to-android` 会写回旧的 `init_boot`。
- 已在测试机上验证：`status`、`verify`、两种 import、`to-ubuntu`、`to-android --reboot`。尚未在设备上验证：
  应用网格中的 **切换到 Android**（`pkexec /usr/sbin/piano-boot-switch to-android --desktop --force`）以及写入失败后的回退。
  当前桌面入口显式忽略重启 inhibitor，请先保存工作；CLI 默认尊重 inhibitor，
  仅 `--reboot --force` 忽略。重启失败与下一次启动目标见[切换说明](FLASHING.zh-CN.md#在-ubuntu-中piano-boot-switch)。

### 6.2 在 Android 中：KernelSU 模块（尚未在设备上验证）

需要 shell 已获 root 的 KernelSU-Next、与 A 槽一致的 `android/` 组，以及 Ubuntu 正常关机（ext4 日志需要恢复时
模块拒绝）。详见 [device/android/piano-switch-ksu](../device/android/piano-switch-ksu/README.md)。

```bash
sh device/android/piano-switch-ksu/build.sh "$PWD/out/ksu"    # 预期 out/ksu/piano-switch-ksu-v0.1.0.zip 与 .sha256
adb -s <SERIAL> push out/ksu/piano-switch-ksu-v0.1.0.zip /data/local/tmp/
adb -s <SERIAL> shell su -c '/data/adb/ksud module install /data/local/tmp/piano-switch-ksu-v0.1.0.zip'
adb -s <SERIAL> reboot
adb -s <SERIAL> shell su -c 'sh /data/adb/modules/piano-switch/piano-switch.sh --check'
# 预期 "CHECK OK: slot A holds the android set and the ubuntu set is ready; nothing written"
adb -s <SERIAL> shell su -c 'ksud module action piano-switch'   # 写入 A 槽并重启
```

退出码 0：已切换或无需操作；1：写入前拒绝；2：写入失败，已恢复 Android 组；3：写入和恢复都失败，不要重启，
用 Fastboot 恢复 Android 组。

### 6.3 从主机：`piano-switch.py`

```bash
# Ubuntu -> Android：先关闭 Ubuntu，再按住电源 + 音量下
python3 tools/piano-switch.py --serial <SERIAL> --images <android-set> --rollback <ubuntu-set>
fastboot -s <SERIAL> reboot
# Android -> Ubuntu
adb -s <SERIAL> reboot bootloader
python3 tools/piano-switch.py --serial <SERIAL> --images <ubuntu-set> --rollback <android-set>
fastboot -s <SERIAL> reboot
# 预期 "Active A boot triplet written; remain in Fastboot for a RAM readback before reboot."
```

- 首次写入前核对 `product: piano`、`unlocked: yes`、`current-slot: a`、`is-userspace: no`、三个分区大小和两组
  `SHA256SUMS`。按 `init_boot_a`、`vendor_boot_a`、`boot_a` 顺序写入；任一写入失败时打印 "Write failed;
  restoring the checked rollback triplet before returning the error." 并写回回退组。
- 每个组目录含 `boot.img`、`init_boot.img`、`vendor_boot.img`（完整分区大小）和 `SHA256SUMS`。三次写入不是原子操作：
  切换中途断电需要从主机恢复。
- 两个方向均已在测试机上验证。

### 6.4 槽属性

```bash
fastboot -s <SERIAL> getvar current-slot          # 预期 "current-slot: a"
fastboot -s <SERIAL> getvar slot-successful:a     # 已标记时为 "yes"
fastboot -s <SERIAL> getvar slot-unbootable:a     # 预期 "no"
fastboot -s <SERIAL> getvar slot-retry-count:a    # 主机写入后为 7，每次未标记的启动减 1
```

| 事件 | 对 A 槽的影响 |
|---|---|
| 主机 `fastboot flash boot_a`（含经 `piano-switch.py`） | 清除 successful，重试计数置 7 |
| 只 `fastboot flash init_boot_a` | 不变 |
| 启动一个未成功的槽 | ABL 将重试计数减 1；减到 0 时该槽变为不可启动 |
| 持久 Ubuntu 启动 | `piano-slot-success.service` 在 `boot-complete.target` 之后置位 bit 54（successful） |
| Android 启动 | Android 启动 HAL 标记该槽 |
| 系统内切换（`piano-boot-switch`、KernelSU 模块） | 不变 |

`piano-slot-success --check` 与 `--dry-run` 从不写入。`--mark` 拒绝调试 RAM 启动、重试计数为 0、备份 GPT 损坏，
以及槽内内容既不是 `ubuntu/` 也不是 `ubuntu.prev/` 的情况。

停在 Fastboot 且 `slot-unbootable:a: yes` 时：`fastboot -s <SERIAL> --set-active=a` 重置 A 槽计数（测试机上同时
提高了 B 槽优先级；B 槽仍不使用），然后切换（§6.3）或 `fastboot -s <SERIAL> reboot`。

### 6.5 平板无法访问时

1. 已开启开发者模式：开机约 22 秒后，`python3 tools/piano-devmode-ssh.py --check` 应输出
   `enx020000880001 piano-<SERIAL> 192.168.88.10 -> 192.168.88.2 ready`。然后
   `sudo piano-reboot-bootloader`（进入 Fastboot）或 `sudo piano-boot-switch to-android --reboot`。
2. 带 `--debug-timeout` 的调试 RAM 启动到时会自行回到 Fastboot；等待 `fastboot devices` 列出 `<SERIAL>`。
3. 否则需要平板旁的人按住电源 + 音量下直到出现 Fastboot，然后按 §6.3 切到 Android。在用户态之前卡住的启动没有计时器。

不要从主机复位 USB 设备，也不要对没有响应的设备重复发送命令；报告状态，等待按键操作。

### 6.6 主机上的镜像组副本

- Android 组：A 槽的备份镜像改名为 `boot.img`、`init_boot.img`、`vendor_boot.img`，再执行
  `sha256sum boot.img init_boot.img vendor_boot.img > SHA256SUMS`。`init_boot` 必须是当前使用的已打 root 补丁的镜像。
- Ubuntu 组：每次更新内核后把存储中的 `ubuntu/` 复制到主机：
  ```bash
  # 平板上
  sudo cp -r /var/lib/piano/switch/ubuntu ~/ubuntu-set && sudo chown -R <USER>: ~/ubuntu-set
  # 主机上
  scp -r <USER>@192.168.88.2:ubuntu-set out/ubuntu-set-current
  (cd out/ubuntu-set-current && sha256sum -c SHA256SUMS)       # 预期三行 "OK"
  ```

## 7. 开发者模式与调试启动

### 7.1 开发者模式（正式启动）

默认关闭。它在普通启动中加入 USB NCM 链路（平板 `192.168.88.2`，主机经 DHCP 获得 `192.168.88.10`）及其上的 SSH。
详见 [device/devmode](../device/devmode/README.md)。

*平板上*：

```bash
sudo systemctl disable --now ssh.socket ssh.service   # 使 SSH 只在 USB 链路上监听
sudo piano-devmode on
piano-devmode status
# 预期 "developer mode: enabled, active"、"USB gadget: bound to a600000.usb"、
#      "SSH socket: active (192.168.88.2 port 22)"
sudo piano-devmode off
```

主机：

```bash
python3 tools/piano-devmode-ssh.py --check              # 预期 "... 192.168.88.10 -> 192.168.88.2 ready"
python3 tools/piano-devmode-ssh.py <USER>               # 登录 shell
python3 tools/piano-devmode-ssh.py -t <USER> sudo piano-reboot-bootloader
```

- 需要 `openssh-server`；没有 `/usr/sbin/sshd` 时 `piano-devmode on` 拒绝。root 不能登录；任何有密码的本地账户都能登录。
  加固：放入 `~/.ssh/authorized_keys`，并在 `/etc/ssh/sshd_config.d/` 中设置 `PasswordAuthentication no`。
- 主机没有取得 DHCP 地址时，工具会打印 `ip` 命令，例如 `sudo ip address replace 192.168.88.10/24 dev enx020000880001`。
- 调试启动（`piano.debug=1`）占用同一链路，开发者模式不在其中启动。
- `piano-reboot-bootloader` 加载 `nvmem_qcom-spmi-sdam` 与 `nvmem-reboot-mode`；没有绑定的 reboot-mode 设备时拒绝重启，
  因为那样平板会重新从 A 槽启动。
- 已在测试机的正式冷启动中验证。
- 默认：发布镜像中关闭。安装过程打开它（[INSTALL.zh-CN.md](INSTALL.zh-CN.md) 第 8a 或 10 步），第 10 步最后执行
  `sudo piano-devmode off`，这是发布版的默认做法。
- 当前设备支持包的套接字带 `BindToDevice=usb0`（NCM 功能的网络设备），并在
  `sys-subsystem-net-devices-usb0.device` 之后启动：仅凭地址时，经 Wi-Fi 到达 192.168.88.2 的连接也会被接受。
  QEMU 中经其他接口路由到该地址的对端被拒绝；尚未在平板上核对。NCM 设备若不叫 usb0，套接字不会启动（`piano-devmode status` 显示 inactive）。

### 7.2 调试 RAM 启动

用与正式镜像组相同的内核、initrd 和 DTB 构建调试镜像组（§3.6，带 `--debug`，通常加 `--debug-timeout 900`），
不写分区，直接启动其 v2 镜像：

```bash
fastboot -s <SERIAL> boot <debug-set>/boot-ram-check.img       # 预期 "Booting OKAY"
ip -brief address show dev enx020000880001                     # 预期约 10 秒内出现 192.168.88.10/24
script -q -c 'telnet 192.168.88.2 2323' private/<run>/console.log
# 预期横幅 "PIANO_PERSISTENT_CONSOLE phase=initrd"（或 "phase=root"）和 "# " 提示符
```

- 控制台是无认证的 root BusyBox shell。initrd 控制台在切根时结束；重新连接即进入根阶段控制台（`phase=root`）。
- 带 `--debug-timeout <s>` 时，平板在该 uptime 同步、加载 reboot-mode 模块并强制重启进入 Fastboot；未保存的工作会丢失。
  `systemctl stop piano-debug-timeout.service` 取消计时，此后须手动返回（`piano-reboot-bootloader`）。
  不带 `--debug-timeout` 时没有计时器。
- 会话剩余不足 60 秒时 `piano-boot-switch` 拒绝写入。`piano-slot-success --mark` 拒绝调试启动。
- 命令行保持简短。较长脚本以文件传输，在平板上核对 SHA-256 后再执行。
- 调试组与正式组的 `vendor_boot.img` 和 `init_boot.img` 相同；`boot.img` 只差命令行。

### 7.3 控制台与内核日志采集

- 在主机上（如上用 `script`）把会话保存到被忽略的 `private/<run>/`；记录主机时间，因为平板时钟每次启动后都不对。
- 内核日志：`dmesg`（从启动起计时），根阶段还可用 `journalctl -k -b`。journal 对切根之前的消息记录的是 journald
  收到的时间（simpledrm 在 journal 中约为 4.3 秒，`dmesg` 中为 0.2 秒）；早期时序以 `dmesg` 为准。
- 在用户态之前停止的启动：用 `--debug-boot-trace` 构建，下次启动时读取 `/sys/fs/pstore/`（Android 中：
  `adb -s <SERIAL> shell su -c 'ls /sys/fs/pstore'`）。跨重启的日志可能不完整。

## 8. 验收

### 8.1 规则

- 进程处于活动状态（`gdm`、`gnome-shell`）、`/dev/fb0` 内容或帧缓冲截图都不是显示通过。硬件判据（DPU 帧计数、
  中断状态、扫描输出内存采样）与人工目视分别记录，不能互相替代。
- 同一软件可能启动到快态或慢态；单一时刻的检查发现不了慢态。每次验收启动都按下列时间与带宽预算检查。
- 新内核或镜像组先在与其内核、initrd 和 DTB 完全相同的调试 RAM 启动中验收（`debug` 配置，§8.3），再经开发者模式
  验收正式冷启动（`cold` 配置，§8.4）。
- `tools/piano_boot_acceptance.py` 以数据形式保存判据与预算，并对保存的采集结果求值。每项判据为 PASS、FAIL 或
  UNAVAILABLE 并附实测值；缺少输入从不判 FAIL。总结论为 PASS、FAIL 或 INCOMPLETE（退出码 0、1 或 3）。

### 8.2 预算

`python3 tools/piano_boot_acceptance.py criteria` 打印当前清单：

| 判据 | 预算 |
|---|---|
| 分类 | FAST：切根 ≤ 18 秒、simpledrm 到 fb0 ≤ 0.5 秒、40 秒时 gdm 已活动 |
| simpledrm | 初始化 < 1 秒（内核日志时间，不用 journal 接收时间） |
| 扫描输出时钟 | `gcc_disp_hf_axi_clk` 自首个 initrd 控制台起及根阶段均由 framebuffer 持有（clk_summary 与 CBCR） |
| 切根 | `UserspaceTimestampMonotonic` ≤ 10 秒 |
| GDM | ≤ 20 秒进入活动状态并保持活动 |
| GMU | 首次 `Loaded GMU firmware` ≤ 20 秒 |
| GPU 错误 | 无 GMU 初始化超时、OOB、hangcheck 或 GMU/Adreno 错误行，观察到 ≥ 180 秒 |
| 内存 | 每次 256 MiB 内存拷贝 ≥ 2000 MB/s |
| 扫描输出 | INTF1、INTF2 为 100–140 Hz，中断状态不变，≥ 5 种颜色，最常见颜色 `222222`（GDM 登录界面） |
| 触控、蓝牙 | THP 首帧且无 SPI/GPI 错误；`hci0` UP RUNNING、Powered yes |
| 系统 | 没有失败单元；rpmhpd 与每个互连提供者均已同步 |
| 身份 | 预期 release 运行于根阶段，只加载其自身模块，`CONFIG_SM_DISPCC_8750=y` |
| 仅 `debug` | 前后两次 Fastboot 读数中的槽属性不变 |
| 仅 `cold` | 存储 manifest 的 release 等于 `uname -r`；`piano-boot-switch status` 的 occupant 为 `ubuntu` |

### 8.3 调试验证启动（`debug` 配置）

证据目录布局：`initrd-*.txt`（首个 initrd 控制台）、`root-*.txt`（根阶段记录，文件名顺序即时间顺序）、
可选的 `kmsg*.txt` / `journal*.txt`、`getvar-before.txt` 和 `getvar-after.txt`。

```bash
R=private/<run>; mkdir -p $R
python3 tools/piano_boot_acceptance.py script --initrd > $R/initrd.sh
python3 tools/piano_boot_acceptance.py script > $R/root.sh
getvars() { date -Iseconds; for v in product unlocked current-slot is-userspace slot-successful:a \
  slot-retry-count:a slot-unbootable:a slot-successful:b slot-retry-count:b slot-unbootable:b \
  battery-voltage battery-soc-ok; do fastboot -s <SERIAL> getvar $v 2>&1 | grep -v '^Finished'; done; }
getvars > $R/getvar-before.txt
fastboot -s <SERIAL> boot <debug-set>/boot-ram-check.img
ip -brief address show dev enx020000880001                           # 等待出现 192.168.88.10/24
python3 -m http.server --bind 192.168.88.10 --directory $R 8000 &     # 提供两个脚本
script -q -c 'telnet 192.168.88.2 2323' $R/initrd-0006s.txt          # 首个 initrd 控制台
```

*平板上*，在该控制台中执行（根阶段同样执行 `root.sh`，约 40 秒时记录为 `$R/root-0041s.txt`，180 秒后记录为
`$R/root-0190s.txt`）：

```sh
B=/usr/libexec/piano/busybox
$B wget -q -O /run/initrd.sh http://192.168.88.10:8000/initrd.sh && $B sha256sum /run/initrd.sh && sh /run/initrd.sh
# 先与主机上 sha256sum 的结果比对，再采信输出
```

会话回到 Fastboot 之后：

```bash
kill %1                                                               # 停止 HTTP 服务
getvars > $R/getvar-after.txt
python3 tools/piano_boot_acceptance.py evaluate --dir $R --release <release> --profile debug --json $R/verdict.json
# 预期每项判据一行 PASS，退出码 0
```

扫描输出判据需要 DPU 扫描输出探针，该探针在本仓库之外维护（`live --scanout-probe`、`script --scanout-probe`）；
没有它时该判据为 UNAVAILABLE，总结论最多为 INCOMPLETE。此时改为记录手工帧计数检查（§8.5），作为单独的硬件结果。

### 8.4 正式冷启动（`cold` 配置）

```bash
python3 tools/piano_boot_acceptance.py live --user <USER> --sudo --profile cold --release <release> --save private/<run>
# 预期每项判据一行 PASS，生成 private/<run>/verdict-*.json，退出码 0
```

`live` 先用 `piano-devmode-ssh.py --check` 检查链路，执行一次读取并求值。带 `--sudo` 时，在此输入的密码作为
`sudo -S` 的第一行输入；密码错误会终止读取。

### 8.5 手工帧计数检查（平板上以 root 执行）

```bash
bb=/usr/libexec/piano/busybox
a=$($bb devmem 0x0ae360ac 32); b=$($bb devmem 0x0ae370ac 32); i=$($bb devmem 0x0ae01014 32); sleep 0.5
echo "INTF1 $a $($bb devmem 0x0ae360ac 32)  INTF2 $b $($bb devmem 0x0ae370ac 32)  TOP0 $i $($bb devmem 0x0ae01014 32)"
# 预期两个 INTF 计数的第二个值各大约多 0x3c，两个 TOP0 值相同
```

这些寄存器是固件扫描输出的 INTF1、INTF2 `FRAME_COUNT` 与 MDP TOP0 中断状态；读取不改变任何状态。TOP0 中早期启动
锁存的欠载位是历史记录而不是频率；只有两次读数之间的变化才算。该检查只覆盖刷新率，不覆盖画面内容。

### 8.6 闪光灯 torch 检查（在平板上以 root 运行）

```bash
python3 tools/check-piano-flash.py            # 读出每路闪光灯的限值与当前 torch
python3 tools/check-piano-flash.py --torch 64 # 写入 torch 值并读回
```

相机的闪光灯是标准 LED class 设备，发行版里的程序可以直接使用，运行时不依赖本仓库的助手脚本。
脚本读的是控制器状态；是否真的发光只能在平板上目视确认。

## 9. 证据与文档

- 状态等级：*待调研*、*源码线索*、*离线通过*、*真机通过*、*真机失败*。未测试不等于失败。源码存在、probe 或服务处于
  活动状态、QEMU 和构建成功都不是真机通过。
- 每个真机结论绑定设备型号与原厂版本、内核提交与 release、集成提交、镜像组的 `SHA256SUMS`、测试场景（调试 RAM
  启动或正式冷启动）和证据位置。其他平板或其他候选的结果不能沿用。
- 保留历史失败；之后的通过结论写明自身适用范围，而不是覆盖失败记录。
- 公开状态维护在 [README 硬件支持表](../README.zh-CN.md)（每项一行）和 [STATUS.md](STATUS.md)（测试配置、已知问题、
  路线图）。原始日志、采集数据和设备记录放在被忽略的 `private/` 目录，从不提交。
- 硬件报告写明设备型号、原厂版本、内核 release 和镜像组身份，去除序列号、蓝牙地址、标定数据和个人信息。
- 项目内核提交不带 `Signed-off-by`、`Fixes:` 或 `Tested-by` 标签；上游投递前由人工贡献者补充。代理不添加人工署名。
