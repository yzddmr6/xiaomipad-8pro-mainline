# Upgrading an Existing Installation

[中文](UPGRADE.zh-CN.md) ｜ [Project home](../README.md) ｜ [Fresh installation](INSTALL.md) ｜ [Switching and recovery](FLASHING.md)

This page upgrades an installed Ubuntu to a new release's kernel and device packages without
reinstalling, repartitioning or erasing data. Prerequisites: the final step of
[INSTALL.md](INSTALL.md) is done (`/etc/piano` holds `boot-profile.json`, `boot-signing.pem` and
`boot-partitions.json`), developer mode can be enabled, and the battery is at 40 % or more.

The release asset `piano-ubuntu-<version>-packages.zip` is the upgrade package: its `debs/`
directory holds every project package installed in that release's image. Always install the
whole set together, never a single package.

## Upgrading from v0.8.0 to v0.9.0

Use `piano-ubuntu-v0.9.0-packages.zip` and install the complete package set with the same steps below. It includes the new kernel (Linux 7.2.14) and its matching camera loopback package; the other packages are the same as in v0.8.0. Reboot to use the new kernel. The previous kernel and rollback boot set are retained; no repartitioning or root image rewrite is needed.

## Upgrading from v0.7.1 to v0.8.0

Use `piano-ubuntu-v0.8.0-packages.zip` with the same steps below and install the complete package set, including the new kernel (Linux 7.2.10) with its matching camera loopback package, the updated desktop Mesa and gpu-2404 snap provider, and `piano-ubuntu-desktop` 0.8.0. Reboot to use the new kernel. Keep the previous kernel and rollback boot set. No repartitioning or root image reflash is needed.

## Upgrading from v0.7.0 to v0.7.1

Use `piano-ubuntu-v0.7.1-packages.zip` with the same steps below and install the complete package set. This updates Mesa for the desktop and the gpu-2404 snap provider. The kernel remains Linux 7.2.10-piano40, and `piano-ubuntu-desktop` remains 0.7.0. Reboot so the snap provider is refreshed and applications load the new driver. Keep the previous kernel and rollback boot set. No repartitioning or root image reflash is needed.

## Upgrading from v0.6.2 to v0.7.0

Use `piano-ubuntu-v0.7.0-packages.zip` with the same steps below and install the complete package set, including Linux 7.2.10-piano40, its matching camera loopback package, the updated `resources` package and `piano-ubuntu-desktop` 0.7.0. Reboot to use the new kernel; the task manager then shows the Temperature page. Keep the installed previous kernel and its rollback boot set. No repartitioning or root image reflash is needed.

## Upgrading from v0.6.1 to v0.6.2

Use `piano-ubuntu-v0.6.2-packages.zip` with the same steps below and install the complete package set, including Linux 7.2.10-piano38, its matching camera loopback package, the new `resources` package and `piano-ubuntu-desktop` 0.6.2. Reboot to use the new kernel; reopen the task manager to see the new data. Keep the installed previous kernel and its rollback boot set. No repartitioning or root image reflash is needed.

## Upgrading from v0.6.0 to v0.6.1

Use `piano-ubuntu-v0.6.1-packages.zip` with the same steps below and install the complete package set, including Linux 7.2.10-piano35 and its matching camera loopback package. Reboot to use the new kernel, touch firmware and sensor service. Keep the installed previous kernel and its rollback boot set. No repartitioning or root image reflash is needed.

## Upgrading from v0.5.1 to v0.6.0

Use `piano-ubuntu-v0.6.0-packages.zip` with the same steps below and install the complete package set, including Linux 7.2.10-piano32 and its matching camera loopback package. Reboot to use the new kernel and UTF-8 filename defaults. Keep the installed previous kernel and its rollback boot set. No repartitioning or root image reflash is needed.

## Upgrading from v0.5.0 to v0.5.1

Follow the same steps below using `piano-ubuntu-v0.5.1-packages.zip`. Install the entire package set together, including Linux 7.2.10-piano28 and its matching camera loopback package. Reboot to activate the touch recovery and audio session-switch fixes. Keep the previous kernel installed and retain its rollback boot set. No repartitioning or root-image flash is needed.

## Upgrading from v0.4.0 to v0.5.0

Follow the same steps below using `piano-ubuntu-v0.5.0-packages.zip`. Install the entire package set together, including Linux 7.2.10-piano27, its matching camera loopback package and all four Ubuntu power-button packages (GSD/common and control-center/data). Keep the previous kernel installed and retain its rollback boot set. No repartitioning or root-image flash is needed. The desktop package depends on `tuned-ppd`; APT replaces `power-profiles-daemon` with this provider.

If these four packages were manually held, clear the old holds before installing the matched release set:

```sh
sudo apt-mark unhold gnome-settings-daemon gnome-settings-daemon-common gnome-control-center gnome-control-center-data
```

The new desktop package supplies an APT pin and matched version dependencies to retain lock support. This also blocks direct Ubuntu security updates to these four packages: install the project's matched rebuilds promptly; see the [maintenance procedure](../ubuntu/README.md). Other Ubuntu packages update normally.

Reboot, then select Lock Screen and a power mode in Settings → Power. Existing personal power-button choices are preserved. Use `tuned-adm` on the command line; if you previously copied `/usr/local/bin/powerprofilesctl` manually, remove that known manual copy during migration. See [power configuration](../device/power/tuned/README.md) for configuration ownership and commands.

If you manually tested Piano TuneD configuration, back up those test files first. After installation, remove the test overrides under `/etc/tuned/profiles/piano-*` so they no longer mask the packaged `/usr/lib/tuned/profiles/piano-*`; retain other intentional administrator configuration. If testing changed `/etc/tuned/ppd.conf`, restore the original file extracted from a `tuned-ppd` deb matching the installed version and inspect `dpkg --verify tuned-ppd`. The project configuration is `/etc/tuned/piano-ppd.conf`; after reboot, the service reads it through `BindReadOnlyPaths`.

## Upgrading from v0.3.2 to v0.4.0

Follow the same steps below using `piano-ubuntu-v0.4.0-packages.zip`. Install the entire package set together, including Linux 7.2.10-piano26 and its matching camera loopback package. Reboot to activate the touch fixes. Keep the previous kernel installed and retain its rollback boot set. No repartitioning or root-image flash is needed.

## Upgrading from v0.3.1 to v0.3.2

Follow the same steps below using `piano-ubuntu-v0.3.2-packages.zip` and install the entire package set together. The kernel remains Linux 7.2.10-piano25. Reboot so the snap graphics provider is refreshed and applications pick up the new driver. No repartitioning or root-image flash is needed.

## Upgrading from v0.3.0 to v0.3.1

Follow the same steps below using `piano-ubuntu-v0.3.1-packages.zip`. Install the entire package set together, including Linux 7.2.10-piano25 and its matching camera loopback package; retain the previous kernel and rollback boot set. No repartitioning or root-image flash is needed.

After reboot, the power-button action appears in Settings → Power. The default is suspend; a previously chosen personal power-button setting is kept. Camera apps now use the automatically started camera service.

## Upgrading from v0.2.0 to v0.3.0

Install the complete v0.3.0 `packages.zip` together: kernel, device support and desktop, Mesa and snap graphics, `piano-camerad`, and `piano-camera-loopback-*` matching the new kernel, plus the included firmware, sensor and authentication packages. No root-image flash, repartitioning or new per-unit extraction is required.

System suspend and GNOME's default idle policy are now enabled; disk hibernation remains disabled. Existing personal power settings are preserved and can be changed in Settings → Power. Reboot after installation to activate the new kernel, WirePlumber camera isolation and session color-profile unit. Rear focus is fixed. Keep the previous kernel and matching modules for rollback.

## 1. Download and verify

On the host, download `piano-ubuntu-<version>-packages.zip` and `SHA256SUMS.assets` from the
release page into an empty directory:

```bash
sha256sum -c --ignore-missing SHA256SUMS.assets    # expect one "OK" line for packages.zip
unzip piano-ubuntu-<version>-packages.zip            # yields debs/
```

## 2. Keep a rollback

The upgrade replaces the current boot set with one for the new kernel; the old set is kept
automatically as `ubuntu.prev`. Save an independent copy as well, so that a later upgrade cannot
rotate it away. On the tablet:

```bash
sudo cp -a /var/lib/piano/switch/ubuntu /var/lib/piano/rollback-$(uname -r)
```

Also keep copies of the current Ubuntu set and the Android set on the host as described in
[FLASHING.md "Image sets"](FLASHING.md#image-sets); they are the basis for recovery from Fastboot.

## 3. Copy to the tablet

Enable developer mode in a terminal on the tablet, then copy from the host:

```bash
# tablet
sudo piano-devmode on
# host
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check     # expect "... ready"
scp -r debs <USER>@192.168.88.2:
```

## 4. Install

Run the installation in a transient unit so that a dropped SSH session cannot abort it; the
kernel package rebuilds the initrd, which takes about 10 minutes.

```bash
sudo apt update
sudo systemd-run --unit=piano-upgrade --setenv=DEBIAN_FRONTEND=noninteractive \
  apt-get install -y /home/<USER>/debs/*.deb
journalctl -f -u piano-upgrade
# expect "Piano boot image set prepared for <new kernel>" and "Slot A now holds store/ubuntu",
# finally "piano-upgrade.service: Deactivated successfully."
sudo dpkg --audit                          # expect no output
sudo piano-boot-switch verify              # expect exit code 0
```

## 5. Reboot and check

```bash
sudo systemctl reboot
```

After logging in again:

```bash
uname -r                                   # expect the new kernel
sudo piano-boot-switch status              # expect "occupant": "ubuntu"
sudo piano-slot-success --check            # expect "state": "already-successful"
sudo piano-devmode off                     # once the host connection is no longer needed
```

The previous kernel package stays installed; the rollback set needs it. Once the new release has
proven itself, remove it with `sudo apt purge linux-image-<old version>`.

## Rollback

- **Ubuntu still boots:**
  ```bash
  sudo piano-boot-switch import-ubuntu --from /var/lib/piano/rollback-<old version>   # expect "result": "imported"
  sudo piano-boot-switch to-ubuntu --reboot                                           # writes the old set back and reboots
  ```
- **Ubuntu does not boot:** hold Power + Volume Down for Fastboot and write the old Ubuntu set
  saved on the host:
  ```bash
  python3 "$REPO/tools/piano-switch.py" --serial <SERIAL> --images <old ubuntu set> --rollback <android-set>
  fastboot -s <SERIAL> reboot
  ```
  Or switch to Android first (`--images <android-set> --rollback <old ubuntu set>`) and sort it
  out from there.
