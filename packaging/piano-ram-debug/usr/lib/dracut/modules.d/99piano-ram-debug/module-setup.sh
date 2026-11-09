#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Never selected automatically, even when this developer package is installed.
check() { return 255; }
depends() { echo piano; }
install() {
    local payload=/usr/share/piano-ram-debug
    inst_simple "$payload/debug-console" /usr/libexec/piano/debug-console
    inst_simple "$payload/debug-shell" /usr/libexec/piano/debug-shell
    inst_simple "$payload/piano-debug.service" /usr/lib/systemd/system/piano-debug.service
    mkdir -p "$initdir/usr/lib/systemd/system/sysinit.target.wants"
    ln_r /usr/lib/systemd/system/piano-debug.service /usr/lib/systemd/system/sysinit.target.wants/piano-debug.service
}
