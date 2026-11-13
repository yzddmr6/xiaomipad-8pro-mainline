# Waydroid：在 Ubuntu 中运行 Android 应用

[English](WAYDROID.en.md) ｜ [项目首页](../README.md)

从 1.1.0 起，内核带有 Waydroid 需要的 Binder、策略路由和 legacy iptables。Waydroid 不随系统预装，需要时按下面步骤安装。

安装 SM8750 图形包后，Android 应用可用 Adreno 渲染，并在 GNOME 中独立开窗。Android 的“省流量”不可用。

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

## 启用 Adreno 硬件渲染

`waydroid init` 完成后，把配套的 `waydroid-sm8750-arm64-only.tar.zst` 图形包解压到空目录，在该目录运行：

```bash
sudo python3 enable-gpu.py
```

安装器会核对 Android 镜像，启用多窗口并重启容器。适用镜像为 20260927 VANILLA arm64_only system 与 20261022 MAINLINE vendor。包内同时修正了首次启动时的 SystemUI 异常，以及下文的 OMX 声明。

## 使用

在 GNOME 应用列表中打开 Waydroid 启动 Android，再从列表打开浏览器、计算器或已安装的 Android 应用。每个应用都有自己的桌面窗口。Android 使用平板当前的网络连接。

## 浏览器和应用登录页黑屏

部分 ARM64_ONLY 镜像声明了不存在的 OMX 媒体服务，导致媒体服务、共享存储和 WebView 一起阻塞。表现为 Android 设置正常，但 Jelly 网页或使用 WebView 的登录页黑屏、内容加载不完整，或共享存储相关功能无法使用；这不等于整个 Android 图形栈不可用。参见 [Waydroid 上游问题](https://github.com/waydroid/waydroid/issues/2453)。

如果日志反复等待 `android.hardware.media.omx@1.0::IOmxStore/default`，可在 Waydroid 正在运行时使用下面的修正。工具先检查镜像确实没有 OMX 服务，只移除无效声明；不改原始镜像、图形后端或 WebView。已有自定义 VINTF 覆盖文件时，应先合并其中的改动，不要直接覆盖。

```bash
(
set -e
curl -fL -o /tmp/waydroid-omx-overlay.py https://raw.githubusercontent.com/yzddmr6/xiaomipad-8pro-mainline/v1.2.0/tools/waydroid-omx-overlay.py
sudo test ! -e /var/lib/waydroid/overlay/vendor/etc/vintf/manifest.xml
sudo python3 /tmp/waydroid-omx-overlay.py --rootfs /var/lib/waydroid/rootfs --output /var/tmp/waydroid-omx-manifest.xml
waydroid session stop
sudo systemctl stop waydroid-container
sudo install -D -m 644 /var/tmp/waydroid-omx-manifest.xml /var/lib/waydroid/overlay/vendor/etc/vintf/manifest.xml
sudo systemctl start waydroid-container
waydroid show-full-ui
)
```

新的镜像若已经提供 OMX 服务或删掉无效声明，工具会拒绝应用这项修正。镜像更新后应检查上游是否已修复，并移除不再需要的 `overlay/vendor/etc/vintf/manifest.xml`。

该修正不添加缺失的 Android App 或专有媒体能力。网页跳转到未安装 App 的私有链接时，Jelly 仍可能显示 `ERR_UNKNOWN_URL_SCHEME`；普通网页显示和这类 App 链接是不同功能。

## 卸载

```bash
waydroid session stop
sudo apt purge waydroid
sudo rm -rf /var/lib/waydroid ~/.local/share/waydroid
```
