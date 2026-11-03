# 命令行虚拟机

在支持虚拟化的 Ubuntu 系统上，可以创建独立的 Ubuntu 26.04 ARM64 虚拟机，使用 SSH、NAT 网络和持久磁盘。默认配置为 2 个 vCPU、2 GiB 内存和 8 GiB 稀疏磁盘。

## 开启

先按最新发布说明升级内核与设备支持包，再进入升级包的 `optional/virtualization/` 目录，安装同一发布提供的可选包。在平板本地桌面的终端执行：

```sh
sudo apt install --no-install-recommends ./piano-crosvm_*_arm64.deb ./piano-vm_*_arm64.deb
sudo usermod -aG gunyah "$USER"
```

退出桌面并重新登录，使组身份和内存限额生效。若仍提示内存锁定额度不足，重启宿主后再登录。仅把可信账户加入 gunyah 组。虚拟机本身以你的账户运行；启用设备和创建、删除网络时，会单独请求管理员认证。

准备现有 SSH 公钥；没有时执行 `ssh-keygen -t ed25519`，按提示选择保存位置。工具不保存私钥到客体。

## 创建和使用

每次宿主开机后，首次使用前启用虚拟化，再创建客体：

```sh
piano-vm load
piano-vm create ubuntu --start
piano-vm list
piano-vm console ubuntu
```

首次创建需要联网下载 Ubuntu 云镜像。串口显示首次配置进度，Ctrl-C 只停止查看，不关闭虚拟机。等 list 显示 guest_ready 和 IP 后：

```sh
piano-vm ssh ubuntu
piano-vm ssh ubuntu -- 'sudo apt-get update'
piano-vm stop ubuntu
piano-vm start ubuntu
```

可在 create 时指定 `--username vmuser --cpus 2 --memory-mib 2048 --disk-gib 16 --ssh-key ~/.ssh/id_ed25519.pub`。默认数据位于 `~/.local/share/piano-vm/`，稀疏磁盘会随实际写入增长；宿主应留出足够磁盘空间。关机后数据保留。

客体安装内核更新后，先正常关机，再用 start 启动。工具会从该磁盘重新提取配套内核和 initrd，并保留上一套启动文件。客体内直接 reboot 不会刷新宿主使用的启动文件。

stop 等待客体正常关机后回收网络。客体自行关机后，也执行一次 stop 以清理网络。只有客体无响应、确认接受未落盘数据可能丢失时，才使用 `piano-vm stop ubuntu --force`。日志和退出结果保存在虚拟机目录的 runs/ 与 state.json。

## 限制

- Ubuntu 客体提供命令行；不提供 Ubuntu 图形桌面、GPU、声音或 USB 直通。
- 每个用户同时运行一台。建议客体内存不超过 3 GiB；运行期间客体占用的内存不能由宿主使用。额度或可用内存不足会明确拒绝启动。
- 从平板本地桌面终端管理网络认证。运行虚拟机期间保持宿主唤醒，关机或重启宿主前先停止客体。
- 默认使用用户态 virtio-net。vhost 加速需要管理员另行配置设备权限，不是默认路径。
- SSH 使用公钥，默认账户没有登录密码；串口主要用于观察启动。设备沙箱启动失败时不会自动降级运行，请保留错误日志。

## 删除和卸载

```sh
piano-vm stop ubuntu
piano-vm delete ubuntu
```

删除会永久删除该虚拟机的磁盘、配置和日志，请先备份需要的文件。确认所有虚拟机均停止、网络清理完成后，可卸载可选包：

```sh
sudo apt purge piano-vm piano-crosvm
sudo gpasswd -d "$USER" gunyah
```

不要移除仍供平板使用的设备支持包。卸载包不会删除家目录中的虚拟机数据。重启宿主后，本次启用的模块和大页内存策略退出；不需要手工卸载运行中的驱动。

## Windows 11 ARM

Windows 模式需要自备微软官方 ARM64 ISO（含 `sources/install.wim`）及适用的 Windows 许可证。工具不提供 Windows 镜像、产品密钥或预设账户。建议在家目录所在的 ext4 或 XFS 文件系统保留至少 40 GiB 可用空间；系统盘是 64 GiB 稀疏文件，安装和更新会增加实际占用。

进入同批升级附件的 `optional/virtualization/`，额外安装 Windows 包：

```sh
sudo apt install --no-install-recommends ./piano-crosvm_*_arm64.deb ./piano-vm_*_arm64.deb ./piano-vm-windows_*_arm64.deb
```

Windows 准备工具、固件、驱动和 Remmina VNC 客户端由这个可选包提供。Linux 用户无需安装它。更新设备支持包后退出桌面并重新登录，取得 gunyah 组的 6 GiB 内存锁定额度；仍不足时重启宿主再登录。

```sh
piano-vm load
piano-vm create windows --os windows11 --iso ~/Downloads/Windows11_ARM64.iso --start
piano-vm view windows
```

创建时会从 WIM 列出版本，由你选择。也可以明确加上 `--edition 'Windows 11 Pro'`，名称必须来自该 ISO。默认 4 个 vCPU、4608 MiB 内存和 256 MiB DMA 池；内存最多 5120 MiB。额度或宿主可用内存不足时，工具会拒绝启动。Linux 的默认资源保持不变。

准备介质全程使用你的账户，不挂载 ISO 或虚拟磁盘。首次启动在客体 WinPE 中运行 DISM，窗口显示部署进度；成功并正常关机后，工具自动摘除、删除安装介质，并从系统盘进入 OOBE。部署失败时保留介质和日志，请先检查窗口与 VM 目录。不要把强停当作部署完成。

在 Remmina 窗口中完成普通 OOBE。首次 OOBE 会联网更新；Windows 请求的正常重启会重新启动 VM，窗口断开后可再次运行 `piano-vm view windows`。VNC 固定只监听 `127.0.0.1`，不向局域网开放。需要使用不同本地端口时，在 create 时加 `--vnc-port 5901`。

默认不创建本地账户，不启用自动登录或 RDP。若需要显式创建本地账户，可在 create 命令中加 `--local-user vmuser`，并在终端交互输入密码。应答文件使用 Windows 的隐藏密码编码，**这不是加密**；部署后宿主介质被删除，首次登录后客体应答文件被删除。需要 RDP 时，由你在支持该功能的 Windows 版本中开启，并在客户端确认服务器证书。

关机请使用 Windows 开始菜单中的“关机”，然后清理网络；下次正常启动：

```sh
piano-vm stop windows
piano-vm start windows
piano-vm view windows
```

Windows 尚在运行时，普通 stop 会要求先在客体内关机；`stop --force` 是可能丢失未落盘数据的强停。关机或重启宿主前先关闭客体，运行期间保持宿主唤醒。

限制：显示为 1280×720 软件帧缓冲，没有 GPU 加速；没有 vTPM，客体安全启动关闭，TPM 等安装检查被绕过。驱动使用测试签名，Windows 会显示测试模式水印；不要关闭驱动依赖的测试签名设置。休眠和快速启动已关闭，不提供音频或 USB 直通。

不再需要时，先关机，再执行 `piano-vm delete windows` 删除这台 VM。可单独 `sudo apt purge piano-vm-windows` 移除 Windows 工具，继续保留 Linux VM 工具；包卸载不会代替 VM 数据删除。
