#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bundle corresponding sources and public registry metadata; exclude build logs and credentials."""
import argparse,json,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',type=Path,required=True);p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
repo=Path(__file__).resolve().parents[1];lock=json.loads((repo/'tools/crosvm.lock.json').read_text());a.output.parent.mkdir(parents=True,exist_ok=True)
os.setpriority(os.PRIO_PROCESS,0,max(10,os.getpriority(os.PRIO_PROCESS,0)))
# No cargo global database/config/credentials, no source .git, no absolute paths in archive names.
cmd=['tar','--sort=name','--mtime=@'+str(lock['source_date_epoch']),'--owner=0','--group=0','--numeric-owner','--mode=u+rwX,go+rX,go-w','--exclude=__pycache__','-I','zstd -T0 -3','-cf',str(a.output.resolve()),'-C',str(repo),'LICENSE','NOTICE','device/virtualization','packaging/piano-vm','packaging/piano-crosvm','docs/VIRTUALIZATION.md','docs/VIRTUALIZATION.zh-CN.md','docs/BUILD-VIRTUALIZATION.md','tools/build-device-debs.py','tools/build-virtualization-debs.py','tools/build-crosvm.py','tools/fetch-crosvm-inputs.py','tools/bundle-virtualization-source.py','tools/crosvm.lock.json','tools/windows.lock.json','tools/build-windows-assets.py','tools/fetch-windows-inputs.py','--transform=s,^cargo-home/,cargo-cache/,','-C',str(a.build.resolve()),'cargo-home/registry/cache','cargo-home/registry/index','--transform=s,^crosvm-96e84c3.tar.gz$,inputs/crosvm-96e84c3.tar.gz,','--transform=s,^minijail-7845d899.tar.gz$,inputs/minijail-7845d899.tar.gz,','-C',str(a.inputs.resolve()),'crosvm-96e84c3.tar.gz','minijail-7845d899.tar.gz']
subprocess.run(cmd,check=True)
