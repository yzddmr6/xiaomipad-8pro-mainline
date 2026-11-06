# Waydroid：在 Ubuntu 中运行 Android 应用

[English](WAYDROID.en.md) ｜ [项目首页](../README.md)

从 1.1.0 起，内核带有 Waydroid 需要的 Binder、策略路由和 legacy iptables。Waydroid 不随系统预装，需要时按下面步骤安装。

**限制：** 只能软件渲染。Waydroid 官方镜像里的 Mesa 还不认识这台平板的 GPU，默认的硬件渲染会让 Android 界面反复崩溃，安装后要先改成软件渲染。Android 的“省流量”不可用。

## 安装

Ubuntu 26.04 的软件源没有 Waydroid，使用 Waydroid 官方源：

```bash
sudo apt update
sudo apt install curl
curl -fsSL https://repo.waydro.id | sudo bash
sudo apt install waydroid
sudo waydroid init
```

`waydroid init` 会按处理器自动下载 `arm64_only` 镜像。这台平板的 CPU 不支持 32 位 ARM，手动下载镜像时也要选 `arm64_only`，带 32 位组件的镜像开机后会自动关机。

## 改用软件渲染

运行 `sudoedit /var/lib/waydroid/waydroid.cfg`，在 `[properties]` 一节里（没有就在文件末尾新建这一节）设置：

```ini
[properties]
ro.hardware.gralloc = default
ro.hardware.egl = swiftshader
```

保存后执行：

```bash
sudo waydroid upgrade --offline
sudo systemctl restart waydroid-container
```

## 使用

从应用列表打开“Waydroid”，或在终端运行 `waydroid show-full-ui`。首次启动约需半分钟，期间 Android 系统界面会反复重启几次，之后恢复正常。Android 通过平板当前的网络上网。

## 卸载

```bash
waydroid session stop
sudo apt purge waydroid
sudo rm -rf /var/lib/waydroid ~/.local/share/waydroid
```
