#!/bin/sh
# SPDX-License-Identifier: MIT
# One implementation of the release package/image policy for both backends.
# The wrapper supplies IN, OUT, T, RELEASE, ROOT_PARTLABEL and target_mounts.
step() { echo "PIANO_ROOTFS_STEP $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
fail() { echo "PIANO_ROOTFS_FAIL $*"; exit 1; }

check_userspace_removals() {
	# The release sensor proxy has always replaced Ubuntu's provider. Permit
	# that removal only when its replacement is included in this transaction.
	awk -v replace_iio="${2:-0}" '
		$1 == "Remv" {
			name=$2; sub(/:arm64$/, "", name)
			if (name != "power-profiles-daemon" &&
			    !(replace_iio == 1 && name == "iio-sensor-proxy")) bad=1
		}
		END { exit bad }' "$1"
}

cache_local_debs() {
	# --no-download also skips file: acquisition. Supply APT's canonical cache
	# names first, including escaped epochs, so dpkg receives absolute paths.
	install -d "$1/var/cache/apt/archives"
	for deb in "$1"/var/cache/piano-install/*.deb; do
		name=$(dpkg-deb --show --showformat='${Package}_${Version}_${Architecture}.deb' "$deb" | sed 's/:/%3a/g')
		ln -f "$deb" "$1/var/cache/apt/archives/$name"
	done
}

in_target() {
	if [ "$T" = / ]; then "$@"; else chroot "$T" "$@"; fi
}

# The chroot backend discards free blocks from the host after the steps instead
# (qemu-user does not pass the FITRIM ioctl through).
trim_target() { fstrim -v "$T"; }

# PHASES (params.sh) selects the work: "base kernel" for a whole build, "base"
# to stop after the distribution kernels are purged (the cacheable state that does
# not depend on the piano kernel), "kernel" to continue from such a base.
rootfs_steps() {
case " ${PHASES:-base kernel} " in
*" base "*) rootfs_base_steps ;;
*) step chroot mounts; target_mounts ;;
esac
case " ${PHASES:-base kernel} " in
*" kernel "*) rootfs_kernel_steps ;;
*) echo PIANO_ROOTFS_BASE_DONE ;;
esac
}

rootfs_base_steps() {
[ -z "$(ls -A "$T/home")" ] || fail 'the base has entries in /home'
[ ! -e "$T/etc/fstab" ] || fail 'the base already has /etc/fstab'
[ ! -e "$T/etc/piano" ] || fail 'the base already has /etc/piano'

step image policy
# The same policy as build-ubuntu-ext4.py: official Ports sources, no CD-ROM
# source, cloud-init disabled.
install -m 644 "$IN/files/etc/apt/sources.list.d/ubuntu.sources" "$T/etc/apt/sources.list.d/ubuntu.sources"
[ -f "$T/etc/apt/sources.list.d/cdrom.sources" ] || fail 'no CD-ROM source to remove'
rm "$T/etc/apt/sources.list.d/cdrom.sources"
install -D -m 644 "$IN/firefox/mozilla.sources" "$T/etc/apt/sources.list.d/mozilla.sources"
install -D -m 644 "$IN/firefox/mozilla.pref" "$T/etc/apt/preferences.d/mozilla"
install -D -m 644 "$IN/firefox/packages.mozilla.org.asc" "$T/etc/apt/keyrings/packages.mozilla.org.asc"
# Keep the official preseed and installed snap state intact. The desktop
# package's oneshot removes Firefox only after snapd.seeded on the first boot.
: >"$T/etc/cloud/cloud-init.disabled"
chmod 644 "$T/etc/cloud/cloud-init.disabled"
# A device name instead of the base's localhost.localdomain.  GNOME Initial
# Setup replaces the static hostname with its "Computer" field (the user name
# unless edited); the pretty hostname stays, and BlueZ prefers it.
install -m 644 "$IN/files/etc/hostname" "$T/etc/hostname"
[ ! -e "$T/etc/machine-info" ] || fail 'the base already has /etc/machine-info'
install -m 644 "$IN/files/etc/machine-info" "$T/etc/machine-info"

step piano layout
# layout.json must exist before the kernel package is configured: it enables
# the dracut module 99piano and the zz-piano-boot hooks.
install -d -m 755 "$T/etc/piano"
install -m 600 "$IN/files/etc/piano/layout.json" "$T/etc/piano/layout.json"
install -m 644 "$IN/files/etc/fstab" "$T/etc/fstab"

step chroot mounts
target_mounts

step package policy
[ ! -e "$T/usr/sbin/policy-rc.d" ] || fail 'the base already has policy-rc.d'
printf '#!/bin/sh\nexit 101\n' >"$T/usr/sbin/policy-rc.d"
chmod 755 "$T/usr/sbin/policy-rc.d"
# The kernel postinst runs dracut once; the dracut upgrade's update-initramfs
# trigger would generate the same initrd again.  Standard initramfs-tools
# setting, present only while the packages are installed.
[ ! -e "$T/etc/initramfs-tools/update-initramfs.conf" ] || fail 'update-initramfs.conf already exists'
printf 'update_initramfs=no\n' >"$T/etc/initramfs-tools/update-initramfs.conf"
# Install the userspace packages first: piano-device-support retains the base's
# automatic device-tree-compiler/libfdt1 dependency during autoremove. The
# dracut upgrade trigger is suppressed above; the kernel is in a separate TAR
# directory so its postinst cannot run until the generic/kdump hooks are gone.
step install userspace packages
install -d "$T/var/cache/piano-install"
cp "$IN"/debs/*.deb "$T/var/cache/piano-install/"
if [ -d "$IN/release-repository" ]; then
    batch=$(cat "$IN/release-repository-id")
    install -d "$T/var/lib/piano/releases/$batch"
    cp -a "$IN/release-repository/." "$T/var/lib/piano/releases/$batch/"
    printf 'deb [trusted=yes] file:/var/lib/piano/releases/%s ./\n' "$batch" > "$T/etc/apt/sources.list.d/piano-release.list"
    in_target apt-get -o Dir::Etc::sourcelist=piano-release.list -o Dir::Etc::sourceparts=- \
        -o APT::Get::List-Cleanup=0 update
    install -d "$T/var/cache/apt/archives"
    for deb in "$T/var/lib/piano/releases/$batch/pool/"*.deb; do
        name=$(dpkg-deb --show --showformat='${Package}_${Version}_${Architecture}.deb' "$deb" | sed 's/:/%3a/g')
        ln -f "$deb" "$T/var/cache/apt/archives/$name"
    done
fi
# APT handles the tuned-ppd Conflicts/Provides transition in one transaction.
# The plan may remove only replaced providers; no package downloads are allowed.
if ls "$T"/var/cache/piano-install/tuned-ppd_*.deb >/dev/null 2>&1; then
	cache_local_debs "$T"
	in_target /usr/bin/env LC_ALL=C /bin/sh -c \
		'apt-get --simulate --no-download --no-install-recommends install /var/cache/piano-install/*.deb' \
		>"$OUT/userspace-plan.txt"
	replace_iio=0
	if ls "$T"/var/cache/piano-install/piano-iio-sensor-proxy_*.deb >/dev/null 2>&1; then
		replace_iio=1
	fi
	check_userspace_removals "$OUT/userspace-plan.txt" "$replace_iio" || \
		fail 'unexpected package removal during provider replacement'
	# The pinned Mozilla deb replaces Ubuntu's epoch-versioned Snap transition
	# package. APT calls that a downgrade, as in the download-only resolver.
	in_target /usr/bin/env SYSTEMD_OFFLINE=1 DEBIAN_FRONTEND=noninteractive \
		/bin/sh -c 'apt-get --yes --allow-downgrades --no-download --no-install-recommends install /var/cache/piano-install/*.deb' </dev/null
else
in_target /usr/bin/env SYSTEMD_OFFLINE=1 DEBIAN_FRONTEND=noninteractive \
	/bin/sh -c 'dpkg --install /var/cache/piano-install/*.deb' </dev/null
fi
audit=$(in_target dpkg --audit)
[ -z "$audit" ] || fail "dpkg --audit: $audit"
rm "$T"/var/cache/piano-install/*.deb

step distribution kernels
# Only the piano kernel boots this tablet.  The base's generic HWE kernel and
# kdump (no crashkernel) would make every update download, rebuild initrds
# for and keep a kernel that never boots.  Purge their packages with what
# apt then no longer needs, keeping linux-firmware, which is not tied to a
# kernel.  Still within policy-rc.d and update_initramfs=no.
in_target dpkg-query -W -f '${db:Status-Abbrev}|${Package}|${Version}|${Architecture}\n' \
	>"$OUT/dpkg-query-base.txt"
kernels=$(in_target dpkg-query -W -f '${db:Status-Abbrev} ${Package}\n' | awk '
	$1 != "ii" { next }
	$2 ~ /^linux-(generic|image-generic|headers-generic|tools-generic)/ ||
	$2 ~ /^linux-(image|image-unsigned|modules|modules-extra|main-modules-[a-z0-9]+|headers|tools)-[0-9].*-generic$/ ||
	$2 == "kdump-tools" { print $2 }')
[ -n "$kernels" ] || fail 'no distribution kernel packages found'
echo "purging:" $kernels
df -B1 --output=used "$T" | tail -1 >"$OUT/used-bytes-before-purge.txt"
in_target apt-mark manual linux-firmware firefox
# shellcheck disable=SC2086 # one word per package name
in_target /usr/bin/env SYSTEMD_OFFLINE=1 DEBIAN_FRONTEND=noninteractive \
	apt-get --yes --purge --autoremove remove $kernels </dev/null
df -B1 --output=used "$T" | tail -1 >"$OUT/used-bytes-after-purge.txt"
audit=$(in_target dpkg --audit)
[ -z "$audit" ] || fail "dpkg --audit after the purge: $audit"
}

rootfs_kernel_steps() {
step install piano kernel
cp "$IN"/kernel/*.deb "$T/var/cache/piano-install/"
in_target /usr/bin/env SYSTEMD_OFFLINE=1 DEBIAN_FRONTEND=noninteractive \
	/bin/sh -c 'dpkg --install /var/cache/piano-install/*.deb' </dev/null
audit=$(in_target dpkg --audit)
[ -z "$audit" ] || fail "dpkg --audit after the kernel: $audit"
rm -r "$T/var/cache/piano-install"


left=$(in_target dpkg-query -W -f '${db:Status-Abbrev} ${Package}\n' 'linux-image-*' 'linux-modules-*' \
	'linux-headers-*' 'kdump-tools' 2>/dev/null | awk 'substr($1, 2, 1) != "n" && $2 != "linux-image-'"$RELEASE"'" { print $2 }')
[ -z "$left" ] || fail "kernel packages left besides linux-image-$RELEASE: $left"
in_target apt-get --simulate autoremove </dev/null >"$OUT/autoremove-simulate.txt"
! grep '^Remv ' "$OUT/autoremove-simulate.txt" || fail 'apt autoremove would still remove packages'
others=$(cd "$T/boot" && ls | grep -v -x -e "vmlinuz-$RELEASE" -e "initrd.img-$RELEASE" -e "config-$RELEASE" \
	-e "System.map-$RELEASE" -e vmlinuz -e initrd.img -e vmlinuz.old -e initrd.img.old || true)
[ -z "$others" ] || fail "/boot has files of another kernel: $others"
# linux-update-symlinks points the .old links at the remaining kernel.
for link in vmlinuz vmlinuz.old initrd.img initrd.img.old; do
	[ ! -L "$T/boot/$link" ] || [ "$(readlink "$T/boot/$link")" = "${link%.old}-$RELEASE" ] ||
		fail "/boot/$link points to $(readlink "$T/boot/$link")"
done
[ "$(ls "$T/usr/lib/modules")" = "$RELEASE" ] || fail "/usr/lib/modules has another kernel: $(ls "$T/usr/lib/modules")"
rm "$T/usr/sbin/policy-rc.d" "$T/etc/initramfs-tools/update-initramfs.conf"
[ "$(grep -c '^dracut: Generating ' "$OUT/install.log")" = 1 ] || fail 'expected exactly one dracut generation'
grep -Fx "dracut: Generating /boot/initrd.img-$RELEASE" "$OUT/install.log" >/dev/null || fail 'wrong dracut kernel'
[ -f "$T/boot/initrd.img-$RELEASE" ] || fail "no initrd for $RELEASE"

step services
# OpenSSH stays installed for developer mode, which is off; its own socket is
# disabled as on the test unit (docs/AGENT-GUIDE.en.md section 7.1).
systemctl --root="$T" disable ssh.socket ssh.service

step release boot set
mkdir -p "$T/run/piano-release" "$T/var/lib/piano/switch"
cp "$IN/release/boot-profile.json" "$IN/release/boot-signing.pem" "$T/run/piano-release/"
in_target python3 /usr/lib/piano/boot/build-persistent-boot.py \
	--kernel "/boot/vmlinuz-$RELEASE" --kernel-release "$RELEASE" \
	--dtb "/usr/lib/linux-image-$RELEASE/piano-boot.dtb" --initrd "/boot/initrd.img-$RELEASE" \
	--profile /run/piano-release/boot-profile.json --root-partlabel "$ROOT_PARTLABEL" --no-serial \
	--mkbootimg /usr/lib/piano/boot/mkbootimg.py --avbtool /usr/lib/piano/boot/avbtool.py \
	--key /run/piano-release/boot-signing.pem --output /var/lib/piano/switch/ubuntu
rm -r "$T/run/piano-release"
(cd "$T/var/lib/piano/switch/ubuntu" && sha256sum -c SHA256SUMS)

step per-machine state
# Created at the first boot or by developer mode (ssh-keygen -A), never shipped.
rm -f "$T"/etc/ssh/ssh_host_*
if [ -s "$T/etc/machine-id" ]; then
	echo 'NOTE: /etc/machine-id had content; emptied'
	: >"$T/etc/machine-id"
fi
rm -f "$T/var/lib/systemd/random-seed" "$T/var/lib/systemd/credential.secret"
rm -f "$T"/var/cache/apt/*.bin "$T"/var/cache/apt/archives/*.deb
# The base's package indexes are those of the build machine's mirror
# (archive.ubuntu.com), not of the Ports sources installed above; without
# them the first apt run needs `apt update` instead of finding nothing.
find "$T/var/lib/apt/lists" -mindepth 1 -maxdepth 1 -type f ! -name lock -delete
# Discard the free blocks (the purged kernels, dpkg and dracut leftovers keep
# their old content): the host punches them out of the image file
# (discard=unmap), and sparse_image.py writes only zero free blocks as DONT_CARE.
step fstrim
trim_target
rm -rf "$T"/var/log/journal/*

step report
cp "$T/var/log/dpkg.log" "$OUT/dpkg.log"
in_target dpkg-query -W -f '${db:Status-Abbrev}|${Package}|${Version}|${Architecture}\n' >"$OUT/dpkg-query.txt"
for u in piano-devmode.service piano-devmode-ssh.socket ssh.socket ssh.service piano-slot-success.service \
		piano-debug-timeout.service gdm.service; do
	echo "$u $(systemctl --root="$T" is-enabled "$u" 2>/dev/null || true)"
done >"$OUT/units.txt"
mkdir "$OUT/ubuntu-set"
cp "$T"/var/lib/piano/switch/ubuntu/* "$OUT/ubuntu-set/"
(cd "$T" && ls -ln boot etc/piano var/lib/piano/switch var/lib/piano/switch/ubuntu home etc/ssh) >"$OUT/listing.txt"
in_target sh -c 'sha256sum /boot/vmlinuz-'"$RELEASE"' /boot/initrd.img-'"$RELEASE"' /usr/lib/linux-image-'"$RELEASE"'/piano-boot.dtb' >"$OUT/boot-files.sha256"
echo PIANO_ROOTFS_GUEST_DONE
}
