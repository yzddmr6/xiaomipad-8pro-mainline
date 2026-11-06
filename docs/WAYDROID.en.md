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

Open “Waydroid” from the app grid, or run `waydroid show-full-ui` in a terminal; the first start takes about half a minute. Android uses the tablet's current network connection.

## Remove

```bash
waydroid session stop
sudo apt purge waydroid
sudo rm -rf /var/lib/waydroid ~/.local/share/waydroid
```
