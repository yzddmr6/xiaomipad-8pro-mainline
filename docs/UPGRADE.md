# 升级已安装的系统

[English](UPGRADE.en.md) ｜ [项目首页](../README.md) ｜ [全新安装](INSTALL.md) ｜ [切换与恢复](FLASHING.md)

本页说明如何把已经装好的 Ubuntu 升级到新版本的内核和配套软件包，不重装、不动分区、不清数据。
升级前提：已按 [INSTALL.md](INSTALL.md) 做完最后一步（`/etc/piano` 中有 `boot-profile.json`、`boot-signing.pem` 和 `boot-partitions.json`），能开启开发者模式，电量不低于 40%。

发布附件里的 `piano-ubuntu-<版本>-packages.zip` 就是升级包：解压后 `debs/` 下是该版本安装在镜像里的全部项目软件包。总是整组一起安装，不要只装其中一个。


## 从 v1.1.1 升级到 v1.2.0

⟦DRAFT:G-PACKAGE⟧
按下方步骤整组安装全部软件包并重启。本版更新 Linux 7.2.15 内核与配套相机模块、设备支持包和“补光相机”应用。保留用户设置、手指标定与回退镜像组。

⟦DRAFT:G-PEN⟧
Focus Pen Pro（P81C）使用 120 Hz。给已有系统补笔标定时，用本机匹配的原厂 INI 运行 `tools/extract-thp-calibration.py`，`--merge-existing` 指向原有标定，`--output` 指向一个新的私有文件。工具会拒绝不同面板，并保留手指几何与方向设置。将结果以 root 所有、0600 权限安装为 `/etc/piano/thp-calibration.json`，再重启。旧文件留作回退；不可在平板之间交换标定。

⟦DRAFT:G-FLASH⟧
打开“补光相机”，选择后置摄像头，勾选“拍照补光”。灯在测光和取帧期间连续照明，完成后关闭；这不是与曝光信号同步的高功率闪光。Snapshot 和其他相机应用继续使用标准摄像头输出。

⟦DRAFT:G-GUNYAH⟧
整组软件包预装 GunyahVM，可从应用菜单打开。

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

## 1. 下载并校验

在主机上，把发布页的 `piano-ubuntu-<版本>-packages.zip` 和 `SHA256SUMS.assets` 下载到一个空目录：

```bash
sha256sum -c --ignore-missing SHA256SUMS.assets    # 预期 packages.zip 一行 "OK"
unzip piano-ubuntu-<版本>-packages.zip               # 得到 debs/
```

## 2. 保留回退

升级会把当前启动镜像组换成新内核的，旧组自动保留为 `ubuntu.prev`。再额外保存一份独立副本，以防之后再次升级把 `ubuntu.prev` 也轮换掉。在平板上：

```bash
sudo cp -a /var/lib/piano/switch/ubuntu /var/lib/piano/rollback-$(uname -r)
```

主机上也按 [FLASHING.md「镜像组」](FLASHING.md#镜像组)保留当前 Ubuntu 镜像组和 Android 镜像组的副本，这是从 Fastboot 恢复的依据。

## 3. 传到平板

在平板终端打开开发者模式，再从主机复制：

```bash
# 平板
sudo piano-devmode on
# 主机
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check     # 预期 "... ready"
scp -r debs <USER>@192.168.88.2:
```

## 4. 安装

用临时单元运行安装，SSH 断开也不会中止；内核包的安装会重建 initrd，约 10 分钟。

```bash
sudo apt update
sudo systemd-run --unit=piano-upgrade --setenv=DEBIAN_FRONTEND=noninteractive \
  apt-get install -y /home/<USER>/debs/*.deb
journalctl -f -u piano-upgrade
# 预期 "Piano boot image set prepared for <新版本内核>" 和 "Slot A now holds store/ubuntu"，
# 最后 "piano-upgrade.service: Deactivated successfully."
sudo dpkg --audit                          # 预期无输出
sudo piano-boot-switch verify              # 预期退出码 0
```

## 5. 重启并核对

```bash
sudo systemctl reboot
```

重新登录后：

```bash
uname -r                                   # 预期新版本的内核
sudo piano-boot-switch status              # 预期 "occupant": "ubuntu"
sudo piano-slot-success --check            # 预期 "state": "already-successful"
sudo piano-devmode off                     # 不再需要主机连接时关闭
```

旧内核包仍然装着，回退镜像组用得到它；确认新版本正常使用一段时间后，可用 `sudo apt purge linux-image-<旧版本>` 清理。

## 回退

- **系统能进 Ubuntu：**
  ```bash
  sudo piano-boot-switch import-ubuntu --from /var/lib/piano/rollback-<旧版本>   # 预期 "result": "imported"
  sudo piano-boot-switch to-ubuntu --reboot                                     # 写回旧组并重启
  ```
- **系统进不去：** 长按电源 + 音量下进入 Fastboot，用主机上保存的旧 Ubuntu 镜像组写回：
  ```bash
  python3 "$REPO/tools/piano-switch.py" --serial <SERIAL> --images <旧 ubuntu 镜像组> --rollback <android-set>
  fastboot -s <SERIAL> reboot
  ```
  或者先切回 Android（`--images <android-set> --rollback <旧 ubuntu 镜像组>`），再从容处理。
