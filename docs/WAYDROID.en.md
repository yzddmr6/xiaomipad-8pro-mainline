# Waydroid: Android apps on Ubuntu

[中文](WAYDROID.md) ｜ [Project home](../README.en.md)

Since 1.1.0 the kernel provides the Binder, policy routing and legacy iptables support that Waydroid needs. Waydroid is not preinstalled; install it when you need it.

**Limitations:** software rendering only. The Mesa build in the official Waydroid images does not recognize this tablet's GPU yet, and the default hardware rendering makes the Android UI crash repeatedly, so switch to software rendering after installing. Android's Data Saver is unavailable.

## Install

Ubuntu 26.04 does not ship Waydroid; use the official Waydroid repository:

```bash
sudo apt update
sudo apt install curl
curl -fsSL https://repo.waydro.id | sudo bash
sudo apt install waydroid
sudo waydroid init
```

`waydroid init` downloads the `arm64_only` images that match the processor. This tablet's CPU cannot run 32-bit ARM code; if you download images manually, also pick `arm64_only`, because images with 32-bit components power off during boot.

## Switch to software rendering

Run `sudoedit /var/lib/waydroid/waydroid.cfg` and set these in the `[properties]` section (add the section at the end of the file if it is missing):

```ini
[properties]
ro.hardware.gralloc = default
ro.hardware.egl = swiftshader
```

Save, then run:

```bash
sudo waydroid upgrade --offline
sudo systemctl restart waydroid-container
```

## Use

Open “Waydroid” from the app grid, or run `waydroid show-full-ui` in a terminal. The first start takes about half a minute, during which the Android system UI restarts a few times before it settles. Android uses the tablet's current network connection.

## Black browser pages and embedded sign-in pages

Some ARM64_ONLY images declare an OMX media service that they do not provide. This blocks media services, shared storage and WebView together: Android Settings can work while Jelly pages or WebView-based sign-in pages stay black or load incompletely, or shared-storage features fail. See the [upstream Waydroid issue](https://github.com/waydroid/waydroid/issues/2453).

If the log repeatedly waits for `android.hardware.media.omx@1.0::IOmxStore/default`, apply the following correction while Waydroid is running. The tool checks that no OMX service is supplied and removes only the invalid declaration. It does not change the original images, graphics backend or WebView. Merge any existing custom VINTF overlay instead of overwriting it.

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

The tool refuses this correction when a newer image supplies OMX or no longer declares it. After an image update, check whether upstream has fixed the issue and remove `overlay/vendor/etc/vintf/manifest.xml` when it is no longer needed.

This does not install missing Android apps or proprietary media features. Redirects to a private URL scheme for an uninstalled app can still produce `ERR_UNKNOWN_URL_SCHEME` in Jelly; normal web rendering and app links are separate features.

## Remove

```bash
waydroid session stop
sudo apt purge waydroid
sudo rm -rf /var/lib/waydroid ~/.local/share/waydroid
```
