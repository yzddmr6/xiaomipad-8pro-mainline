#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fetch the exact host Mesa inputs; every file is checked against committed hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

REPO=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    lock=json.loads((REPO/'mesa/ubuntu/source.json').read_text());mesa=json.loads((REPO/'mesa/source.json').read_text())
    files=[*lock['components']['mesa']['files'],*lock['ubuntu_binaries']['files'],lock['build_root'],
           dict(file=mesa['archive'],url=mesa['archive_url'],sha256=mesa['archive_sha256'])]
    a.output.mkdir(parents=True,exist_ok=True)
    for item in files:
        path=a.output/item['file']
        if not path.exists():
            temporary=path.with_suffix(path.suffix+'.part')
            subprocess.run(['curl','--fail','--location','--connect-timeout','30','--max-time','1800','-o',str(temporary),item['url']],check=True)
            temporary.replace(path)
        with path.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=item['sha256']:raise SystemExit('checksum mismatch: '+str(path))
        print(actual+'  '+str(path))

if __name__=='__main__':main()
