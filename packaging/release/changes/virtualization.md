可选安装命令行虚拟机工具，在平板上运行独立的 Ubuntu 客体。

## 更新

- **Ubuntu 虚拟机**：普通用户可创建和管理 ARM64 客体，支持 SSH、NAT 上网、持久磁盘和正常关机后重新启动。
- **按需启用**：虚拟机工具单独安装，虚拟化模块按需加载；设备与网络操作分别请求管理员认证。
- **使用指南**：[命令行虚拟机](https://github.com/yzddmr6/xiaomipad-8pro-mainline/blob/main/docs/VIRTUALIZATION.zh-CN.md) 包含安装、创建、使用和卸载步骤。

## 已知限制

- 虚拟机仅提供命令行，不支持图形桌面或设备直通；每个用户同时运行一台，运行期间会占用宿主内存。

- 插过 USB-C 显示器后，再用 USB 线连接电脑使用开发者连接，需要重启平板才能识别。
- 拔下 OTG 设备后，直接插入的 OTG 转接头可能不被识别；插拔一次充电器或电脑线，或改用扩展坞即可。
- 蓝牙偶发需要重新连接才能恢复数据或音频。
- 后摄为固定焦距，近景可能偏虚，颜色调校仍有限。

手写笔、指纹和休眠到磁盘仍不支持。保留默认扬声器音量上限。

---

Optional command-line tools run independent Ubuntu guests on the tablet.

## Changes

- **Ubuntu virtual machines**: ordinary users can create and manage ARM64 guests with SSH, NAT networking, persistent disks, clean shutdown and restart.
- **Enable on demand**: install the VM tools separately and load virtualization modules when needed; device and network operations request administrator authentication separately.
- **User guide**: [Command-line virtual machines](https://github.com/yzddmr6/xiaomipad-8pro-mainline/blob/main/docs/VIRTUALIZATION.md) covers installation, creation, use and removal.

## Known limitations

- Virtual machines provide a command line without a graphical desktop or device passthrough. Each user can run one VM at a time; its memory is unavailable to the host while running.

- After a USB-C display has been connected, the tablet must be restarted before a USB cable to a computer is recognized for the developer connection.
- After an OTG device is unplugged, a plain OTG adapter plugged in next may not be detected; plug a charger or computer cable in and out once, or use a dock.
- Bluetooth occasionally needs reconnecting before data or audio works again.
- The rear camera has fixed focus; nearby subjects may be blurry and color tuning is limited.

Stylus, fingerprint and disk hibernation remain unsupported. Keep the default speaker volume cap.
