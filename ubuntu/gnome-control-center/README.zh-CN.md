# gnome-control-center：电源键锁屏动作与关于页作者行源包补丁

固定 Ubuntu 26.04 源包 `1:50.3-0ubuntu0.2`，本地版本 `1:50.3-0ubuntu0.2+piano2`。
已完成源包认证与 ARM64 构建；配套运行时包的固定摘要与发布接入见 [Ubuntu 集成](../README.md)。
补丁按原文件 GPL-2.0-or-later 分发，许可见 [COPYING](COPYING)，不适用集成仓的 MIT 许可。

## 行为与来源

`power-button-action` 增加 `lock`，原有枚举 0–3 不变，新增值为 4。
GSD 保持 media-keys 的按键和 handle-power-key inhibitor 所有权。未锁屏时异步调用
Shell 的 `org.gnome.ScreenSaver.Lock`，成功返回后调用 `SetActive(true)`；错误只记录 warning，
不会回退到 suspend。锁定被管理员禁用时不执行动作。已处于锁屏/解锁界面时不再激活屏保，
由 Mutter 输入活动和 gsd-power 原有 user-active watch 唤屏，不调用解锁或挂起。
原有长按、恢复后按键抑制、VM 电源策略均保留。

**接口边界：** Shell 50 的 `SetActive(true)` 本身不设置密码锁；Super+L 使用 `Lock`。
因此不能仅调用 SetActive 并声称安全锁定。无密码账户遵循 Shell 原有策略。
上述请求沿用 Shell 原生动画和 ActiveChanged→gsd-power blank 链，不能保证零动画或指定毫秒内熄屏。
锁屏界面已经亮着时再次按键也仅算输入，不实现亮/灭双向切换。
不新增延时、轮询、重试、GnomeRR 调用、daemon 或自动睡眠策略。

参考 [GSD !448](https://gitlab.gnome.org/GNOME/gnome-settings-daemon/-/merge_requests/448)
（Phoebe Harris，`b80693acdce2931550081b8d27550e19c9eed36d`）的末尾 LOCK enum，
以及 [!320](https://gitlab.gnome.org/GNOME/gnome-settings-daemon/-/merge_requests/320)
（Cédric Bellegarde，`89f1042d9068ba5ea2f51fa846fcac148a490479`）的 blank 设计；
后者仅作设计参考，不导入其 500 ms 限流或旧显示 API。补丁头保留作者和来源。
控制中心复用已有 “Lock Screen” / “锁定屏幕” 翻译，允许 tablet/handset 显示该行；
VM 仍隐藏。控制中心构建及运行时均要求含新 enum/schema 的 GSD 本地版本。

## 关于页作者行

`about-author.patch` 在“设置 → 系统 → 关于”底部加一个“作者”分组：一行显示 yzddmr6 与项目地址，
点击用默认浏览器打开项目主页。Ubuntu 语言包不含这段文字，分组标题按当前语言在中文“作者”与英文
“Author”之间选择。

## 认证并准备源包

[source.json](source.json) 锁定签名快照、大小、SHA256、补丁与 changelog。
认证链：Ubuntu Archive InRelease 签名 → Sources.xz SHA256 → dsc/orig/debian SHA256；
dpkg-source 另核 DSC 内的归档校验和。DSC 上传者签名未独立验证。
必须先用可信 Ubuntu archive keyring 验证 InRelease，而不能只对下载文件互相比 hash。

从集成仓根目录先设置 `assets=$(realpath ubuntu/gnome-control-center)`，再进入空目录。
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
stanza = next(s for s in sources.split('\n\n') if s.startswith('Package: gnome-control-center\n'))
sha_section = stanza.split('Checksums-Sha256:\n', 1)[1].split('\nDirectory:', 1)[0]
for item in lock['files']:
    assert [item['sha256'], str(item['bytes']), item['file']] in [line.split() for line in sha_section.splitlines()]
    assert pathlib.Path(item['file']).stat().st_size == item['bytes']
    assert digest(item['file']) == item['sha256']
for item in lock['patches']:
    assert digest(pathlib.Path(sys.argv[1]).parent / item['file']) == item['sha256']
assert digest(pathlib.Path(sys.argv[1]).parent / 'changelog-entry') == lock['changelog_entry_sha256']
VERIFY
dpkg-source -x gnome-control-center_50.3-0ubuntu0.2.dsc source
cp "$assets/power-button-lock.patch" source/debian/patches/piano-power-button-lock.patch
cp "$assets/about-author.patch" source/debian/patches/piano-about-author.patch
printf '\n%s\n%s\n' piano-power-button-lock.patch piano-about-author.patch >>source/debian/patches/series
patch -d source -p1 <"$assets/packaging-dependencies.patch"
{ cat "$assets/changelog-entry"; printf '\n'; cat source/debian/changelog; } >source/debian/changelog.new
mv source/debian/changelog.new source/debian/changelog
(cd source && dpkg-source --before-build . && dpkg-source -b .)
```

随后在独立 Ubuntu 26.04 ARM64 构建环境安装源包声明的 Build-Depends，运行
`dpkg-buildpackage -b -us -uc`，保留 buildinfo/changes、工具版本和所有 deb 的 SHA256。
[build-root.json](build-root.json) 固定 Ubuntu Base、APT 快照和本地 GSD 构建依赖的摘要；
x86 主机使用 qemu-user 时设置 `QEMU_CPU=max,pauth-impdef=on`。
先构建并安装 GSD 的 dev/common 包到构建环境，再构建控制中心。不要用 `-d` 绕过依赖检查。
设备包打包器不编译 GNOME 源包；使用上述发行版构建流程，发布时显式传入配套运行时 deb。

安装后以登录用户设置 `gsettings set org.gnome.settings-daemon.plugins.power power-button-action lock`，
或在电源面板选择“锁定屏幕”。须安装配套 GSD common/schema 与控制中心 data 包并重新登录。
恢复官方 GSD 前先把此设置恢复为 `suspend`，避免旧 schema 不认识 `lock`。

退出条件：上游合并或所选 Ubuntu 源包提供等价实现，且本机短按、长按、已灭屏唤屏、睡眠恢复、
失败安全行为及卸载回退通过后，撤去电源键补丁和依赖版本覆盖。自动睡眠仍是独立策略。作者行补丁随本地控制中心包保留。

关于页的作者行由设备集成包提供 `/usr/share/gnome-control-center/port-author.ini`：

```ini
[Author]
Name=yzddmr6
URL=https://github.com/yzddmr6/xiaomipad-8pro-mainline
```

共享二进制不包含设备型号或项目链接。未提供有效 HTTPS 配置时隐藏作者行；
其他设备提供自己的配置即可使用相同的包。
