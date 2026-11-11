#!/bin/sh
# SPDX-License-Identifier: MIT
# Shared source: xiaomipad-8pro-mainline/device/boot/switch-common.sh.
# Vendor this file unchanged; callers supply log(), PROG and DEV_DIR.

# Emit each slot claim, retaining malformed claims so callers refuse them.
switch_bootconfig_slots() {
    sed -n '/^[[:space:]]*androidboot\.slot_suffix[[:space:]=]/ {
        s/^[[:space:]]*androidboot\.slot_suffix[[:space:]]*=[[:space:]]*"\{0,1\}\(_[ab]\)"\{0,1\}[[:space:]]*$/\1/
        p
    }' "$1"
}

switch_battery() {
    sb_root=$1
    sb_min=101
    for sb_supply in "$sb_root"/*; do
        [ -d "$sb_supply" ] || continue
        sb_type=$(cat "$sb_supply/type") || return 1
        [ "$sb_type" = Battery ] || continue
        if [ -e "$sb_supply/scope" ]; then
            sb_scope=$(cat "$sb_supply/scope") || return 1
            [ "$sb_scope" != Device ] || continue
        fi
        if [ -e "$sb_supply/present" ]; then
            sb_present=$(cat "$sb_supply/present") || return 1
            [ "$sb_present" = 1 ] || return 1
        fi
        sb_capacity=$(cat "$sb_supply/capacity") || return 1
        case $sb_capacity in ''|*[!0-9]*|????*) return 1 ;; esac
        [ "$sb_capacity" -le 100 ] || return 1
        [ "$sb_capacity" -ge "$sb_min" ] || sb_min=$sb_capacity
    done
    [ "$sb_min" -le 100 ] || return 1
    printf '%s\n' "$sb_min"
}

require_switch_battery() {
    sb_path=/sys/class/power_supply
    [ -z "$DEV_DIR" ] || sb_path=$DEV_DIR/power_supply
    if ! sb_level=$(switch_battery "$sb_path"); then
        die 'Cannot read battery charge. Switching requires at least 40%. 无法读取电量，切换需要至少 40%。'
    fi
    [ "$sb_level" -ge 40 ] ||
        die "Battery $sb_level%; switching requires at least 40%. 电量不足，切换需要至少 40%。"
    log "battery=$sb_level% (minimum 40%)"
}

# Preserve the command status and its stderr both for the caller and the log.
# No pipeline around the command: tee must never hide a failed dd or reboot.
run_logged() {
    sl_dir=/tmp
    [ -d "$sl_dir" ] || sl_dir=/data/local/tmp
    sl_err=$(mktemp "$sl_dir/switch-stderr.XXXXXX") || return 1
    sl_rc=0
    "$@" 2>"$sl_err" || sl_rc=$?
    if [ -s "$sl_err" ]; then
        cat "$sl_err" >&2
        log "$(cat "$sl_err")"
    fi
    rm -f "$sl_err"
    return "$sl_rc"
}

switch_notify_failure() {
    case ${PKEXEC_UID:-} in ''|*[!0-9]*) return 0 ;; esac
    sn_user=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
    [ -n "$sn_user" ] || return 0
    run_logged busctl --user "--machine=$sn_user@.host" call org.freedesktop.Notifications \
        /org/freedesktop/Notifications org.freedesktop.Notifications Notify 'susssasa{sv}i' \
        "$PROG" 0 dialog-error 'System switch failed / 系统切换失败' \
        "$*
The tablet was not restarted. 平板未重启。
Details / 详情: $STORE/switch.log" 0 0 0 || :
}

# Python callers use the same parser without sourcing shell functions.
if [ "${1:-}" = --battery ]; then
    switch_battery "$2"
    exit $?
fi
