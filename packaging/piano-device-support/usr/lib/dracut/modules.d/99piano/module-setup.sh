#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
check() { [[ -f /etc/piano/layout.json ]]; }
depends() { echo systemd; }
installkernel() { instmods nvmem_qcom-spmi-sdam nvmem-reboot-mode; }
install() {
    inst_multiple /bin/sh mount mkdir tr grep od awk readlink sleep sync modprobe systemctl
    inst_simple /usr/libexec/piano/piano-physical-platform
    # PID1 caches virtualization before ordinary dracut hooks run.
    rm -f "$initdir/init"
    inst_simple "$moddir/init" /init
    inst_simple /usr/libexec/piano/busybox
    inst_simple /usr/libexec/piano/usb-diag
    inst_simple /etc/piano-network
    mkdir -p "$initdir/usr/lib/systemd/system/sysinit.target.wants"
    inst_simple /usr/libexec/piano/debug-timeout
    inst_simple /usr/lib/systemd/system/piano-debug-timeout.service
    ln_r /usr/lib/systemd/system/piano-debug-timeout.service /usr/lib/systemd/system/sysinit.target.wants/piano-debug-timeout.service
    # Native KMS can request the built-in A830 firmware before switch-root.
    inst_simple /usr/lib/firmware/qcom/gen80000_gmu.bin
    inst_simple /usr/lib/firmware/qcom/gen80000_aqe.fw
    inst_simple /usr/lib/firmware/qcom/gen80000_sqe.fw
    inst_simple /usr/lib/firmware/qcom/sm8750/xiaomi/piano/gen80000_zap.mbn
    # Iris uses the board firmware-name, not the driver's generic default.
    inst_simple /usr/lib/firmware/qcom/sm8750/vpu35_4v.mbn
    # PAS and input consumers probe before the persistent root exists.
    inst_simple /usr/lib/firmware/nanosic/piano/MCU_Upgrade.bin
    for f in /usr/lib/firmware/qca/gngbtfw20.mbn /usr/lib/firmware/qca/gngbtnv20.*; do
        inst_simple "$f"
    done
    for f in /usr/lib/firmware/qcom/sm8750/piano/adsp.* /usr/lib/firmware/qcom/sm8750/piano/adsp_dtb.* \
        /usr/lib/firmware/qcom/sm8750/piano/cdsp.* /usr/lib/firmware/qcom/sm8750/piano/cdsp_dtb.*; do
        inst_simple "$f"
    done
}
