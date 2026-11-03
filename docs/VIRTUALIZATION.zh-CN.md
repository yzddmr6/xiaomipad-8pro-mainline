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

- 当前提供命令行客体；不提供图形桌面、GPU、声音或 USB 直通。
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
