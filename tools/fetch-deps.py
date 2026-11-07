#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fetch a named pinned dependency, or export a vendored source snapshot."""
import argparse
import os
from pathlib import Path
from lib.deps import fetch, inventory, export_vendor, git_source

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--name', action='append', help='dependency name (repeatable); default: all file inputs')
p.add_argument('--output', type=Path, required=True)
p.add_argument('--export-vendor', help='export this vendored source into --output')
p.add_argument('--offline', action='store_true')
p.add_argument('--own-only', action='store_true')
a = p.parse_args()
if a.offline:
    os.environ['DEPS_OFFLINE'] = '1'
if a.own_only:
    os.environ['DEPS_OWN_ONLY'] = '1'
if a.export_vendor:
    export_vendor(a.export_vendor, a.output)
else:
    data = inventory()
    names = set(a.name or [r['name'] for r in data['files']])
    known = {r['name'] for r in [*data['files'], *data['git']]}
    if names - known:
        p.error('unknown dependency: ' + ', '.join(sorted(names - known)))
    for row in data['files']:
        if row['name'] in names:
            if row.get('upstream'):
                url = row['upstream'][0]
            elif row.get('vendored'):
                url = (Path(__file__).resolve().parents[1] / row['vendored']).as_uri()
            else:
                url = row['own_url']
            fetch(url, a.output / row['filename'], row['sha256'])
    for row in data['git']:
        if row['name'] in names:
            git_source(a.output / (row['repository'] + '.git'), row['upstream'], row['commit'], row['repository'])
