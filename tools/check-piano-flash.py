#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Report and drive the flash of the camera through the LED class.

The flash of the camera is a standard LED class device, so any program can
use it and no helper of this repository is needed at runtime. This script
reads the limits of the device and can write the torch value and read it
back, which is what a hardware check of the flash needs.

The torch of a flash LED is its ordinary brightness; the strobe has its
own file and its own limits. The names are the ones of
Documentation/ABI/testing/sysfs-class-led-flash.

The actual light of the flash is only visible on the device; this script
reports the state of the controller.
"""
import argparse
import json
from pathlib import Path
import sys

# The files that tell a flash LED from an ordinary one.
FLASH_FILES = ('max_flash_brightness', 'flash_brightness')


def devices(root):
    """The LED class devices which can flash, in a stable order."""
    found = []
    leds = root / 'sys/class/leds'
    if not leds.is_dir():
        return found
    for entry in sorted(leds.iterdir()):
        if any((entry / name).exists() for name in FLASH_FILES):
            found.append(entry)
    return found


def read_value(path):
    try:
        return path.read_text().strip()
    except OSError as error:
        return f'unreadable: {error.strerror}'


def report(root):
    rows = []
    for entry in devices(root):
        row = {
            'name': entry.name,
            'brightness': read_value(entry / 'brightness'),
            'max_brightness': read_value(entry / 'max_brightness'),
        }
        for name in ('flash_brightness', 'max_flash_brightness',
                     'flash_timeout', 'max_flash_timeout'):
            value = read_value(entry / name)
            if not value.startswith('unreadable'):
                row[name] = value
        rows.append(row)
    return rows


def drive(root, value):
    """Write the torch value and read it back on every flash device."""
    rows = []
    for entry in devices(root):
        written = None
        try:
            (entry / 'brightness').write_text(str(value))
            written = read_value(entry / 'brightness')
        except OSError as error:
            written = f'write failed: {error.strerror}'
        rows.append({'name': entry.name, 'requested': value, 'read_back': written})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'),
                        help='prefix of the sysfs tree; for a check of another system')
    parser.add_argument('--torch', type=int,
                        help='write this torch value to every flash device and read it back')
    args = parser.parse_args()
    root = args.root
    if args.torch is not None:
        if args.torch < 0:
            parser.error('the torch value must not be negative')
        rows = drive(root, args.torch)
        print(json.dumps({'torch': rows}, indent=2))
        return 0 if rows and all(not row['read_back'].startswith(('write failed', 'unreadable'))
                                 for row in rows) else 1
    rows = report(root)
    print(json.dumps({'flash': rows}, indent=2))
    if not rows:
        print('no flash device in the LED class', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
