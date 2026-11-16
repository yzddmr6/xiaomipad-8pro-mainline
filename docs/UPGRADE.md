# 升级已安装的系统

[English](UPGRADE.en.md) ｜ [项目首页](../README.md) ｜ [全新安装](INSTALL.md) ｜ [切换与恢复](FLASHING.md)

本页说明如何把已经装好的 Ubuntu 升级到新版本的内核和配套软件包，不重装、不动分区、不清数据。整组升级全程在平板上完成，不需要电脑，步骤见[整组升级（只用平板）](#整组升级只用平板)；各版本的注意事项在下面对应的小节。
升级前提：已按 [INSTALL.md](INSTALL.md) 做完最后一步（`/etc/piano` 中有 `boot-profile.json`、`boot-signing.pem` 和 `boot-partitions.json`）。

发布附件里的 `piano-ubuntu-v<版本>-packages.zip` 是新版本的完整软件包批次，`piano-ubuntu-v<版本>-rollback.zip` 是供整版回退的上一版批次。总是用升级工具整组安装，不要只装其中一个包。


## v1.4.1 → v1.5.0

打开发布通知，点“更新”，下载完成后检查清理预览，再点“安装”。按提示重启。需要回退时，打开“系统更新”，点“回退到上一版”，完成后重启。 旧版通知只有“查看更新说明”时，首次升级请使用下方手工步骤。

手工整组升级使用 `piano-1.5.0` 和 `--previous piano-1.4.1-rollback-1.5.0`。升级前关闭虚拟机，完成后重启。

## v1.4.0 → v1.4.1

从 v1.4.0 升级：按[整组升级（只用平板）](#整组升级只用平板)在平板上下载并解压软件包和回退两个附件，整组升级时使用 `piano-1.4.1` 和 `--previous piano-1.4.0-rollback-1.4.1`。升级前关闭虚拟机，完成后重启。

从 1.4.1 起，整组升级成功或整版回退完成后，工具自动清掉不再需要的东西：旧批次、启动镜像副本、旧内核及其模块，以及版本里已经去掉的项目包；只留本次升级的新旧两批、它们的启动镜像组，以及正在运行或回退要用的内核。以前按本页手工保存的 `/var/lib/piano/rollback-<内核版本>` 副本，对应内核不再保留的也一并删掉。个人文件、Waydroid、虚拟机和改过的配置文件不受影响，删了什么、为什么删记在 `/var/lib/piano/releases/cleanup.json`。以后需要再清理时，可以先加 `--dry-run` 预览要删的内容和原因（不做任何改动），再正式执行：

```sh
sudo /usr/bin/python3 /var/lib/piano/releases/piano-1.4.1/install.py cleanup --product piano --dry-run
sudo /usr/bin/python3 /var/lib/piano/releases/piano-1.4.1/install.py cleanup --product piano
```

GunyahVM 更新到 1.3.0，合并为单个 `gunyahvm` 包；升级时 APT 自动移除旧的 `gunyahvm-runtime`、`gunyahvm-guest-kernel` 和 `gunyahvm-guest-mesa`。

## v1.3.1 → v1.4.0

从 v1.3.1 升级，解压软件包和回退附件，整组升级时使用 `piano-1.4.0` 和 `--previous piano-1.3.1-rollback-1.4.0`。完成后重启。

## v1.3.0 → v1.3.1

解压本版软件包和回退 ZIP 到同一目录。沿用下文整版升级命令，新仓库选 `piano-1.3.1`，`--previous` 选 `piano-1.3.0-rollback-1.3.1`。升级前关闭虚拟机，整组安装后重启。


## 从 v1.2.1 升级到 v1.3.0

先把本版的 `*-packages.zip` 和 `*-rollback.zip` 解压到同一目录，分别得到新旧两批仓库。

使用完整的 `piano-1.3.0` 软件包批次和配套启动镜像，保留附件中的
`piano-1.2.1` 批次供整版回退。本版同时更新内核、宿主 Mesa 同名包和
Snap 图形提供者；升级前正常关闭虚拟机，完成后重启。默认 NPU 演示包含
运行库、模型和本机型固件。沿用下文整版升级与回退命令，将仓库路径替换为
本版的新旧两批；无需重分区。


传感器服务关闭时先撤销客户端回调，修复停止或重启服务时的崩溃。

重启进入新会话后，GNOME 内置整屏录制默认使用 Iris 硬件编码。录屏进程的 CPU 占用较低；默认画质设置下，文件可能比软件编码大。

## v1.2.0 → v1.2.1

宿主 Mesa 改为 Ubuntu 同名包，安装到标准多架构目录，使网页进程和其他沙箱
能够使用设备驱动。保留原驱动版本和补丁；Snap 图形提供者不变。
升级附件包含按整版组织的 `repositories/piano-1.2.1/`，完整 Mesa 同源包
供以后安装开发包和 OpenCL 使用，默认只安装桌面所需子集。
libdrm 使用 Ubuntu 原样包，不钉版本，后续正常接收 Ubuntu 更新。

升级前正常关闭正在运行的虚拟机。本批包含 GunyahVM 1.1.0 和控制中心 50.3。

先校验发布附件的校验文件。解压本版 packages 附件，其中同时提供新、旧两批仓库。旧批次必须包括已安装发布包
及其 Mesa/libdrm 依赖的精确版本；缺包时升级工具会拒绝继续，不会仅保存启动镜像。
把两个仓库目录传到平板后，在平板运行：

```sh
sudo systemd-run --unit=piano-release-upgrade --setenv=DEBIAN_FRONTEND=noninteractive \
  /usr/bin/python3 /home/<USER>/repositories/piano-1.2.1/install.py upgrade \
  /home/<USER>/repositories/piano-1.2.1 \
  --previous /home/<USER>/repositories/piano-1.2.0
journalctl -f -u piano-release-upgrade
sudo dpkg --audit
sudo reboot
```

工具将完整批次保留在 `/var/lib/piano/releases/`，在修改包之前保存当前启动
镜像组及已安装包版本。相同批次可重复运行；若安装中断，先运行
`sudo dpkg --configure -a`、`sudo apt-get -f install`，再重跑同一升级命令。
已有个人文件和应用数据不在回退范围内。

### 整版回退

通过终端或开发者 SSH 通道运行；无图形界面时也可使用。不要先切回旧内核。

```sh
sudo systemd-run --unit=piano-release-rollback --setenv=DEBIAN_FRONTEND=noninteractive \
  /usr/bin/python3 /var/lib/piano/releases/piano-1.2.1/install.py rollback --product piano
journalctl -f -u piano-release-rollback
sudo dpkg --audit
sudo reboot
```

回退先把发布管理的用户态包整组恢复为上一批精确版本，随后恢复已保存的启动镜像组。
Mesa 回退会恢复旧的 `/opt` 库和注册，并移除新过渡包拥有的精确钉版本文件。
任一步失败都会停止，不会宣布回退完成。新旧仓库及启动镜像组应保留到新版本稳定。
Fastboot 仅恢复启动分区，不能恢复用户态包；只能进入恢复系统时，先修复根分区内的
软件包，再使用对应旧启动组。不要把“换回启动镜像”当作整版回退。

## 从 v1.1.1 升级到 v1.2.0

已安装 Waydroid 且网页黑屏或加载不完整的用户，请按 [Waydroid 指南](WAYDROID.md#浏览器和应用登录页黑屏)应用 OMX 声明覆盖修正；系统升级不会修改 Android 镜像。

按下方步骤整组安装全部软件包并重启。本版更新 Linux 7.2.15 内核与配套相机模块、设备支持包，以及配套的 GSD/common 软件包；整组安装可同步更新 APT 版本固定。保留用户设置、手指标定与回退镜像组。升级同时移除旧 USB 调试控制台；日常远程维护使用开发者模式 SSH，独立 RAM 调试包不随升级包安装。

整组软件包包含 GunyahVM 1.0.1 及全部配套包，升级后首次登录会把 GunyahVM 追加到 Dock 收藏，保留已有收藏和顺序；以后手动移除不会被再次添加。新用户默认即可在 Dock 看到它，也可从应用菜单打开。本机活动桌面会话由 GunyahVM 自带的 logind/udev 和 polkit 规则自动授权，无需手工加入组。

## 从 v1.1.0 升级到 v1.1.1

使用 `piano-ubuntu-v1.1.1-packages.zip`，按本页步骤整组安装全部软件包并重启；无需重分区或重刷根镜像。本版更新配套内核（Linux 7.2.15）与相机回环模块、设备支持包、传感器代理、图形驱动（Mesa 26.2.6）、snap 图形支持包和 `piano-ubuntu-desktop`，其余默认包与 1.1.0 相同。

充电器认证改由内核处理，升级时 APT 会移除旧的 `piano-mipps-auth` 包；这是正常替换，不要把旧包重新装回，`piano-charging.service` 继续提供充电策略。已有电池保护设置和本机标定继续保留，无需提取笔标定。

保留旧内核、回退启动镜像组和 v1.1.0 的完整 `packages.zip`，需要时可恢复配套软件。安装新版本后先正常重启，再使用充电和传感器功能。

## 从 v1.0.0 升级到 v1.1.0

使用 `piano-ubuntu-v1.1.0-packages.zip`，按下方相同步骤整组安装全部软件包，重启后生效。本版包含新内核（Linux 7.2.15）与配套的相机回环包、更新的设备支持包和 `piano-ubuntu-desktop` 1.1.0，其余默认包与 1.0.0 相同；虚拟机可选包未变，已安装的无需重装。重启后可在“设置 → 电源”开启“保护电池健康”。保留旧内核与回退镜像组，无需重分区或重刷根镜像。

## 从 v0.10.0 升级到 v1.0.0

Windows 用户同时安装同批 `piano-vm-windows` 可选包。设备支持包将 gunyah 组的有限 memlock 调整为 6 GiB；更新后重新登录，必要时重启再创建 Windows VM。Linux 默认配置不变。

按下方步骤整组升级内核和设备支持包并重启，保留旧内核与回退镜像组。不需要虚拟机的用户无需安装可选包。需要时进入升级包的 `optional/virtualization/`，安装同版 `piano-vm` 和 `piano-crosvm`，再按[虚拟机指南](VIRTUALIZATION.zh-CN.md)授权用户并重新登录。已有虚拟机在升级前正常关闭；家目录中的客体磁盘保留，不需要重新创建。

## 从 v0.9.2 升级到 v0.10.0

使用 `piano-ubuntu-v0.10.0-packages.zip`，按下方相同步骤整组安装全部软件包。本版包含新内核（相机、视频、外接显示器与充电修正）及配套的相机回环包、更新的相机服务、重建的桌面 Mesa 与 gpu-2404 snap 提供者、更新的设备集成包，以及 `piano-ubuntu-desktop` 0.10.0。重启后生效。保留旧内核与回退镜像组，无需重分区或重刷根镜像。

## 从 v0.9.1 升级到 v0.9.2

使用 `piano-ubuntu-v0.9.2-packages.zip`，按下方相同步骤整组安装全部软件包。本版包含显示初始化与资源管理修复、新内核的配套相机回环包，以及新增作者信息的系统设置。重启后生效。保留旧内核与回退镜像组，无需重分区或重刷根镜像。

### 触摸方向校正

在本机 `/etc/piano/thp-calibration.json` 的顶层设置 `invert_y`，然后运行 `sudo systemctl restart piano-thp`，即可校正上下方向。`true` 表示将触摸程序算出的 Y 坐标上下翻转；`false` 表示直接使用该坐标；不设置则沿用模组默认方向（BOE 为 `false`，CSOT 为 `true`）。例如，使用默认设置的 BOE 上下颠倒时，加入 `"invert_y": true`；CSOT 则可用 `false` 取消默认翻转。无需改内核、设备树或重新刷机。

如果已有 libinput 校准规则且方向正确，可以继续保留；改用上述选项时应撤掉旧规则中的 Y 翻转，避免重复校正。方向本来正常的设备无需操作。

## 从 v0.9.0 升级到 v0.9.1

使用 `piano-ubuntu-v0.9.1-packages.zip`，按下方相同步骤整组安装全部软件包，其中包括新内核（Linux 7.2.14，显示链路时钟常开）及配套的相机回环包；其余软件包与 v0.9.0 相同。重启后使用新内核。保留旧内核与回退镜像组，无需重分区或重刷根镜像。

## 从 v0.8.0 升级到 v0.9.0

使用 `piano-ubuntu-v0.9.0-packages.zip`，按下方相同步骤整组安装全部软件包，其中包括新内核（Linux 7.2.14）及配套的相机回环包；其余软件包与 v0.8.0 相同。重启后使用新内核。保留旧内核与回退镜像组，无需重分区或重刷根镜像。

## 从 v0.7.1 升级到 v0.8.0

使用 `piano-ubuntu-v0.8.0-packages.zip`，按下方相同步骤整组安装全部软件包，其中包括新内核（Linux 7.2.10）及配套的相机回环包、更新的桌面 Mesa 与 gpu-2404 snap 提供者，以及 `piano-ubuntu-desktop` 0.8.0。重启后使用新内核。保留旧内核与回退镜像组，无需重分区或重刷根镜像。

## 从 v0.7.0 升级到 v0.7.1

使用 `piano-ubuntu-v0.7.1-packages.zip`，按下方相同步骤整组安装全部软件包。本版更新桌面 Mesa 与 gpu-2404 snap 提供者；内核仍为 Linux 7.2.10-piano40，`piano-ubuntu-desktop` 仍为 0.7.0。重启使 snap 提供者刷新、应用加载新驱动。保留旧内核与回退镜像组，无需重分区或重刷根镜像。

## 从 v0.6.2 升级到 v0.7.0

按下方相同步骤使用 `piano-ubuntu-v0.7.0-packages.zip`，一起安装全部软件包，包括 Linux 7.2.10-piano40 及匹配的相机 loopback 包、更新的 `resources` 和 `piano-ubuntu-desktop` 0.7.0。安装后重启，使新内核生效；任务管理器随即出现“温度”页。保留已安装的旧内核及其回退启动镜像组。无需重分区或重刷根镜像。

## 从 v0.6.1 升级到 v0.6.2

按下方相同步骤使用 `piano-ubuntu-v0.6.2-packages.zip`，一起安装全部软件包，包括 Linux 7.2.10-piano38 及匹配的相机 loopback 包、新增的 `resources` 和 `piano-ubuntu-desktop` 0.6.2。安装后重启，使新内核生效；任务管理器重新打开即可看到新增数据。保留已安装的旧内核及其回退启动镜像组。无需重分区或重刷根镜像。

## 从 v0.6.0 升级到 v0.6.1

按下方相同步骤使用 `piano-ubuntu-v0.6.1-packages.zip`，一起安装全部软件包，包括 Linux 7.2.10-piano35 及匹配的相机 loopback 包。安装后重启，使新内核、触控固件和传感器服务生效。保留已安装的旧内核及其回退启动镜像组。无需重分区或重刷根镜像。

## 从 v0.5.1 升级到 v0.6.0

按下方相同步骤使用 `piano-ubuntu-v0.6.0-packages.zip`，一起安装全部软件包，包括 Linux 7.2.10-piano32 及匹配的相机 loopback 包。安装后重启，使新内核及 UTF-8 文件名默认值生效。保留已安装的旧内核及其回退启动镜像组。无需重分区或重刷根镜像。

## 从 v0.5.0 升级到 v0.5.1

按下方相同步骤使用 `piano-ubuntu-v0.5.1-packages.zip`，一起安装全部软件包，包括 Linux 7.2.10-piano28 及匹配的相机 loopback 包。安装后重启，使触控恢复与音频会话切换修复生效。保留已安装的旧内核及其回退启动镜像组。无需重分区或重刷根镜像。

## 从 v0.4.0 升级到 v0.5.0

按下方相同步骤使用 `piano-ubuntu-v0.5.0-packages.zip`，一起安装全部软件包，包括 Linux 7.2.10-piano27、匹配的相机 loopback 包，以及电源键的 GSD/common 和控制中心/data 四个 Ubuntu 补丁包。保留已安装的旧内核及其回退启动镜像组。无需重分区或重刷根镜像。新桌面包依赖 `tuned-ppd`，APT 会替换旧 `power-profiles-daemon`；软件包名称的变化是正常的。

若以前手工 hold 了上述四包，安装整组新包前先解除旧 hold：

```sh
sudo apt-mark unhold gnome-settings-daemon gnome-settings-daemon-common gnome-control-center gnome-control-center-data
```

新包的 APT pin 和配套版本依赖会防止 Ubuntu 更新覆盖锁屏支持；这也阻止这四包直接接收 Ubuntu 安全更新，必须及时安装项目重建的配套包，详见 [维护流程](../ubuntu/README.md)。其他 Ubuntu 软件包照常更新。

重启后，在“设置 → 电源”选择锁定屏幕及三档性能模式。个人电源键选择不会被覆盖。命令行使用 `tuned-adm`；若曾手工复制 `/usr/local/bin/powerprofilesctl`，迁移时删除这份已知手工副本。配置归属与命令见 [电源配置](../device/power/tuned/README.md)。

若曾手工测试 Piano 的 TuneD 配置，先备份这些测试文件。安装后移除测试用的 `/etc/tuned/profiles/piano-*`，避免它们遮蔽包内 `/usr/lib/tuned/profiles/piano-*`；保留有意维护的其他管理员配置。若测试改过 `/etc/tuned/ppd.conf`，从与已安装版本相同的 `tuned-ppd` deb 提取原文件并恢复，核对 `dpkg --verify tuned-ppd` 的结果。项目配置由 `/etc/tuned/piano-ppd.conf` 提供，重启后服务通过 `BindReadOnlyPaths` 读取它。

## 从 v0.3.2 升级到 v0.4.0

按下方相同步骤使用 `piano-ubuntu-v0.4.0-packages.zip`，一起安装全部软件包，包括 Linux 7.2.10-piano26 及匹配的相机 loopback 包。安装后重启，使触控修复生效。保留已安装的旧内核及其回退启动镜像组。无需重分区或重刷根镜像。

## 从 v0.3.1 升级到 v0.3.2

按下方相同步骤使用 `piano-ubuntu-v0.3.2-packages.zip`，一起安装全部软件包。内核仍为 Linux 7.2.10-piano25。安装后重启，以刷新 snap 图形提供程序并让应用加载新驱动。无需重分区或重刷根镜像。

## 从 v0.3.0 升级到 v0.3.1

按下方相同步骤使用 `piano-ubuntu-v0.3.1-packages.zip`，一起安装全部软件包，包括 Linux 7.2.10-piano25 及匹配的相机 loopback 包；保留旧内核与回退启动镜像组。无需重分区或重刷根镜像。

重启后，设置 → 电源中会出现电源键动作选项。默认动作为待机；此前自行选择的电源键设置会保留。相机服务自动启动，相机可供应用使用。

## 从 v0.2.0 升级到 v0.3.0

使用 v0.3.0 的完整 `packages.zip`：一起安装内核包、设备支持/桌面包、Mesa 与 snap 图形包，以及 `piano-camerad` 和与新内核匹配的 `piano-camera-loopback-*`；其余随包提供的固件、传感器和认证包也按下文整组安装。无需重刷根镜像、重分区或重新提取本机文件。

本版启用系统待机并恢复 GNOME 默认空闲策略；休眠到磁盘仍关闭。原有个人电源设置不会被覆盖，可在“设置 → 电源”中调整。安装后重启，使新内核、WirePlumber 相机隔离和桌面颜色用户单元一起生效。相机后摄为固定焦距。保留旧内核及其匹配模块供回退。

## 整组升级（只用平板）

以下步骤都在平板上完成。`<版本>` 是要升级到的版本（发布页标题里的版本号，不带 `v`），`<上一版>` 是平板当前的版本。

1. **下载。** 用 Firefox 打开[最新发布页](https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/latest)，下载 `piano-ubuntu-v<版本>-packages.zip`、`piano-ubuntu-v<版本>-rollback.zip` 和 `SHA256SUMS.assets`，默认存到「下载」目录。

2. **校验并解压。** 打开「终端」：

   ```sh
   cd "$(xdg-user-dir DOWNLOAD)"
   sha256sum -c --ignore-missing SHA256SUMS.assets   # packages 和 rollback 两行都应为 OK
   unzip -o piano-ubuntu-v<版本>-packages.zip        # 得到 repositories/piano-<版本>
   unzip -o piano-ubuntu-v<版本>-rollback.zip        # 得到 repositories/piano-<上一版>-rollback-<版本>
   ```

3. **预览清理（可选）。** 从 1.4.1 起，整组升级完成后会自动清掉旧版本留下的批次、内核和启动镜像副本。想先看会删什么、为什么删，可运行下面的命令，它只列出、不做改动；从没做过整组升级的全新安装会提示没有可清理的记录，跳过即可：

   ```sh
   sudo /usr/bin/python3 repositories/piano-<版本>/install.py cleanup --product piano --dry-run
   ```

4. **整组升级。** 先正常关闭正在运行的虚拟机，再运行：

   ```sh
   sudo systemd-run --unit=piano-release-upgrade --setenv=DEBIAN_FRONTEND=noninteractive \
     /usr/bin/python3 "$PWD/repositories/piano-<版本>/install.py" upgrade \
     "$PWD/repositories/piano-<版本>" --previous "$PWD/repositories/piano-<上一版>-rollback-<版本>"
   journalctl -f -u piano-release-upgrade   # 出现 "Deactivated successfully" 后按 Ctrl+C
   sudo dpkg --audit                        # 应无输出
   ```

   升级在独立的系统单元里运行，关掉终端也不会中断。若中途失败，先运行 `sudo dpkg --configure -a`，再重跑同一条升级命令。

5. **重启并核对。**

   ```sh
   sudo reboot
   ```

   重新登录后：

   ```sh
   uname -r                                 # 应为新版本的内核
   sudo piano-boot-switch status            # 应为 "occupant": "ubuntu"
   ```

   升级工具已在 `/var/lib/piano/releases/` 保留回退要用的新旧两批，确认正常后可以删掉「下载」目录里的附件和解压出的 `repositories/`、`debs/`、`optional/`。

### 用电脑传输（可选）

也可以在电脑上下载并校验同样三个文件，再经开发者模式传到平板：平板终端运行 `sudo piano-devmode on`，电脑上运行 `scp piano-ubuntu-v<版本>-packages.zip piano-ubuntu-v<版本>-rollback.zip SHA256SUMS.assets <USER>@192.168.88.2:`，然后在平板终端（或经 `ssh <USER>@192.168.88.2`）的家目录里从第 2 步继续，`cd` 改为 `cd ~`。不再需要时运行 `sudo piano-devmode off`。

## 回退

- **系统能进 Ubuntu：** v1.2.1 及以后的整组升级用上文的「整版回退」；更早的升级用自动保留的 `ubuntu.prev`：
  ```bash
  sudo piano-boot-switch import-ubuntu --from /var/lib/piano/switch/ubuntu.prev   # 预期 "result": "imported"
  sudo piano-boot-switch to-ubuntu --reboot                                      # 写回旧组并重启
  ```
- **系统进不去：** 需要电脑。长按电源 + 音量下进入 Fastboot，用事先按 [FLASHING.md「镜像组」](FLASHING.md#镜像组)保存到电脑上的旧 Ubuntu 镜像组写回：
  ```bash
  python3 "$REPO/tools/piano-switch.py" --serial <SERIAL> --images <旧 ubuntu 镜像组> --rollback <android-set>
  fastboot -s <SERIAL> reboot
  ```
  或者先切回 Android（`--images <android-set> --rollback <旧 ubuntu 镜像组>`），再从容处理。
