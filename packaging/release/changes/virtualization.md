可选安装虚拟机工具，在平板上运行独立的 Ubuntu 和 Windows ARM 客体。

## 更新

- **Windows 11 ARM**：从自备官方 ISO 创建 Windows 虚拟机，通过本地 VNC 完成 OOBE 和日常操作；固件、签名驱动及介质工具独立可选安装。
- **Ubuntu 虚拟机**：普通用户可创建和管理 ARM64 客体，支持 SSH、NAT 上网、持久磁盘和正常关机后重新启动。
- **按需启用**：虚拟机工具单独安装，虚拟化模块按需加载；设备与网络操作分别请求管理员认证。
- **使用指南**：[命令行虚拟机](https://github.com/yzddmr6/xiaomipad-8pro-mainline/blob/main/docs/VIRTUALIZATION.zh-CN.md) 包含安装、创建、使用和卸载步骤。

## 已知限制

- Ubuntu 客体提供命令行；Windows 使用软件显示，无 GPU 加速、vTPM 或安全启动，绕过 TPM 等安装检查并显示测试模式水印。每个用户同时运行一台，运行期间会占用宿主内存。

- 插过 USB-C 显示器后，再用 USB 线连接电脑使用开发者连接，需要重启平板才能识别。
- 拔下 OTG 设备后，直接插入的 OTG 转接头可能不被识别；插拔一次充电器或电脑线，或改用扩展坞即可。
- 蓝牙偶发需要重新连接才能恢复数据或音频。
- 后摄为固定焦距，近景可能偏虚，颜色调校仍有限。

手写笔、指纹和休眠到磁盘仍不支持。保留默认扬声器音量上限。

---

Optional virtual machine tools run independent Ubuntu and Windows ARM guests on the tablet.

## Changes

- **Windows 11 ARM**: create a VM from your official ISO and use local VNC for OOBE and daily operation. Firmware, signed drivers and media tools are a separate optional installation.
- **Ubuntu virtual machines**: ordinary users can create and manage ARM64 guests with SSH, NAT networking, persistent disks, clean shutdown and restart.
- **Enable on demand**: install the VM tools separately and load virtualization modules when needed; device and network operations request administrator authentication separately.
- **User guide**: [Command-line virtual machines](https://github.com/yzddmr6/xiaomipad-8pro-mainline/blob/main/docs/VIRTUALIZATION.md) covers installation, creation, use and removal.

## Known limitations

- Ubuntu guests provide a command line. Windows uses software display without GPU acceleration, vTPM or Secure Boot; TPM and other installation checks are bypassed and a Test Mode watermark is shown. Each user can run one VM at a time; its memory is unavailable to the host while running.

- After a USB-C display has been connected, the tablet must be restarted before a USB cable to a computer is recognized for the developer connection.
- After an OTG device is unplugged, a plain OTG adapter plugged in next may not be detected; plug a charger or computer cable in and out once, or use a dock.
- Bluetooth occasionally needs reconnecting before data or audio works again.
- The rear camera has fixed focus; nearby subjects may be blurry and color tuning is limited.

Stylus, fingerprint and disk hibernation remain unsupported. Keep the default speaker volume cap.
