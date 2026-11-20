# Ubuntu integration inputs

## Power-button lock packages

The lock action uses the two Ubuntu source packages documented in
[gnome-settings-daemon](gnome-settings-daemon/README.zh-CN.md) and
[gnome-control-center](gnome-control-center/README.zh-CN.md). Build them with
Ubuntu's `dpkg-buildpackage`, GSD first, preserving the source locks, patch
licences and corresponding source for binary redistribution. The four runtime
binaries are pinned by [power-button-lock.SHA256SUMS](power-button-lock.SHA256SUMS):
GSD/common `50.0-1ubuntu1+piano2`, control-center/data `1:50.3-0ubuntu0.2+piano2`.
Development, debug and build-environment files are not release payloads.

Before constructing an image, verify the supplied binaries, with `REPO` pointing
to this checkout and `PATCH_DEBS` to their explicitly supplied directory:

```sh
(cd "$PATCH_DEBS" && sha256sum -c "$REPO/ubuntu/power-button-lock.SHA256SUMS")
```

Pass these four files to `tools/build-rootfs-image.py` as separate `--deb`
arguments, together with the device packages. The existing explicit-deb path
records each package name/version/architecture/hash as `source: piano` (meaning
release-supplied, including rebuilt Ubuntu packages). APT distributes the project builds from the source configured by piano-archive-keyring.
The desktop package depends on the matching versions of all four runtime packages.

**Security updates to these four packages are also blocked.** On each Ubuntu
update, authenticate the new sources and update both source locks under
`ubuntu/gnome-settings-daemon` and `ubuntu/gnome-control-center`; rebase the
minimal patches and changelog, build GSD then control-center in Ubuntu ARM64,
run their tests, update the binary hashes and desktop version dependencies, and
ship the complete matched set after device regression. Do not silently unpin
an official update whose schema lacks `lock`.

After upstream merges the functionality or Ubuntu ships an equivalent version,
verify enum/schema and control-center compatibility and device short/long press,
locked-screen wake, suspend/resume and lock-failure behavior. Then retire the project builds and their exact desktop dependencies together.

## Brightness slider (gnome-shell)

[gnome-shell](gnome-shell/README.zh-CN.md) carries upstream `29c61c40` so that the brightness
slider survives logout and reboot instead of being read back from the backlight at every login.
Build it from its source lock as for GSD. The two runtime binaries are pinned by
[gnome-shell/SHA256SUMS](gnome-shell/SHA256SUMS) and the desktop package depends on their exact
version; `gnome-shell-extension-prefs` of the same build is published in the signed APT source for
systems that have it installed. As with the four packages above, Ubuntu's gnome-shell updates are
held back by the exact dependency: rebase the patch on each Ubuntu update, and retire the package
once Ubuntu ships gnome-shell 51 or a 50.x with the upstream change.

## 宿主 Mesa 与发布批次

宿主 Mesa 的发行版打包基线、快照和认证链见
[mesa/ubuntu/source.json](../mesa/ubuntu/source.json)。使用 Canonical 的 resolute
回移包，保留 GLVND 与完整 arm64 驱动集；六个设备补丁由共用源码锁校验。
Ubuntu 原样 libdrm 二进制随批次保全，不加入 Mesa 的精确版本策略。

根镜像安装运行时子集。完整同源包通过签名 APT 源提供，见 [升级指南](../docs/UPGRADE.md)。
