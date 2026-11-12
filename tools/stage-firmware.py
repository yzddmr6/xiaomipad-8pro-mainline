#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Stage verified DSP firmware for a native package recipe or RAM initramfs.

By default the files come from the repository's firmware/ tree, which carries the
ADSP and CDSP images the kernel loads; --firmware-dir selects an owner's own flat stock
modem image directory instead. The output contains a plain payload root and a
manifest; it is not an installed package and grants no rights in the firmware.
Requires only Python.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import tempfile

import firmware_payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir', type=Path,
                        help='Flat directory with the locked DSP files, such as your own stock modem image '
                             'directory (default: the repository tree)')
    parser.add_argument('--output', type=Path, required=True, help='New private output directory')
    parser.add_argument('--lock', type=Path, default=Path(__file__).resolve().parents[1] / 'firmware/piano-os3.0.303.0.json')
    parser.add_argument('--image', action='append',
                        help='MDT header to select; repeat as needed (default: all locked files with '
                             '--firmware-dir, otherwise ' + ' and '.join(firmware_payload.KERNEL_DSP_IMAGES) + ')')
    args = parser.parse_args()
    lock = firmware_payload.load_lock(args.lock)
    firmware_dir, images = args.firmware_dir, args.image
    if firmware_dir is None:
        directory = PurePosixPath(lock['firmware_directory']).relative_to(firmware_payload.FIRMWARE_ROOT)
        firmware_dir = firmware_payload.REPO_FIRMWARE / directory
        images = images or list(firmware_payload.KERNEL_DSP_IMAGES)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error('Output must not already exist')
    os.umask(0o022)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.piano-firmware-', dir=output.parent) as temporary:
        result = Path(temporary) / 'result'
        stage = result / 'root'
        stage.mkdir(parents=True)
        result.chmod(0o700)
        rows = firmware_payload.stage_payload(lock, firmware_dir, stage, images)
        manifest = {'state': 'LOCAL_PAYLOAD_STAGED_NOT_INSTALLED',
                    'stock_build': lock['stock_build'], 'firmware_directory': lock['firmware_directory'],
                    'images': images, 'files': rows,
                    'input_lock_sha256': firmware_payload.sha(args.lock),
                    'builder_sha256': firmware_payload.sha(__file__),
                    'payload_builder_sha256': firmware_payload.sha(firmware_payload.__file__),
                    'redistribution': lock['redistribution']}
        (result / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        result.rename(output)
    print(json.dumps({'state': manifest['state'], 'files': len(rows), 'output': str(output)}))


if __name__ == '__main__':
    main()
