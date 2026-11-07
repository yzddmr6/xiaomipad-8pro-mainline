# Ubuntu for Xiaomi Pad 8 Pro

在小米平板 8 Pro（代号 `piano`，骁龙 8 至尊版）上原生运行 Ubuntu 26.04 LTS GNOME 桌面，与 Android 双系统共存，内核基于上游 Linux 7.2 适配。不是容器，不是远程桌面。[English](README.en.md)

**最新版本见 [Releases](https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/latest)。官方发布只在本仓库 Releases，免费提供。** 支持状态见[硬件支持](#硬件支持)。

## 🧭 路线选择

让一台安卓平板跑起 Linux 桌面，大致有两条路。

一条是**先跑通，再兼容**：沿用厂商的下游驱动，桌面和应用哪里不配合就改哪里，功能来得快。代价是设备的特殊之处散落在内核、桌面和应用的各个角落，彼此耦合，每升级一次系统，这些修改都要重新对一遍。

另一条是**贴近上游**：硬件问题回到内核和驱动里，按上游主线的方式解决；Ubuntu、GNOME 这些软件尽量保持原样，设备通过标准接口被系统认识。这条路更慢、更难，很多问题必须追到根因才能修好。

我们选择后一条。

| | 先跑通，再兼容 | 贴近上游（本项目） |
|---|---|---|
| 内核与驱动 | 厂商下游驱动，加临时绕过 | 主线内核和上游驱动，缺的部分按上游的方式补齐 |
| 桌面与应用 | 修改桌面组件来适配这台设备 | 尽量使用发行版原版软件，设备差异不外溢到桌面 |
| 遇到问题 | 加延时、重试、兼容层，先让它能用 | 找到根因，在出问题的那一层修好 |
| 系统升级 | 修改要逐个重新适配，容易卡住 | 跟着上游和发行版正常升级 |
| 别人复用 | 依赖这一整套特定修改 | 换发行版、换同芯片设备，成本都很低 |

**为什么这样选：**

- **解耦。** 屏幕、触控、充电这些硬件差异由内核和设备树描述，桌面看到的只是标准的显示器、触摸屏和电池，不需要知道这是哪台平板。
- **升级不被绑住。** 改动越少，升级越顺：内核可以跟进上游新版本，Ubuntu 的更新照常安装。确实必须修改的少数桌面组件单独打包、跟踪上游，上游支持后就撤掉。
- **修一次，大家受益。** 按上游方式解决的问题，可以回到上游，让同芯片的设备和其他发行版都用上；上游的修复，我们也能直接拿来。
- **长期可维护。** 修改少、边界清楚，出了问题容易定位，不会越修越乱。

代价是功能来得慢一些，有些问题要花很长时间才能追到根因。比如触摸时偶发的闪屏，我们没有靠降低刷新率去回避，而是一直追到触控数据传输和系统省电之间的时序冲突，在驱动里修好。我们愿意付出这个代价，因为这才是能长期走下去的路。

## 📮 关注与交流

技术长文、经验分享和进展动态在公众号；交流群在小红书，扫码进群交流。刷机问题也欢迎在这两处反馈。

<div align="center">
<table>
<tr><th>公众号：熵减矩阵</th><th>小红书：yzddmr6</th></tr>
<tr>
<td align="center"><img src="docs/assets/gzh-qr.png" width="300" alt="公众号：熵减矩阵"></td>
<td align="center"><img src="docs/assets/xhs-qr.jpg" width="300" alt="小红书：yzddmr6"></td>
</tr>
<tr><td align="center">技术长文 · 经验分享 · 进展动态</td><td align="center">进群交流 · 问题反馈</td></tr>
</table>
</div>

## ⚠️ 安装前请阅读

- **这是实验性项目，不是日用方案。** 安装会解锁、重分区、清空数据；操作不当可能变砖，恢复需要你会用 `adb`、`fastboot` 和分区备份。请确认自己有相应的动手能力，想清楚再做，后果自负。
- **会清空平板上的全部 Android 数据。** 安装重建 `userdata` 并清除 `metadata`。
- **必须先解锁 Bootloader**（按小米官方流程），并取得 Android root（用于读取分区做备份）。
- **只支持原厂版本 OS3.0.303.0.WPYCNXM，运行在 A 槽。**
- **先备份。** 任何写入之前，备份除 `userdata` 外的全部分区和每个 UFS LUN 的分区表。切回 Android 需要 A 槽原厂的 `boot`、`init_boot`、`vendor_boot`。
- **切换系统前保持电量不低于 40%。**
- **没有扬声器保护。** 扬声器可以播放，但音量上限受限，不要自行提高增益。
- **只在一台机器上验证过：** 型号 25091RP04C（国行），16 GB 内存，512 GB 存储。其他型号和容量未验证。
- **恢复方法：** 无画面或无响应时，长按电源 + 音量下进入 Fastboot，用 `tools/piano-switch.py` 写回 Android，见[使用与维护](#使用与维护)。

分区布局：512 GB 机型把原 `userdata` 的范围拆成 `userdata`（200 GiB，Android）、`linux_rootfs`（60 GiB，Ubuntu `/`）和 `linux_data`（约 201 GiB，`/home`）；256 GB 机型 `userdata` 为 110 GiB，`linux_data` 约 61 GiB。其他分区不变。
**不要刷写 `vbmeta`，不要写入或激活 B 槽。** 标定和设备专属配置必须取自你自己的平板，不能用别人的。

## 🚀 安装

你需要一台 Linux x86_64 主机、一根 USB 数据线，以及坐在平板旁边的一个多小时。

### 推荐：让 AI 帮你安装

安装步骤多、顺序严格，最省力的方式是交给能执行命令的 AI 编程助手（Claude Code、Codex CLI、Gemini CLI 等）。在 Linux 主机上启动助手，把下面这段完整复制给它。它会读文档、检查环境、做备份，每一步写入前先向你确认；你只负责在平板上按键和做选择。

```text
我要在小米平板 8 Pro（代号 piano）上安装 Ubuntu，与 Android 双系统共存。请你作为操作助手，按下面的规则帮我完成。

项目：https://github.com/yzddmr6/xiaomipad-8pro-mainline ，使用最新发布版本（https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/latest 上标为 Latest 的版本）。

1. 先 git clone 该仓库并检出最新发布版本对应的标签，通读 README.md 的「安装前请阅读」、docs/INSTALL.md、docs/FLASHING.md，以及 docs/AGENT-GUIDE.md 第 1 节的安全规则，然后再开始。
2. 从该版本的 GitHub Release 把全部附件下载到一个空目录，先执行 sha256sum -c SHA256SUMS.assets，再按 Release 说明还原发布目录并再次校验。
3. 检查主机工具（adb、fastboot、python3、zstd、sgdisk、sfdisk、img2simg 等，以 INSTALL 的「准备」表为准）和平板状态（型号 piano、原厂版本 OS3.0.303.0.WPYCNXM、当前 A 槽、已解锁、已 root、电量不低于 40%）。任何一项不符就停下来告诉我，不要绕过。
4. 严格按 docs/INSTALL.md 的步骤顺序执行，每条设备命令都用 -s 或 --serial 明确指定我的平板。每一步开始前用一两句话告诉我这步做什么、会写哪里；凡是写入平板的操作（分区表、擦除、flash、写 A 槽），先列出将要执行的命令，等我回复「确认」再执行。
5. 绝不写入 vbmeta、dtbo、任何 _b 分区、persist 或其他校准、基带、固件分区；绝不执行 fastboot --set-active=b；绝不使用别的平板的标定、设备专属配置或镜像。
6. 备份完成并校验哈希之后才能进入任何写入步骤。备份和本机文件放在仓库之外的目录，不要提交，不要上传到任何地方。
7. 出错就停：任何命令返回错误，或输出与文档中的「预期」不符，立即停止并把完整输出给我看；不要自行重试写入命令，不要中断正在进行的写入。
8. 我会全程坐在平板旁边。需要我在平板上操作（按键进入 Fastboot、初始设置建账户、输入密码）时，明确告诉我做什么、等什么现象。
9. 安装完成后按文档做完最后一步（装入本机文件、保存 Android 镜像组、重建启动镜像组、关闭开发者模式），然后教我怎样在两个系统之间切换、怎样从 Fastboot 恢复 Android。
```

### 手动安装

1. 从[最新发布页](https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/latest)把全部附件下载到一个空目录（目录里只放这一个版本），校验并还原发布目录：

   ```bash
   sha256sum -c SHA256SUMS.assets
   unzip piano-ubuntu-*-boot.zip && unzip piano-ubuntu-*-tools.zip && unzip piano-ubuntu-*-packages.zip
   mkdir -p rootfs && cat piano-ubuntu-*-rootfs.img.zst.part* | zstd -d > rootfs/root.sparse.img
   sha256sum -c SHA256SUMS
   ```

2. 按 [docs/INSTALL.md](docs/INSTALL.md) 逐步操作。切换、Fastboot 恢复和绝不能写入的分区见 [docs/FLASHING.md](docs/FLASHING.md)。

### 已安装用户升级

按 [docs/UPGRADE.md](docs/UPGRADE.md) 用发布页的 `packages.zip` 升级内核和配套软件包，升级会保留回退镜像组。

### 从源码构建

内核、Mesa、设备软件包、根镜像和启动镜像组都能在主机上无 root 权限构建，输入锁定在 `kernel/source.json`、`mesa/source.json`、`ubuntu/` 和 `device/*/source.json`。见 [docs/BUILD.md](docs/BUILD.md)。

## 硬件支持

以下为最新发布版本的支持情况。✅ 可用 · 🟡 部分支持 · ❌ 不支持。
当前支持范围与各版本改进见 [docs/STATUS.md](docs/STATUS.md)（英文）。

### 平台、显示与输入

| 功能 | 器件 / 实现 | 状态 | 说明 |
|---|---|---|---|
| SoC / CPU | 高通骁龙 8 至尊版（SM8750P） | ✅ 可用 | 八个核心均可调频 |
| Android / Linux 双系统 | A 槽镜像组轮换 | ✅ 可用 | Ubuntu 内一条命令、Android 内 KernelSU 模块、主机 Fastboot 三种切换方式都已验证 |
| 内置存储 | 铠侠 UFS / ext4 | ✅ 可用 | 60 GiB 根分区，512 GB 机型约 201 GiB `/home`；启用 UFS 多队列 |
| 内置显示 | 原生 DSI KMS（`msm_dpu`） | ✅ 可用 | 3200×2136，60/90/120/144 Hz 切换及关屏/亮屏通过 |
| 夜灯 | GNOME / DPU 色温调整 | ✅ 可用 | 支持色温调整，关屏和待机恢复后保持设置 |
| GNOME 桌面 | Ubuntu 26.04 GNOME / Wayland / GDM | ✅ 可用 | 冷启动约 13 秒到登录界面 |
| GPU | Adreno 830 / Mesa 26.2 | ✅ 可用 | 桌面与 snap 应用（含 Firefox）均走 GPU 渲染 |
| 系统监控 | GNOME Resources | ✅ 可用 | 任务管理器可看 CPU 各核频率与温度，GPU 型号、频率、温度和占用率，每个应用的 GPU 占用，以及两块电池各自的温度；“温度”页按设备列出全部温度传感器并显示历史曲线 |
| 触摸屏 | Novatek NT36532E（THP，SPI） | ✅ 可用 | 单指与多指触控、手势可用，支持 60/90/120/144 Hz 及关屏、待机恢复；自动识别屏型（CSOT 或 BOE）并加载对应的触控固件和方向 |
| 磁吸键盘 | Nanosic WN8030 | ✅ 可用 | 支持打字、熄屏按键唤醒和待机唤醒 |

暂不支持：手写笔、指纹。

### 无线与 USB

| 功能 | 器件 / 实现 | 状态 | 说明 |
|---|---|---|---|
| Wi-Fi | 高通 WCN7860 / ath12k | ✅ 可用 | 2.4 GHz 与 5 GHz 80 MHz 连接稳定 |
| 蓝牙 | 高通 WCN7860 / `hci_qca` | ✅ 可用 | 配对、文件传输和蓝牙音频可用 |
| USB 设备模式 | USB 3 / 5 Gbit/s 网络（NCM） | ✅ 可用 | 开发者模式下经 USB 线 SSH 登录，默认关闭 |
| USB 主机 / OTG | Type-C 角色切换 / USB 3 / 5 Gbit/s | 🟡 部分支持 | U 盘、键鼠、扩展坞可用，正反插 5 Gbit/s；拔出 OTG 设备后直接重插转接头可能不被识别 |
| USB-C 外接显示 | DisplayPort Alt Mode | ✅ 可用 | 正反插、热插拔，实测 4K 60 Hz，扩展桌面 |

### 音视频与传感器

| 功能 | 器件 / 实现 | 状态 | 说明 |
|---|---|---|---|
| 扬声器 | FS19xx 四路功放 | ✅ 可用 | 四路扬声器播放，最大音量已提高；保持默认音量上限 |
| 内置麦克风 | DMIC / ADSP / PipeWire | ✅ 可用 | 录音已验证 |
| 视频硬解 | 高通 Iris / V4L2 | ✅ 可用 | 系统播放器（GStreamer）、Firefox（H.264/HEVC/VP9）、Chrome（H.264/VP9）自动硬件解码；AV1 为软件解码 |
| 视频硬编 | 高通 Iris / V4L2 | ✅ 可用 | H.264/H.265 1080p、4K 编码已验证 |
| 摄像头 | 后摄 S5KJN1SQ03、前摄 OV32D40 | ✅ 可用 | 前后摄像头可在相机应用中使用；后摄固定焦距，适合非近距离拍摄 |
| 加速度、陀螺、光感、距离 | SSC / hexagonrpcd / libssc | ✅ 可用 | SensorProxy 可报告方向和环境光 |
| 自动旋转 | SensorProxy / Mutter | ✅ 可用 | 拆下键盘后自动旋转；装着键盘时按 GNOME 规则锁定 |

Firefox 播放部分 HEVC 视频可能有轻微丢帧。Chromium snap 不支持此硬解集成。

### 电源与时间

| 功能 | 器件 / 实现 | 状态 | 说明 |
|---|---|---|---|
| 电源键 / 音量键 | 高通 PMIC | ✅ 可用 | 电源键默认锁屏，熄屏后再按唤醒锁屏界面，可在设置 → 电源中选择动作；音量键调节音量 |
| 性能模式 | GNOME / TuneD | ✅ 可用 | 快速设置或设置 → 电源中切换节能、平衡、性能，默认平衡 |
| 电量 / 温度 | 2 × TI BQ27Z561 / 温区 | ✅ 可用 | 电量、电压、电流和温度可读 |
| 机身温度 | PMIC ADC 热敏电阻 | ✅ 可用 | 11 路板载温度可读，任务管理器“温度”页可看 |
| 充电 | `xiaomi_mca` / SC8541 | 🟡 部分支持 | USB PD 充电器以 9 V 快充，快充功率暂低于原厂；高通 QC 等其他快充协议按普通 5 V 充电。「设置 → 电源」可开启「保护电池健康」：80% 停充、降到 75% 复充；开启后插电且未到上限时系统保持唤醒，与 Android 一致 |
| 待机 | suspend-to-idle | ✅ 可用 | 支持定时、键盘、电源键和开盖唤醒，使用系统默认空闲策略；插电时合上键盘盖只锁屏、不进入睡眠 |

暂不支持：休眠到磁盘、硬件时钟写入（启动后联网校时）。

### 虚拟化

| 功能 | 实现 | 状态 | 说明 |
|---|---|---|---|
| 命令行虚拟机 | Gunyah / crosvm | ✅ 可选功能 | Ubuntu/Windows ARM 客体、SSH/VNC、NAT 和持久磁盘；[使用指南](docs/VIRTUALIZATION.zh-CN.md) |
| Android 应用 | Waydroid | 🟡 部分支持 | 可选安装，运行 Android 应用并联网；仅软件渲染，无 GPU 加速；[安装与设置](docs/WAYDROID.md) |

## 使用与维护

主机命令按 [INSTALL.md 的「准备」](docs/INSTALL.md#准备)设置 REPO、RELEASE 和 WORK，在 WORK 中执行。详细说明见 [docs/FLASHING.md](docs/FLASHING.md)。

**切换系统。** 在 Ubuntu 中运行 `sudo piano-boot-switch to-android --reboot`，它把保存的 Android 镜像组写入 A 槽、读回校验后重启。在 Android 中用 [KernelSU 切换模块](device/android/piano-switch-ksu/README.md)切到 Ubuntu。在系统内切换不改变槽属性。

**紧急回 Android。** Ubuntu 无响应时，打开键盘盖，同时按住音量上、下键 10 秒；镜像组校验成功后切回 Android 并重启，未保存的内容会丢失，合盖时不响应；若仍无响应，使用下面的 Fastboot 恢复方法。

**从主机恢复。** 平板处于 Fastboot（电源 + 音量下）时，`tools/piano-switch.py` 先核对设备与两组镜像，再写入 A 槽，任一步失败即写回原镜像组：

```bash
python3 "$REPO/tools/piano-switch.py" --serial <serial> --images <android-set> --rollback <ubuntu-set>
fastboot -s <serial> reboot
```

主机写入会重置 A 槽的重试计数，下一次 Ubuntu 启动后自动重新标记。若 Fastboot 显示 `slot-unbootable:a` 为 `yes`，执行 `fastboot -s <serial> --set-active=a` 重置；B 槽仍然不能用。

**开发者模式。** `sudo piano-devmode on` 开启经 USB 线的 SSH；默认关闭。主机上用 `python3 "$REPO/tools/piano-devmode-ssh.py" --serial <serial> <user>` 连接。装完后用 `sudo piano-devmode off` 关闭。详见 [device/devmode](device/devmode/README.md)。

**升级。** 见 [docs/UPGRADE.md](docs/UPGRADE.md)。内核包安装后会自动重建启动镜像组并改写 A 槽，旧镜像组保留为回退。

**时间。** 硬件时钟不可写，启动后联网自动校时；没有网络时用 `sudo date -s 'YYYY-MM-DD HH:MM'` 手动设置。

**传感器标定。** 镜像只带默认标定。推荐安装你自己平板的原厂标定：按[安装指南](docs/INSTALL.md#推荐准备本机传感器-registry)或[传感器说明](device/sensors/README.zh-CN.md)从自己的备份准备，不要用别人的。

**初始设置之后。** 初始设置会把「计算机」栏设为主机名，之后可用 `sudo hostnamectl hostname --static <名字>` 修改。第一次 `apt install` 之前先 `sudo apt update`，镜像不带软件包索引。

**应用中心。** 联网后运行 `sudo snap refresh snap-store gnome-46-2404` 更新应用中心。

**自检。** 运行 `piano-check` 查看本地状态，或运行 `piano-check --markdown` 生成可贴入 issue 的自检摘要；不需要 root，摘要不含设备序列号、无线地址、网络名或用户名。

## 开发与贡献

| 仓库 | 内容 |
|---|---|
| xiaomipad-8pro-mainline | 设备用户态、Ubuntu 集成、构建/打包/切换工具、固件、文档 |
| [linux-sm8750-piano](https://github.com/yzddmr6/linux-sm8750-piano) | Linux 内核：在上游 Linux 7.2 stable 之上维护驱动、设备树与配置；发布提交见输入锁 |

构建内核、Mesa、设备软件包与镜像见 [docs/BUILD.md](docs/BUILD.md)；[kernel/source.json](kernel/source.json) 锁定内核提交与配置片段。

在设备上操作前，请先阅读面向人工操作者和 AI 助手的 [docs/AGENT-GUIDE.md](docs/AGENT-GUIDE.md)；贡献规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

问题请通过 [GitHub Issues](https://github.com/yzddmr6/xiaomipad-8pro-mainline/issues) 反馈，贴上 `piano-check --markdown` 的输出并注明原厂版本；提交前去除序列号、蓝牙地址、标定数据和个人信息。

## 赞助

本项目由个人维护。移植、调试和每个版本的真机测试都要投入大量时间和精力，开发中也需要持续购买 AI 编程工具的 token。

项目免费开源，不赞助也能使用全部功能。如果它对你有帮助，欢迎赞助，经费用于购买 token 和后续的开发维护。赞助是自愿的支持，不对应任何服务或承诺。

- 小红书：店铺中的「致谢名单」（扫上方小红书二维码进入主页）

赞助时留下 GitHub ID 或想显示的昵称，会加到下面的名单；不留则不公开。

感谢以下朋友的支持（按累计金额排序，¥50 及以上注明金额）：

| 赞助者 | 金额 |
|---|---|
| [@pidbid](https://github.com/pidbid) | ¥100 |

## 致谢与许可证

本项目复用并改编了以下工作：
- 一加平板 2 Pro（`erhai`）主线集成项目（GPL-2.0-only）；
- 小米平板 6 Pro（`liuqin`）主线项目：[集成仓](https://github.com/yzddmr6/xiaomipad-6pro-mainline)（MIT）与[内核](https://github.com/yzddmr6/linux-sm8450-liuqin)（GPL-2.0）；
- [BigfootACA/linux](https://github.com/BigfootACA/linux/tree/full-v7.1.3)（GPL-2.0）；
- [ROCKNIX](https://github.com/ROCKNIX/distribution/tree/92a6fca6afda5ab7e1874af8985095beee19b6a8) 与 Teguh Sobirin 的 [SM8750 内核补丁](https://github.com/ROCKNIX/distribution/tree/92a6fca6afda5ab7e1874af8985095beee19b6a8/projects/ROCKNIX/devices/SM8750/patches/linux)（GPL-2.0；DTS BSD-3-Clause）；
- 高通（Jie Zhang、Akhil P Oommen、Gaurav Kohli）：[Adreno 830 系列](https://patchew.org/linux/20260809-pakala-gpu-v1-0-207ad1fac1c7%40oss.qualcomm.com/)（GPL-2.0）；
- Junjie Cao：[NT36532 面板驱动](https://patch.msgid.link/20260804074233.85629-1-junjie.cao@linux.dev)（GPL-2.0-only）；
- Alexander Koskovich 与 Jonathan Marek：[DSC 单包多 slice 补丁](https://patch.msgid.link/20260318-dsi-dsc-slice-per-pkt-v2-1-0a1b316f8250@pm.me)（GPL-2.0）；
- 小米 [vendor_xiaomi_proprietary_touch-driver](https://github.com/MiCode/vendor_xiaomi_proprietary_touch-driver/tree/piano-w-oss)（GPL-2.0）；
- ianchb 的 [xiaomi-sheng-thp](https://github.com/ianchb/xiaomi-sheng-thp)（Apache-2.0）；
- map220v 的 [hid-nanosic-wn8030](https://github.com/ianchb/sm8550-mainline/blob/42f3b40c702abbc37c3a04c95af008cd283aeaf3/drivers/hid/hid-nanosic-wn8030.c)（GPL-2.0-only）；
- AOSP [mkbootimg](https://android.googlesource.com/platform/system/tools/mkbootimg)（Apache-2.0）与
  [avbtool](https://android.googlesource.com/platform/external/avb/)（MIT）；
- [Mesa](https://gitlab.freedesktop.org/mesa/mesa)（MIT）；
- linux-msm [hexagonrpc](https://github.com/linux-msm/hexagonrpc)（GPL-3.0、LGPL-3.0）与
  [qrtr](https://github.com/linux-msm/qrtr)（BSD-3-Clause）；
- [libssc](https://codeberg.org/DylanVanAssche/libssc)（GPL-3.0）；
- [iio-sensor-proxy](https://gitlab.freedesktop.org/hadess/iio-sensor-proxy)（GPL-3.0）；
- [AudioReach topology](https://github.com/linux-msm/audioreach-topology)（BSD-3-Clause）；
- [TinyALSA](https://github.com/tinyalsa/tinyalsa)（BSD-3-Clause）；
- [systemd](https://github.com/systemd/systemd)（LGPL-2.1-or-later、GPL-2.0-or-later）；
- BusyBox（GPL-2.0）；
- [Ubuntu](https://ubuntu.com/download/desktop)。

相关项目（作为参考）：
- [Project-SunUEFI](https://github.com/slwyts/Project-SunUEFI)（按文件许可）；
- [blu-sharky/linux-piano](https://github.com/blu-sharky/linux-piano)（GPL-2.0）；
- [blu-sharky/debian-piano](https://github.com/blu-sharky/debian-piano)（MIT）。

各文件的来源、提交与改动记录在 [NOTICE](NOTICE)。除文件 SPDX 头另有声明外，原创集成代码采用
[MIT](LICENSE)；若干改编自 GPL-2.0-only 来源的工具保持 GPL-2.0-only。内核为 GPL-2.0。第三方组件保留各自
许可（见 `LICENSES/`）。原厂固件与静态传感器配置的分发政策和自备提取文件的方法统一见
[firmware/README.md](firmware/README.md#distribution-policy)。
