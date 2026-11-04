# Fastboot, Switching and Recovery

[中文](FLASHING.zh-CN.md) | [Project overview](../README.en.md) | [Installation](INSTALL.en.md)

Android and Ubuntu both boot from slot A. Switching writes the other system's image set into
`init_boot_a`, `vendor_boot_a` and `boot_a`; the active slot never changes. This page covers the
switching tools, the slot attributes, Fastboot recovery and what must never be written.

## Never write

- `vbmeta` and `dtbo`;
- any `_b` partition, and never `fastboot --set-active=b`: slot B on the test unit holds an older
  stock chain that does not boot Android;
- `persist` or other calibration, modem or firmware partitions;
- another tablet's images, calibration or addresses.

Bind every command to your tablet (`adb -s <SERIAL>`, `fastboot -s <SERIAL>`,
`piano-switch.py --serial <SERIAL>`) and run one tool against it at a time. Leave Android with at
least 40 % battery before partition work. Current charging support and limitations are in
[STATUS.md](STATUS.md); the early diagnostic kernel's lack of charging is historical.

Before writing, [back up](INSTALL.en.md#2-back-up) and keep both boot sets on the host.
Attend installation; release images have no debug console. Do not overlap boots, writes or
captures with another session. On failure, stop at the earliest failing step: do not repeat a
write after one failed status query, re-flash a completed filesystem write or interrupt a write
still in progress. Check a new image with the [debug RAM procedure](AGENT-GUIDE.en.md#72-debug-ram-boot)
before writing slot A; debug sets are not for normal persistent use.

For host commands, set REPO, RELEASE and WORK as in [INSTALL.en.md](INSTALL.en.md#what-you-need):
REPO is the absolute path to the checkout at the release's recorded integration commit, RELEASE is
the extracted release directory, and WORK is your private installation directory outside the checkout.
Run host commands from WORK; source tools use `"$REPO/tools/NAME"` and release files use `"$RELEASE/PATH"`.

## Fastboot

Hold **Power + Volume Down** until Fastboot appears; this works from any state, including a hang.
From a running system: `adb -s <SERIAL> reboot bootloader` in Android,
`sudo piano-reboot-bootloader` in Ubuntu.

```bash
fastboot devices                                  # expect your <SERIAL>
fastboot -s <SERIAL> getvar current-slot          # expect "current-slot: a"
fastboot -s <SERIAL> getvar battery-soc-ok        # expect "battery-soc-ok: yes"
```

## Maintenance RAM image

`boot/boot-ram-maintenance.img` of the release is a RAM-only Linux for work on the UFS disks that
must not run from a mounted system, such as the partition table write of
[INSTALL.en.md step 6](INSTALL.en.md#6-write-the-partition-table-on-the-tablet). It mounts no UFS partition and writes nothing automatically; slot A is unchanged.
[INSTALL.en.md step 8a](INSTALL.en.md#8a-recommended-install-your-units-files-before-the-first-boot)
explicitly mounts the new root to install your unit’s files.

```bash
fastboot -s <SERIAL> boot "$RELEASE/boot/boot-ram-maintenance.img"    # expect "Booting OKAY"
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check         # expect "... 192.168.88.10 -> 192.168.88.2 ready"
telnet 192.168.88.2 2323                                              # root shell, no password
```

| In the session | Effect |
|---|---|
| (login) | prints the kernel, the LUN table, `Mounted from the UFS disks: none` and `sgdisk -p` of LUN 0 |
| `piano-maint map` | the same table again |
| `piano-maint status` | idle count and limit, open sessions, whether the Fastboot reboot is ready |
| `piano-maint keepalive`, `piano-maint idle SECONDS` | restart the idle count; set the limit (30–3600 s) |
| `piano-maint fastboot` | reboot into Fastboot now |
| `wget -O /run/FILE http://192.168.88.10:8000/FILE` | copy a file from `python3 -m http.server --bind 192.168.88.10 8000` on the host |

With no open session for 10 minutes the tablet reboots into Fastboot by itself; a session counts
only while the cable is connected. `fastboot -s <SERIAL> reboot` then starts slot A. Tools:
`sgdisk`, `sfdisk`, `lsblk`, `partx`, `blockdev` (Ubuntu 26.04 binaries), BusyBox `dd`,
`sha256sum` and `wget`. Every command runs as root: `dd`, `sgdisk` and `sfdisk` write the disk
when you tell them to.

## Image sets

An image set is a directory with `boot.img`, `init_boot.img`, `vendor_boot.img` (each the full
partition size) and a `SHA256SUMS` file listing the three.

| Set | Where |
|---|---|
| Android | your backup of slot A ([INSTALL.en.md](INSTALL.en.md#2-back-up)); `init_boot` is the root-patched image in use |
| Ubuntu | the release's `boot/`; after `piano-boot-update`, the store's `ubuntu/` |

On the tablet the store `/var/lib/piano/switch/` holds `android/`, `ubuntu/` and `ubuntu.prev/`
(the Ubuntu set before the current one). Keep host copies of the Android set and of the current
Ubuntu set. After each update, enable developer mode and choose a new SET_NAME, using the same name on the tablet and host. Do not reuse an existing destination.

```bash
# Tablet: choose a new name for each update.
SET_NAME=ubuntu-set-update-001
sudo piano-devmode on
test ! -e "$HOME/$SET_NAME" &&
  sudo cp -a /var/lib/piano/switch/ubuntu "$HOME/$SET_NAME" &&
  sudo chown -R "$USER:" "$HOME/$SET_NAME"

# Host: use the same name.
SET_NAME=ubuntu-set-update-001
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check
test ! -e "$SET_NAME" &&
  scp -r <USER>@192.168.88.2:"$SET_NAME" . &&
  (cd "$SET_NAME" && sha256sum -c SHA256SUMS)

# Tablet, after successful transfer and verification:
sudo piano-devmode off
```

When the Android root patch in slot A changes (for example Magisk to KernelSU), the stored
`android/` no longer matches and `to-android` would write the old `init_boot` back. Make a new
Android set from the current slot A images and store it with
`sudo piano-boot-switch import-android --from <dir>`.

## From Ubuntu: `piano-boot-switch`

| Command | Writes | Result |
|---|---|---|
| `sudo piano-boot-switch status` | nothing | slot A occupant (`android`, `ubuntu`, `ubuntu.prev`, `unknown`), slot flags, backup GPT state |
| `sudo piano-boot-switch verify` | nothing | exit status 0 when every set is intact |
| `sudo piano-boot-switch to-android --dry-run` | nothing | `"result": "would-write"` |
| `sudo piano-boot-switch to-android --reboot [--force]` | slot A | `"result": "written"`, then requests a reboot into Android |
| `sudo piano-boot-switch to-ubuntu [--reboot]` | slot A | `written`, or `unchanged` when slot A already holds it |
| `sudo piano-boot-switch import-android --from DIR` | store | `"result": "imported"`; replaces `android/` |
| `sudo piano-boot-switch import-ubuntu [--from DIR]` | store | `imported`; the old `ubuntu/` becomes `ubuntu.prev/` |

- Complete [INSTALL.en.md step 10](INSTALL.en.md#10-install-your-units-files) first to install your unit's files.
- It needs your `boot-profile.json` and `boot-partitions.json` in `/etc/piano/`, and writes only
  when slot A holds a known set, which serves as the rollback. Each partition is written and read
  back in full; on a failure the previous set is written back.
- `"result": "refused"` means nothing was written; `code` and `message` say why.
  `rollback-failed` or `final-mismatch`: do not reboot; recover from Fastboot (below).
- Before rebooting it lists the processes that block shutdown (inhibitors); if the reboot is denied it exits 1 with slot A already holding Android, so the next boot is Android. Save your work and reboot normally, or use `--reboot --force` to ignore inhibitors. The app-grid entry **Switch to Android** runs the same tool.
- Verified on the test unit: `status`, `verify`, both imports, `to-ubuntu`, `to-android --reboot`.

## From Android: KernelSU module

Switching from Android is verified on the test unit. The module
`tools/piano-switch-ksu-<version>.zip` of the release needs KernelSU-Next with shell root, a stored
`android/` set equal to slot A and a clean Ubuntu shutdown.

```bash
KSU_VERSION=$(sed -n 's/^version=//p' "$REPO/device/android/piano-switch-ksu/module/module.prop")
KSU_ZIP="piano-switch-ksu-$KSU_VERSION.zip"
adb -s <SERIAL> push "$RELEASE/tools/$KSU_ZIP" /data/local/tmp/
adb -s <SERIAL> shell su -c "/data/adb/ksud module install /data/local/tmp/$KSU_ZIP"
adb -s <SERIAL> reboot
adb -s <SERIAL> shell su -c 'sh /data/adb/modules/piano-switch/piano-switch.sh --check'
# expect "CHECK OK: slot A holds the android set and the ubuntu set is ready; nothing written"
adb -s <SERIAL> shell su -c 'ksud module action piano-switch'   # writes slot A and reboots
```

Exit status 3 means the write and the restore both failed: do not reboot; restore the Android set
from Fastboot. Details: [device/android/piano-switch-ksu](../device/android/piano-switch-ksu/README.md).

## From the host: `piano-switch.py`

Verified on the test unit in both directions. In Fastboot:

```bash
# to Android
python3 "$REPO/tools/piano-switch.py" --serial <SERIAL> --images <android-set> --rollback <ubuntu-set>
fastboot -s <SERIAL> reboot
# to Ubuntu
python3 "$REPO/tools/piano-switch.py" --serial <SERIAL> --images <ubuntu-set> --rollback <android-set>
fastboot -s <SERIAL> reboot
# expect "Active A boot triplet written; remain in Fastboot for a RAM readback before reboot."
```

- Before the first write it checks `product: piano`, `unlocked: yes`, `current-slot: a`, the three
  partition sizes and both `SHA256SUMS`. If a write fails it writes the rollback set and reports the
  error.
- The three writes are not atomic: after a power loss during a switch, run it again from Fastboot.
- Power off Ubuntu before entering Fastboot; do not interrupt a running write.

## Slot attributes

```bash
fastboot -s <SERIAL> getvar slot-successful:a     # "yes" once marked
fastboot -s <SERIAL> getvar slot-unbootable:a     # expect "no"
fastboot -s <SERIAL> getvar slot-retry-count:a    # 7 after a host write, one less after each unmarked boot
```

| Event | Effect on slot A |
|---|---|
| Host write of `boot_a` (also through `piano-switch.py`) | successful cleared, retry count set to 7 |
| Boot of a slot not yet marked successful | ABL lowers the retry count; at 0 the slot becomes unbootable |
| Ubuntu boot | `piano-slot-success.service` marks the slot successful after the boot completes |
| Android boot | Android marks the slot |
| Switching inside either system | unchanged |

Prefer switching inside a system: it keeps the attributes. `sudo piano-slot-success --check` shows
the state in Ubuntu without writing.

If Fastboot shows `slot-unbootable:a: yes`, run `fastboot -s <SERIAL> --set-active=a` to reset
slot A's counter, then switch or `fastboot -s <SERIAL> reboot`. On the test unit this also raised
slot B's priority; slot B stays unused.

## When the tablet does not respond

1. **Developer mode on** (`sudo piano-devmode on` in Ubuntu): about 22 s after power-on,
   `python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check` should report the link as `ready`. Then
   `python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> -t <USER> sudo piano-reboot-bootloader`, or switch to Android
   with `piano-boot-switch to-android --reboot`.
2. **Otherwise** hold Power + Volume Down until Fastboot appears, then write the Android set with
   `piano-switch.py`. A boot that hangs before the system starts has no timer and stays hung until
   you press the keys.

Do not reset the USB device from the host and do not repeat commands against a tablet that does
not answer.

## Restoring the original layout

Removing Ubuntu and restoring the original partition table has no tool and no verified procedure yet; the [maintenance RAM image](#maintenance-ram-image) is the environment such a write would
need. Keep the partition table backups of [INSTALL.en.md](INSTALL.en.md#2-back-up) and the full
partition backup.
