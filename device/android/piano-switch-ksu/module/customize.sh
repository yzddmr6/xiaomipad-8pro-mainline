# SPDX-License-Identifier: MIT
# Copyright (c) 2026 yzddmr6
# https://github.com/yzddmr6/xiaomipad-8pro-mainline
# Sourced by the KernelSU-Next module installer.
ui_print "- $(sed -n 's/^name=//p' "$MODPATH/module.prop") $(sed -n 's/^version=//p' "$MODPATH/module.prop")"
ui_print "- Copyright (c) 2026 yzddmr6"
ui_print "- https://github.com/yzddmr6/xiaomipad-8pro-mainline"
[ "$(getprop ro.product.device)" = piano ] || abort "! This module is only for the Xiaomi Pad 8 Pro (piano)"
ui_print "- After the next reboot, check with:"
ui_print "  su -c sh /data/adb/modules/piano-switch/piano-switch.sh --check"
