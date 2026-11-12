# Firmware

Stock firmware for the Xiaomi Pad 8 Pro (`piano`), stored under the paths the mainline kernel
requests below `/usr/lib/firmware`. The packaging tools use this tree by default.
CDSP images and split segments are included under the same board directory and lock.
CDSP 主镜像、DTB 与分段同样随仓库提供，构建不需要额外提取步骤。

## Origin

All stock payloads come from stock build **OS3.0.303.0.WPYCNXM** and are byte-identical to the stock files.
Only the Wi-Fi files are renamed, to the names `ath12k` requests (the mapping is in `piano-wlan.json`).
The source partitions are written by system updates and are the same on every unit with this build.
Per-unit data is excluded under the [distribution policy](#distribution-policy).

| Directory | Files | Loaded by | Stock source | Lock |
|---|---|---|---|---|
| `qcom/sm8750/piano/` | 56: `adsp.mdt`, `adsp.b01`–`adsp.b54` (no `b27`), `adsp_dtb.mdt`, `adsp_dtb.b01` | `qcom_q6v5_pas` (audio DSP) | `modem_a` FAT, `image/` | `piano-os3.0.303.0.json` |
| `qcom/` | `gen80000_sqe.fw`, `gen80000_gmu.bin`, `gen80000_aqe.fw` | `msm` (Adreno 830) | `vendor_a`, `firmware/` | `piano-gpu.json` |
| `qcom/sm8750/xiaomi/piano/` | `gen80000_zap.mbn` (signed for this model) | `msm` zap shader | `odm_a`, `firmware/` | `piano-gpu.json` |
| `qcom/sm8750/` | `vpu35_4v.mbn` (signed ELF32) | `qcom-iris` (SM8750 VPU35) | `odm_a`, `firmware/vpu35_4v.mbn` | `piano-iris.json` |
| `qca/` | 27: `gngbtfw20.mbn`, `gngbtnv20.bin` and 25 board-specific `gngbtnv20.bXX` | `hci_qca` (WCN7860 Bluetooth, ROM 0x20) | `bluetooth_a` FAT, `image/` | `piano-bluetooth-rom20.json` |
| `ath12k/WCN7860/hw2.0/` | `amss.bin`, `m3.bin`, `aux_ucode.bin`, `regdb.bin`, `qdss.cfg`, `board.bin` | `ath12k` (WCN7860 Wi-Fi) | `modem_a` FAT, `image/peach/` | `piano-wlan.json` |
| `novatek/piano/` | `novatek_nt36532_piano_fw_csot.bin`, `novatek_nt36532_piano_fw_boe.bin` (CSOT and BOE panels; the driver picks one by panel ID) | `nt36532e-thp` (touch) | `odm_a`, `firmware/` | `piano-input.json` |
| `nanosic/piano/` | `MCU_Upgrade.bin` | `hid-nanosic` (keyboard bridge) | `odm_a`, `firmware/` | `piano-input.json` |
| `foursemi/piano/` | `fs19xx.fsm` (register preset of the four amplifiers) | `snd-soc-fs19xx` (speaker amplifiers) | `odm_a`, `firmware/` | `piano-audio.json` |
| `sensors/odm/`, `sensors/vendor/` | 66 static sensor configuration files | `piano-sensors` registry staging | `odm_a`, `vendor_a` | `sensors/source.json` |

Each lock pins the name, size and SHA-256 of its files. `piano-os3.0.303.0.json` also pins the CDSP
images and service maps of the same build. They are not shipped because the current kernel does not
load them. `board.bin` is the stock board file for the China region (`bd_p81.elf`). Units sold for
other regions use a different stock board file.

**Not included:**
- touch calibration, which `tools/extract-thp-calibration.py` generates per device;
- the Bluetooth address, the Wi-Fi MAC address (`persist`) and other per-unit calibration;
- stock files the current kernel does not load (CDSP, other board files, the touch `mp` test images);
- the audio capture topology `qcom/sm8750/Piano-Capture-Diag-tplg.bin`, which is built from
  [`device/audio`](../device/audio/README.md).

## Packaging and using your own extraction

`tools/build-firmware-deb.py` builds `piano-local-firmware` from this tree. The package contains
every locked file and the capture topology built from `device/audio`. Each file is checked against
its lock:

```sh
python3 tools/build-firmware-deb.py --topology out/audio/Piano-Capture-Diag-tplg.bin \
  --card-topology out/audio/Xiaomi-Pad-8-Pro-tplg.bin \
  --maintainer 'Your Name <you@example.org>' --output out/firmware-deb
```

To use files from your own device instead, copy them from its stock partitions into a directory
with the same layout. Rename the Wi-Fi files as listed in `piano-wlan.json`. Then add
`--firmware-tree /path/to/your/tree`. Files outside the locks are ignored, and a missing or
different file stops the build. A different stock build therefore needs updated locks.
`tools/stage-firmware.py --firmware-dir /path/to/modem/image` stages the DSP files alone from the
flat stock `image/` directory.

## Distribution policy

Generic stock firmware and static ODM/vendor configuration shared by units of the same stock
build may be included here. Per-unit sensor registry, factory and touch calibration, radio
addresses, credentials and stock backups remain private. ROM-default sensor `fac_cal` entries
are generic configuration; values from a unit's persist registry are not.

The stock payloads are distributed unmodified solely for interoperability. No redistribution licence
has been granted by their rights holders, and the project's licences do not cover them. Files
will be removed at a rights holder's request. Users may supply their own extraction as described
above.

## Licence

The stock payloads listed in the table carry `LicenseRef-Proprietary-Firmware`
([text](../LICENSES/LicenseRef-Proprietary-Firmware.txt)); [REUSE.toml](../REUSE.toml) identifies
those files. Project documentation and identity locks, including this README,
`sensors/README.md`, `sensors/source.json` and `piano-*.json`, remain MIT.
