#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Model registration and slot activation as separate operations in the disposable APT root."""
import json
from pathlib import Path
import shutil
import sys

assert Path('/etc/release-disposable-test-root').is_file()
board = Path('/etc/offline-product').read_text().strip()
base = Path('/var/lib') / board
registered = base / 'switch/ubuntu'
slots = Path('/dev/disk/by-partlabel')
names = ('boot', 'init_boot', 'vendor_boot')
command = Path(sys.argv[0]).name
args = sys.argv[1:]
if command == 'erhai-switch' and args == ['status']:
    print('slot suffix: _a')
    sys.exit(0)
assert slots.is_dir() and not slots.is_symlink()
nodes = {name: slots / (name + '_a') for name in names}
assert all(p.is_file() and not p.is_symlink() for p in nodes.values())
transaction = base / 'releases/transaction.json'
phase = json.loads(transaction.read_text())['phase'] if transaction.is_file() else None
with Path('/results/boot-calls.jsonl').open('a') as log:
    log.write(json.dumps(dict(command=command, argv=args, phase=phase)) + '\n')
if command == 'piano-boot-switch' and args[:2] == ['import-ubuntu', '--from']:
    source = Path(args[2])
elif command == 'erhai-switch-install-ubuntu-images' and len(args) == 1:
    source = Path(args[0])
else:
    assert command == 'piano-boot-switch' and args == ['to-ubuntu', '--only-if-ubuntu']
    source = None
if source is not None:
    for name in names:
        shutil.copyfile(source / (name + '.img'), registered / (name + '.img'))
    if command == 'piano-boot-switch':
        sys.exit(0)
for name, node in nodes.items():
    if name != 'vendor_boot' or not Path('/run/fixture-omit-slot-write').exists():
        shutil.copyfile(registered / (name + '.img'), node)
