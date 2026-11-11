#!/bin/sh
# SPDX-License-Identifier: MIT
# Host tests for module/piano-switch.sh. Partitions are regular files behind a
# fake by-name directory; linux_rootfs is a real ext4 image made with
# mkfs.ext4 -d and "mounted" by the debugfs-based shim in shims/.
#
# usage: tests/run-tests.sh [SHELL [APPLET_DIR]]
#   SHELL       shell that runs piano-switch.sh (default: sh), e.g. mksh
#   APPLET_DIR  directory of toybox applet links put ahead of the host tools
# busybox ash is not a useful SHELL here: it runs its own applets ahead of the
# shims in PATH. On the device action.sh runs the script with /system/bin/sh.
set -eu

HERE=$(cd "${0%/*}" && pwd)
SCRIPT=$HERE/../module/piano-switch.sh
TEST_SH=${1:-sh}
APPLETS=${2:-}
PATH=$PATH:/usr/sbin:/sbin
command -v mkfs.ext4 >/dev/null && command -v debugfs >/dev/null ||
	{ echo "mkfs.ext4 and debugfs are required" >&2; exit 1; }
if [ -x "$APPLETS/dd" ]; then REAL_DD=$APPLETS/dd; else REAL_DD=$(command -v dd); fi
export REAL_DD

T=$(mktemp -d)
mkdir "$T/module"
cp "$SCRIPT" "$T/module/"
cp "$HERE/../../../boot/switch-common.sh" "$T/module/"
SCRIPT=$T/module/piano-switch.sh
trap 'chmod -R u+w "$T" 2>/dev/null; rm -rf "$T"' 0
NAMES="init_boot vendor_boot boot"

for set in android ubuntu; do
	mkdir -p "$T/base/$set"
	for p in $NAMES; do
		case $p in init_boot) mb=1 ;; vendor_boot) mb=3 ;; boot) mb=2 ;; esac
		head -c $((mb * 1048576)) /dev/urandom >"$T/base/$set/$p.img"
	done
	(cd "$T/base/$set" && sha256sum init_boot.img vendor_boot.img boot.img >SHA256SUMS)
done

PASS=0
FAIL=0
N=0
STORE=src/var/lib/piano/switch

# setup [SLOT_SET]: fresh case directory; slot A holds SLOT_SET (default android).
setup() {
	N=$((N + 1))
	W=$T/case$N
	mkdir -p "$W/byname" "$W/state/power_supply/battery" "$W/$STORE"
	printf 'Battery\n' > "$W/state/power_supply/battery/type"
	printf '80\n' > "$W/state/power_supply/battery/capacity"
	cp -r "$T/base/android" "$T/base/ubuntu" "$W/$STORE/"
	for p in $NAMES; do
		cp "$T/base/${1:-android}/$p.img" "$W/${p}_a"
		ln -s "../${p}_a" "$W/byname/${p}_a"
	done
	printf 'androidboot.slot_suffix = "_a"\nandroidboot.serialno = "test"\n' >"$W/bootconfig"
	: >"$W/mounts"
	: >"$W/shim.log"
}

mkroot() {
	mkfs.ext4 -q -F -d "$W/src" -L linux_rootfs "$W/linux_rootfs" 32M >/dev/null 2>&1
	ln -sf ../linux_rootfs "$W/byname/linux_rootfs"
}

# run [VAR=value ...] -- [script arguments]
run() {
	vars=
	while [ $# -gt 0 ] && [ "$1" != -- ]; do vars="$vars $1"; shift; done
	[ $# -gt 0 ] && shift
	set +e
	# shellcheck disable=SC2086
	env PIANO_SWITCH_TEST=1 PIANO_PATH="$HERE/shims${APPLETS:+:$APPLETS}" \
		PIANO_BYNAME="$W/byname" PIANO_BOOTCONFIG="$W/bootconfig" PIANO_MOUNTS="$W/mounts" \
		PIANO_STATE="$W/state" SHIM_LOG="$W/shim.log" FAIL_DD_COUNTER="$W/dd.count" \
		$vars $TEST_SH "$SCRIPT" "$@" >"$W/out" 2>&1
	RC=$?
	set -e
}

slot() {
	s=
	for set in android ubuntu; do
		same=1
		for p in $NAMES; do cmp -s "$W/${p}_a" "$T/base/$set/$p.img" || same=0; done
		[ $same = 1 ] && s=$set
	done
	echo "${s:-mixed}"
}

CASE=
ERR=
check() { # check DESCRIPTION COMMAND...
	d=$1
	shift
	"$@" || ERR="$ERR; $d"
}
has() { grep -qF -- "$1" "$W/out"; }
log_has() { grep -qF -- "$1" "$W/shim.log"; }
no_writes() { ! grep -q '^dd ' "$W/shim.log"; }
rebooted() { grep -qx 'svc power reboot' "$W/shim.log"; }
clean() { [ ! -s "$W/mounts" ] && [ ! -e "$W/state/lock" ] && [ ! -e "$W/state/mnt/var" ]; }

begin() { CASE=$1; ERR=; }
end() {
	if [ -z "$ERR" ]; then
		PASS=$((PASS + 1))
		echo "ok   $CASE"
	else
		FAIL=$((FAIL + 1))
		echo "FAIL $CASE (rc $RC$ERR)"
		sed 's/^/     | /' "$W/out"
	fi
}

# refused CASE RC TEXT: common assertions for a refusal before any write.
refused() {
	check "rc $2" [ "$RC" = "$2" ]
	check "message '$3'" has "$3"
	check "slot unchanged" [ "$(slot)" = android ]
	check "no writes" no_writes
	check "no reboot" eval '! rebooted'
	check "cleaned up" clean
}

begin "check mode passes without writing"
setup; mkroot; run -- --check
check "rc 0" [ "$RC" = 0 ]
check "CHECK OK" has "CHECK OK"
check "slot still android" [ "$(slot)" = android ]
check "no writes" no_writes
check "no reboot" eval '! rebooted'
check "mounted ro,noload" log_has "mount -t ext4 -o ro,noload $W/byname/linux_rootfs $W/state/mnt"
check "unmounted" log_has "umount $W/state/mnt"
check "cleaned up" clean
check "log written" grep -qF "CHECK OK" "$W/state/switch.log"
end

begin "switch writes init_boot, vendor_boot, boot, verifies and reboots"
setup; mkroot; run --
check "rc 0" [ "$RC" = 0 ]
check "slot ubuntu" [ "$(slot)" = ubuntu ]
check "reboot" rebooted
check "order" [ "$(sed -n 's/^dd .*of=[^ ]*\/\([a-z_]*\)_a .*/\1/p' "$W/shim.log" | tr '\n' ' ')" = "init_boot vendor_boot boot " ]
check "unmounted before reboot" [ "$(grep -n -e '^umount' -e '^svc' "$W/shim.log" | cut -d: -f2 | cut -c1-3 | tr '\n' ' ')" = "umo svc " ]
check "readback logged" has "readback ok boot_a"
check "cleaned up" clean
end

begin "--no-reboot writes and verifies only"
setup; mkroot; run -- --no-reboot
check "rc 0" [ "$RC" = 0 ]
check "slot ubuntu" [ "$(slot)" = ubuntu ]
check "no reboot" eval '! rebooted'
check "cleaned up" clean
end

begin "slot already ubuntu: nothing written, still reboots"
setup ubuntu; mkroot; run --
check "rc 0" [ "$RC" = 0 ]
check "message" has "already holds the ubuntu set"
check "no writes" no_writes
check "reboot" rebooted
check "cleaned up" clean
end

begin "refuses slot _b"
setup; mkroot; printf 'androidboot.slot_suffix = "_b"\n' >"$W/bootconfig"; run --
refused "$CASE" 1 'active slot is not _a'
check "not mounted" eval '! log_has mount'
end

begin "refuses without bootconfig"
setup; mkroot; rm "$W/bootconfig"; run --
refused "$CASE" 1 'bootconfig: unreadable'
end

begin "refuses another device"
setup; mkroot; run FAKE_DEVICE=marble -- --check
refused "$CASE" 1 "this is 'marble', not piano"
end

begin "refuses without linux_rootfs"
setup; run --
refused "$CASE" 1 'linux_rootfs is missing'
end

begin "refuses a non-ext4 linux_rootfs"
setup; head -c 4194304 /dev/urandom >"$W/linux_rootfs"; ln -s ../linux_rootfs "$W/byname/linux_rootfs"; run --
refused "$CASE" 1 'linux_rootfs is not ext4'
end

begin "refuses needs_recovery without mounting"
setup; mkroot; debugfs -w -R 'feature needs_recovery' "$W/linux_rootfs" >/dev/null 2>&1; run --
refused "$CASE" 1 'journal needs recovery'
check "not mounted" eval '! log_has mount'
end

begin "refuses an already mounted linux_rootfs"
setup; mkroot; printf '%s /mnt/x ext4 rw 0 0\n' "$(readlink -f "$W/linux_rootfs")" >"$W/mounts"; run --
check "rc 1" [ "$RC" = 1 ]
check "message" has 'already mounted on /mnt/x'
check "no writes" no_writes
end

begin "refuses a linux_rootfs mounted through its by-name path"
setup; mkroot; printf '%s /mnt/y ext4 ro 0 0\n' "$W/byname/linux_rootfs" >"$W/mounts"; run -- --check
check "rc 1" [ "$RC" = 1 ]
check "message" has 'already mounted on /mnt/y'
check "no writes" no_writes
end

begin "refuses when the mount fails"
setup; mkroot; run FAKE_MOUNT_FAIL=1 --
refused "$CASE" 1 'cannot mount linux_rootfs read-only'
end

begin "refuses an ubuntu image that does not match SHA256SUMS"
setup; printf 'X' | dd of="$W/$STORE/ubuntu/boot.img" bs=1 seek=10 conv=notrunc 2>/dev/null; mkroot; run --
refused "$CASE" 1 'ubuntu/boot.img does not match'
end

begin "refuses an ubuntu image smaller than the partition"
setup; head -c 2097152 /dev/urandom >"$W/$STORE/ubuntu/vendor_boot.img"
(cd "$W/$STORE/ubuntu" && sha256sum init_boot.img vendor_boot.img boot.img >SHA256SUMS); mkroot; run --
refused "$CASE" 1 'vendor_boot.img is 2097152 bytes, vendor_boot_a is 3145728 bytes'
end

begin "refuses a SHA256SUMS without an entry"
setup; grep -v ' boot.img$' "$T/base/ubuntu/SHA256SUMS" >"$W/$STORE/ubuntu/SHA256SUMS"; mkroot; run --
refused "$CASE" 1 'must list boot.img exactly once'
end

begin "refuses a SHA256SUMS with a duplicate entry"
setup; grep ' init_boot.img$' "$T/base/android/SHA256SUMS" >>"$W/$STORE/android/SHA256SUMS"; mkroot; run --
refused "$CASE" 1 'must list init_boot.img exactly once'
end

begin "refuses a store without the android set"
setup; rm -r "$W/$STORE/android"; mkroot; run --
refused "$CASE" 1 'android/SHA256SUMS'
end

begin "refuses an unknown slot occupant with the import-android hint"
setup; mkroot; printf 'X' | dd of="$W/boot_a" bs=1 seek=10 conv=notrunc 2>/dev/null; cp "$W/boot_a" "$W/boot_a.before"; run -- --check
check "rc 1" [ "$RC" = 1 ]
check "hint" has "import-android"
check "no writes" no_writes
check "boot_a untouched" cmp -s "$W/boot_a" "$W/boot_a.before"
check "cleaned up" clean
end

begin "refuses while another switch holds the lock"
setup; mkroot; mkdir "$W/state/lock"; run --
check "rc 1" [ "$RC" = 1 ]
check "message" has 'another switch is running'
check "foreign lock kept" [ -d "$W/state/lock" ]
check "no writes" no_writes
end

begin "dd failure on vendor_boot_a restores the android set"
setup; mkroot; run FAIL_DD_ON=vendor_boot_a FAIL_DD_MODE=fail --
check "rc 2" [ "$RC" = 2 ]
check "slot android" [ "$(slot)" = android ]
check "message" has "slot A holds the android set again"
check "no reboot" eval '! rebooted'
check "cleaned up" clean
end

begin "readback mismatch on boot_a restores the android set"
setup; mkroot; run FAIL_DD_ON=boot_a FAIL_DD_MODE=corrupt --
check "rc 2" [ "$RC" = 2 ]
check "detected" has "readback mismatch on boot_a"
check "slot android" [ "$(slot)" = android ]
check "no reboot" eval '! rebooted'
end

begin "failed restore reports a mixed slot and does not reboot"
setup; mkroot; run FAIL_DD_ON=vendor_boot_a FAIL_DD_MODE=fail FAIL_DD_TIMES=2 --
check "rc 3" [ "$RC" = 3 ]
check "message" has "do not reboot"
check "slot mixed" [ "$(slot)" = mixed ]
check "no reboot" eval '! rebooted'
check "cleaned up" clean
end

begin "rejects unknown arguments"
setup; mkroot; run -- --force
check "rc 64" [ "$RC" = 64 ]
check "no writes" no_writes
end

for level in 39 unknown missing; do
	begin "refuses battery $level before any write"
	setup; mkroot
	if [ "$level" = missing ]; then rm "$W/state/power_supply/battery/capacity"
	else printf '%s\n' "$level" > "$W/state/power_supply/battery/capacity"; fi
	run --
	refused "$CASE" 1 '40%'
	end
done
begin "battery 40 permits switching"
setup; mkroot; echo 40 > "$W/state/power_supply/battery/capacity"; run --
check 'rc 0' [ "$RC" = 0 ]; check 'reboot' rebooted
end
begin "conflicting bootconfig refuses slot"
setup; mkroot; printf 'androidboot.slot_suffix = "_b"\nandroidboot.slot_suffix = "_a"\n' > "$W/bootconfig"; run --
refused "$CASE" 1 'active slot is not _a'
end
begin "reboot failure is not success and keeps stderr"
setup ubuntu; mkroot; run FAIL_REBOOT=1 --
check 'rc nonzero' [ "$RC" != 0 ]
check 'diagnostic retained' has 'INJECTED reboot failure'
check 'diagnostic logged' grep -qF 'INJECTED reboot failure' "$W/state/switch.log"
check 'no partition rewrite' no_writes
end
begin "already-target check and no-reboot suppress reboot"
setup ubuntu; mkroot; run -- --check
check 'rc 0' [ "$RC" = 0 ]; check 'no reboot' eval '! rebooted'
run -- --no-reboot
check 'rc 0' [ "$RC" = 0 ]; check 'no reboot' eval '! rebooted'
end

echo "$PASS passed, $FAIL failed ($TEST_SH${APPLETS:+, applets $APPLETS})"
[ "$FAIL" = 0 ]
