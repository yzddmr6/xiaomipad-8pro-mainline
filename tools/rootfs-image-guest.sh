#!/bin/sh
# SPDX-License-Identifier: MIT
# Guest half of build-rootfs-image.py: runs as root in a disposable QEMU ARM64
# guest whose own root is the read-only pristine Desktop SquashFS.  It applies
# docs/AGENT-GUIDE.en.md section 4.4 to the target ext4 on a second disk: image
# policy, /etc/piano/layout.json and fstab, then dpkg --install in a chroot
# with policy-rc.d and SYSTEMD_OFFLINE=1, so the distribution kernel hooks run
# dracut and the piano hooks inside the target. The base's generic kernels
# and kdump are purged before the piano kernel is installed. It then builds
# the release boot image set into the store and removes per-machine state and the base's package indexes.  The target
# never boots.  Inputs are a tar on the third disk, results a tar on the fourth.
set -eu
IN=/run/piano-inputs
OUT=/run/piano-out
T=/run/piano-target
. "$IN/params.sh"
mkdir -p "$OUT" "$T"
exec >"$OUT/install.log" 2>&1
# The console copy is for following progress; the log travels in the result tar.
tail -n +1 -f "$OUT/install.log" >/dev/console &

mounted=''
finish() {
	status=$?
	set +e
	for m in $mounted; do umount "$m"; done
	echo "PIANO_ROOTFS_EXIT status=$status"
	sync
	# The host reads this tar whether or not the steps passed.
	tar -cf "$result" -C "$OUT" .
	sync
	exit "$status"
}
. "$IN/steps.sh"

target_mounts() {
mount -t proc proc "$T/proc"; mounted="$T/proc $mounted"
mount -t sysfs -o ro sysfs "$T/sys"; mounted="$T/sys $mounted"
mount --bind /dev "$T/dev"; mounted="$T/dev $mounted"
mount --bind /dev/pts "$T/dev/pts"; mounted="$T/dev/pts $mounted"
# Nothing written below /run or /tmp of the target reaches the image.
mount -t tmpfs -o mode=755 tmpfs "$T/run"; mounted="$T/run $mounted"
mount -t tmpfs -o mode=1777 tmpfs "$T/tmp"; mounted="$T/tmp $mounted"
}

# Fail closed on the disk order before anything is written.
for pair in vda:piano-helper vdb:piano-target vdc:piano-inputs vdd:piano-result; do
	dev=${pair%%:*}
	[ "$(cat "/sys/block/$dev/serial")" = "${pair#*:}" ] || fail "/dev/$dev is not ${pair#*:}"
done
result=/dev/vdd
trap finish EXIT

step mount target
mount -t ext4 /dev/vdb "$T"
mounted="$T"
rootfs_steps
