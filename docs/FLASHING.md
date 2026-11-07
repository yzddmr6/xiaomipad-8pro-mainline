# Fastboot、切换与恢复

[English](FLASHING.en.md) ｜ [项目首页](../README.md) ｜ [安装](INSTALL.md)

Android 和 Ubuntu 都从 A 槽启动。切换就是把另一个系统的镜像组写入 `init_boot_a`、`vendor_boot_a`
和 `boot_a`，活动槽始终不变。本页说明切换工具、槽属性、Fastboot 恢复以及绝不能写入的分区。

## 绝不写入

- `vbmeta` 与 `dtbo`；
- 任何 `_b` 分区，也不要执行 `fastboot --set-active=b`：测试机的 B 槽是旧原厂链，无法启动 Android；
- `persist` 或其他校准、基带、固件分区；
- 其他平板的镜像、标定或地址。

每条命令都绑定到你的平板（`adb -s <SERIAL>`、`fastboot -s <SERIAL>`、`piano-switch.py --serial <SERIAL>`），
同一时间只用一个工具操作。分区操作前确认电量至少 40%；当前充电支持与限制见
[硬件状态](STATUS.md)，不再沿用早期诊断内核不充电的结论。

写入前按[安装指南](INSTALL.md#2-备份)备份，保留主机上的两套启动镜像。
安装时保持有人照看；正式镜像没有调试控制台。启动、写入和采集不要与其他会话重叠。
出现失败时停下并定位最早失败的步骤：不要因一次状态查询失败而重写，不要重刷已经写好的文件系统，
也不要中断仍在执行的写入。新镜像先按[调试启动流程](AGENT-GUIDE.md#72-调试-ram-启动)检查，
再写 A 槽；调试镜像不用于日常持久启动。

主机命令使用 [INSTALL.md](INSTALL.md#准备) 中的 REPO、RELEASE 和 WORK 设置：
REPO 为发布记录提交的集成仓检出目录绝对路径，RELEASE 为解压后的发布目录，WORK 为仓库之外的私有安装工作目录。
主机命令在 WORK 中执行；源码工具使用 `"$REPO/tools/NAME"`，发布文件使用 `"$RELEASE/PATH"`。

## Fastboot

长按**电源 + 音量下**直到出现 Fastboot；任何状态（包括卡死）都有效。系统运行时也可以在 Android 中执行
`adb -s <SERIAL> reboot bootloader`，在 Ubuntu 中执行 `sudo piano-reboot-bootloader`。

```bash
fastboot devices                                  # 预期列出你的 <SERIAL>
fastboot -s <SERIAL> getvar current-slot          # 预期 "current-slot: a"
fastboot -s <SERIAL> getvar battery-soc-ok        # 预期 "battery-soc-ok: yes"
```

## 维护 RAM 镜像

发布目录的 `boot/boot-ram-maintenance.img` 是只在 RAM 中运行的 Linux，用于不能在已挂载系统中进行的 UFS 盘操作，
例如 [INSTALL.md 第 6 步](INSTALL.md#6-写入分区表平板上)的分区表写入。它不自动挂载 UFS 分区或写入任何内容，A 槽保持不变。
[INSTALL.md 第 8a 步](INSTALL.md#8a-推荐首次启动前安装本机文件)会显式挂载新根以安装本机文件。

```bash
fastboot -s <SERIAL> boot "$RELEASE/boot/boot-ram-maintenance.img"    # 预期 "Booting OKAY"
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check         # 预期 "... 192.168.88.10 -> 192.168.88.2 ready"
telnet 192.168.88.2 2323                                              # root shell，无密码
```

| 会话中 | 作用 |
|---|---|
| （登录） | 打印内核、LUN 表、`Mounted from the UFS disks: none` 和 LUN 0 的 `sgdisk -p` |
| `piano-maint map` | 再打印一次该表 |
| `piano-maint status` | 空闲计数与时限、打开的会话、能否重启进入 Fastboot |
| `piano-maint keepalive`、`piano-maint idle SECONDS` | 重新计数；设置时限（30–3600 s） |
| `piano-maint fastboot` | 立即重启进入 Fastboot |
| `wget -O /run/FILE http://192.168.88.10:8000/FILE` | 从主机上的 `python3 -m http.server --bind 192.168.88.10 8000` 复制文件 |

10 分钟内没有打开的会话时，平板自行重启进入 Fastboot；会话只在连着线时计入。之后 `fastboot -s <SERIAL> reboot` 启动 A 槽。
工具：`sgdisk`、`sfdisk`、`lsblk`、`partx`、`blockdev`（Ubuntu 26.04 二进制）、BusyBox `dd`、`sha256sum` 和 `wget`。
所有命令以 root 运行：你让 `dd`、`sgdisk`、`sfdisk` 写盘时它们就会写盘。

## 镜像组

镜像组是一个目录，含 `boot.img`、`init_boot.img`、`vendor_boot.img`（均为完整分区大小）和列出这三个文件的 `SHA256SUMS`。

| 镜像组 | 位置 |
|---|---|
| Android | 你的 A 槽备份（[INSTALL.md](INSTALL.md#2-备份)）；`init_boot` 为正在使用的、已打 root 补丁的镜像 |
| Ubuntu | 发布目录的 `boot/`；运行 `piano-boot-update` 之后为存储中的 `ubuntu/` |

平板上的存储目录 `/var/lib/piano/switch/` 保存 `android/`、`ubuntu/` 和 `ubuntu.prev/`（当前之前的 Ubuntu 镜像组）。
主机上保留 Android 镜像组和当前 Ubuntu 镜像组的副本。每次更新后打开开发者模式，选择一个新的 SET_NAME，在平板与主机上使用同一名称。不要复用已有目标目录。

```bash
# 平板：每次更新选择一个新名称。
SET_NAME=ubuntu-set-update-001
sudo piano-devmode on
test ! -e "$HOME/$SET_NAME" &&
  sudo cp -a /var/lib/piano/switch/ubuntu "$HOME/$SET_NAME" &&
  sudo chown -R "$USER:" "$HOME/$SET_NAME"

# 主机：使用相同的名称。
SET_NAME=ubuntu-set-update-001
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check
test ! -e "$SET_NAME" &&
  scp -r <USER>@192.168.88.2:"$SET_NAME" . &&
  (cd "$SET_NAME" && sha256sum -c SHA256SUMS)

# 平板：传输与校验成功后：
sudo piano-devmode off
```

A 槽的 Android root 补丁变化后（例如从 Magisk 换成 KernelSU），存储的 `android/` 不再一致，`to-android`
会写回旧的 `init_boot`。请用当前 A 槽镜像生成新的 Android 镜像组，并用
`sudo piano-boot-switch import-android --from <dir>` 存入。

## 在 Ubuntu 中：`piano-boot-switch`

| 命令 | 写入 | 结果 |
|---|---|---|
| `sudo piano-boot-switch status` | 不写 | A 槽内容（`android`、`ubuntu`、`ubuntu.prev`、`unknown`）、槽标志、备份 GPT 状态 |
| `sudo piano-boot-switch verify` | 不写 | 所有镜像组完好时退出码为 0 |
| `sudo piano-boot-switch to-android --dry-run` | 不写 | `"result": "would-write"` |
| `sudo piano-boot-switch to-android --reboot [--force]` | A 槽 | `"result": "written"`，随后请求重启进入 Android |
| `sudo piano-boot-switch to-ubuntu [--reboot]` | A 槽 | `written`；A 槽已是该镜像组时为 `unchanged` |
| `sudo piano-boot-switch import-android --from DIR` | 存储 | `"result": "imported"`；替换 `android/` |
| `sudo piano-boot-switch import-ubuntu [--from DIR]` | 存储 | `imported`；原 `ubuntu/` 变为 `ubuntu.prev/` |

- 需要先完成 [INSTALL.md 第 10 步](INSTALL.md#10-安装本机文件)，装入本机文件。
- 需要 `/etc/piano/` 中有本机的 `boot-profile.json` 和 `boot-partitions.json`；只在 A 槽为已知镜像组时写入，
  该镜像组同时作为回滚。每个分区都完整写入并读回；失败时写回原镜像组。
- `"result": "refused"` 表示什么都没写，`code` 与 `message` 说明原因。
  `rollback-failed` 或 `final-mismatch`：不要重启，从 Fastboot 恢复（见下文）。
- 重启前会列出阻止关机的进程（inhibitor）；重启被拒时退出 1，此时 A 槽已写入 Android，下一次启动将进入 Android。保存工作后正常重启，或用 `--reboot --force` 忽略 inhibitor。应用网格中的 **切换到 Android** 调用同一工具。
- 测试机已验证：`status`、`verify`、两种导入、`to-ubuntu`、`to-android --reboot`。

## 在 Android 中：KernelSU 模块

Android 侧切换已在测试机验证。
发布目录中的 `tools/piano-switch-ksu-<version>.zip` 需要带 shell root 的 KernelSU-Next、与 A 槽一致的已存储
`android/` 镜像组，以及 Ubuntu 正常关机。

```bash
KSU_VERSION=$(sed -n 's/^version=//p' "$REPO/device/android/piano-switch-ksu/module/module.prop")
KSU_ZIP="piano-switch-ksu-$KSU_VERSION.zip"
adb -s <SERIAL> push "$RELEASE/tools/$KSU_ZIP" /data/local/tmp/
adb -s <SERIAL> shell su -c "/data/adb/ksud module install /data/local/tmp/$KSU_ZIP"
adb -s <SERIAL> reboot
adb -s <SERIAL> shell su -c 'sh /data/adb/modules/piano-switch/piano-switch.sh --check'
# 预期 "CHECK OK: slot A holds the android set and the ubuntu set is ready; nothing written"
adb -s <SERIAL> shell su -c 'ksud module action piano-switch'   # 写入 A 槽并重启
```

退出码 3 表示写入与恢复都失败：不要重启，从 Fastboot 恢复 Android 镜像组。
详见 [device/android/piano-switch-ksu](../device/android/piano-switch-ksu/README.md)（英文）。

## 从主机：`piano-switch.py`

测试机已验证两个方向。在 Fastboot 中：

```bash
# 切到 Android
python3 "$REPO/tools/piano-switch.py" --serial <SERIAL> --images <android-set> --rollback <ubuntu-set>
fastboot -s <SERIAL> reboot
# 切到 Ubuntu
python3 "$REPO/tools/piano-switch.py" --serial <SERIAL> --images <ubuntu-set> --rollback <android-set>
fastboot -s <SERIAL> reboot
# 预期 "Active A boot triplet written; remain in Fastboot for a RAM readback before reboot."
```

- 首次写入前检查 `product: piano`、`unlocked: yes`、`current-slot: a`、三个分区大小和两组 `SHA256SUMS`。
  写入失败时写回回滚镜像组并报告错误。
- 三次写入不是原子操作：切换中途断电后，从 Fastboot 重新执行。
- 进入 Fastboot 前先关闭 Ubuntu；不要中断正在进行的写入。

## 槽属性

```bash
fastboot -s <SERIAL> getvar slot-successful:a     # 已标记时为 "yes"
fastboot -s <SERIAL> getvar slot-unbootable:a     # 预期 "no"
fastboot -s <SERIAL> getvar slot-retry-count:a    # 主机写入后为 7，每次未标记的启动减 1
```

| 事件 | 对 A 槽的影响 |
|---|---|
| 主机写入 `boot_a`（包括经 `piano-switch.py`） | 清除 successful，重试计数设为 7 |
| 启动尚未标记成功的槽 | ABL 将重试计数减 1；减到 0 时该槽变为不可启动 |
| Ubuntu 启动 | 启动完成后 `piano-slot-success.service` 将该槽标记为成功 |
| Android 启动 | Android 标记该槽 |
| 在任一系统内切换 | 不变 |

优先在系统内切换，它保留槽属性。在 Ubuntu 中可用 `sudo piano-slot-success --check` 查看状态，不会写入。

如果 Fastboot 显示 `slot-unbootable:a: yes`，执行 `fastboot -s <SERIAL> --set-active=a` 重置 A 槽计数，
然后切换或 `fastboot -s <SERIAL> reboot`。在测试机上这也提高了 B 槽的优先级；B 槽仍不使用。

## 平板无响应时

1. **已开启开发者模式**（在 Ubuntu 中 `sudo piano-devmode on`）：开机约 22 秒后，
   `python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check` 应报告连接 `ready`。然后执行
   `python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> -t <USER> sudo piano-reboot-bootloader`，或用
   `piano-boot-switch to-android --reboot` 切到 Android。
2. **否则**长按电源 + 音量下直到出现 Fastboot，再用 `piano-switch.py` 写入 Android 镜像组。
   在系统启动前卡住的启动没有计时器，会一直停在那里，直到按下组合键。

不要从主机重置 USB 设备，也不要对没有响应的平板重复发命令。

## 恢复原布局

已有一台 `piano` 512 GB／BOE 从 Ubuntu 双系统恢复本机原厂 GPT、再线刷
OS4.0.5.0.XPYCNXM 的[单机实证与人工操作顺序](RESTORE-STOCK.md)。必须保留
[INSTALL.md](INSTALL.md#2-备份)中的本机原始分区表和完整备份，并在
[维护 RAM 镜像](#维护-ram-镜像)中先恢复备表、读回验证，再恢复主表；该会话不能格式化。

`tools/prepare-stock-restore.py` 仅离线验证备份、提取精确 GPT 区域和生成审阅计划，
拒绝非普通输入且不执行设备读写。参数化工具只有离线测试，不是已上机验证的自动卸载器。
普通原厂线刷不恢复 GPT；不能整段写回 34 块转储，也不能直接写厂包 GPT 模板。
其他容量／布局、无本机备份或降级仍未验证；完整原厂固件写入须另行明确授权，不能沿用 Ubuntu 更新权限。
