#!/system/bin/sh
# SPDX-License-Identifier: MIT
# Switch piano from Android to Ubuntu: write the stored Ubuntu boot set into
# active slot A (init_boot_a, vendor_boot_a, boot_a), read every partition
# back, then reboot. POSIX sh and toybox only; no images are bundled.
#
# usage: piano-switch.sh [--check] [--no-reboot]
#   --check      run every check, including the slot occupant check; write nothing
#   --no-reboot  write and verify, but leave the reboot to the user
#
# Exit status: 0 done (or check passed), 1 refused before any write,
# 2 write failed and the Android set was restored, 3 write and restore failed.
#
# Android never writes the Linux filesystem: linux_rootfs is mounted
# ro,noload and refused while its journal needs recovery. The slot must hold
# exactly the stored android/ set; anything else is left alone.

set -u

. "${0%/*}/switch-common.sh"
PROG=piano-switch
DEV_DIR=

: "${PIANO_PATH:=/system/bin}"
PATH=$PIANO_PATH:$PATH
export PATH
: "${PIANO_BYNAME:=/dev/block/by-name}"
: "${PIANO_BOOTCONFIG:=/proc/bootconfig}"
: "${PIANO_MOUNTS:=/proc/mounts}"
: "${PIANO_STATE:=/data/adb/piano-switch}"
: "${PIANO_STORE:=var/lib/piano/switch}"
# Host test harness only: accept regular files as partitions, skip the uid check.
: "${PIANO_SWITCH_TEST:=0}"

NAMES="init_boot vendor_boot boot"
LOG=$PIANO_STATE/switch.log
MNT=$PIANO_STATE/mnt
LOCK=$PIANO_STATE/lock
MOUNTED=0
LOCKED=0
[ "$PIANO_SWITCH_TEST" != 1 ] || DEV_DIR=$PIANO_STATE

log() {
	line="$(date '+%Y-%m-%dT%H:%M:%S%z') $*"
	printf '%s\n' "$line"
	printf '%s\n' "$line" >>"$LOG" 2>/dev/null
}

die() {
	log "REFUSED: $*"
	exit 1
}

cleanup() {
	if [ "$MOUNTED" = 1 ]; then
		umount "$MNT" && MOUNTED=0
	fi
	if [ "$LOCKED" = 1 ]; then
		rmdir "$LOCK" 2>/dev/null
	fi
}

# sha FILE: set H to the SHA-256 of FILE (a whole partition for block devices).
sha() {
	H=$(sha256sum <"$1") || return 1
	H=${H%% *}
	[ ${#H} = 64 ]
}

# node LABEL: set N to /dev/block/by-name/LABEL, which must be a block device.
node() {
	N=$PIANO_BYNAME/$1
	[ -b "$N" ] && return 0
	[ "$PIANO_SWITCH_TEST" = 1 ] && [ -f "$N" ] && return 0
	die "$N is missing or not a block device"
}

# sum_of DIR NAME: set W to the single SHA256SUMS entry for NAME in DIR.
sum_of() {
	W=
	count=0
	while read -r sum_h sum_f; do
		[ "${sum_f#\*}" = "$2" ] || continue
		count=$((count + 1))
		W=$sum_h
	done <"$1/SHA256SUMS"
	[ "$count" = 1 ] || die "$1/SHA256SUMS must list $2 exactly once"
	case $W in
	*[!0-9a-f]*) die "invalid checksum for $2 in $1/SHA256SUMS" ;;
	esac
	[ ${#W} = 64 ] || die "invalid checksum for $2 in $1/SHA256SUMS"
}

# verify_set SET: check SET's images against SHA256SUMS and the partition
# sizes; remember the hashes as SUM_<SET>_<name>.
verify_set() {
	d=$MNT/$PIANO_STORE/$1
	[ -f "$d/SHA256SUMS" ] || die "missing $d/SHA256SUMS"
	for p in $NAMES; do
		f=$d/$p.img
		[ -f "$f" ] || die "missing $f"
		sum_of "$d" "$p.img"
		size=$(wc -c <"$f") || die "cannot read $f"
		eval "want=\$SIZE_$p"
		[ "$((size))" = "$want" ] || die "$f is $((size)) bytes, ${p}_a is $want bytes"
		sha "$f" || die "cannot read $f"
		[ "$H" = "$W" ] || die "$f does not match $d/SHA256SUMS"
		eval "SUM_$1_$p=\$W"
	done
	eval "log \"verified $1 set: init_boot \$SUM_$1_init_boot vendor_boot \$SUM_$1_vendor_boot boot \$SUM_$1_boot\""
}

# read_slot: set CUR_<name> to the full-partition hash of each <name>_a.
read_slot() {
	for p in $NAMES; do
		eval "n=\$NODE_$p"
		sha "$n" || { log "cannot read $n"; return 1; }
		eval "CUR_$p=\$H"
	done
}

# slot_is SET: true if every partition equals SET.
slot_is() {
	for p in $NAMES; do
		eval "[ \"\$CUR_$p\" = \"\$SUM_$1_$p\" ]" || return 1
	done
}

# write_set SET: write init_boot, vendor_boot, boot in that order and read
# each partition back; stop at the first failure.
write_set() {
	for p in $NAMES; do
		eval "n=\$NODE_$p want=\$SUM_$1_$p"
		log "writing $1/$p.img to ${p}_a"
		run_logged dd if="$MNT/$PIANO_STORE/$1/$p.img" of="$n" bs=1048576 conv=notrunc,fsync ||
			{ log "dd failed on ${p}_a"; return 1; }
		sha "$n" || { log "cannot read back ${p}_a"; return 1; }
		[ "$H" = "$want" ] || { log "readback mismatch on ${p}_a: $H"; return 1; }
		log "readback ok ${p}_a"
	done
	read_slot || return 1
	slot_is "$1" || { log "final readback of slot A differs from the $1 set"; return 1; }
}

finish_switch() {
	[ "$CHECK" = 0 ] && [ "$REBOOT" = 1 ] || return 0
	umount "$MNT" || { log 'FAILED: image ready, but unmount failed; not rebooting'; return 1; }
	MOUNTED=0
	sync
	log "rebooting"
	run_logged svc power reboot || {
		log 'FAILED: Ubuntu images are ready, but reboot failed; save your work and reboot manually.'
		return 1
	}
}

CHECK=0
REBOOT=1
for arg in "$@"; do
	case $arg in
	--check) CHECK=1 ;;
	--no-reboot) REBOOT=0 ;;
	*) printf 'usage: %s [--check] [--no-reboot]\n' "$0" >&2; exit 64 ;;
	esac
done

mkdir -p "$PIANO_STATE" || exit 1
trap cleanup 0
log "start: $([ "$CHECK" = 1 ] && echo check || echo switch), uid $(id -u)"

[ "$PIANO_SWITCH_TEST" = 1 ] || [ "$(id -u)" = 0 ] || die "root is required"
device=$(getprop ro.product.device)
[ "$device" = piano ] || die "this is '$device', not piano"
slot=$(switch_bootconfig_slots "$PIANO_BOOTCONFIG")
[ "$slot" = _a ] || die "active slot is not _a (bootconfig: ${slot:-unreadable})"
require_switch_battery

mkdir "$LOCK" 2>/dev/null || die "another switch is running (or remove a stale $LOCK)"
LOCKED=1

for p in $NAMES; do
	node "${p}_a"
	eval "NODE_$p=\$N"
	size=$(blockdev --getsize64 "$N") || die "cannot read the size of $N"
	eval "SIZE_$p=\$size"
done

node linux_rootfs
ROOT=$N
root_dev=$(readlink -f "$ROOT")
while read -r src dst rest; do
	case $src in "$root_dev" | "$ROOT") die "linux_rootfs is already mounted on $dst" ;; esac
	[ "$dst" = "$MNT" ] && die "$MNT is already a mount point"
done <"$PIANO_MOUNTS"

# ext4 superblock at byte 1024: s_magic (0xef53) at +0x38, s_feature_incompat at +0x60.
set -- $(od -A n -t u1 -j 1080 -N 2 "$ROOT")
[ $# = 2 ] && [ $(($1 + $2 * 256)) = 61267 ] || die "linux_rootfs is not ext4"
set -- $(od -A n -t u1 -j 1120 -N 1 "$ROOT")
[ $# = 1 ] || die "cannot read the linux_rootfs superblock"
[ $(($1 & 4)) = 0 ] || die "linux_rootfs journal needs recovery: boot Ubuntu and shut it down cleanly"

mkdir -p "$MNT" || die "cannot create $MNT"
mount -t ext4 -o ro,noload "$ROOT" "$MNT" || die "cannot mount linux_rootfs read-only"
MOUNTED=1

verify_set ubuntu
verify_set android
read_slot || die "cannot read slot A"
log "slot A: init_boot $CUR_init_boot vendor_boot $CUR_vendor_boot boot $CUR_boot"
if slot_is ubuntu; then
	log "slot A already holds the ubuntu set; nothing written"
	finish_switch
	exit $?
fi
slot_is android || die "slot A does not hold the stored android set; boot Ubuntu and run 'piano-boot-switch import-android' (or refresh /$PIANO_STORE/android) before switching from Android"
if [ "$CHECK" = 1 ]; then
	log "CHECK OK: slot A holds the android set and the ubuntu set is ready; nothing written"
	exit 0
fi

trap '' HUP INT TERM
for p in $NAMES; do
	eval "n=\$NODE_$p"
	if [ -b "$n" ] && [ "$(blockdev --getro "$n")" != 0 ]; then
		blockdev --setrw "$n" || die "cannot make ${p}_a writable"
	fi
done
if ! write_set ubuntu; then
	log "restoring the android set"
	restore_ok=1
	for restore_part in $NAMES; do
		eval "restore_node=\$NODE_$restore_part restore_want=\$SUM_android_$restore_part"
		run_logged dd if="$MNT/$PIANO_STORE/android/$restore_part.img" of="$restore_node" bs=1048576 conv=notrunc,fsync || restore_ok=0
		if ! sha "$restore_node" || [ "$H" != "$restore_want" ]; then restore_ok=0; fi
	done
	if [ "$restore_ok" = 1 ]; then
		log "FAILED: slot A holds the android set again; not rebooting"
		exit 2
	fi
	log "FAILED: slot A is mixed; do not reboot. Retry, or restore android-restore-ksu with fastboot"
	exit 3
fi

log "switched: slot A holds the ubuntu set"
finish_switch
exit $?
