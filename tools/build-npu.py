#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the pinned glibc FastRPC + llama.cpp/v79 runtime and local demo packages.

Host: Python 3.12, CMake >=3.22, Ninja, Autoconf, Automake, Libtool, pkg-config,
aarch64-linux-gnu GCC/G++, dpkg-deb. No root or device access. A missing cached
input is an error unless --download is explicitly selected. The SDK is used
only for building; board DSP shells/libraries come from the hash-locked firmware/npu tree.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

REPO=Path(__file__).resolve().parents[1]
ASSETS=REPO/'packaging/npu'
VERSION='0.1+git20261112.e1f7593-5'
LIB='usr/lib/aarch64-linux-gnu/qcom-npu'

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def run(cmd, **kw):
    subprocess.run([str(x) for x in cmd],check=True,**kw)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True,help='New output directory')
    p.add_argument('--download',action='store_true')
    p.add_argument('--jobs',type=int,default=os.cpu_count())
    a=p.parse_args();a.cache=a.cache.resolve();a.output=a.output.resolve()
    if a.jobs<1:p.error('--jobs must be positive')
    lock=json.loads((ASSETS/'sources.json').read_text());inputs=lock['inputs']
    a.cache.mkdir(parents=True,exist_ok=True)
    def get(row):
        f=a.cache/row['filename']
        if not f.is_file():
            if not a.download:raise ValueError('missing cached input: '+f.name)
            tmp=f.with_suffix(f.suffix+'.partial')
            try:
                urllib.request.urlretrieve(row['url'],tmp)
                if tmp.stat().st_size!=row['bytes'] or sha(tmp)!=row['sha256']:raise ValueError('download identity mismatch: '+f.name)
                tmp.replace(f)
            finally:tmp.unlink(missing_ok=True)
        if f.stat().st_size!=row['bytes'] or sha(f)!=row['sha256']:raise ValueError('cached input identity mismatch: '+f.name)
        return f
    archives={k:get(v) for k,v in inputs.items() if isinstance(v,dict)}
    dependencies=[get(row) for row in inputs['build-dependencies']]
    a.output.mkdir(parents=True,exist_ok=False)
    b=a.output;source=b/'source';source.mkdir()
    for key in ('llama','fastrpc','hexagon-sdk'):
        dest=source/key;dest.mkdir()
        with tarfile.open(archives[key]) as t:t.extractall(dest,filter='data')
    llama=next((source/'llama').iterdir());rpc=next((source/'fastrpc').iterdir());sdk=next((source/'hexagon-sdk').iterdir())
    for patch in lock.get('patches', []):
        path=ASSETS/patch['file']
        if sha(path)!=patch['sha256']:raise ValueError('Build patch identity mismatch')
        run(['patch','-p1','--forward','-i',path],cwd=llama)
    dep=b/'sysroot';dep.mkdir()
    for f in dependencies:run(['dpkg-deb','-x',f,dep])
    env=dict(os.environ,GIT_CEILING_DIRECTORIES=str(b),SOURCE_DATE_EPOCH=str(lock['source_date_epoch']),LC_ALL='C.UTF-8',
             CFLAGS=f'-ffile-prefix-map={b}=. -fno-record-command-line',
             CXXFLAGS=f'-ffile-prefix-map={b}=. -fno-record-command-line')
    rpc_env=dict(env,CC='aarch64-linux-gnu-gcc',CXX='aarch64-linux-gnu-g++',
                 PKG_CONFIG_LIBDIR=str(dep/'usr/lib/aarch64-linux-gnu/pkgconfig'),PKG_CONFIG_SYSROOT_DIR=str(dep),
                 BSD_LIBS='-l:libbsd.so.0 -l:libmd.so.0',
                 CFLAGS=f'-O2 -ffile-prefix-map={b}=.',
                 LDFLAGS=f'-L{dep}/usr/lib/aarch64-linux-gnu -Wl,-rpath-link,{dep}/usr/lib/aarch64-linux-gnu')
    run(['bash','./autogen.sh'],cwd=rpc,env=rpc_env)
    run(['bash','./configure','--host=aarch64-linux-gnu','--prefix=/usr','--libdir=/usr/lib/aarch64-linux-gnu','--with-config-base-dir=/usr/share/qcom'],cwd=rpc,env=rpc_env)
    run(['make','-j'+str(a.jobs)],cwd=rpc,env=rpc_env)
    run(['make','install','DESTDIR='+str(b/'fastrpc-install')],cwd=rpc,env=rpc_env)
    build=b/'llama-build'
    run(['cmake','-S',llama,'-B',build,'-G','Ninja','-DCMAKE_SYSTEM_NAME=Linux','-DCMAKE_SYSTEM_PROCESSOR=aarch64',
         '-DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc','-DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++','-DCMAKE_BUILD_TYPE=Release',
         '-DGGML_NATIVE=OFF','-DGGML_CPU_ARM_ARCH=armv8.7-a+fp16+dotprod+i8mm','-DGGML_OPENMP=OFF','-DGGML_LLAMAFILE=OFF',
         '-DGGML_HEXAGON=ON','-DGGML_BACKEND_DL=ON','-DHEXAGON_SDK_ROOT='+str(sdk),'-DPREBUILT_LIB_DIR=linux_aarch64',
         '-DLLAMA_OPENSSL=OFF','-DLLAMA_BUILD_TESTS=OFF','-DLLAMA_BUILD_EXAMPLES=OFF','-DLLAMA_BUILD_SERVER=OFF','-DLLAMA_BUILD_APP=OFF',
         '-DLLAMA_BUILD_NUMBER=0','-DLLAMA_BUILD_COMMIT='+inputs['llama']['revision'][:12],'-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON','-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON','-DCMAKE_INSTALL_RPATH=$ORIGIN',
         '-DCMAKE_C_FLAGS=-ffile-prefix-map='+str(b)+'=.','-DCMAKE_CXX_FLAGS=-ffile-prefix-map='+str(b)+'=.'],env=env)
    # Select just v79; the other upstream ExternalProject targets are not needed on SM8750.
    run(['cmake','--build',build,'--target','llama-completion','llama-bench','htp-v79','-j'+str(a.jobs)],env=env)
    run(['aarch64-linux-gnu-g++','-std=c++17','-O3','-Wall','-Wextra','-I'+str(llama/'include'),'-I'+str(llama/'ggml/include'),
         ASSETS/'hybrid.cpp','-L'+str(build/'bin'),'-Wl,-rpath,$ORIGIN','-Wl,-rpath-link,'+str(build/'bin'),'-lllama','-lggml','-lggml-base','-o',build/'bin/npu-hybrid'],env=env)
    stage=b/'runtime';stage.mkdir()
    def copy(src,rel,mode=0o644):
        target=stage/rel;target.parent.mkdir(parents=True,exist_ok=True)
        if src.is_symlink():target.symlink_to(os.readlink(src))
        else:shutil.copyfile(src,target);target.chmod(mode)
    for f in sorted((build/'bin').glob('*.so*')):copy(f,LIB+'/'+f.name)
    for name in ('npu-hybrid','llama-completion','llama-bench'):copy(build/'bin'/name,LIB+'/'+name,0o755)
    for f in (b/'fastrpc-install/usr/lib/aarch64-linux-gnu').glob('libcdsp*.so*'):copy(f,'usr/lib/aarch64-linux-gnu/'+f.name)
    # Only ARM64 objects are stripped by the ARM64 tools. DSP skel keeps its SWIV/signature section.
    for f in [*(stage/LIB).iterdir(), *(stage/'usr/lib/aarch64-linux-gnu').glob('libcdsp*.so*')]:
        if not f.is_symlink():run(['aarch64-linux-gnu-strip','--strip-unneeded',f])
    copy(build/'ggml/src/ggml-hexagon/libggml-htp-v79.so',LIB+'/libggml-htp-v79.so')
    # The Hexagon ExternalProject has its own compiler flags. Guard all ELF
    # payloads, including its diagnostics/command notes, against build paths.
    for f in (stage/'usr/lib').rglob('*'):
        if f.is_file() and not f.is_symlink() and str(b).encode() in f.read_bytes():
            raise ValueError('Build path leaked into runtime: '+f.name)
    for name,rel in [('npu-demo','usr/bin/npu-demo')]:copy(ASSETS/name,rel,0o755)
    copy(ASSETS/'70-qcom-npu.rules','usr/lib/udev/rules.d/70-qcom-npu.rules')
    copy(ASSETS/'npu.yaml','usr/share/qcom/conf.d/90-mainline-npu.yaml')
    copy(ASSETS/'prompt.txt','usr/share/qcom-npu/prompt.txt')
    for rel,body in {'usr/lib/sysusers.d/qcom-npu.conf':'g render - -\n',
                     'usr/lib/modules-load.d/qcom-npu.conf':'qcom_q6v5_pas\nfastrpc\nsystem_heap\n'}.items():
        f=stage/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(body)
    docs=stage/'usr/share/doc/qcom-npu-runtime';docs.mkdir(parents=True)
    shutil.copyfile(ASSETS/'sources.json',docs/'sources.json')
    shutil.copyfile(llama/'LICENSE',docs/'llama-LICENSE')
    shutil.copyfile(rpc/'LICENSE.txt',docs/'fastrpc-LICENSE')
    shutil.copyfile(ASSETS/'README.md',docs/'README.md')
    receipt={'source_lock_sha256':sha(ASSETS/'sources.json'),'inputs':lock,'build_tools':{},'packages':{}}
    for name in ('aarch64-linux-gnu-gcc','cmake','ninja','autoconf','dpkg-deb'):
        receipt['build_tools'][name]=subprocess.check_output([name,'--version'],text=True).splitlines()[0]
    (docs/'build.json').write_text(json.dumps({'tools':receipt['build_tools'],'llama_revision':inputs['llama']['revision'],'fastrpc_revision':inputs['fastrpc']['revision']},indent=2)+'\n')
    def package(root,name,depends,description,postinst=False,architecture="arm64",extra=""):
        control=root/'DEBIAN';control.mkdir()
        (control/'triggers').write_text('activate-noawait ldconfig\n') if postinst else None
        (control/'control').write_text(f'Package: {name}\nVersion: {VERSION}\nArchitecture: {architecture}\nMaintainer: yzddmr6 <46088090+yzddmr6@users.noreply.github.com>\nDepends: {depends}\n{extra}Description: {description}\n')
        if postinst:
            shutil.copyfile(ASSETS/'postinst',control/'postinst');(control/'postinst').chmod(0o755)
        files={str(f.relative_to(root)):sha(f) for f in sorted(root.rglob('*')) if f.is_file() and not f.is_symlink() and control not in f.parents}
        (control/'md5sums').write_text(''.join(hashlib.md5((root/f).read_bytes(),usedforsecurity=False).hexdigest()+'  '+f+'\n' for f in files))
        deb=b/f'{name}_{VERSION}_{architecture}.deb'
        run(['dpkg-deb','--root-owner-group','-Zxz','-z1','--build',root,deb],env=env)
        receipt['packages'][name]={'file':deb.name,'sha256':sha(deb),'bytes':deb.stat().st_size,'files':files}
    package(stage,'qcom-npu-runtime','libc6 (>= 2.38), libstdc++6 (>= 13), libgcc-s1, libbsd0, libmd0, libyaml-0-2, python3, systemd, udev, qcom-npu-dsp',
            'Source-built FastRPC and llama.cpp Hexagon v79 with a hybrid CPU/NPU demo',True)
    fwlock=json.loads((REPO/'firmware/npu/source.json').read_text())
    board=b/'board';dst=board/'usr/share/qcom/npu-dsp';dst.mkdir(parents=True)
    for row in fwlock['files']:
        f=REPO/'firmware/npu'/row['name']
        if f.is_symlink() or f.stat().st_size!=row['bytes'] or sha(f)!=row['sha256']:
            raise ValueError('Stock DSP identity mismatch: '+row['name'])
        shutil.copyfile(f,dst/row['name']);(dst/row['name']).chmod(0o644)
    fwdocs=board/'usr/share/doc'/fwlock['package'];fwdocs.mkdir(parents=True)
    for name in ('source.json','README.md'):shutil.copyfile(REPO/'firmware/npu'/name,fwdocs/name)
    package(board,fwlock['package'],'', 'Stock Hexagon runtime for '+fwlock['device'],architecture='all',
            extra='Provides: qcom-npu-dsp\nConflicts: qcom-npu-dsp\n')
    model=b/'model';target=model/'usr/share/qcom-npu/Qwen3-0.6B-Q4_0.gguf';target.parent.mkdir(parents=True);shutil.copyfile(archives['model'],target)
    model_doc=model/'usr/share/doc/qcom-npu-demo-model';model_doc.mkdir(parents=True)
    (model_doc/'copyright').write_text('Qwen3-0.6B: Qwen team, Apache-2.0. GGUF conversion: Unsloth.\nSource and immutable revision: '+inputs['model']['url']+'\nOn Debian, see /usr/share/common-licenses/Apache-2.0.\n')
    package(model,'qcom-npu-demo-model','qcom-npu-runtime (= '+VERSION+')','Qwen3 0.6B Q4_0 model for the offline npu-demo command')
    (b/'manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('Built source-identified NPU packages; hardware acceptance is still required.')
if __name__=='__main__':main()
