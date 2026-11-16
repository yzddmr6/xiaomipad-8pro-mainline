#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Build the architecture-independent updater; project settings are product data."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import tempfile
p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
source = Path(__file__).resolve().parent
a.output.mkdir(parents=True, exist_ok=True)
product=json.loads((source/'usr/share/mainline-update/project.json').read_text())['product']
version='0.1.0+'+product+'5'
os.environ.setdefault('SOURCE_DATE_EPOCH', '1794787200')
with tempfile.TemporaryDirectory() as temp:
    stage = Path(temp)/'package'
    stage.mkdir()
    for name in ('DEBIAN','usr','etc'):
        shutil.copytree(source/name,stage/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    control=stage/'DEBIAN/control'
    control.write_text(control.read_text().replace('Version: 0.1.0', 'Version: '+version))
    for po in sorted((source/'po').glob('*.po')):
        out=stage/'usr/share/locale'/po.stem/'LC_MESSAGES/mainline-update.mo'
        out.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['msgfmt','--check',str(po),'-o',str(out)],check=True)
    stage.chmod(0o755)
    for entry in stage.rglob('*'):
        entry.chmod(0o755 if entry.is_dir() or entry.stat().st_mode & 0o111 else 0o644)
    subprocess.run(['dpkg-deb','--root-owner-group','-Zxz','-z1','--build',str(stage),str(a.output/('mainline-update_'+version+'_all.deb'))],check=True)
