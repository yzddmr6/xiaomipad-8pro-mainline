#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Sourced by dracut, only in the explicitly prepared RAM display experiment.
case " $(cat /proc/cmdline) " in *' piano.native_display=1 '*|*' piano.ram_modules=1 '*) ;; *) return 0;; esac
case " $(cat /proc/cmdline) " in *' piano.debug=1 '*) ;; *) die "native display requires the debug boot";; esac

piano_display_handoff() (
    set -eu
    B=/usr/libexec/piano/busybox
    r=$(uname -r)
    [ "$r" = "$(cat /etc/piano-native-display/release)" ] || exit 1
    [ -d /sysroot/usr/lib/modules ] || exit 1
    [ ! -L /sysroot/usr/lib/modules ] || exit 1
    [ -x /sysroot/usr/sbin/sshd ] || exit 1
    [ -f /sysroot/usr/lib/piano/devmode/sshd_config ] || exit 1
    # Keep the complete tree in /run across switch-root. The bind mount hides
    # the installed tree for this boot only; it does not create files on disk.
    "$B" mkdir -p /run/piano-native-display /run/systemd/system || exit 1
    # Explicit RAM-only payload: none of these console files are installed on disk.
    "$B" touch /run/piano-ram-debug || exit 1
    "$B" cp /usr/libexec/piano/debug-console /usr/libexec/piano/debug-shell /run/piano-native-display/ || exit 1
    "$B" cp /usr/libexec/piano/debug-timeout /run/piano-native-display/debug-timeout || exit 1
    "$B" cp -a /usr/lib/modules /run/piano-native-display/modules || exit 1
    (cd /run/piano-native-display/modules && "$B" sha256sum -c /etc/piano-native-display/modules.sha256) \
        > /run/piano-native-display/modules-verified.txt || exit 1
    "$B" mount -o bind /run/piano-native-display/modules /sysroot/usr/lib/modules || exit 1
    "$B" mount -o remount,bind,ro /sysroot/usr/lib/modules || exit 1
    # Iris firmware is requested on open, which root services may do before
    # the decoder test starts. Keep its locked bytes available across pivot.
    if [ -f /etc/piano-native-display/iris-firmware.sha256 ]; then
        fw=/run/piano-native-display/firmware
        "$B" mkdir -p "$fw/qcom/sm8750" || exit 1
        "$B" cp /usr/lib/firmware/qcom/sm8750/vpu35_4v.mbn "$fw/qcom/sm8750/" || exit 1
        (cd "$fw" && "$B" sha256sum -c /etc/piano-native-display/iris-firmware.sha256) || exit 1
        printf '%s\n' "$fw" > /sys/module/firmware_class/parameters/path || exit 1
        echo 'PIANO_IRIS_FIRMWARE_HANDOFF verified=sha256 location=RAM' > /dev/kmsg
    fi
    "$B" cp -a /etc/piano-native-display/systemd/. /run/systemd/system/ || exit 1
    "$B" cp /etc/piano-native-display/manifest.json /run/piano-native-display/ || exit 1
    echo "PIANO_NATIVE_DISPLAY_HANDOFF release=$r modules=complete root=bind-readonly" > /dev/kmsg
)

if ! piano_display_handoff; then
    # dracut ignores this service's exit status, and asynchronous job
    # cancellation can race switch-root. Keep pre-pivot running until the
    # existing helper reboots at the original kernel-uptime deadline.
    warn "native display module/debug handoff failed; waiting for bounded return"
    if ! /usr/libexec/piano/debug-timeout; then
        warn "native display bounded reboot helper failed"
    fi
    # A successful reboot never returns. Refuse the root if recovery itself
    # failed; dracut's fail-closed poweroff then needs operator recovery.
    die "native display reboot returned; refusing unverified module handoff"
fi
