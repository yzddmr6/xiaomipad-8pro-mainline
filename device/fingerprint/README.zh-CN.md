# piano 指纹产品集成

适用于 Ubuntu 26.04 ARM64、带 QTEE（`/dev/tee0`）与项目内核的平板。

## 包和边界

`piano-fingerprint` 提供一个命令行客户端 `piano-fingerprint-ctl`，以及它需要的四个原厂固件文件。

指纹传感器的总线在安全世界里，应用处理器不能直接访问它。客户端通过 Qualcomm 公开的 MinkIPC
接口访问工厂应用，用内核里的 uio 驱动控制传感器的供电、复位并接收中断。

| 能力 | 状态 |
| --- | --- |
| 读取传感器信息 | 已实现 |
| 初始化传感器并读取几何与标定 | 已实现 |
| 绑定 Keymaster | 已实现 |
| 录入指纹 | **未实现** |
| 认证指纹 | **未实现** |

录入与认证还需要 Goodix 自己的命令协议和 libfprint 后端。SunUEFI 的同一客户端也停在这一步，
因此这里移植的是同样的范围，不是被削弱的版本。客户端自己的 `--help` 里也写明了这一点。

## 构建

三个上游工程按 `device/fingerprint/source.json` 里锁定的提交取回：quic-teec、qualcomm/minkipc、
laurencelundblade/QCBOR。客户端是本仓的 `device/fingerprint/piano-goodix.c`，未作改动。

```bash
python3 tools/build-fingerprint-deb.py --output out/fingerprint
```

需要交叉编译器 `aarch64-linux-gnu-gcc`、`qemu-aarch64-static`（用于冒烟测试）和 `dpkg-deb`。
产物是静态链接的客户端，`out/fingerprint/manifest.json` 记录每一个输入的哈希。

安装：

```bash
sudo apt install ./out/fingerprint/piano-fingerprint_0.1+20261028.1_arm64.deb
```

客户端装在 `/usr/libexec/piano/piano-fingerprint-ctl`，四个固件文件装在 `/usr/lib/firmware/`。

## 原厂固件

`firmware/fingerprint/` 的四个文件来自 OS3.0.303.0.WPYCNXM 原厂包，与本仓其它固件同一个 stock build：

| 文件 | 字节 | 来源 |
| --- | --- | --- |
| `gfpia.elf` | 11681880 | `modem_a` FAT `image/` 的 `gfpia.mdt` 与 `gfpia.b00`–`gfpia.b08`，按 ELF program header 偏移重组 |
| `piano-goodix-cap-s.bin` | 308544 | `odm_a` `lib64/libgf_hal.so` 的 `__asp_CAP_S_2_bin` |
| `piano-goodix-cap-t.bin` | 308544 | 同上，`__asp_CAP_T_2_bin` |
| `piano-goodix-dubai-u4.bin` | 298120 | 同上，`DLWeightLightDubaiU4_12_10_14MisTouch` |

`gfpia.elf` 是原厂签名的安全程序，分发它与分发其它任何原厂固件性质一致；安全程序与其加载接口都没有
被修改或绕过。三个 blob 是按这颗芯片选定的静态算法数据。

设备所有者 2026-10-28 的决定只涵盖这四个文件，明确**不**包括：厂商 `libgf_hal.so` 本体、任何逐机数据、
persist registry、工厂标定、指纹模板与已录入的生物特征数据、任何签名绕过产物。这些文件是传感器的静态
数据，不含密钥，也不含单台设备的信息。逐文件来源与哈希见 `firmware/fingerprint/source.json`。

## 内核侧

内核仓（`yzddmr6/linux-sm8750-piano`）提供：

- `drivers/uio/uio_piano_fingerprint.c`：传感器的供电、复位与中断，以及 UIO 设备。
- `drivers/tee/qcomtee/shm.c`：大共享缓冲走 CMA。11 MiB 的安全程序超过伙伴分配器最大阶数，
  小缓冲仍在伙伴路径上；公开缓冲用板级声明的 CMA 池，因为该应用不接受 4 GiB 以上的缓冲。
- 设备树：根块内的 `fingerprint` 节点、`piano_fp_vdd`（PMIC B 的第九个 LDO，3296000–3300000 µV，
  初始模式 HPM）、gpio73 的复位（主动低）与 gpio85 的中断（主动高）。
- 配置：`CONFIG_UIO=y`、`CONFIG_UIO_PIANO_FINGERPRINT=y`、`CONFIG_LEDS_CLASS_FLASH=y`、
  `CONFIG_LEDS_QCOM_FLASH=y`。`UIO_PIANO_FINGERPRINT` 在 `if UIO` 菜单里、`LEDS_QCOM_FLASH` 在
  `if LEDS_CLASS_FLASH` 菜单里，只写子选项会被 `olddefconfig` 静默丢弃。

## 未验证

安全程序的签名有效性无法在静态分析中确认；加载、传感器初始化、录入与认证都需要真机。
本包目前只有主机侧构建与 qemu 冒烟测试的证据。
