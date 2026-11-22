#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 yzddmr6
# https://github.com/yzddmr6/xiaomipad-8pro-mainline
# Shared source: xiaomipad-8pro-mainline/device/boot/switch-common.sh.
# Vendor this file unchanged; callers supply log(), PROG and STORE.

# Emit each slot claim, retaining malformed claims so callers refuse them.
switch_bootconfig_slots() {
    sed -n '/^[[:space:]]*androidboot\.slot_suffix[[:space:]=]/ {
        s/^[[:space:]]*androidboot\.slot_suffix[[:space:]]*=[[:space:]]*"\{0,1\}\(_[ab]\)"\{0,1\}[[:space:]]*$/\1/
        p
    }' "$1"
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

