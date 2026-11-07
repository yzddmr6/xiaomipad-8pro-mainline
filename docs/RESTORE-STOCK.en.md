# Returning from Ubuntu dual boot to stock HyperOS: one-unit evidence

[中文](RESTORE-STOCK.md)

**On 2026-11-07, one `piano` 512 GB / BOE unit successfully restored its original GPT and flashed HyperOS 4.**
This is a user-supplied hardware report, not a maintainer certification for every capacity, panel or stock build.
The procedure destroys Android and Linux user data. To use Android temporarily, use
[boot-set switching](FLASHING.en.md); do not change GPT just to switch operating systems.

## Tested scope and limitations

- Before installation: OS3.0.303.0.WPYCNXM, with the original `userdata` reaching LUN0's last usable LBA.
- Dual boot: Ubuntu 26.04, Linux `7.2.14-piano56`; 200 GiB userdata, 60 GiB Linux root and approximately 201.27 GiB Linux data.
- Release maintenance RAM image SHA-256:
  `0f03ca69833289671a7bd13577b5eeeefcf7132765aeb4110805ece263f68fe5`.
  The integration checkout used for installation was `5152d48f4153752abfa7bd1c8ff940a3a8f6dff8`.
- After restoring this unit's GPT, the **ordinary**, complete stock fastboot sequence installed
  OS4.0.5.0.XPYCNXM (Android 17). Archive SHA-256:
  `d16a12872f5a47cd93fea38e1bb6783d552fe06682244664a0b71a7867d92425`.
- No relock or `persist` / per-unit NV calibration writes. The stock boot chain replaced the old
  KernelSU patch and Linux switching set; neither was reinstalled.
- Complete stock recovery, including both-slot firmware, radio, `vbmeta` and `dtbo`, was explicitly
  authorized for this operation. This **does not** relax the normal Ubuntu installation/update
  [do-not-write boundaries](FLASHING.en.md#do-not-write).
- A device-bound controller performed the actual experiment. The new host-only
  `tools/prepare-stock-restore.py` extracts the same GPT validation, exact-region extraction and
  factory-action planning logic. **The parameterized tool has only offline tests, not another
  on-device run.** It rejects nonregular input paths before opening, rechecks the opened file identity,
and issues no device reads/writes, ADB/Fastboot commands or automatic uninstall/flash.
- Other capacities, layouts, missing original backups, downgrade to OS3 and power-loss recovery are untested.

## Why ordinary stock flashing is not sufficient

The observed OS3/OS4 `flash_all.sh` scripts are identical, with SHA-256
`8610cf859ab4fdda40acaa81c1c25ef27b233d8622df716587e7c518360ccb10`.
They replace Android and boot firmware but **do not restore GPT or remove Linux partitions**.
The archive's `gpt_both0.bin` is a capacity-adaptation template, not a dump to write directly to this disk.
The backed-up `android-set/init_boot.img` contains a KernelSU patch and is not an unmodified stock image.

## 1. Validate offline and prepare exact fragments

Use [this unit's pre-installation GPT backups](INSTALL.en.md#2-back-up), plus current primary/backup
GPT dumps exported from maintenance RAM and preserved on the host. Inputs are head/tail dumps,
not whole disks. Never use another unit's backup. Independently identify the unit, LUN0 geometry,
logical block size and disk GUID before supplying them explicitly:

```sh
python3 -I tools/prepare-stock-restore.py \
  --stock-head /private/stock/sda-head.bin --stock-tail /private/stock/sda-tail.bin \
  --current-head /private/current/sda-head.bin --current-tail /private/current/sda-tail.bin \
  --block-size 4096 --disk-blocks <measured-block-count> --expected-disk-guid <this-disk-guid> \
  --output /private/new-stock-restore
```

Without `--output` this only validates and writes no files. An output directory must not already exist.
The stock/current fragments, `SHA256SUMS` and `plan.json` are private; do not commit them.
The tool accepts only the observed 4096-byte logical-block geometry. It verifies header/array CRCs,
primary/backup consistency, GUID/geometry and partition overlaps/bounds. Existing partitions must be
unchanged except for a shorter userdata end; the two Linux filesystem partitions must exactly fill its old tail.
It accepts the factory protective MBR's saturated `0xffffffff` length rather than silently repairing it.

The following numbers describe **this experiment, not defaults for another unit**:

| Item | Observed |
|---|---|
| LUN0 | 4096 B × 124552192 blocks |
| Usable LBAs | 6–124552186 |
| Primary GPT reserved region | LBA 0–5, 24576 B |
| Backup GPT reserved region | LBA 124552187–124552191, 20480 B |
| Original userdata | LBA 3632128–124552186, 495288561664 B |

**Never restore all 34 blocks of either dump.** The head contains real `switch` and other partition data
starting at block 6; the first 29 blocks of the tail are userdata. The extractor exports only GPT reserved
regions, excluding those bytes. Installation-generated `work/new-primary.bin` and `new-backup.bin`
contain the Linux layout, not the stock table.

## 2. Enter independent maintenance RAM and recheck

Use the [maintenance RAM image](FLASHING.en.md#maintenance-ram-image): shut Ubuntu down normally,
enter Fastboot and temporarily boot the verified image with your serial explicitly bound.
Never restore GPT from Ubuntu while its `/` and `/home` are mounted on the partitions being removed.
Verify a RAM root, no mounted internal-storage partitions, no disk swaps/holders, unique LUN0 with the
expected geometry/GUID, at least 40% battery, and `Fastboot reboot ready` from `piano-maint status`.
Stop on uncertainty; do not repeat `fastboot boot` or reset USB from the host.

The ordinary installed Linux session required interactive privilege escalation for raw GPT reads, so
collection took place in maintenance RAM instead; no permission bypass was attempted.
A Telnet prompt may contain the cursor query `ESC[6n`; a buffer not ending exactly in the prompt is
not evidence of a failed boot. A RAM-only `piano-maint idle 3600` can extend the idle timeout, followed
by another status read. Do not leave a host keepalive process running.
**Do not exit, disconnect or let the idle watchdog reboot between the backup and primary writes.**

## 3. Restore one region at a time and verify immediately

First preserve current GPT dumps on the host and verify them. Transfer stock fragments to RAM `/run`
and verify their exact lengths/SHA-256. This is a parameterized reference to the successful sequence,
**not an unattended flashing script**. Resolve `DISK` from the measured LUN0 mapping and fill the other
values from the verified `plan.json`. Copying these instructions does not authorize a device operation.

```sh
# Already in RAM, with UFS unmounted. Explicitly fill and verify these variables:
# DISK=/dev/<measured-LUN0>; R=/run/<verified-fragment-directory>
# BS=<block_size>; TAIL_START=<tail_start_lba>
# TAIL_COUNT=<tail_blocks>; HEAD_COUNT=<head_blocks>
# Execute separately; inspect each exit status before proceeding.
dd if="$R/stock-backup.bin" of="$DISK" bs="$BS" seek="$TAIL_START" count="$TAIL_COUNT" conv=notrunc,fsync
dd if="$DISK" of="$R/readback-backup.bin" bs="$BS" skip="$TAIL_START" count="$TAIL_COUNT"
# Check readback-backup length and SHA256 against the recorded stock-backup.
# On mismatch: stop, do not write the primary, auto-rollback or reboot.
dd if="$R/stock-primary.bin" of="$DISK" bs="$BS" count="$HEAD_COUNT" conv=notrunc,fsync
dd if="$DISK" of="$R/readback-primary.bin" bs="$BS" count="$HEAD_COUNT"
# Verify exact length and SHA256 again. Both regions must pass before continuing.
sync
sgdisk --verify "$DISK"
sgdisk -p "$DISK"
```

Export readbacks to the host. Validate both headers/arrays, GUID, complete partition entries and
userdata boundaries, and compare both fragments byte-for-byte with stock.
This factory layout emits warnings for the 8 KiB entry array, reserved gaps and last-partition alignment.
Explain warnings against the original backup instead of automatically correcting them or relying only
on exit status. Stop on an actual error; do not interrupt an ongoing write or resend it after a timeout.

**The running kernel may still have the old partition view; do not mount or format in this session.**
Only after all verification and Fastboot readiness pass, explicitly authorize `piano-maint fastboot`.
In the new Fastboot view check the expanded userdata and absence of both Linux partitions.
Fastboot `partition-size` values are hexadecimal even without an `0x` prefix. A bootloader may print
`FAILED` while returning 0; check the output as well as the return code.

## 4. Separately authorized complete stock flash and acceptance

Check the model/China build, image hashes, unlocked state, anti value, slot A, battery and restored
userdata size, then approve clearing both operating systems, the complete firmware write scope and
reboot only on success. This experiment explicitly erased the expanded userdata with
`fastboot -s <SERIAL> erase userdata` before the reviewed ordinary stock sequence rebuilt userdata /
metadata and flashed OS4. No additional host `mkfs` or speculative `fastboot format` was used.
`_ab` includes both slots; it does not mean A only. Do not use a lock script, write `persist`/calibration,
or change GPT again.

Optionally pass the exact audited `flash_all.sh` as `--factory-script /private/rom/flash_all.sh` to the
offline tool: it only produces the additional userdata erase plus the 55 original actions for review.
The two CRC actions require an explicit `crc: 1`. **A script hash does not identify the ROM build**:
these two builds shared a script, so verify the whole ROM and every image independently.
Unrecognized scripts are refused. Stop on failure without automatic retries, slot changes, reboot or relock.

All 50 flash commands, four erases, A-slot selection and reboot succeeded. ADB after first boot showed:

- `OS4.0.5.0.XPYCNXM`, Android 17 / SDK 37, `sys.boot_completed=1`;
- slot A, `flash.locked=0`, `vbmeta.device_state=unlocked`, `verifiedbootstate=orange`, verity enforcing;
- f2fs `/data` totaling 495286464512 B (approximately 461.27 GiB), not the old 200 GiB or the stock
  userdata sparse image's 45 GiB logical size;
- neither Linux partition link exists; Wi-Fi connected and battery 100%; the user subsequently
  confirmed touch, speakers, Bluetooth connection and camera operation.

Acceptance did not reflash the unit or escalate privileges. USB debugging was enabled by the user.
All temporary host processes and the dedicated ADB server exited. This demonstrates recovery
usability, not forensic erasure or a security attestation that root is impossible.
