# 全新安装

[English](INSTALL.en.md) ｜ [项目首页](../README.zh-CN.md) ｜ [Fastboot、切换与恢复](FLASHING.zh-CN.md)

本指南把发布版本安装到小米平板 8 Pro（`piano`）的 Android 旁边。目前没有图形安装器：可以自己按本文操作，也可以按[项目首页](../README.zh-CN.md#推荐让-ai-帮你安装)把它交给 AI 助手执行。写入新分区表（[第 6 步](#6-写入分区表平板上)）在发布目录的维护 RAM 镜像中进行。开始前请通读全文。本流程已在测试机上按 v0.2.0 完整执行过一次。

每个版本都沿用此安装流程。已经安装过的系统请按[升级指南](UPGRADE.zh-CN.md)升级，不要重新分区或刷根镜像。

## 先读

- **会清除全部 Android 数据。** `userdata` 被缩小并重建，`metadata` 被清空。
- **只测试过一台。** 型号 25091RP04C（国行）、512 GB UFS、A 槽原厂版本 `OS3.0.303.0.WPYCNXM`。
  其他型号、容量和原厂版本未测试；预构建启动镜像组只适用于 `OS3.0.303.0.WPYCNXM`。
- **Bootloader 必须已解锁**，通过小米官方流程。
- **先备份**（[第 2 步](#2-备份)）。原厂 A 槽镜像是回到 Android 的依据。
- 操作边界、电量检查、设备绑定与失败后的处理统一见
  [Fastboot、切换与恢复](FLASHING.zh-CN.md#绝不写入)；当前充电支持见 [STATUS.md](STATUS.md)。

因尚无项目工具而使用标准命令的步骤标为*手工*。

## 准备

| 项目 | 说明 |
|---|---|
| 小米平板 8 Pro，原厂 `OS3.0.303.0.WPYCNXM`，A 槽活动 | Android 中可用 `adb` |
| Android root | 用 Magisk 或 KernelSU-Next 修补 `init_boot_a`，用于读取分区（*手工*，按该项目的说明操作） |
| 小米磁吸键盘 | 安装本机触控标定之前唯一的输入方式（[第 10 步](#10-安装本机文件)） |
| Linux x86_64 主机 | Android platform-tools（`adb`、`fastboot`）、Python 3.11 或更高版本、Git、`tar`、`zstd`、`openssl`、`uuidgen`、`sgdisk`、`sfdisk`、`losetup`、`mkfs.ext4`、`img2simg`、`sha256sum`、`ip`（iproute2）、包含 `scp` 的 OpenSSH 客户端、`telnet` 和 `nc`（OpenBSD netcat） |
| AOSP `avbtool.py` | 由 [tools/aosp-avb.json](../tools/aosp-avb.json) 固定，按下方命令获取并校验 |
| 本仓库 | 检出到发布版 `RELEASE-NOTES.md` 中的集成提交；在下方设置 REPO 后获取 AOSP mkbootimg |
| 发布目录 | 同一版本的全部文件，不与其他版本混用 |
| 主机可用空间 | 约 40 GB：备份（测试机 16 GB）、发布目录（约 9 GB）与数据镜像 |

发布目录按[项目首页「手动安装」](../README.zh-CN.md#手动安装)从发布附件还原；只用同一版本的完整目录，不要混入其他版本的文件。

REPO 为发布记录提交的集成仓检出目录的绝对路径，RELEASE 为解压后的发布目录，WORK 为仓库之外的私有安装工作目录。后续主机命令均在 WORK 中执行；源码工具使用 `"$REPO/tools/NAME"`，发布文件使用 `"$RELEASE/PATH"`。
每个使用的主机终端都需设置这些路径。下一次校验检查 RELEASE 中的全部文件。

```bash
REPO=/absolute/path/to/xiaomipad-8pro-mainline
RELEASE=/absolute/path/to/extracted-release
WORK=/absolute/path/to/private-install-work
mkdir -p "$WORK"
cd "$WORK"
sh "$REPO/tools/fetch-aosp-mkbootimg.sh"
sh "$REPO/tools/fetch-aosp-avb.sh"
(cd "$RELEASE" && sha256sum -c SHA256SUMS)
```

发布目录包含 `boot/`（A 槽的 Ubuntu 启动镜像组、`boot-ram-check.img` 和 `boot-ram-maintenance.img`）、`rootfs/root.sparse.img`（60 GiB 根）、
`debs/`、`tools/piano-switch.py` 和 KernelSU 模块，内容说明见 [RELEASE.md](RELEASE.md)（英文）。

镜像预装内核包和全部项目软件包（设备支持、固件、Mesa、桌面默认设置、snap 显卡供应方、MiPPS 认证、传感器），版本以发布说明为准。GStreamer good/bad 插件已装入；传感器只带默认标定，没有任何设备的私有标定。

## 1. 解锁并核对原厂版本

用小米官方工具解锁 Bootloader（会清除平板数据），必要时刷回该原厂版本，并取得 Android root。然后：

```bash
adb -s <SERIAL> shell getprop ro.product.device              # 预期 "piano"
adb -s <SERIAL> shell getprop ro.build.version.incremental   # 预期 "OS3.0.303.0.WPYCNXM"
adb -s <SERIAL> shell getprop ro.boot.slot_suffix            # 预期 "_a"
adb -s <SERIAL> shell su -c id                               # 预期 "uid=0(root) ..."
adb -s <SERIAL> shell dumpsys battery | grep level           # 预期 40 或更高
adb -s <SERIAL> reboot bootloader
fastboot -s <SERIAL> getvar unlocked                         # 预期 "unlocked: yes"
fastboot -s <SERIAL> getvar current-slot                     # 预期 "current-slot: a"
fastboot -s <SERIAL> reboot
```

任何一项不符就停止。

## 2. 备份

*手工。* 备份除 `userdata` 外的全部分区，以及每个 UFS LUN 的分区表。镜像数据用 `adb exec-out`
（可安全传输二进制），不要用 `adb shell`。备份放在仓库之外。

```bash
mkdir -p backup/gpt
adb -s <SERIAL> shell su -c 'for d in /sys/block/sd?; do echo ${d##*/} $(cat $d/size) $(cat $d/queue/logical_block_size); done'
# 预期六个 LUN，逻辑块大小 4096；size 以 512 字节为单位
adb -s <SERIAL> shell su -c 'ls -l /dev/block/by-name/'     # 分区名 -> /dev/block/sdXN

# 除 userdata 外的每个分区
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/by-name/<label> bs=1048576 2>/dev/null' > backup/<label>.img
adb -s <SERIAL> shell su -c 'sha256sum /dev/block/by-name/<label>'    # 必须与下一行相同
sha256sum backup/<label>.img

# 每个 LUN sdX：MBR 与主 GPT、备份 GPT；<blocks> = size / 8
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/sdX bs=4096 count=34 2>/dev/null' > backup/gpt/sdX-head.bin
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/sdX bs=4096 skip=<blocks-34> 2>/dev/null' > backup/gpt/sdX-tail.bin
```

- 记录清单：每个文件的分区名、设备节点、LUN、大小与 SHA-256，以及每个 LUN 的磁盘 GUID。
  `sdX` 盘符每次启动可能不同，只有这份清单能把文件对应到分区。
- `persist` 与 `metadata` 由 Android 读写挂载，读取期间可能变化：计算两次哈希，不一致就重读。

用备份生成 **Android 镜像组**。`init_boot` 必须是 A 槽当前使用的、已打 root 补丁的镜像：

```bash
mkdir android-set
cp backup/boot_a.img android-set/boot.img
cp backup/init_boot_a.img android-set/init_boot.img
cp backup/vendor_boot_a.img android-set/vendor_boot.img
(cd android-set && sha256sum boot.img init_boot.img vendor_boot.img > SHA256SUMS)
```

在主机上保留 `android-set/` 和发布目录 `boot/` 的副本：切换与恢复都用这两组镜像（[FLASHING.zh-CN.md](FLASHING.zh-CN.md)）。

## 3. 收集本机文件

这些文件属于你的平板，不要分享或提交，也不要使用其他平板的文件。它们在[第 10 步](#10-安装本机文件)装入 Ubuntu。

| 文件 | 缺少时 |
|---|---|
| `thp-calibration.json` | 没有触控输入 |
| `bluetooth-address` | 蓝牙控制器不完成配置 |
| `wlan-address` | Wi-Fi 每次启动使用随机 MAC 地址 |
| `boot-profile.json`、`boot-signing.pem` | 无法重建启动镜像组：地址不生效，内核更新也不会改写 A 槽 |
| `boot-partitions.json`（第 8a 或 10 步生成） | `piano-boot-switch` 拒绝切换，内核与 initrd 更新不改动启动镜像组和 A 槽 |

**触控标定。** 平板的屏幕有 CSOT 和 BOE 两种。发布版会自动识别屏型，并加载对应的触控固件和方向；
只有标定需要你自己提供。原厂 ODM 分区有 `piano_nova_csot_thp_config.ini`（CSOT 面板，与测试机相同）和
`piano_nova_boe_thp_config.ini`。使用原厂驱动加载的那一份，原厂内核日志里写着文件名
（`dmesg` 没有输出时，重启 Android 后立即再查一次）。

```bash
adb -s <SERIAL> shell su -c 'dmesg | grep config_file_name'   # ...=piano_nova_csot_thp_config.ini 或 ..._boe_...
adb -s <SERIAL> shell su -c 'find /odm -name "piano_nova_*_thp_config.ini"'
adb -s <SERIAL> exec-out su -c 'cat <ini 路径>' > thp.ini
python3 "$REPO/tools/extract-thp-calibration.py" --input thp.ini --output thp-calibration.json
```

**蓝牙地址。**

```bash
adb -s <SERIAL> shell settings get secure bluetooth_address > bluetooth-address
cat bluetooth-address                  # 预期一行 XX:XX:XX:XX:XX:XX
```

**Wi-Fi 地址**（*手工*）。原厂地址是 `persist` 中 `wlan/wlan_mac.bin` 的一行 `wlan0=<12 位十六进制>`，
Android 在 WLAN 固件目录中链接到它。转换为 `XX:XX:XX:XX:XX:XX`：

```bash
adb -s <SERIAL> exec-out su -c 'cat /vendor/firmware/wlan/qca_cld/peach_v2/wlan_mac.bin' \
  | sed -n 's/^wlan0=\(..\)\(..\)\(..\)\(..\)\(..\)\(..\).*/\1:\2:\3:\4:\5:\6/p' > wlan-address
cat wlan-address                       # 预期一行 XX:XX:XX:XX:XX:XX
```

**签名密钥。** 你自己构建的启动镜像组使用的 AVB 密钥：

```bash
openssl genrsa -out boot-signing.pem 4096
```

**启动配置**（*手工*，尚无生成工具）。启动镜像必须带原厂头部字段、回滚索引与 AVB 属性，否则 ABL
会把该槽标为不可启动。按原厂镜像的数值编写 `boot-profile.json`：

```
{
  "serial": "<SERIAL>",
  "boot":        {"header_version": 4, "capacity": 100663296, "rollback_index": <int>, "avb_properties": {<props>}},
  "init_boot":   {"header_version": 4, "capacity": 8388608,   "rollback_index": <int>, "avb_properties": {<props>}},
  "vendor_boot": {"header_version": 4, "capacity": 100663296, "rollback_index": <int>, "avb_properties": {<props>},
                  "page_size": <int>, "kernel_addr": <int>, "ramdisk_addr": <int>, "tags_addr": <int>, "dtb_addr": <int>}
}
```

```bash
python3 "$REPO/tools/local/aosp-mkbootimg/unpack_bootimg.py" --boot_img backup/vendor_boot_a.img --out /tmp/vb
# 头版本、页大小、kernel/ramdisk/tags 加载地址、dtb 地址
python3 "$REPO/tools/local/aosp-avb/avbtool.py" info_image --image backup/boot_a.img
# "Rollback Index:" 与所有 "Prop: com.android.build.boot.*" 行；init_boot_a、vendor_boot_a 同样处理
```

`capacity` 是分区字节数。把每个镜像的全部 `Prop:` 写进对应的 `avb_properties`；`boot` 至少需要
`com.android.build.boot.os_version` 与 `com.android.build.boot.security_patch`。发布目录的
`boot/boot-profile.json` 展示了该原厂版本的同一结构（不含序列号）。

### 推荐：准备本机传感器 registry

本机 registry 带有这台平板的出厂传感器标定。只用默认标定时，有的机器出现过传感器和声音失效。
在主机用这台平板自己的 persist 提取备份执行；输入是含内层 `registry/` 的外层 `sensors/registry/`。这个私有目录和 tar 不得分享，也不放入发布镜像。第 8a 步在首次 Ubuntu 启动前安装它。

```bash
python3 "$REPO/tools/stage-sensor-registry.py" \
  --registry /path/to/own/extracted/persist/sensors/registry \
  --output "$WORK/private-sensor-payload"
tar -C "$WORK/private-sensor-payload" -cf "$WORK/sensor-registry.tar" usr
sha256sum "$WORK/sensor-registry.tar"
```

不提供时服务会从原厂静态配置生成默认标定；普通重启不会覆盖已经生成的状态。后装私有标定须按[传感器说明](../device/sensors/README.zh-CN.md)处理现有状态。

## 4. 分区布局

LUN 0 上原 `userdata` 的范围拆成三个分区，其他分区和 LUN 不变。

| 条目 | 名称 | 512 GB 机型 | 256 GB 机型 | 用途 |
|---|---|---|---|---|
| 34 | `userdata` | 200 GiB | 110 GiB | Android 数据；保留起点、类型 GUID、唯一 GUID 和属性 |
| 35 | `linux_rootfs` | 60 GiB | 60 GiB | Ubuntu `/` |
| 36 | `linux_data` | 余下约 201.27 GiB | 余下约 60.93 GiB | Ubuntu `/home` |

用第 2 步得到的 LUN 0 大小区分容量：`size / 8` 是 4096 字节块数，512 GB 机型为 124,552,192（约 475 GiB），
256 GB 机型为 64,167,936（约 245 GiB）。1 GiB 等于 262,144 块。256 GB 一列是一台 256 GB 机器的实测值：
`userdata` 28,835,840 块、`linux_rootfs` 15,728,640 块、`linux_data` 15,971,323 块，结束于最后一个可用 LBA。

发布版按名称查找分区，名称必须严格为 `linux_rootfs` 和 `linux_data`；两者的 GUID 可以是任意新值。

## 5. 生成新分区表（主机）

*手工。* 只在 LUN 0 分区表的副本上操作，不要直接对平板操作。LUN 0 块大小为 4096 字节
（512 GB 机型共 124,552,192 块，256 GB 机型共 64,167,936 块）。

```bash
ROOT_UUID=$(uuidgen); DATA_UUID=$(uuidgen)
BLOCKS=<LUN 0 size / 8>                         # 取自第 2 步
truncate -s $((BLOCKS * 4096)) lun0.img
dd if=backup/gpt/<lun0>-head.bin of=lun0.img bs=4096 conv=notrunc
dd if=backup/gpt/<lun0>-tail.bin of=lun0.img bs=4096 seek=$((BLOCKS - 34)) conv=notrunc
LOOP=$(sudo losetup --find --show --sector-size 4096 lun0.img)
sudo sgdisk --print "$LOOP"       # 预期 "Sector size (logical): 4096 bytes" 以及你的分区表
sudo sgdisk --info=34 "$LOOP"     # 预期 "Partition name: 'userdata'"；记下起始扇区、类型 GUID、唯一 GUID、属性
sudo sfdisk --json "$LOOP" > gpt-before.json

S=<userdata 起始扇区>             # 测试机为 3632128
U=<userdata 唯一 GUID>; T=<userdata 类型 GUID>
A=52428800                        # userdata 块数：512 GB 机型 200 GiB x 262144；256 GB 机型 28835840（110 GiB）
R=15728640                        # linux_rootfs 块数：60 GiB x 262144
L=0FC63DAF-8483-4772-8E79-3D69D8477DE4      # Linux filesystem
sudo sgdisk --set-alignment=256 --delete=34 \
  --new=34:$S:$((S + A - 1)) --typecode=34:$T --partition-guid=34:$U --change-name=34:userdata \
  --new=35:$((S + A)):$((S + A + R - 1)) --typecode=35:$L --partition-guid=35:$ROOT_UUID --change-name=35:linux_rootfs \
  --new=36:$((S + A + R)):0 --typecode=36:$L --partition-guid=36:$DATA_UUID --change-name=36:linux_data \
  "$LOOP"
sudo sgdisk --verify "$LOOP"      # 预期 "No problems found."
sudo sfdisk --json "$LOOP" > gpt-after.json
```

比较 `gpt-before.json` 与 `gpt-after.json`：只允许条目 34–36 不同，起点分别为 `S`、`S + A`、
`S + A + R`。`linux_data` 占用其余空间直到最后一个可用 LBA，共 `LAST - S - A - R + 1` 块
（512 GB 测试机 52,762,619 块，256 GB 机器 15,971,323 块）。磁盘 GUID、首末可用 LBA 和表长度不得改变。
若 `userdata` 原有非零属性，用 `--attributes=34:=:<hex>` 复制。

取出两段分区表区域（首个可用 LBA 为 6；最后可用 LBA 在 512 GB 机型为 124552186，256 GB 机型为
64167930，即 LUN 0 末尾前 6 块）：

```bash
FIRST=<first usable LBA>; LAST=<last usable LBA>        # 取自 sgdisk --print
sudo dd if="$LOOP" of=new-primary.bin bs=4096 count=$FIRST
sudo dd if="$LOOP" of=new-backup.bin bs=4096 skip=$((LAST + 1))
dd if=backup/gpt/<lun0>-head.bin bs=4096 count=$FIRST 2>/dev/null | sha256sum    # 当前主分区表区域
sha256sum new-primary.bin new-backup.bin
sudo losetup -d "$LOOP"
```

## 6. 写入分区表（平板上）

*在项目环境中手工执行命令。* `boot/boot-ram-maintenance.img` 从 RAM 启动发布版内核和一个小 initramfs：
带 UFS 驱动、经 USB 线提供的 root shell（192.168.88.2，telnet 端口 2323，无密码；该链路只连这根线）以及本步所需工具
（`sgdisk`、`sfdisk`、`lsblk`、`partx`、`blockdev`、`dd`、`sha256sum`、`wget`）。它不挂载任何 UFS 盘的分区，自身不写任何内容，
A 槽仍为 Android。10 分钟内没有打开的会话（拔线后同样计时）时，它会自行重启进入 Fastboot。

在主机上，平板处于 Fastboot，当前目录里有第 5 步的 `new-primary.bin` 和 `new-backup.bin`：

```bash
fastboot -s <SERIAL> boot "$RELEASE/boot/boot-ram-maintenance.img"    # 预期 "Booting OKAY"
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check         # 预期 "... 192.168.88.10 -> 192.168.88.2 ready"（约 5 s）
python3 -m http.server --bind 192.168.88.10 8000 &                    # 向平板提供这两个文件
telnet 192.168.88.2 2323
```

`--check` 报告链路就绪后再启动 `http.server`：地址 192.168.88.10 只在链路建立后存在（否则报
`Cannot assign requested address`）。若报 `Address already in use`，说明已有其他服务占用该端口（可能监听全部地址），
请改用空闲端口并同步修改下面的 `wget` 行；下载文件的哈希核对能发现连错服务器。

如果 `--check` 打印的是 `ip` 命令，执行后再检查一次。登录后会打印 LUN 表（LUN、磁盘、以 512 字节扇区计的大小、块大小、设备路径）、
`Mounted from the UFS disks: none` 和 LUN 0 的 `sgdisk -p` 分区表。LUN 0 是以 `0` 开头的那一行，size 在 512 GB 机型为
996417536、256 GB 机型为 513343488，块大小 4096；核对该分区表就是第 2 步备份的分区表。然后在 telnet 会话中：

```sh
FIRST=<first usable LBA>; LAST=<last usable LBA>; BLOCKS=<LUN 0 blocks>    # 第 5 步的数值
disk=/dev/sdX                                           # 登录表中的 LUN 0
piano-maint map                                         # 再看一次："LUN 0: /dev/sdX"、"Mounted from the UFS disks: none"
wget -O /run/new-primary.bin http://192.168.88.10:8000/new-primary.bin
wget -O /run/new-backup.bin http://192.168.88.10:8000/new-backup.bin
sha256sum /run/new-primary.bin /run/new-backup.bin      # 预期等于第 5 步的哈希
dd if=$disk bs=4096 count=$FIRST | sha256sum            # 预期等于第 5 步当前主分区表区域的哈希
dd if=/run/new-backup.bin of=$disk bs=4096 seek=$((LAST + 1)) conv=fsync
dd if=$disk bs=4096 skip=$((LAST + 1)) count=$((BLOCKS - LAST - 1)) | sha256sum   # 预期等于 new-backup.bin
dd if=/run/new-primary.bin of=$disk bs=4096 conv=fsync
dd if=$disk bs=4096 count=$FIRST | sha256sum            # 预期等于 new-primary.bin
sync
sgdisk --verify $disk                                   # 预期 "No problems found."
sgdisk -p $disk                                         # 预期 34–36 项为 userdata、linux_rootfs、linux_data
piano-maint fastboot                                    # 重启进入 Fastboot
```

先写备份分区表，再写主分区表，每次读回比对通过后再继续。重启前内核仍使用旧的分区列表；本会话中不要挂载或格式化任何分区。
完成后停止主机上的 `http.server`。

- `piano-maint status` 显示空闲计数；`piano-maint keepalive` 重新计数，`piano-maint idle SECONDS`（30–3600）修改时限。
  打开的 telnet 会话只在连着线时才阻止计时。
- 如果 `piano-maint fastboot` 提示 `nvmem-reboot-mode is not bound`，它不会重启；请按电源 + 音量下。


## 7. 写入文件系统（Fastboot）

核对新分区，再按 `linux_data` 的大小生成 `/home` 镜像：

```bash
fastboot -s <SERIAL> getvar partition-size:userdata       # 预期 "3200000000"（十六进制，不带 0x）；256 GB 机型为 102 GiB
fastboot -s <SERIAL> getvar partition-size:linux_rootfs   # 预期 "0xF00000000"
fastboot -s <SERIAL> getvar partition-size:linux_data     # linux_data 块数 x 4096 的十六进制：512 GB 机型为 "0x32517FB000"
truncate -s $((0x<linux_data 大小>)) data.ext4
mkfs.ext4 -L linux_data data.ext4 && img2simg -s data.ext4 data.sparse.img     # 约 6 MB
```

256 GB 机型上 ABL 报告的 `userdata` 为 102 GiB（`1980000000`），比 GPT 中的 110 GiB 小 8 GiB。
这是正常现象，与原厂一致：原厂 Android 的 `/data` 同样比分区小 8 GiB。

擦除 Android 数据并写入两个文件系统：

```bash
fastboot -s <SERIAL> erase userdata
fastboot -s <SERIAL> erase metadata
python3 "$REPO/tools/sparse_image.py" summary "$RELEASE/rootfs/root.sparse.img" data.sparse.img   # 平板将写入的量
fastboot -s <SERIAL> flash linux_rootfs "$RELEASE/rootfs/root.sparse.img"   # 数分钟；写入 8.7 至 10.1 GB
fastboot -s <SERIAL> flash linux_data data.sparse.img                      # 数秒
# 预期每个 Sending 与 Writing 行均为 "OKAY"，最后 "Finished."
```

平板只写稀疏镜像中实际使用的块（约 9 GB），跳过空闲块；`img2simg -s` 同样不写 `data.sparse.img` 的空闲块。

**`OKAY` 不代表写入已完成。** 引导程序在数据传完时即应答，随后在后台继续写入；下一条 Fastboot 命令会等它写完。命令等待期间不要拔线、复位或关机，已返回 `OKAY` 的写入不要重复。
Android 下次启动时重建 `userdata` 与 `metadata`；全部 Android 数据（包括 root 管理器的配置）都会丢失。

## 8. 可选：不写 A 槽先试启动

```bash
fastboot -s <SERIAL> boot "$RELEASE/boot/boot-ram-check.img"     # 预期 "Booting OKAY"
```

它从 RAM 启动一次已安装的根，内核、initrd 与命令行与发布镜像组相同，不写任何启动分区。
这是没有调试控制台的普通启动：用电源 + 音量下回到 Fastboot。

## 8a. 推荐：首次启动前安装本机文件

可以用[第 6 步](#6-写入分区表平板上)的维护镜像，在 Ubuntu 首次启动前生成分区映射，把它与触控标定、地址一起放进新根，并打开开发者模式。
这样初始设置中触控可用，账户建好后主机即可经 SSH 连接平板。不做本步时，初始设置使用磁吸键盘，并在第 10 步复制文件。

boot-profile.json 与 boot-signing.pem 留在主机上，到第 10 步再安装，以便在 Android 恢复镜像组准备好之后重建启动镜像组。

平板处于 Fastboot，主机当前目录里有第 3 步的文件：

```bash
fastboot -s <SERIAL> boot "$RELEASE/boot/boot-ram-maintenance.img"    # 预期 "Booting OKAY"
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check         # 预期 "... ready"
python3 -m http.server --bind 192.168.88.10 8000 &                    # 空闲端口，同第 6 步
telnet 192.168.88.2 2323
```

在主机的第二个终端中（同一目录）等待启动 LUN 的分区表：

```bash
nc -l 192.168.88.10 8001 | python3 "$REPO/tools/extract-boot-partitions.py" --input - \
  --profile boot-profile.json --output boot-partitions.json
```

在 telnet 会话中发送（也可换用其他空闲端口）：

```sh
sfdisk --json /dev/sdY | nc 192.168.88.10 8001      # sdY：登录表中的 LUN 4
```

第二个终端打印 `init_boot_a`（8388608 B）、`vendor_boot_a` 与 `boot_a`（各 100663296 B），并写出 `boot-partitions.json`。
没有 `boot_a` 与 `boot_b` 的磁盘、大小与启动配置不符时工具拒绝；磁盘不是 LUN 4 时 `piano-boot-switch` 之后会拒绝该映射。然后在主机上：

```bash
sha256sum thp-calibration.json bluetooth-address wlan-address boot-partitions.json
```

再在 telnet 会话中：

```sh
disk=/dev/sdX                                       # 登录表中的 LUN 0
for p in /sys/class/block/${disk##*/}*; do grep -qx PARTNAME=linux_rootfs $p/uevent && root=/dev/${p##*/}; done
echo $root                                          # LUN 0 的第 35 项，例如 /dev/sda35
mkdir -p /mnt && mount -t ext4 $root /mnt
for f in thp-calibration.json bluetooth-address wlan-address boot-partitions.json; do
  wget -O /mnt/etc/piano/$f http://192.168.88.10:8000/$f && chmod 600 /mnt/etc/piano/$f
done
sha256sum /mnt/etc/piano/*                          # 四个哈希必须与主机一致
mkdir -p /mnt/etc/systemd/system/usb-gadget.target.wants
ln -sf /usr/lib/systemd/system/piano-devmode.service /mnt/etc/systemd/system/usb-gadget.target.wants/
# 推荐：第 3 步准备的 sensor-registry.tar（没有时跳过这四行）
wget -O /tmp/sensor-registry.tar http://192.168.88.10:8000/sensor-registry.tar
sha256sum /tmp/sensor-registry.tar                  # 必须与主机一致
tar -xf /tmp/sensor-registry.tar -C /mnt
chown -R 0:0 /mnt/usr/share/piano/sensors/registry-private
sync; umount /mnt
piano-maint fastboot
```

此环境中 `lsblk` 不显示分区标签，上面的循环从 sysfs 读取。该符号链接就是 `piano-devmode on` 所创建的。


## 9. 写入 A 槽并首次启动

核对电量，再把发布镜像组写入 A 槽。`piano-switch.py` 先检查平板和两组镜像，写入失败时写回 Android 镜像组：

```bash
fastboot -s <SERIAL> getvar battery-soc-ok         # 预期 "battery-soc-ok: yes"
python3 "$REPO/tools/piano-switch.py" --serial <SERIAL> --images "$RELEASE/boot" --rollback android-set
# 预期 "Active A boot triplet written; remain in Fastboot for a RAM readback before reboot."
fastboot -s <SERIAL> reboot
```

- **GNOME 初始设置**创建第一个账户。做过[第 8a 步](#8a-推荐首次启动前安装本机文件)时触控可用；否则使用磁吸键盘（第 10 步之前触控不可用）。
  跳过网络页面。必须有人在平板旁：账户建好且开发者模式打开（第 8a 或 10 步）之前，主机无法连接平板
  （开发者模式需要账户且拒绝 root 登录），唯一的退路是电源 + 音量下。
- **计算机名。**账户页的“计算机”栏默认填入你的用户名，初始设置把它设为主机名，替换镜像的 `xiaomipad-8pro`；
  需要别的名字时修改该栏，或之后运行 `sudo hostnamectl hostname --static <名字>`。蓝牙显示的友好名称“Xiaomi Pad 8 Pro”保持不变。
- **时钟。**硬件时钟不可写：启动时恢复上次关机时保存的时间，联网后自动校时；没有网络时手动设置 `sudo date -s 'YYYY-MM-DD HH:MM'`。
- 启动完成后该槽被标记为启动成功：`sudo piano-slot-success --check` 应报告 `"state": "already-successful"`。

第 10 步之前：没有蓝牙，Wi-Fi 使用随机 MAC，不能从 Ubuntu 切换，内核更新也不会改写 A 槽（地址由该步重建的启动镜像组带入）。只有做过第 8a 步，首次启动起触控才可用。
初始设置的键盘页会重建 initrd；`/etc/piano` 中没有 `boot-profile.json` 与 `boot-signing.pem` 时，启动镜像组不受影响。

## 10. 安装本机文件

**把文件复制到平板**，经开发者模式（USB 线上的 SSH；USB 主机与外接存储不支持）。在平板终端（Ctrl+Alt+T）中：

```bash
sudo piano-devmode on      # 做过第 8a 步时已打开
piano-devmode status       # 预期 "developer mode: enabled, active" 与 "SSH socket: active (192.168.88.2 port 22)"
```

在主机上（`<USER>` 为初始设置中创建的账户）：

```bash
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check     # 预期 "... 192.168.88.10 -> 192.168.88.2 ready"
```

如果主机没有取得 DHCP 地址，`--check` 会打印设置地址的 `ip` 命令。

**生成分区映射**（第 8a 步已生成时跳过）。在平板上：

```bash
d=$(lsblk -no PKNAME "$(readlink -f /dev/disk/by-partlabel/boot_a)"); readlink -f /sys/block/$d/device
# 预期路径以 ":4" 结尾（LUN 4）
sudo sfdisk --json /dev/$d > ~/boot-lun.json
```

在主机上：

```bash
scp <USER>@192.168.88.2:boot-lun.json .
python3 "$REPO/tools/extract-boot-partitions.py" --input boot-lun.json --profile boot-profile.json --output boot-partitions.json
# 预期 init_boot_a 8388608 B，vendor_boot_a 与 boot_a 各 100663296 B
```

全新安装无需单独安装支持包，它已在镜像中。boot-profile.json 与 boot-signing.pem 留在主机上，到本步再安装，以便在 Android 恢复镜像组准备好之后重建启动镜像组。

**复制文件：**

```bash
scp -r thp-calibration.json bluetooth-address wlan-address boot-partitions.json boot-profile.json \
  boot-signing.pem android-set <USER>@192.168.88.2:
```

做过第 8a 步时其中四个文件已在 `/etc/piano` 中，只需复制 `boot-profile.json`、`boot-signing.pem` 和 `android-set`。

**安装文件、存入 Android 镜像组并重建启动镜像组：**

```bash
cd ~
sudo install -m 600 bluetooth-address wlan-address thp-calibration.json boot-profile.json \
  boot-signing.pem boot-partitions.json /etc/piano/       # 做过第 8a 步时只装 boot-profile.json boot-signing.pem
sudo piano-boot-switch status          # 预期 "occupant": "ubuntu" 与 "backup_gpt": "valid"
sudo piano-boot-switch import-android --from ~/android-set     # 预期 "result": "imported"
sudo piano-boot-update $(uname -r)
# 预期 "Piano boot image set prepared for <release>" 与 "Slot A now holds store/ubuntu"
sudo systemctl reboot
```

新镜像组带有你的地址、序列号和密钥；发布镜像组保留为 `ubuntu.prev`。重启后：

```bash
sudo piano-boot-switch verify          # 预期退出码 0
```

然后删除主目录中的副本（`rm -r ~/android-set ~/boot-signing.pem ...`），并在平板终端中关闭开发者模式（它只为安装而开启）：

```bash
sudo piano-devmode off
piano-devmode status       # 预期 "developer mode: disabled, inactive"
```

需要主机连接时用 `sudo piano-devmode on` 再次打开。开启期间它经 USB 线接受所有设有密码的本地账户，如需仅允许 SSH 密钥，见 [device/devmode](../device/devmode/README.md)。它只监听 USB 网络地址 `192.168.88.2`。

如果重建的镜像组无法启动：按电源 + 音量下，从主机写回发布目录的 `boot/` 镜像组（[FLASHING.zh-CN.md](FLASHING.zh-CN.md#从主机piano-switchpy)）。

## 安装之后

- **切换与恢复：**[FLASHING.zh-CN.md](FLASHING.zh-CN.md)。
- **软件包：**第一次 `apt install` 之前先运行 `sudo apt update`，镜像不带软件源索引。镜像只装项目内核，没有 Ubuntu 通用内核和 C 库头文件；编译软件前先安装 `build-essential`。
- **内核更新：**见 [UPGRADE.zh-CN.md](UPGRADE.zh-CN.md)。
- **Android：**首次启动 Android 会重建数据，需要几分钟。如需 root，请重新安装 root 管理器应用；
  A 槽中已打补丁的 `init_boot_a` 不变。更换 A 槽的 root 补丁后，要刷新存储的 Android 镜像组（[FLASHING.zh-CN.md](FLASHING.zh-CN.md#镜像组)）。
- **已知问题与支持状态：**[README](../README.zh-CN.md#硬件支持)及发布目录中的 `RELEASE-NOTES.md`。

## 排障

- **没有声音。** 先查内核日志：`sudo journalctl -k -b | grep -E 'crash detected in adsp|sns_watchdog'`。
  反复出现说明传感器服务在让音频 DSP 不断重启。同一次启动中 DSP 崩溃满三次、且至少一次来自 `sns_watchdog` 时，
  传感器服务会把 registry 移到
  `/var/lib/piano/sensors/registry.quarantine-<时间>` 并重新开始（`journalctl -b -u piano-hexagonrpcd-sns`
  会写出该目录）。如果仍持续出现，按[传感器说明](../device/sensors/README.zh-CN.md)安装本机传感器 registry
  （[第 3 步](#推荐准备本机传感器-registry)）。
- **触控无反应或方向不对。** `journalctl -b -u piano-thp` 会写出识别到的屏型。出现 `THP calibration error`
  行说明 `thp-calibration.json` 取自另一种屏的 ini：按该行给出的 ini 重新提取（第 3 步），复制到
  `/etc/piano/thp-calibration.json`，再运行 `sudo systemctl restart piano-thp`。

## 尚无项目工具的步骤

| 步骤 | 现状 |
|---|---|
| Android root | 按 root 管理器自身的流程 |
| 分区备份与清单（第 2 步） | 标准 `adb`/`dd` 命令 |
| Wi-Fi 地址、启动配置（第 3 步） | 标准命令，无生成工具 |
| 新分区表（第 5 步） | 在 loop 设备上用标准 `sgdisk`/`sfdisk` |
| 写入分区表（第 6 步） | 在发布目录的维护 RAM 镜像中执行标准 `dd` 命令 |
| 恢复原分区表并移除 Ubuntu | 无工具，也无已验证流程；请保留分区表备份 |
