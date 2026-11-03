#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build locked arm64 crosvm in a fresh, unprivileged output directory."""
import argparse,hashlib,json,os,shlex,shutil,subprocess,tarfile
from pathlib import Path

def run(args,**kw):return subprocess.run([str(x) for x in args],check=True,**kw)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ['inputs','cargo-cache','rust-toolchain','output']:p.add_argument('--'+n,type=Path,required=True)
 a=p.parse_args();repo=Path(__file__).resolve().parents[1];lock=json.loads((repo/'tools/crosvm.lock.json').read_text())
 os.setpriority(os.PRIO_PROCESS,0,max(10,os.getpriority(os.PRIO_PROCESS,0)))
 os.umask(0o022);out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
 rust=a.rust_toolchain.resolve();inputs=a.inputs.resolve()
 for name,version in lock['host_packages'].items():
  actual=subprocess.check_output(['dpkg-query','-W','-f=${Version}',name],text=True)
  if actual!=version:raise RuntimeError('locked build host package differs: '+name)
 for binary in ['rustc','cargo']:
  if subprocess.check_output([str(rust/'bin'/binary),'--version'],text=True).strip()!=lock[binary]:raise RuntimeError('wrong '+binary)
 for rel,expected in lock.get('rust_toolchain_files',{}).items():
  if digest(rust/rel)!=expected:raise RuntimeError('locked Rust component differs: '+rel)
 for rel,expected in lock['inputs'].items():
  if digest(inputs/rel)!=expected:raise RuntimeError('input checksum mismatch: '+rel)
 for patch in lock['patches']:
  if digest(repo/patch['file'])!=patch['sha256']:raise RuntimeError('patch checksum mismatch: '+patch['file'])
 src=out/'src';src.mkdir()
 run(['tar','-xzf',inputs/'crosvm-96e84c3.tar.gz','--strip-components=1','-C',src])
 run(['git','init','-q',src])
 for patch in lock['patches']:run(['git','-C',src,'apply',repo/patch['file']])
 run(['tar','-xzf',inputs/'minijail-7845d899.tar.gz','--strip-components=1','-C',src/'third_party/minijail'])
 if digest(src/'Cargo.lock')!=lock['cargo_lock_sha256']:raise RuntimeError('Cargo.lock differs')
 sysroot=out/'sysroot';host=out/'host';sysroot.mkdir();host.mkdir()
 for directory,dest in [('sysroot-debs',sysroot),('host-debs',host)]:
  for deb in sorted((inputs/directory).glob('*.deb')):run(['dpkg-deb','-x',deb,dest])
 if not (sysroot/'lib').exists():(sysroot/'lib').symlink_to('usr/lib')
 cargo=out/'cargo-home';cargo.mkdir();shutil.copytree(a.cargo_cache.resolve()/'registry',cargo/'registry')
 wrappers=out/'tool-bin';wrappers.mkdir();q=shlex.quote
 wrapper='#!/bin/sh\nexec /usr/bin/aarch64-linux-gnu-gcc '+ ' '.join(map(q,[f'--sysroot={sysroot}','-nostdinc','-isystem','/usr/lib/gcc-cross/aarch64-linux-gnu/13/include','-isystem',str(sysroot/'usr/include/aarch64-linux-gnu'),'-isystem',str(sysroot/'usr/include'),f'-B{sysroot}/usr/lib/aarch64-linux-gnu/',f'-L{sysroot}/usr/lib/aarch64-linux-gnu',f'-ffile-prefix-map={out}=/build/piano-crosvm',f'-fdebug-prefix-map={out}=/build/piano-crosvm']))+' "$@"\n'
 (wrappers/'aarch64-linux-gnu-gcc').write_text(wrapper);(wrappers/'aarch64-linux-gnu-gcc').chmod(0o755)
 for binary in ['ar','nm','objcopy','ranlib','readelf','strip']:(wrappers/('aarch64-linux-gnu-'+binary)).symlink_to('/usr/bin/aarch64-linux-gnu-'+binary)
 (wrappers/'aarch64-linux-gnu-pkg-config').write_text('#!/bin/sh\nexport PKG_CONFIG_SYSROOT_DIR='+q(str(sysroot))+'\nexport PKG_CONFIG_LIBDIR='+q(str(sysroot/'usr/lib/aarch64-linux-gnu/pkgconfig')+':'+str(sysroot/'usr/share/pkgconfig'))+'\nexec /usr/bin/pkg-config \"$@\"\n')
 (wrappers/'aarch64-linux-gnu-pkg-config').chmod(0o755)
 env={k:v for k,v in os.environ.items() if k in ['HOME','USER','LOGNAME','TERM']}
 env.update(PATH=str(rust/'bin')+':'+str(wrappers)+':/usr/bin:/bin',LC_ALL='C',TZ='UTC',SOURCE_DATE_EPOCH=str(lock['source_date_epoch']),CARGO_HOME=str(cargo),CARGO_TARGET_DIR=str(out/'target'),CARGO_INCREMENTAL='0',RUSTC=str(rust/'bin/rustc'),RUSTDOC=str(rust/'bin/rustdoc'),JOBS=str(len(os.sched_getaffinity(0))),PKG_VERSION=lock['pkg_version'],DEFAULT_PIVOT_ROOT='/usr/share/piano/crosvm/empty',MINIJAIL_BINDGEN_TARGET=lock['target'],LIBCLANG_PATH=str(host/'usr/lib/llvm-18/lib'),LD_LIBRARY_PATH=str(host/'usr/lib/x86_64-linux-gnu'),CROSS_COMPILE=str(wrappers/'aarch64-linux-gnu-'),CC_aarch64_unknown_linux_gnu=str(wrappers/'aarch64-linux-gnu-gcc'),AR_aarch64_unknown_linux_gnu='/usr/bin/aarch64-linux-gnu-ar',CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=str(wrappers/'aarch64-linux-gnu-gcc'),PKG_CONFIG_SYSROOT_DIR_aarch64_unknown_linux_gnu=str(sysroot),PKG_CONFIG_LIBDIR_aarch64_unknown_linux_gnu=str(sysroot/'usr/lib/aarch64-linux-gnu/pkgconfig')+':'+str(sysroot/'usr/share/pkgconfig'),PKG_CONFIG_ALLOW_CROSS='1',BINDGEN_EXTRA_CLANG_ARGS_aarch64_unknown_linux_gnu=f'--sysroot={sysroot} -isystem {host}/usr/lib/llvm-18/lib/clang/18/include -isystem {sysroot}/usr/include/aarch64-linux-gnu',RUSTFLAGS=f'--remap-path-prefix={out}=/build/piano-crosvm --remap-path-prefix={rust}=/toolchain')
 run([rust/'bin/cargo','build','--release','--frozen','--offline','--target',lock['target'],'--no-default-features','--features',','.join(lock['features']),'--bin','crosvm','-j',env['JOBS']],cwd=src,env=env)
 shutil.copyfile(out/'target'/lock['target']/'release/crosvm',out/'crosvm');(out/'crosvm').chmod(0o755)
 for name,expected in lock.get('artifacts',{}).items():
  if digest(out/name)!=expected:raise RuntimeError('build differs from locked artifact: '+name)
 print('crosvm SHA256 '+digest(out/'crosvm'))
if __name__=='__main__':main()
