# One-tap system update / 一键更新

Build the product package with `python3 packaging/mainline-update/build.py --output out/update`.
Include that deb in the complete release repository and root-image package inputs alongside
the desktop metapackage. Product data gives the packages distinct Debian versions
(`0.1.0+piano3` / `0.1.0+erhai3`) so package identity is unambiguous. Its product settings come from
`usr/share/mainline-update/project.json`; all other files must match across products.
The updater depends on the existing release marker and attachment installer contract.

“更新” opens the selected release, while “查看更新说明” still opens its release page.
The frontend runs in the user session. Download and preparation require administrator
confirmation too, since cleanup preview executes the verified attachment's installer.
After download, a separate confirmation shows the installer's actual cleanup preview;
then Install runs its `upgrade NEW --previous OLD`. No installer is embedded here.
Recovery follows UPGRADE: `dpkg --configure -a`, `apt-get -y -f install`, and the same
upgrade command. A failed recovery stops at that step and offers rollback.

Downloads use a root-owned cache and Range requests. TAR hard links are accepted only
when they name an already extracted regular file; symbolic links and special files are
rejected. Completed attachment files are size and SHA-256 checked before extraction.
Jump upgrades fetch the installed release's own packages when the supplied rollback
batch does not match. No background installation or battery test is performed.
A logind inhibitor covers download/preparation and installation/rollback; it is released
while waiting for user confirmation. The D-Bus service survives closing the window.
Its root service must permit the attachment installer and package maintainer scripts to
write their normal system paths; a selective read-only system sandbox is incompatible
with APT installation. There are no password-free polkit rules.

An interrupted service exposes the installer's transaction phase and offers Continue /
Rollback. The first login after a different kernel boot ID sends the completion notice;
logging out and back in before reboot does not claim completion. After the notice,
the window offers a fresh update check while retaining the completed release’s
rollback action; later installations and rollbacks get their own completion notice. Detailed logs come from
`journalctl -u mainline-update.service`. The metered-connection advisory follows Gio.NetworkMonitor; download sizes use
GLib.format_size. Running VM names come from the read-only `gunyahvm list` CLI
(config/status contract from GunyahVM 1.3.0); stopped and interrupted instances
are omitted. A missing optional CLI does not block the window. Refreshing the VM
list requires a user action; installation buttons wait for known active VMs to stop.
The GTK frontend uses Adw.ToolbarView and compact Adw.StatusPage layouts, with
release changes in an expander and separate download/verify/extract/install rows.

Offline network/security checks: `sudo python3 packaging/mainline-update/tests/offline.py`.
`tests/transactions.py /path/to/released/install.py` is **only** for the disposable
APT-scenario chroot, without hardware block nodes. It builds compact real APT batches
with a deliberately failing postinst and runs the attachment installer unchanged for
normal upgrade, recovery, persistent configuration failure, rollback and jump upgrades
for ZIP and TAR products. Full release acceptance additionally uses the existing
apt-scenarios fixture and real release attachments. Preview JSON is a user-interface
screenshot input only; it never invokes the service.

中文：先下载校验、显示真实清理预览，再确认安装。恢复步骤严格沿用 UPGRADE，失败后提供整版回退。
离线夹具通过故意失败的软件包配置脚本验证恢复和回退；它不替代发布批次测试或真机测试。
两台除 project.json 外逐字一致；parity 条目作为独立补丁交给工作区校验脚本的所有者合入。
