# 从 Ubuntu 双系统恢复原厂 HyperOS：单机实证

[English](RESTORE-STOCK.en.md)

**2026-11-07，一台 `piano` 512 GB／BOE 平板已成功恢复原厂 GPT 并线刷 HyperOS 4。**
这是用户提供的硬件实证，不是维护者替所有容量、屏型或原厂版本作的支持承诺。
恢复会清除 Android 和 Linux 数据；只想临时使用 Android，应使用
[启动镜像组切换](FLASHING.md)，不要为切换系统修改 GPT。

## 已验证范围与边界

- 安装前：OS3.0.303.0.WPYCNXM，原始 `userdata` 延伸至 LUN0 最后可用 LBA。
- 双系统：Ubuntu 26.04，Linux `7.2.14-piano56`；`userdata` 200 GiB、Linux root 60 GiB、Linux data 约 201.27 GiB。
- 使用发布维护 RAM 镜像，SHA-256 `0f03ca69833289671a7bd13577b5eeeefcf7132765aeb4110805ece263f68fe5`。
  安装时集成仓检出为 `5152d48f4153752abfa7bd1c8ff940a3a8f6dff8`。
- 本机原 GPT 恢复后，使用原厂 OS4.0.5.0.XPYCNXM（Android 17）**普通**完整线刷流程。
  包 SHA-256 `d16a12872f5a47cd93fea38e1bb6783d552fe06682244664a0b71a7867d92425`。
- 没有回锁或写 `persist`／本机 NV 校准；恢复原厂启动链后不再使用旧 KernelSU 补丁或 Linux 切换组。
- 本次专门批准了原厂完整恢复涉及的双槽固件、基带、`vbmeta`／`dtbo` 写入。
  这**不是**对 Ubuntu 安装／更新中[禁写边界](FLASHING.md#绝不写入)的普遍放宽。
- 原始操作使用逐机绑定控制器。新增 `tools/prepare-stock-restore.py` 提取相同的 GPT 验证、
  精确截取及原厂步骤规划逻辑，**只经过离线测试，没有再次上机运行**。
  它在打开前拒绝非普通文件并复核打开后的身份，不执行设备读写、ADB/Fastboot、自动卸载或自动刷机。
- 其他容量、不同布局、无本机原厂备份、降级回 OS3、断电恢复均未验证。

## 为什么完整线刷不够

本次 OS3／OS4 普通 `flash_all.sh` 内容相同，SHA-256 为
`8610cf859ab4fdda40acaa81c1c25ef27b233d8622df716587e7c518360ccb10`。
它覆盖系统及启动固件，**不恢复 GPT、不删除 Linux 分区**。
包内 `gpt_both0.bin` 是容量适配模板，不能当成本机原表直接 `dd`。
旧备份 `android-set/init_boot.img` 含 KernelSU 补丁，也不是纯原厂恢复镜像。

## 1. 先离线验证和准备片段

需要[安装前的本机分区表备份](INSTALL.md#2-备份)，以及从维护 RAM 导出并保存到主机的当前主／备表。
输入是头／尾转储，不是完整磁盘；严禁使用别人的备份。
先独立核对设备身份、LUN0 容量、逻辑块大小及磁盘 GUID，再显式传入：

```sh
python3 -I tools/prepare-stock-restore.py \
  --stock-head /private/stock/sda-head.bin --stock-tail /private/stock/sda-tail.bin \
  --current-head /private/current/sda-head.bin --current-tail /private/current/sda-tail.bin \
  --block-size 4096 --disk-blocks <本机实测块数> --expected-disk-guid <本机磁盘GUID> \
  --output /private/new-stock-restore
```

省略 `--output` 只验证、不写文件；指定输出时必须是尚不存在的新目录。
输出 `stock-primary.bin`、`stock-backup.bin`、当前表片段、`SHA256SUMS` 和 `plan.json` 均属私人资料，不要提交仓库。
工具只接受观测过的 4096 字节逻辑块布局；验证主备头／数组 CRC、几何、GUID、分区重叠／越界，
要求原始分区不变，只有 `userdata` 末端缩短，恰由两个 Linux filesystem 分区填满剩余部分。
原厂保护 MBR 的饱和长度 `0xffffffff` 被接受，不擅自“修复”原厂表。

本次数字仅作为实证，**不得复制到另一台设备**：

| 项目 | 本机观测 |
|---|---|
| LUN0 | 4096 B × 124552192 块 |
| GPT 可用区 | LBA 6–124552186 |
| 主 GPT 保留区 | LBA 0–5，24576 B |
| 备 GPT 保留区 | LBA 124552187–124552191，20480 B |
| 原厂 userdata | LBA 3632128–124552186，495288561664 B |

**不要整段写回 34 块转储**：头转储从第 6 块起含 `switch` 等真实分区数据；
尾转储前 29 块属于 userdata。工具仅导出 GPT 保留区，不包含这些数据。
`work/new-primary.bin`／`new-backup.bin` 若来自安装阶段，则是 Linux 新表，不是原厂恢复源。

## 2. 进入独立维护 RAM 并重新检查

按[维护 RAM 镜像](FLASHING.md#维护-ram-镜像)操作，正常退出 Ubuntu 后进入 Fastboot，
绑定自己序列号临时启动已校验镜像；不要在仍挂载 `/`、`/home` 的 Ubuntu 内改表。
先检查 RAM 根、内部存储完全未挂载、无磁盘 swap／holders、LUN0 唯一且几何和 GUID 一致，
电量至少 40%，`piano-maint status` 为 `Fastboot reboot ready`。
状态不明就停止，不重复 `fastboot boot`，不从主机重置 USB。

本次在普通 Linux 中无法免密提权读原始 GPT，因此在维护环境中读取；没有绕过权限。
Telnet 提示符可带 `ESC[6n` 光标查询，不能把接收缓冲区未“以提示符结尾”误判为启动失败。
可延长 RAM 内 `piano-maint idle 3600` 后再次读状态；不要遗留主机 keepalive 进程。
**备表和主表之间不得退出、断线或允许空闲计时触发重启。**

## 3. 逐段恢复并立即读回（人工确认的参考序列）

先将当前 GPT 导出到主机并验证，再把 stock 片段传到 `/run`（RAM），验证长度和 SHA-256。
下面是成功操作的参数化参考，**不是可以不经核对直接运行的刷机脚本**；
`DISK` 必须由实测 LUN0 映射确定，其余值从已验证的 `plan.json` 逐项填写。
复制本段不构成设备写入授权。

```sh
# 已在 RAM，UFS 未挂载；先明确填写并核对这些变量。
# DISK=/dev/<实测LUN0>; R=/run/<已校验片段目录>
# BS=<block_size>; TAIL_START=<tail_start_lba>
# TAIL_COUNT=<tail_blocks>; HEAD_COUNT=<head_blocks>
# 每条独立执行，检查退出码；读回长度及 SHA256 匹配才继续。
dd if="$R/stock-backup.bin" of="$DISK" bs="$BS" seek="$TAIL_START" count="$TAIL_COUNT" conv=notrunc,fsync
dd if="$DISK" of="$R/readback-backup.bin" bs="$BS" skip="$TAIL_START" count="$TAIL_COUNT"
# 验证 readback-backup 与已记录 stock-backup 的精确长度／SHA256。
# 未通过：停止，不写主表、不自动回滚、不重启。
dd if="$R/stock-primary.bin" of="$DISK" bs="$BS" count="$HEAD_COUNT" conv=notrunc,fsync
dd if="$DISK" of="$R/readback-primary.bin" bs="$BS" count="$HEAD_COUNT"
# 再验证精确长度／SHA256；两段都成功才继续。
sync
sgdisk --verify "$DISK"
sgdisk -p "$DISK"
```

读回导出到主机，验证主备 CRC、完整分区数组、GUID及 userdata 边界，与 stock 片段逐字节比较。
本机原厂 8 KiB 数组、头尾保留间隙及最后分区对齐会产生警告；不要自动调整它们，
必须与原厂备份逐项解释，不能只看命令退出码。
任一真实错误都停止，不中断仍在执行的写入，不因超时重发写命令。

**该会话仍可能保留旧内核分区视图，不能挂载或格式化。** 全部校验通过且回退 ready 后，
另行确认执行 `piano-maint fastboot`，在新 Fastboot 视图核对完整 userdata 和两个 Linux 分区消失。
`partition-size` 没有 `0x` 前缀时仍按十六进制解析；设备可在输出 `FAILED` 时返回 0，必须检查文本。

## 4. 明确授权的完整原厂恢复及验收

核对正确机型／国行版本、镜像哈希、解锁状态、anti、A 槽、电量及扩大后的容量，
再批准清空两套系统数据、原厂完整写入范围和成功后的重启。
本次先 `fastboot -s <SERIAL> erase userdata`，再按审核的普通原厂流程重建 userdata／metadata、
刷入全套 OS4 镜像；没有额外主机 `mkfs` 或猜测 `fastboot format`。
`_ab` 明确包括双槽，不能理解成只写 A；不使用 lock 版，不刷 `persist`／校准分区，不改 GPT。

可用 `--factory-script /private/rom/flash_all.sh` 向离线工具提供上述固定指纹脚本，
仅导出额外 erase userdata 加原厂 55 条动作的审阅计划；CRC 两项仅在明确 `crc: 1` 时执行。
**脚本指纹不代表 ROM 版本**：本次两个版本脚本相同，仍须独立校验整个 ROM 和全部镜像。
未知新版脚本工具会拒绝；不要跳过审核。失败即停，不自动重试、切槽、重启或回锁。

本次 50 条 flash、4 条 erase、设置 A 槽及重启全部成功。首启后 ADB 实测：

- `OS4.0.5.0.XPYCNXM`、Android 17／SDK 37、`sys.boot_completed=1`；
- A 槽，`flash.locked=0`、`vbmeta.device_state=unlocked`、`verifiedbootstate=orange`、verity enforcing；
- `/data` 为 f2fs，总量 495286464512 B（约 461.27 GiB），不是原先 200 GiB，也不是 stock userdata 镜像的 45 GiB 逻辑尺寸；
- Linux 分区链接不存在；Wi-Fi 已连接，电量 100%；用户随后确认触控、扬声器、蓝牙连接、相机正常。

没有因验收而重新刷写或提权；USB 调试由用户开启。所有临时主机进程及专用 ADB server 已结束。
这是恢复可用性验证，不是取证级数据不可恢复保证或“无 root”安全认证。
