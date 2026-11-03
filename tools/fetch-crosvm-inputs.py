#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fetch only immutable inputs named and hashed by crosvm.lock.json."""
import argparse,hashlib,json,os,shutil,urllib.request,uuid
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
os.setpriority(os.PRIO_PROCESS,0,max(10,os.getpriority(os.PRIO_PROCESS,0)))
lock=json.loads((Path(__file__).resolve().parent/'crosvm.lock.json').read_text())
for rel,expected in lock['inputs'].items():
 dest=a.output/rel;dest.parent.mkdir(parents=True,exist_ok=True)
 if not dest.exists():
  temp=dest.with_name(dest.name+'.part-'+uuid.uuid4().hex)
  try:
   with urllib.request.urlopen(lock['input_urls'][rel],timeout=60) as source,temp.open('xb') as out:shutil.copyfileobj(source,out,1024**2)
   with temp.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
   if actual!=expected:raise ValueError('download hash mismatch: '+rel)
   temp.replace(dest)
  finally:temp.unlink(missing_ok=True)
 else:
  with dest.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
  if actual!=expected:raise ValueError('cache hash mismatch: '+rel)
 print(rel)
