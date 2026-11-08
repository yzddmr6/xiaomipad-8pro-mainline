#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile integer/min-max GLSL through an ARM64 provider's actual libgallium.

Requires unsquashfs, unprivileged user namespaces, a prepared core24 ARM64 SDK
and registered qemu-aarch64 binfmt (or a native ARM64 host). No device access.
Only the small probe is compiled; Mesa is never rebuilt. Runtime is the supplied
core24 snap plus each provider, with no SDK libraries, network or GPU devices.
An optional old provider must reproduce ir.cpp:1603 with SIGABRT/exit 134.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'tools/fixtures/snap-graphics/glsl-integer.c'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def unpack(snap, target, log, *extra):
    with log.open('w') as stream:
        subprocess.run(['unsquashfs', '-no-progress', '-no-xattrs', *extra,
                        '-d', str(target), str(snap), *(['dev'] if extra else [])],
                       stdout=stream, stderr=subprocess.STDOUT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', type=Path, required=True)
    parser.add_argument('--old-provider', type=Path)
    parser.add_argument('--core24', type=Path, required=True)
    parser.add_argument('--sdk-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sdk = args.sdk_root.resolve()
    shutil.copyfile(SOURCE, sdk / 'tmp/piano-glsl-integer.c')
    # Compile offline with only harmless device nodes; keep the SDK's /dev private.
    compile_wrapper = ('set -eu; mount -t tmpfs tmpfs "$1/dev"; '
                       'touch "$1/dev/null"; mount --bind /dev/null "$1/dev/null"; '
                       'mount -t proc proc "$1/proc"; '
                       'exec chroot "$1" /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C QEMU_CPU=max,pauth-impdef=on '
                       'gcc -O2 -Wall -Wextra -o /tmp/piano-glsl-integer '
                       '/tmp/piano-glsl-integer.c -lEGL -lGLESv2')
    with (out / 'compile.log').open('w') as stream:
        subprocess.run(['timeout', '-k', '5', '60', 'unshare', '--map-root-user', '--mount',
                        '--net', '--pid', '--fork', '--kill-child', 'sh', '-c', compile_wrapper,
                        'sh', str(sdk)], stdout=stream, stderr=subprocess.STDOUT, check=True)
    root = out / 'runtime'
    unpack(args.core24, root, out / 'core24-unpack.log', '-excludes')
    (root / 'test').mkdir()
    shutil.copyfile(sdk / 'tmp/piano-glsl-integer', root / 'test/piano-glsl-integer')
    (root / 'test/piano-glsl-integer').chmod(0o755)
    shutil.copy2(root / 'test/piano-glsl-integer', out / 'piano-glsl-integer')
    (root / 'tmp/runtime').mkdir(mode=0o700)
    (root / 'dev').mkdir(exist_ok=True)
    for node in ('null', 'random', 'urandom'):
        (root / 'dev' / node).touch()
    # A shell waits for the probe so an abort is reaped as 134, even in a PID
    # namespace. Running qemu as PID 1 can otherwise wait until timeout kills it.
    wrapper = ('set -eu; ulimit -c 0; for node in null random urandom; do '
               'mount --bind /dev/$node "$1/dev/$node"; done; '
               'mount -t proc proc "$1/proc"; '
               'exec chroot "$1" /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C QEMU_CPU=max,pauth-impdef=on '
               'XDG_RUNTIME_DIR=/tmp/runtime MESA_SHADER_CACHE_DISABLE=true '
               'LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=softpipe '
               '/bin/sh -c \'"$@"; code=$?; exit "$code"\' sh '
               '"$2/bin/gpu-2404-provider-wrapper" /test/piano-glsl-integer')
    report = {'scope': 'ARM64 core24 + provider; softpipe only, no snapd/confinement/GPU',
              'core24_sha256': sha(args.core24), 'source_sha256': sha(SOURCE),
              'probe_sha256': sha(out / 'piano-glsl-integer'), 'cases': {}}
    variants = [('old', args.old_provider)] if args.old_provider else []
    variants.append(('new', args.provider))
    for name, snap in variants:
        provider = root / 'test' / name
        unpack(snap, provider, out / f'{name}-unpack.log')
        relative = '/test/' + name
        result = subprocess.run([
            'timeout', '-k', '5', '60', 'unshare', '--map-root-user', '--mount', '--net',
            '--pid', '--fork', '--kill-child', 'sh', '-c', wrapper, 'sh', str(root), relative],
            capture_output=True, text=True)
        (out / f'{name}.log').write_text(result.stdout + result.stderr)
        libraries = list((provider / 'usr/lib/aarch64-linux-gnu').glob('libgallium-*.so'))
        # Stock payload may retain an older Gallium; identify the library actually mapped.
        libraries = [p for p in libraries
                     if f'gallium={relative}/usr/lib/aarch64-linux-gnu/{p.name}\n' in result.stdout]
        if len(libraries) != 1:
            raise RuntimeError('expected exactly one mapped provider Gallium library')
        gallium = libraries[0]
        mapped = f'gallium={relative}/usr/lib/aarch64-linux-gnu/{gallium.name}\n' in result.stdout
        assertion = ('ir.cpp:1603:' in result.stderr and 'ir_constant::is_value' in result.stderr
                     and 'Assertion' in result.stderr)
        driver_matches = mapped and 'renderer=softpipe\n' in result.stdout
        shader_passed = (driver_matches and result.returncode == 0 and 'Assertion' not in result.stderr
                         and 'shader_compile_link=PASS\n' in result.stdout
                         and 'render_readback=PASS\n' in result.stdout)
        matches_expected = (driver_matches and result.returncode == 134 and assertion
                            if name == 'old' else shader_passed)
        report['cases'][name] = {
            'snap': snap.name, 'snap_sha256': sha(snap), 'gallium_sha256': sha(gallium),
            'returncode': result.returncode, 'assertion_observed': assertion,
            'expected_result': 'exit 134 at ir_constant::is_value' if name == 'old' else 'compile/link/readback pass',
            'shader_passed': shader_passed, 'matches_expected': matches_expected,
            'stdout': result.stdout, 'stderr': result.stderr}
        label = 'EXPECTED_ASSERTION' if name == 'old' else 'PASS'
        print(name, label if matches_expected else 'FAIL', 'exit', result.returncode, flush=True)
    passed = all(case['matches_expected'] for case in report['cases'].values())
    report['status'] = 'OFFLINE_GLSL_REGRESSION_PASS' if passed else 'FAIL'
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    if not passed:
        raise SystemExit('GLSL regression failed; see result.json and per-provider logs')


if __name__ == '__main__':
    main()
