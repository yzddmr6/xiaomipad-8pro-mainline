#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assemble optional VM packages using the device-deb staging convention."""
import argparse,hashlib,json,os,shutil,subprocess
from pathlib import Path

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--version',required=True);p.add_argument('--crosvm-build',type=Path,required=True);p.add_argument('--windows-build',type=Path);a=p.parse_args()
 os.setpriority(os.PRIO_PROCESS,0,max(10,os.getpriority(os.PRIO_PROCESS,0)));os.umask(0o022)
 repo=Path(__file__).resolve().parents[1];src=repo/'device/virtualization';build=a.crosvm_build.resolve();lock=json.loads((repo/'tools/crosvm.lock.json').read_text())
 os.environ['SOURCE_DATE_EPOCH']=str(lock['source_date_epoch'])
 sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
 for name in ['crosvm']:
  if sha(build/name)!=lock['artifacts'][name]:raise ValueError('unverified build artifact: '+name)
 out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);stages=out/'stages';stages.mkdir()
 def copy(origin,root,target,mode=0o644):
  dest=root/target;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(origin,dest);dest.chmod(mode)
 def pack(name,depends,description,scripts,conffiles=()):
  root=stages/name;docs=root/'usr/share/doc'/name;docs.mkdir(parents=True,exist_ok=True)
  for f in ['NOTICE','LICENSE','LICENSE-MIT']:copy(src/f,root,'usr/share/doc/'+name+'/'+f)
  (docs/'copyright').write_text('Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n\nFiles: *\nCopyright: Piano mainline, iceland-gvm, ChromiumOS and component contributors (see NOTICE and licenses/)\nLicense: component-licenses\n See NOTICE, LICENSE, LICENSE-MIT and the per-component licence texts in licenses/.\n Source files retain their individual SPDX notices; binary aggregation does not relicense them.\n')
  control=root/'DEBIAN';control.mkdir()
  (control/'control').write_text(f'Package: {name}\nVersion: {a.version}\nArchitecture: arm64\nMaintainer: Piano local build <piano@localhost>\nDepends: {depends}\nDescription: {description}\n')
  for n,body in scripts.items():
   (control/n).write_text('#!/bin/sh\nset -e\n'+body);(control/n).chmod(0o755)
  if conffiles:(control/'conffiles').write_text(''.join(x+'\n' for x in conffiles))
  subprocess.run(['dpkg-deb','--root-owner-group','-Zxz','-z1','--build',str(root),str(out/f'{name}_{a.version}_arm64.deb')],check=True)
 busy_guard='if [ \"$1\" = remove ]; then\n for exe in /proc/[0-9]*/exe; do\n  if [ \"$(readlink \"$exe\" 2>/dev/null || true)\" = /usr/lib/piano/crosvm/crosvm ]; then\n   echo \"Stop virtual machines and clean their networks before removing this package.\" >&2; exit 1\n  fi\n done\nfi\n'
 for name in ['piano-crosvm','piano-vm']:
  root=stages/name;shutil.copytree(repo/'packaging'/name,root);root.chmod(0o755)
  for f in root.rglob('*'):
   if not f.is_symlink():f.chmod(0o755 if f.is_dir() else 0o644)
 vmm=stages/'piano-crosvm';vm=stages/'piano-vm'
 # Git does not store empty directories; create the sandbox root during packaging.
 (vmm/'usr/share/piano/crosvm/empty').mkdir(parents=True,exist_ok=True)
 copy(build/'crosvm',vmm,'usr/lib/piano/crosvm/crosvm',0o755)
 copy(repo/'tools/crosvm.lock.json',vmm,'usr/share/doc/piano-crosvm/source-lock.json')
 for folder,label in [(build/'src','crosvm'),(build/'src/third_party/minijail','minijail')]:copy(folder/'LICENSE',vmm,'usr/share/doc/piano-crosvm/licenses/'+label+'/LICENSE')
 for name in ['LICENSE.GPL-2.0','ADDITIONAL-PERMISSIONS','CREDITS']:copy(build/'src'/name,vmm,'usr/share/doc/piano-crosvm/licenses/crosvm/'+name)
 for crate in sorted((build/'cargo-home/registry/src').glob('*/*')):
  if not crate.is_dir():continue
  for f in crate.rglob('*'):
   if f.is_file() and (f.name.upper().startswith(('LICENSE','LICENCE','COPYING','NOTICE'))):copy(f,vmm,'usr/share/doc/piano-crosvm/licenses/'+crate.name+'/'+str(f.relative_to(crate)))
 pack('piano-crosvm','libc6 (>= 2.43), libcap2, libgcc-s1, libwayland-client0, libvncserver1, apparmor', 'Gunyah crosvm with unprivileged device sandboxes',{
  'postinst':'if [ "$1" = configure ] && [ -d /run/systemd/system ] && [ "$(stat -Lc %d:%i /)" = "$(stat -Lc %d:%i /proc/1/root 2>/dev/null || true)" ] && [ -d /sys/kernel/security/apparmor ]; then\n apparmor_parser -r /etc/apparmor.d/piano-crosvm\nfi\n',
  # Profile stays loaded until reboot on remove: do not weaken a still-running VMM.
  'prerm':busy_guard,
  'postrm':': # No driver unload, process kill, or live profile removal. Reboot clears the profile.\n'},['/etc/apparmor.d/piano-crosvm'])
 for f in ['piano-vm','vm-image.py','windows_vm.py','guest-control.py','guest-control.service','NOTICE']:
  copy(src/f,vm,'usr/lib/piano/vm/'+f,0o755 if f in ['piano-vm','vm-image.py','guest-control.py'] else 0o644)
 (vm/'usr/bin').mkdir(parents=True);(vm/'usr/bin/piano-vm').symlink_to('../lib/piano/vm/piano-vm')
 copy(src/'piano-vm-net',vm,'usr/libexec/piano/piano-vm-net',0o755)
 for name in ['VIRTUALIZATION.zh-CN.md','VIRTUALIZATION.md']:copy(repo/'docs'/name,vm,'usr/share/doc/piano-vm/'+name)
 pack('piano-vm',f'piano-crosvm (= {a.version}), piano-device-support (>= {a.version}), python3, ca-certificates, qemu-utils, e2fsprogs, zstd, genisoimage, openssh-client, network-manager, dnsmasq-base, iproute2, pkexec, polkitd, sudo, systemd',
  'Ordinary-user Ubuntu virtual machines on Piano',{'postinst':'if [ "$1" = configure ] && [ -d /run/systemd/system ] && [ "$(stat -Lc %d:%i /)" = "$(stat -Lc %d:%i /proc/1/root 2>/dev/null || true)" ]; then\n systemd-tmpfiles --create piano-vm-memory.conf\nfi\n','prerm':busy_guard})
 if a.windows_build:
  assets=a.windows_build.resolve()/'assets';win=stages/'piano-vm-windows';win.mkdir()
  winlock=json.loads((repo/'tools/windows.lock.json').read_text())
  for name,expected in winlock['artifacts'].items():
   if sha(assets/name)!=expected:raise ValueError('unverified Windows artifact: '+name)
  for name in ['prepare-windows.py','windows_deploy.py','patch-bcd.py']:
   copy(src/'windows'/name,win,'usr/lib/piano/vm/windows/'+name,0o755 if name=='prepare-windows.py' else 0o644)
  for f in assets.rglob('*'):
   if f.is_file():copy(f,win,'usr/share/piano/vm/windows/'+str(f.relative_to(assets)))
  copy(repo/'tools/windows.lock.json',win,'usr/share/doc/piano-vm-windows/source-lock.json')
  copy(src/'windows/NOTICE',win,'usr/share/doc/piano-vm-windows/windows-NOTICE')
  pack('piano-vm-windows',f'piano-vm (= {a.version}), 7zip, wimtools, python3-hivex, dosfstools, mtools, ntfs-3g, fdisk, util-linux, remmina, remmina-plugin-vnc',
   'Optional Windows ARM64 media tools, firmware and signed guest drivers',{'prerm':busy_guard})
 (out/'SHA256SUMS').write_text(''.join(sha(f)+'  '+f.name+'\n' for f in sorted(out.glob('*.deb'))))
if __name__=='__main__':main()
