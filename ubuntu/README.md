# Ubuntu integration inputs

## Power-button lock packages

The lock action uses the two Ubuntu source packages documented in
[gnome-settings-daemon](gnome-settings-daemon/README.zh-CN.md) and
[gnome-control-center](gnome-control-center/README.zh-CN.md). Build them with
Ubuntu's `dpkg-buildpackage`, GSD first, preserving the source locks, patch
licences and corresponding source for binary redistribution. The four runtime
binaries are pinned by [power-button-lock.SHA256SUMS](power-button-lock.SHA256SUMS):
GSD/common `50.0-1ubuntu1+piano2`, control-center/data `1:50.0-0ubuntu6+piano2`.
Development, debug and build-environment files are not release payloads.

Before constructing an image, verify the supplied binaries, with `REPO` pointing
to this checkout and `PATCH_DEBS` to their explicitly supplied directory:

```sh
(cd "$PATCH_DEBS" && sha256sum -c "$REPO/ubuntu/power-button-lock.SHA256SUMS")
```

Pass these four files to `tools/build-rootfs-image.py` as separate `--deb`
arguments, together with the device packages. The existing explicit-deb path
records each package name/version/architecture/hash as `source: piano` (meaning
release-supplied, including rebuilt Ubuntu packages). `tools/build-release.py`
copies every such input into `debs/` only after comparing its hash with the
root-image manifest; `packages.zip` therefore includes the same four binaries.
Do not add them only to an already built release: the root image must contain
the same versions. No implicit lookup of earlier build output is performed.

`piano-ubuntu-desktop` depends on the exact four versions and installs
`/etc/apt/preferences.d/piano-power-button-lock` as a conffile. The standard
[APT preferences](https://manpages.ubuntu.com/manpages/resolute/man5/apt_preferences.5.html)
policy prefers the exact patched versions and rejects other versions of these
four packages. Exact pins also cover installed local debs absent from an APT
repository; wildcard version pins do not reliably cover that case.
It protects both new images and upgrades without a maintainer script changing
APT's hold selections. Existing explicit holds must be removed when installing
a newer matched release set; `apt-mark showhold` lists them. This pin does not
block updates to other Ubuntu packages.

**Security updates to these four packages are also blocked.** On each Ubuntu
update, authenticate the new sources and update both source locks under
`ubuntu/gnome-settings-daemon` and `ubuntu/gnome-control-center`; rebase the
minimal patches and changelog, build GSD then control-center in Ubuntu ARM64,
run their tests, update the binary hashes, exact APT pins and desktop version dependencies, and
ship the complete matched set after device regression. Do not silently unpin
an official update whose schema lacks `lock`.

After upstream merges the functionality or Ubuntu ships an equivalent version,
verify enum/schema and control-center compatibility and device short/long press,
locked-screen wake, suspend/resume and lock-failure behavior. Then remove the
patches, pins and local version dependencies together. For an earlier official
rollback, first select `suspend` for each user, replace the vendor `lock` default
with `suspend`, remove this pin and any explicit holds, and install the matched
official runtime/schema/data packages; log out and back in. The desktop package's
exact dependencies must also be removed or replaced for that rollback.

## 宿主 Mesa 与发布批次

⟦DRAFT:离线候选与真机迁移门槛⟧

宿主 Mesa 的发行版打包基线、快照和认证链见
[mesa/ubuntu/source.json](../mesa/ubuntu/source.json)。使用 Canonical 的 resolute
回移包，保留 GLVND 与完整 arm64 驱动集；六个设备补丁由共用源码锁校验。
Ubuntu 原样 libdrm 二进制随批次保全，不加入 Mesa 的精确版本策略。

`tools/release-repository.py create` 把整版发布包组成一个本地 APT 仓库。
升级前保留上一批完整包以及已安装同源依赖的精确版本；回退先恢复用户态，
再恢复启动镜像组。完整 Mesa 同源包供以后安装开发包或 OpenCL 使用，根镜像
仅安装依赖需要的子集。仓库和回退说明见 [UPGRADE](../docs/UPGRADE.md)。
