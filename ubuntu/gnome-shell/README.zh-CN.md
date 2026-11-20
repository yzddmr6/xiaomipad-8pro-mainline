# gnome-shell：按显示器保存和恢复亮度滑块

固定 Ubuntu 26.04 源包 `50.1-0ubuntu1.5`（resolute-updates / resolute-security 的当前版本，含
CVE-2026-91786 的安全修复；已发布的系统经 apt 升级后装的就是它），本地版本 `50.1-0ubuntu1.5+piano2`。
源包已认证；按 Ubuntu 发行版流程构建 ARM64 软件包。补丁按原文件 GPL-2.0-or-later 分发，许可见
[COPYING](COPYING)，不适用集成仓的 MIT 许可。

## 行为与来源

GNOME Shell 50 打开自动亮度时，把屏幕亮度定为“自动亮度目标 + 滑块 − 0.5”，而滑块在每次登录时
从当时的背光读回。登录界面保留上次会话的亮度，所以每次登录，亮度都会在上次的基础上再偏一次
（目标高于滑块中点就越来越亮，低于就越来越暗）。

[brightness-persist.patch](brightness-persist.patch) 是上游提交
[`29c61c40`](https://gitlab.gnome.org/GNOME/gnome-shell/-/commit/29c61c40370cbfbeec1684ebf003104b20374bb4)
（Sebastian Wick，[MR !4006](https://gitlab.gnome.org/GNOME/gnome-shell/-/merge_requests/4006)，
GNOME Shell 51）的回移：按显示器（厂商、型号、序列号，EDID 不可靠时再加接口名）把滑块值存进
Shell 的持久状态 `~/.local/share/gnome-shell/brightnesses`，登录时恢复；没有存值时照旧从背光读回。
回移只改了一处：mutter 50 没有 `meta_monitor_get_color_mode_string()`，存值键里的色彩模式字段留空。

[brightness-auto-first-run.patch](brightness-auto-first-run.patch) 是本项目的补丁，跟在回移后面：
一块屏还没有存值、用户也没拖过滑块时，滑块取的是当时的绝对背光；自动亮度接管这块屏时，把滑块放到中点
0.5，不把那个背光当成偏移存下来（否则升级后第一次登录，停在 5% 的背光会变成一直偏暗 45 个百分点）。
登录界面照 50.x 的做法跟着当时的背光走，不存也不恢复。

[brightness-no-edid.patch](brightness-no-edid.patch) 也是本项目的补丁：没有 EDID 的屏（两台平板的 DSI 屏）
没有厂商、型号和序列号，mutter 给的是 null，而 GVariant 的字符串不能是 null，存值时报错，什么都存不下来；
缺的字段按空串存，接口名照样能把屏区分开。

退出条件：Ubuntu 换到 gnome-shell 51 或以上，或者 Ubuntu 把 MR !4006 回移到 50.x 时，删掉回移补丁；本项目的
补丁分别等上游对“没存过值时自动亮度从哪里开始”有了等价处理、能存没有 EDID 的屏的亮度时再删。都删掉以后，撤掉本包
和桌面集合里的精确依赖。
在此之前，精确依赖会挡住 Ubuntu 后续的 gnome-shell 更新：Ubuntu 每发布一个 gnome-shell 更新，就在它上面重编
本包，并在两台平板上复测一次注销、登录和重启后的亮度。

## 认证并准备源包

[source.json](source.json) 锁定签名快照、大小、SHA256、补丁与 changelog。
认证链：Ubuntu Archive InRelease 签名 → Sources.xz SHA256 → dsc/orig/debian SHA256；
dpkg-source 另核 DSC 内的归档校验和。DSC 上传者签名未独立验证。

从集成仓根目录先设置 `assets=$(realpath ubuntu/gnome-shell)`，再进入空目录。
下载锁文件所列的 InRelease、Sources.xz 和 files（按各 URL），然后执行：

```sh
gpgv --keyring /usr/share/keyrings/ubuntu-archive-keyring.gpg InRelease
# assets 指向本目录；以下检查认证过的索引与归档，不能跳过上面的签名验证。
python3 - "$assets/source.json" <<'VERIFY'
import hashlib, json, lzma, pathlib, sys
lock = json.load(open(sys.argv[1]))
def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
auth = lock['authentication']
assert digest('InRelease') == auth['inrelease_sha256']
assert digest('Sources.xz') == auth['sources_sha256']
index = pathlib.Path('InRelease').read_text()
assert any(line.split() == [digest('Sources.xz'), str(pathlib.Path('Sources.xz').stat().st_size),
                           'main/source/Sources.xz'] for line in index.splitlines())
sources = lzma.decompress(pathlib.Path('Sources.xz').read_bytes()).decode()
stanza = next(s for s in sources.split('\n\n') if s.startswith('Package: gnome-shell\n') and '\nVersion: ' + lock['base_version'] + '\n' in s)
sha_section = stanza.split('Checksums-Sha256:\n', 1)[1].split('\nDirectory:', 1)[0]
for item in lock['files']:
    assert [item['sha256'], str(item['bytes']), item['file']] in [line.split() for line in sha_section.splitlines()]
    assert pathlib.Path(item['file']).stat().st_size == item['bytes']
    assert digest(item['file']) == item['sha256']
for item in lock['patches']:
    assert digest(pathlib.Path(sys.argv[1]).parent / item['file']) == item['sha256']
assert digest(pathlib.Path(sys.argv[1]).parent / 'changelog-entry') == lock['changelog_entry_sha256']
VERIFY
```

## 构建

在干净的 Ubuntu resolute ARM64 环境里构建，构建依赖取 [build-root.json](build-root.json) 固定的快照
（其中的 mutter 是本项目 mutter 包的基础版本 `50.1-0ubuntu2.5`）：

```sh
dpkg-source -x gnome-shell_50.1-0ubuntu1.5.dsc gnome-shell
cd gnome-shell
for p in brightness-persist brightness-auto-first-run brightness-no-edid; do
  cp "$assets/$p.patch" debian/patches/piano-$p.patch
  echo piano-$p.patch >> debian/patches/series
done
{ cat "$assets/changelog-entry"; echo; cat debian/changelog; } > debian/changelog.new && mv debian/changelog.new debian/changelog
sudo apt-get build-dep -y .
DEB_BUILD_OPTIONS=nocheck dpkg-buildpackage -b -us -uc
```

测试套件需要显示服务器，项目构建跳过（`nocheck`）。运行时包 `gnome-shell` 与 `gnome-shell-common`
的摘要见 [SHA256SUMS](SHA256SUMS)，桌面集合按精确版本依赖它们。
