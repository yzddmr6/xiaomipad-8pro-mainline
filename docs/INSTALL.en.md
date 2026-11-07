# Fresh Installation

[中文](INSTALL.md) | [Project overview](../README.en.md) | [Fastboot, switching and recovery](FLASHING.en.md)

This guide installs the release next to Android on a Xiaomi Pad 8 Pro (`piano`). There is no graphical installer yet: follow this guide yourself, or hand it to an AI assistant as described on the [project page](../README.en.md#recommended-let-an-ai-assistant-install-it). The new partition table ([step 6](#6-write-the-partition-table-on-the-tablet)) is written from the maintenance RAM image of the release directory. Read the whole guide before starting. This procedure was carried out once in full on the test unit with v0.2.0.

Every release uses this installation procedure. Existing installations should follow [the upgrade guide](UPGRADE.en.md), without repartitioning or flashing the root image.

## Read first

- **All Android data is erased.** `userdata` is shrunk and re-created, `metadata` is cleared.
- **One tested unit.** Model 25091RP04C (China), 512 GB UFS, stock build `OS3.0.303.0.WPYCNXM` on
  slot A. Other models, capacities and stock builds are untested; the prebuilt boot set fits
  `OS3.0.303.0.WPYCNXM` only.
- **The bootloader must be unlocked** through Xiaomi's official process.
- **Back up first** ([step 2](#2-back-up)). Your stock slot A images are your way back to Android.
- Follow [device-operation boundaries](FLASHING.en.md#never-write) for battery checks, device
  selection, write restrictions and failure handling. Current charging support is in [STATUS.md](STATUS.md).

Steps that use standard commands because no project tool exists yet are marked *manual*.

## What you need

| Item | Notes |
|---|---|
| Xiaomi Pad 8 Pro on stock `OS3.0.303.0.WPYCNXM`, slot A active | `adb` reachable in Android |
| Root in Android | Magisk or KernelSU-Next in a patched `init_boot_a`, to read partitions (*manual*, follow that project's instructions) |
| Xiaomi magnetic keyboard | the only input until your touch calibration is installed ([step 10](#10-install-your-units-files)) |
| Linux x86_64 host | Android platform-tools (`adb`, `fastboot`), Python 3.11 or later, Git, `tar`, `zstd`, `openssl`, `uuidgen`, `sgdisk`, `sfdisk`, `losetup`, `mkfs.ext4`, `img2simg`, `sha256sum`, `ip` (iproute2), an OpenSSH client including `scp`, `telnet`, and `nc` (OpenBSD netcat) |
| AOSP `avbtool.py` | pinned by [tools/aosp-avb.json](../tools/aosp-avb.json), fetched and verified below |
| This repository | checked out at the integration commit in the release's `RELEASE-NOTES.md`; fetch AOSP mkbootimg after setting REPO below |
| The release directory | every file of one release, not mixed with other releases |
| Free host space | about 40 GB: the backup (16 GB on the test unit), the release (about 9 GB) and the data image |

Restore the release directory from the release assets as in the [README's manual installation](../README.en.md#manual-installation); use only the complete directory of one version and never mix in files of another.

REPO is the absolute path to the integration checkout at the release’s recorded commit. RELEASE is the extracted release directory. WORK is a private installation directory outside the checkout. Run subsequent host commands from WORK; use `"$REPO/tools/NAME"` for source tools and `"$RELEASE/PATH"` for release files.
Set these paths in each host terminal you use. The next check validates every file in RELEASE.

```bash
REPO=/absolute/path/to/xiaomipad-8pro-mainline
RELEASE=/absolute/path/to/extracted-release
WORK=/absolute/path/to/private-install-work
mkdir -p "$WORK"
cd "$WORK"
sh "$REPO/tools/fetch-aosp-mkbootimg.sh"
sh "$REPO/tools/fetch-aosp-avb.sh"
(cd "$RELEASE" && sha256sum -c SHA256SUMS)
```

The release contains `boot/` (the Ubuntu boot set for slot A, `boot-ram-check.img` and
`boot-ram-maintenance.img`),
`rootfs/root.sparse.img` (the 60 GiB root), `debs/`, `tools/piano-switch.py` and the KernelSU module.
Its contents are described in [RELEASE.md](RELEASE.md).

The image ships the kernel package and every project package (device support, firmware, Mesa, desktop defaults, snap graphics provider, MiPPS authentication, sensors); the versions are in the release notes. GStreamer good/bad plugins are installed. Sensors carry default calibration only, no unit's private calibration.

## 1. Unlock and check the stock build

Unlock the bootloader with Xiaomi's official tool (this erases the tablet), install the stock build
if needed, and root Android. Then:

```bash
adb -s <SERIAL> shell getprop ro.product.device              # expect "piano"
adb -s <SERIAL> shell getprop ro.build.version.incremental   # expect "OS3.0.303.0.WPYCNXM"
adb -s <SERIAL> shell getprop ro.boot.slot_suffix            # expect "_a"
adb -s <SERIAL> shell su -c id                               # expect "uid=0(root) ..."
adb -s <SERIAL> shell dumpsys battery | grep level           # expect 40 or higher
adb -s <SERIAL> reboot bootloader
fastboot -s <SERIAL> getvar unlocked                         # expect "unlocked: yes"
fastboot -s <SERIAL> getvar current-slot                     # expect "current-slot: a"
fastboot -s <SERIAL> reboot
```

Stop if any value differs.

## 2. Back up

*Manual.* Back up every partition except `userdata`, and the partition tables of every UFS LUN. Use
`adb exec-out` (binary-safe), not `adb shell`, for image data. Keep the backup outside the repository.

```bash
mkdir -p backup/gpt
adb -s <SERIAL> shell su -c 'for d in /sys/block/sd?; do echo ${d##*/} $(cat $d/size) $(cat $d/queue/logical_block_size); done'
# expect six LUNs with logical block size 4096; size is in 512-byte units
adb -s <SERIAL> shell su -c 'ls -l /dev/block/by-name/'     # label -> /dev/block/sdXN

# for each partition label except userdata
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/by-name/<label> bs=1048576 2>/dev/null' > backup/<label>.img
adb -s <SERIAL> shell su -c 'sha256sum /dev/block/by-name/<label>'    # must equal the next line
sha256sum backup/<label>.img

# for each LUN sdX: MBR and primary GPT, backup GPT; <blocks> = size / 8
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/sdX bs=4096 count=34 2>/dev/null' > backup/gpt/sdX-head.bin
adb -s <SERIAL> exec-out su -c 'dd if=/dev/block/sdX bs=4096 skip=<blocks-34> 2>/dev/null' > backup/gpt/sdX-tail.bin
```

- Write down a manifest: label, device node, LUN, size and SHA-256 of each file, and each LUN's disk
  GUID. `sdX` names change between boots; only this manifest maps files to partitions.
- `persist` and `metadata` are mounted read-write by Android and can change while you read them:
  hash twice and repeat the read if the hashes differ.

Make the **Android image set** from the backup. `init_boot` must be the root-patched image that is
in slot A now:

```bash
mkdir android-set
cp backup/boot_a.img android-set/boot.img
cp backup/init_boot_a.img android-set/init_boot.img
cp backup/vendor_boot_a.img android-set/vendor_boot.img
(cd android-set && sha256sum boot.img init_boot.img vendor_boot.img > SHA256SUMS)
```

Keep `android-set/` and a copy of the release's `boot/` on the host: they are the two sets used for
switching and recovery ([FLASHING.en.md](FLASHING.en.md)).

## 3. Collect your unit's files

These files belong to your tablet. Never share or commit them, and never use another tablet's.
They are installed in Ubuntu in [step 10](#10-install-your-units-files).

| File | Without it |
|---|---|
| `thp-calibration.json` | no touch input |
| `bluetooth-address` | the Bluetooth controller stays unconfigured |
| `wlan-address` | Wi-Fi uses a random MAC address on every boot |
| `boot-profile.json`, `boot-signing.pem` | the boot set cannot be rebuilt: the addresses are not applied and kernel updates leave slot A unchanged |
| `boot-partitions.json` (made in step 8a or 10) | `piano-boot-switch` refuses to switch, and kernel and initrd updates leave the boot set and slot A unchanged |

**Touch calibration.** Units have a CSOT or a BOE display panel. The release recognizes either
automatically and loads the matching touch firmware and orientation; only the calibration comes from
you. The stock ODM partition holds `piano_nova_csot_thp_config.ini` (CSOT panel, as on the test unit)
and `piano_nova_boe_thp_config.ini`. Use the one your stock driver loads: the stock kernel log names
it (if `dmesg` prints nothing, reboot Android and run it again right after boot).

```bash
adb -s <SERIAL> shell su -c 'dmesg | grep config_file_name'   # ...=piano_nova_csot_thp_config.ini or ..._boe_...
adb -s <SERIAL> shell su -c 'find /odm -name "piano_nova_*_thp_config.ini"'
adb -s <SERIAL> exec-out su -c 'cat <path to the ini>' > thp.ini
python3 "$REPO/tools/extract-thp-calibration.py" --input thp.ini --output thp-calibration.json
```

**Bluetooth address.**

```bash
adb -s <SERIAL> shell settings get secure bluetooth_address > bluetooth-address
cat bluetooth-address                  # expect one XX:XX:XX:XX:XX:XX line
```

**Wi-Fi address** (*manual*). The stock address is the line `wlan0=<12 hex digits>` in
`wlan/wlan_mac.bin` on `persist`; Android links it from the WLAN firmware directory. Convert it to
`XX:XX:XX:XX:XX:XX`:

```bash
adb -s <SERIAL> exec-out su -c 'cat /vendor/firmware/wlan/qca_cld/peach_v2/wlan_mac.bin' \
  | sed -n 's/^wlan0=\(..\)\(..\)\(..\)\(..\)\(..\)\(..\).*/\1:\2:\3:\4:\5:\6/p' > wlan-address
cat wlan-address                       # expect one XX:XX:XX:XX:XX:XX line
```

**Signing key.** Your own AVB key for the boot sets you build:

```bash
openssl genrsa -out boot-signing.pem 4096
```

**Boot profile** (*manual*, no generator yet). ABL marks the slot unbootable unless the boot images
carry the stock header fields, rollback indices and AVB properties. Write `boot-profile.json` with the
values of your stock images:

```
{
  "serial": "<SERIAL>",
  "boot":        {"header_version": 4, "capacity": 100663296, "rollback_index": <int>, "avb_properties": {<props>}},
  "init_boot":   {"header_version": 4, "capacity": 8388608,   "rollback_index": <int>, "avb_properties": {<props>}},
  "vendor_boot": {"header_version": 4, "capacity": 100663296, "rollback_index": <int>, "avb_properties": {<props>},
                  "page_size": <int>, "kernel_addr": <int>, "ramdisk_addr": <int>, "tags_addr": <int>, "dtb_addr": <int>}
}
```

```bash
python3 "$REPO/tools/local/aosp-mkbootimg/unpack_bootimg.py" --boot_img backup/vendor_boot_a.img --out /tmp/vb
# header version, page size, kernel, ramdisk and tags load addresses, dtb address
python3 "$REPO/tools/local/aosp-avb/avbtool.py" info_image --image backup/boot_a.img
# "Rollback Index:" and every "Prop: com.android.build.boot.*" line; repeat for init_boot_a and vendor_boot_a
```

`capacity` is the partition size in bytes. Copy every `Prop:` of each image into its
`avb_properties`; `boot` needs at least `com.android.build.boot.os_version` and
`com.android.build.boot.security_patch`. The release's `boot/boot-profile.json` shows the same
structure for the stock build, without a serial.

### Recommended: prepare your sensor registry

Your tablet's own registry gives the sensors their factory calibration. With the default calibration
alone, sensors and sound have failed on some units. On the host, use this tablet's own extracted
persist backup. The input is the outer `sensors/registry/` directory containing the inner `registry/`. Keep this private payload and tar out of the release and never share them. Install it in step 8a before Ubuntu first boots.

```bash
python3 "$REPO/tools/stage-sensor-registry.py" \
  --registry /path/to/own/extracted/persist/sensors/registry \
  --output "$WORK/private-sensor-payload"
tar -C "$WORK/private-sensor-payload" -cf "$WORK/sensor-registry.tar" usr
sha256sum "$WORK/sensor-registry.tar"
```

Without it, the service generates defaults from the static stock configuration. Normal reboots do not replace existing state; for later calibration installation follow the [sensor instructions](../device/sensors/README.zh-CN.md).

## 4. Partition layout

The original `userdata` range on LUN 0 is split into three partitions. All other partitions and
LUNs stay unchanged.

| Entry | Name | 512 GB unit | 256 GB unit | Use |
|---|---|---|---|---|
| 34 | `userdata` | 200 GiB | 110 GiB | Android data; keeps its start, type GUID, unique GUID and attributes |
| 35 | `linux_rootfs` | 60 GiB | 60 GiB | Ubuntu `/` |
| 36 | `linux_data` | the remainder, about 201.27 GiB | the remainder, about 60.93 GiB | Ubuntu `/home` |

LUN 0's size from step 2 tells the capacities apart: `size / 8` is the number of 4096-byte blocks,
124,552,192 (about 475 GiB) on 512 GB units and 64,167,936 (about 245 GiB) on 256 GB units. One GiB
is 262,144 blocks. The 256 GB column was measured on a 256 GB unit: `userdata` 28,835,840 blocks,
`linux_rootfs` 15,728,640 and `linux_data` 15,971,323, which ends at the last usable LBA.

The release finds its partitions by name: the names must be exactly `linux_rootfs` and
`linux_data`. Their GUIDs can be any new values.

## 5. Build the new partition table (host)

*Manual.* Work on a copy of LUN 0's table, never on the tablet. LUN 0 has 4096-byte blocks
(124,552,192 on 512 GB units, 64,167,936 on 256 GB units).

```bash
ROOT_UUID=$(uuidgen); DATA_UUID=$(uuidgen)
BLOCKS=<LUN 0 size / 8>                         # from step 2
truncate -s $((BLOCKS * 4096)) lun0.img
dd if=backup/gpt/<lun0>-head.bin of=lun0.img bs=4096 conv=notrunc
dd if=backup/gpt/<lun0>-tail.bin of=lun0.img bs=4096 seek=$((BLOCKS - 34)) conv=notrunc
LOOP=$(sudo losetup --find --show --sector-size 4096 lun0.img)
sudo sgdisk --print "$LOOP"       # expect "Sector size (logical): 4096 bytes" and your table
sudo sgdisk --info=34 "$LOOP"     # expect "Partition name: 'userdata'"; note first sector, type GUID, unique GUID, attributes
sudo sfdisk --json "$LOOP" > gpt-before.json

S=<first sector of userdata>      # 3632128 on the test unit
U=<unique GUID of userdata>; T=<type GUID of userdata>
A=52428800                        # userdata blocks: 200 GiB x 262144 on 512 GB units; 28835840 (110 GiB) on 256 GB units
R=15728640                        # linux_rootfs blocks: 60 GiB x 262144
L=0FC63DAF-8483-4772-8E79-3D69D8477DE4      # Linux filesystem
sudo sgdisk --set-alignment=256 --delete=34 \
  --new=34:$S:$((S + A - 1)) --typecode=34:$T --partition-guid=34:$U --change-name=34:userdata \
  --new=35:$((S + A)):$((S + A + R - 1)) --typecode=35:$L --partition-guid=35:$ROOT_UUID --change-name=35:linux_rootfs \
  --new=36:$((S + A + R)):0 --typecode=36:$L --partition-guid=36:$DATA_UUID --change-name=36:linux_data \
  "$LOOP"
sudo sgdisk --verify "$LOOP"      # expect "No problems found."
sudo sfdisk --json "$LOOP" > gpt-after.json
```

Compare `gpt-before.json` and `gpt-after.json`: only entries 34–36 may differ, starting at `S`,
`S + A` and `S + A + R`. `linux_data` takes the rest up to the last usable LBA, `LAST - S - A - R + 1`
blocks (52,762,619 on the 512 GB test unit, 15,971,323 on the 256 GB unit). The disk GUID, first and
last usable LBA and table length must not change. If `userdata` had non-zero attributes, copy them
with `--attributes=34:=:<hex>`.

Extract the two table regions (first usable LBA 6; last usable LBA 124552186 on 512 GB units and
64167930 on 256 GB units, 6 blocks before the end of LUN 0):

```bash
FIRST=<first usable LBA>; LAST=<last usable LBA>        # from sgdisk --print
sudo dd if="$LOOP" of=new-primary.bin bs=4096 count=$FIRST
sudo dd if="$LOOP" of=new-backup.bin bs=4096 skip=$((LAST + 1))
dd if=backup/gpt/<lun0>-head.bin bs=4096 count=$FIRST 2>/dev/null | sha256sum    # current primary region
sha256sum new-primary.bin new-backup.bin
sudo losetup -d "$LOOP"
```

## 6. Write the partition table (on the tablet)

*Manual commands in a project environment.* `boot/boot-ram-maintenance.img` starts the release
kernel from RAM with a small initramfs: the UFS driver, a root shell over the USB cable
(192.168.88.2, telnet port 2323, no password; the link carries only this cable) and the tools of
this step (`sgdisk`, `sfdisk`, `lsblk`, `partx`, `blockdev`, `dd`, `sha256sum`, `wget`). It mounts
no partition of any UFS disk and writes nothing by itself; slot A keeps Android. With no open
session for 10 minutes (also after the cable is pulled), it reboots into Fastboot.

On the host, with the tablet in Fastboot and `new-primary.bin` and `new-backup.bin` of step 5 in
the current directory:

```bash
fastboot -s <SERIAL> boot "$RELEASE/boot/boot-ram-maintenance.img"    # expect "Booting OKAY"
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check         # expect "... 192.168.88.10 -> 192.168.88.2 ready" (about 5 s)
python3 -m http.server --bind 192.168.88.10 8000 &                    # serves the two files to the tablet
telnet 192.168.88.2 2323
```

Start `http.server` after `--check` reports the link: the address 192.168.88.10 exists only while the
link is up (`Cannot assign requested address` otherwise). If it reports `Address already in use`, another
server holds the port, possibly on every address; use a free port in the `wget` lines below. The hash
checks of the downloaded files catch a wrong server.

If `--check` prints `ip` commands instead, run them and check again. The login prints the LUN
table (LUN, disk, size in 512-byte sectors, block size, device path), `Mounted from the UFS
disks: none` and the `sgdisk -p` table of LUN 0. LUN 0 is the line starting with `0`, size
996417536 on 512 GB units or 513343488 on 256 GB units, block size 4096; check that the table is
your table of step 2.
Then, in the telnet session:

```sh
FIRST=<first usable LBA>; LAST=<last usable LBA>; BLOCKS=<LUN 0 blocks>    # the values of step 5
disk=/dev/sdX                                           # LUN 0 from the login table
piano-maint map                                         # again: "LUN 0: /dev/sdX", "Mounted from the UFS disks: none"
wget -O /run/new-primary.bin http://192.168.88.10:8000/new-primary.bin
wget -O /run/new-backup.bin http://192.168.88.10:8000/new-backup.bin
sha256sum /run/new-primary.bin /run/new-backup.bin      # expect the hashes of step 5
dd if=$disk bs=4096 count=$FIRST | sha256sum            # expect the current primary region hash of step 5
dd if=/run/new-backup.bin of=$disk bs=4096 seek=$((LAST + 1)) conv=fsync
dd if=$disk bs=4096 skip=$((LAST + 1)) count=$((BLOCKS - LAST - 1)) | sha256sum   # expect new-backup.bin
dd if=/run/new-primary.bin of=$disk bs=4096 conv=fsync
dd if=$disk bs=4096 count=$FIRST | sha256sum            # expect new-primary.bin
sync
sgdisk --verify $disk                                   # expect "No problems found."
sgdisk -p $disk                                         # expect entries 34-36 userdata, linux_rootfs, linux_data
piano-maint fastboot                                    # reboots into Fastboot
```

Write the backup copy first, then the primary, and compare each readback before continuing. The
kernel keeps the old partition list until the reboot; do not mount or format anything in this
session. Stop the host's `http.server` afterwards.

- `piano-maint status` shows the idle count; `piano-maint keepalive` restarts it and
  `piano-maint idle SECONDS` (30–3600) changes the limit. An open telnet session holds the
  timer only while the cable is connected.
- If `piano-maint fastboot` says `nvmem-reboot-mode is not bound`, it does not reboot; use
  Power + Volume Down.


## 7. Write the filesystems (Fastboot)

Check the new partitions, then create the `/home` image for your `linux_data` size:

```bash
fastboot -s <SERIAL> getvar partition-size:userdata       # expect "3200000000" (hex, printed without 0x); 102 GiB on 256 GB units
fastboot -s <SERIAL> getvar partition-size:linux_rootfs   # expect "0xF00000000"
fastboot -s <SERIAL> getvar partition-size:linux_data     # linux_data blocks x 4096 in hex: "0x32517FB000" on the 512 GB unit
truncate -s $((0x<linux_data size>)) data.ext4
mkfs.ext4 -L linux_data data.ext4 && img2simg -s data.ext4 data.sparse.img     # about 6 MB
```

On 256 GB units ABL reports `userdata` as 102 GiB (`1980000000`), 8 GiB less than the 110 GiB GPT
entry. This is normal: stock Android's `/data` is 8 GiB smaller than its partition as well.

Erase Android's data and write both filesystems:

```bash
fastboot -s <SERIAL> erase userdata
fastboot -s <SERIAL> erase metadata
python3 "$REPO/tools/sparse_image.py" summary "$RELEASE/rootfs/root.sparse.img" data.sparse.img   # what the tablet will write
fastboot -s <SERIAL> flash linux_rootfs "$RELEASE/rootfs/root.sparse.img"   # a few minutes; 8.7 to 10.1 GB written
fastboot -s <SERIAL> flash linux_data data.sparse.img                      # seconds
# expect "OKAY" on every Sending and Writing line, then "Finished."
```

The tablet writes only the blocks the filesystem actually uses (about 9 GB) and skips the free blocks; `img2simg -s` likewise skips the free blocks of `data.sparse.img`.

**`OKAY` does not mean the write is complete.** The bootloader answers as soon as the data has arrived and keeps writing in the background; the next Fastboot command waits for it. Do not unplug, reset or power off while a command waits, and do not repeat a write that returned `OKAY`.

## 8. Optional: test-boot without writing slot A

```bash
fastboot -s <SERIAL> boot "$RELEASE/boot/boot-ram-check.img"     # expect "Booting OKAY"
```

This boots the installed root once from RAM, with the same kernel, initrd and command line as the
release set, and writes no boot partition. It is a normal boot without a debug console: return to
Fastboot with Power + Volume Down.

## 8a. Recommended: install your unit's files before the first boot

The maintenance image of [step 6](#6-write-the-partition-table-on-the-tablet) can make the partition
map, put it with your touch calibration and addresses into the new root and turn on developer mode
before Ubuntu first boots. Touch then works in Initial Setup, and the host can reach the tablet over
SSH as soon as the account exists. Without this step, use the magnetic keyboard in Initial Setup and
copy the files in step 10.

Keep boot-profile.json and boot-signing.pem on the host until step 10 so boot-set regeneration happens after the Android recovery set is ready.

In Fastboot, with the files of step 3 in the current directory on the host:

```bash
fastboot -s <SERIAL> boot "$RELEASE/boot/boot-ram-maintenance.img"    # expect "Booting OKAY"
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check         # expect "... ready"
python3 -m http.server --bind 192.168.88.10 8000 &                    # a free port, as in step 6
telnet 192.168.88.2 2323
```

In a second terminal on the host, in the same directory, wait for the boot LUN's partition table:

```bash
nc -l 192.168.88.10 8001 | python3 "$REPO/tools/extract-boot-partitions.py" --input - \
  --profile boot-profile.json --output boot-partitions.json
```

In the telnet session, send it (another free port works as well):

```sh
sfdisk --json /dev/sdY | nc 192.168.88.10 8001      # sdY: LUN 4 from the login table
```

The second terminal prints `init_boot_a` (8388608 B), `vendor_boot_a` and `boot_a` (100663296 B
each) and writes `boot-partitions.json`. The tool refuses a disk without `boot_a` and `boot_b`, and
sizes other than those in your boot profile; `piano-boot-switch` later refuses the map unless the
disk is LUN 4. Then, on the host:

```bash
sha256sum thp-calibration.json bluetooth-address wlan-address boot-partitions.json
```

and in the telnet session:

```sh
disk=/dev/sdX                                       # LUN 0 from the login table
for p in /sys/class/block/${disk##*/}*; do grep -qx PARTNAME=linux_rootfs $p/uevent && root=/dev/${p##*/}; done
echo $root                                          # entry 35 of LUN 0, for example /dev/sda35
mkdir -p /mnt && mount -t ext4 $root /mnt
for f in thp-calibration.json bluetooth-address wlan-address boot-partitions.json; do
  wget -O /mnt/etc/piano/$f http://192.168.88.10:8000/$f && chmod 600 /mnt/etc/piano/$f
done
sha256sum /mnt/etc/piano/*                          # the four hashes must equal the host's
mkdir -p /mnt/etc/systemd/system/usb-gadget.target.wants
ln -sf /usr/lib/systemd/system/piano-devmode.service /mnt/etc/systemd/system/usb-gadget.target.wants/
# Recommended: your sensor-registry.tar of step 3 (skip these four lines without it)
wget -O /tmp/sensor-registry.tar http://192.168.88.10:8000/sensor-registry.tar
sha256sum /tmp/sensor-registry.tar                  # must equal the host's
tar -xf /tmp/sensor-registry.tar -C /mnt
chown -R 0:0 /mnt/usr/share/piano/sensors/registry-private
sync; umount /mnt
piano-maint fastboot
```

`lsblk` shows no partition labels in this environment; the loop above reads them from sysfs. The
symbolic link is what `piano-devmode on` creates.


## 9. Write slot A and first boot

Check the battery, then write the release set into slot A. `piano-switch.py` checks the tablet and
both sets first, and writes the Android set back if a write fails:

```bash
fastboot -s <SERIAL> getvar battery-soc-ok         # expect "battery-soc-ok: yes"
python3 "$REPO/tools/piano-switch.py" --serial <SERIAL> --images "$RELEASE/boot" --rollback android-set
# expect "Active A boot triplet written; remain in Fastboot for a RAM readback before reboot."
fastboot -s <SERIAL> reboot
```

- **GNOME Initial Setup** creates the first account. After [step 8a](#8a-recommended-install-your-units-files-before-the-first-boot)
  touch works; otherwise use the magnetic keyboard (touch is off until step 10). Skip the network
  page. Someone must be at the tablet: until the account exists and developer mode is on (step 8a or
  10) the host cannot reach it (developer mode needs an account and refuses root logins), and the only
  way back is Power + Volume Down.
- **Computer name.** The account page fills its *Computer* field with your user name and makes it
  the hostname, replacing the image's `xiaomipad-8pro`; edit the field for
  another name, or later run `sudo hostnamectl hostname --static <name>`. The pretty name
  "Xiaomi Pad 8 Pro", which Bluetooth shows, stays.
- **Clock.** The hardware clock cannot be written: a boot restores the time saved at the last shutdown and corrects it from the network once online; without a network, set it by hand with `sudo date -s 'YYYY-MM-DD HH:MM'`.
- The slot is marked as successfully booted after the boot completes:
  `sudo piano-slot-success --check` should report `"state": "already-successful"`.

Until step 10: no Bluetooth, a random Wi-Fi MAC address, no switching from Ubuntu, and kernel
updates leave slot A unchanged (the addresses are applied by the boot set rebuilt there). Touch works
from the first boot only after step 8a. Initial Setup's keyboard page rebuilds the initrd; without
`boot-profile.json` and `boot-signing.pem` in `/etc/piano` this leaves the boot set alone.

## 10. Install your unit's files

**Copy the files to the tablet** over USB with developer mode (SSH over the USB cable; USB host and
external drives are not supported). In a terminal on the tablet (Ctrl+Alt+T):

```bash
sudo piano-devmode on      # already on after step 8a
piano-devmode status       # expect "developer mode: enabled, active" and "SSH socket: active (192.168.88.2 port 22)"
```

On the host, with `<USER>` the account from Initial Setup:

```bash
python3 "$REPO/tools/piano-devmode-ssh.py" --serial <SERIAL> --check     # expect "... 192.168.88.10 -> 192.168.88.2 ready"
```

If the host did not take the DHCP address, `--check` prints the `ip` command to set it.

**Create the partition map** unless step 8a made it. On the tablet:

```bash
d=$(lsblk -no PKNAME "$(readlink -f /dev/disk/by-partlabel/boot_a)"); readlink -f /sys/block/$d/device
# expect a path ending in ":4" (LUN 4)
sudo sfdisk --json /dev/$d > ~/boot-lun.json
```

On the host:

```bash
scp <USER>@192.168.88.2:boot-lun.json .
python3 "$REPO/tools/extract-boot-partitions.py" --input boot-lun.json --profile boot-profile.json --output boot-partitions.json
# expect init_boot_a 8388608 B, vendor_boot_a and boot_a 100663296 B each
```

A fresh installation needs no separate support package; it is in the image. Keep boot-profile.json and boot-signing.pem on the host until this step, so that the boot set is rebuilt after the Android recovery set is ready.

**Copy the files:**

```bash
scp -r thp-calibration.json bluetooth-address wlan-address boot-partitions.json boot-profile.json \
  boot-signing.pem android-set <USER>@192.168.88.2:
```

After step 8a four of the files are already in `/etc/piano`; copy only `boot-profile.json`,
`boot-signing.pem` and `android-set`.

**Install the files, store the Android set and rebuild the boot set:**

```bash
cd ~
sudo install -m 600 bluetooth-address wlan-address thp-calibration.json boot-profile.json \
  boot-signing.pem boot-partitions.json /etc/piano/       # after step 8a: boot-profile.json boot-signing.pem
sudo piano-boot-switch status          # expect "occupant": "ubuntu" and "backup_gpt": "valid"
sudo piano-boot-switch import-android --from ~/android-set     # expect "result": "imported"
sudo piano-boot-update $(uname -r)
# expect "Piano boot image set prepared for <release>" and "Slot A now holds store/ubuntu"
sudo systemctl reboot
```

The new set carries your addresses, serial and key; the release set is kept as `ubuntu.prev`. After
the reboot:

```bash
sudo piano-boot-switch verify          # expect exit status 0
```

Then delete the copies in your home directory (`rm -r ~/android-set ~/boot-signing.pem ...`) and,
in a terminal on the tablet, turn developer mode off; it was on for the installation only:

```bash
sudo piano-devmode off
piano-devmode status       # expect "developer mode: disabled, inactive"
```

`sudo piano-devmode on` turns it on again when you need the host connection. While on, it accepts
every local account with a password over the USB cable; see [device/devmode](../device/devmode/README.md)
to restrict it to SSH keys. It listens only on the USB network address `192.168.88.2`.

If the rebuilt set does not boot: Power + Volume Down, then write the release `boot/` set back from
the host ([FLASHING.en.md](FLASHING.en.md#from-the-host-piano-switchpy)).

## After installation

- **Switching and recovery:** [FLASHING.en.md](FLASHING.en.md).
- **Packages:** run `sudo apt update` before the first `apt install`; the image ships without package indexes. Only the project kernel is installed, without Ubuntu's generic kernel and the C library headers: install `build-essential` before compiling software.
- **Kernel updates:** see [UPGRADE.en.md](UPGRADE.en.md).
- **Android:** the first Android boot re-creates its data and takes several minutes. Reinstall the
  root manager app if you need root; the root-patched `init_boot_a` is unchanged. After changing the
  root patch in slot A, refresh the stored Android set ([FLASHING.en.md](FLASHING.en.md#image-sets)).
- **Known issues and support status:** [README](../README.en.md#hardware-support), the release's
  `RELEASE-NOTES.md`.

## Troubleshooting

- **No sound.** Check the kernel log first:
  `sudo journalctl -k -b | grep -E 'crash detected in adsp|sns_watchdog'`. Repeated lines mean the
  sensor service keeps restarting the audio DSP. Once the DSP has crashed three times in a boot, at
  least once from `sns_watchdog`, the sensor service moves its registry to
  `/var/lib/piano/sensors/registry.quarantine-<time>` and starts over
  (`journalctl -b -u piano-hexagonrpcd-sns` names it). If the lines continue, install your own
  sensor registry ([step 3](#recommended-prepare-your-sensor-registry)) as described in the
  [sensor instructions](../device/sensors/README.zh-CN.md) (Chinese).
- **Touch does not respond or moves the wrong way.** `journalctl -b -u piano-thp` names the detected
  panel. A `THP calibration error` line means `thp-calibration.json` comes from the other panel's ini:
  extract it again from the ini named there (step 3), copy it to `/etc/piano/thp-calibration.json` and
  run `sudo systemctl restart piano-thp`.

## Steps without a project tool

| Step | State |
|---|---|
| Android root | the root manager's own procedure |
| Partition backup and manifest (step 2) | standard `adb`/`dd` commands |
| Wi-Fi address, boot profile (step 3) | standard commands; no generator |
| New partition table (step 5) | standard `sgdisk`/`sfdisk` on a loop device |
| Writing the partition table (step 6) | standard `dd` commands in the release's maintenance RAM image |
| Restoring the original partition table and removing Ubuntu | [512 GB one-unit evidence / manual sequence](RESTORE-STOCK.en.md); `prepare-stock-restore.py` validates/extracts offline only, not an automatic uninstaller; keep this unit's backups |
