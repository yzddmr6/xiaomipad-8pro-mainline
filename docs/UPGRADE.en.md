# Upgrading an Existing Installation

[中文](UPGRADE.md) ｜ [Project home](../README.en.md) ｜ [Fresh installation](INSTALL.en.md) ｜ [Switching and recovery](FLASHING.en.md)

This page upgrades an installed Ubuntu to a new release's kernel and device packages without
reinstalling, repartitioning or erasing data. Prerequisites: the final step of
[INSTALL.en.md](INSTALL.en.md) is done (`/etc/piano` holds `boot-profile.json`, `boot-signing.pem` and
`boot-partitions.json`), developer mode can be enabled, and the battery is at 40 % or more.

The release asset `piano-ubuntu-<version>-packages.zip` is the upgrade package: its `debs/`
directory holds every project package installed in that release's image. Always install the
whole set together, never a single package.


## Upgrading from v1.1.1 to v1.2.0

If an existing Waydroid installation shows black or incomplete web pages, apply the OMX declaration overlay in the [Waydroid guide](WAYDROID.en.md#black-browser-pages-and-embedded-sign-in-pages). The system upgrade does not modify Android images.

Install the complete package set and reboot using the steps below. This updates the Linux 7.2.15 kernel and matching camera module, device support and Fill Light Camera application, plus the matched GSD/common packages. Installing the complete set also updates their exact APT version pins. Existing user settings, finger calibration and rollback boot sets are preserved. The upgrade also removes the old USB debug console. Use developer-mode SSH for normal remote maintenance; the separate RAM diagnostics package is excluded from the upgrade set.

Focus Pen Pro (P81C) input uses 120 Hz. To add pen calibration to an existing installation, run `tools/extract-thp-calibration.py` with the tablet’s matching original INI, `--merge-existing` pointing to its existing calibration and `--output` pointing to a new private file. The tool rejects a different panel and preserves finger geometry and orientation. Install the resulting file as `/etc/piano/thp-calibration.json` (root-owned, mode 0600) before rebooting. Keep the old file for rollback; never share calibration between tablets.

Open Fill Light Camera and select the rear camera. “Photo fill light” illuminates the scene while exposure settles and the photograph is captured. This is continuous fill during capture, not an exposure-triggered high-power strobe. Snapshot and other webcam applications keep using the standard camera outputs.

The complete package set includes GunyahVM 1.0.1 and all its companion packages. The first login after upgrade appends GunyahVM to Dock favorites, preserving the existing entries and order; removing it later is respected. New accounts receive it in their default Dock, and it is also available in the application menu. GunyahVM’s own logind/udev and polkit rules grant access to the active local desktop session without manual group membership.

## Upgrading from v1.1.0 to v1.1.1

Use `piano-ubuntu-v1.1.1-packages.zip`, install the complete package set using this page’s instructions, and reboot; no repartitioning or root-image flash is needed. This release updates the matching kernel (Linux 7.2.15) and camera loopback module, the device-support package, the sensor proxy, the graphics driver (Mesa 26.2.6), the snap graphics package and `piano-ubuntu-desktop`; the other default packages are the same as in 1.1.0.

Charger authentication moves into the kernel, so APT removes the old `piano-mipps-auth` package during the upgrade; this replacement is expected, and the old package should not be reinstalled. `piano-charging.service` continues to provide charging policy. Existing battery-protection settings and per-unit calibration are retained, and no pen-calibration extraction is required.

Keep the previous kernel, rollback boot set and complete v1.1.0 `packages.zip` so that matching software can be restored if needed. Reboot normally after installing the new version before using charging and sensor features.

## Upgrading from v1.0.0 to v1.1.0

Use `piano-ubuntu-v1.1.0-packages.zip` and install the whole set with the steps below, then reboot. This release brings a new kernel (Linux 7.2.15) with its matching camera loopback package, an updated device-support package and `piano-ubuntu-desktop` 1.1.0; the other default packages are the same as in 1.0.0. The optional VM packages are unchanged and need no reinstall. After the reboot, “Preserve Battery Health” is available in Settings → Power. The previous kernel and rollback boot set are kept; no repartitioning or root image reflash is needed.

## Upgrading from v0.10.0 to v1.0.0

Windows users also install the matching optional `piano-vm-windows` package. Device support raises the finite gunyah-group memlock limit to 6 GiB; log in again after upgrading, rebooting if needed before creating Windows VMs. Linux defaults remain unchanged.

Upgrade the complete kernel and device-support set below, reboot, and retain the previous kernel and rollback boot set. VM packages are optional. To enable them, open `optional/virtualization/` in the upgrade archive, install the matching piano-vm and piano-crosvm packages, and follow the [VM guide](VIRTUALIZATION.md) to authorize a user and log in again. Shut down existing guests before upgrading; their disks remain in your home directory and do not need recreation.

## Upgrading from v0.9.2 to v0.10.0

Use `piano-ubuntu-v0.10.0-packages.zip` and install the complete package set with the same steps below. This release includes the new kernel (camera, video, external display and charging fixes) and its matching camera loopback package, the updated camera service, the rebuilt desktop Mesa and gpu-2404 snap provider, the updated device integration package, and `piano-ubuntu-desktop` 0.10.0. Reboot to apply the update. The previous kernel and rollback boot set are retained; no repartitioning or root image rewrite is needed.

## Upgrading from v0.9.1 to v0.9.2

Use `piano-ubuntu-v0.9.2-packages.zip` and install the complete package set with the same steps below. This release includes display initialization and resource-management fixes, the new kernel's matching camera loopback package, and System Settings with author information. Reboot to apply the update. The previous kernel and rollback boot set are retained; no repartitioning or root image rewrite is needed.

### Touch orientation

Set `invert_y` at the top level of your tablet's `/etc/piano/thp-calibration.json`, then run `sudo systemctl restart piano-thp`. `true` reverses the Y coordinate produced by touch processing; `false` uses it directly. Omitting the field keeps the panel default (`false` for BOE, `true` for CSOT). For example, a BOE tablet with mirrored input under the default setting can use `"invert_y": true`; a CSOT tablet can use `false` to cancel its default reversal. No kernel, device-tree or boot image change is needed.

An existing libinput calibration that already works can be retained. When switching to this option, remove the Y reversal from that rule to avoid applying both corrections. Tablets with the correct orientation need no changes.

## Upgrading from v0.9.0 to v0.9.1

Use `piano-ubuntu-v0.9.1-packages.zip` and install the complete package set with the same steps below. It includes the new kernel (Linux 7.2.14 with the display link clock kept running) and its matching camera loopback package; the other packages are the same as in v0.9.0. Reboot to use the new kernel. The previous kernel and rollback boot set are retained; no repartitioning or root image rewrite is needed.

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
[FLASHING.en.md "Image sets"](FLASHING.en.md#image-sets); they are the basis for recovery from Fastboot.

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
