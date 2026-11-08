#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Adapted for piano, 2026-10-11; see mesa/snap/provenance.json.
"""Build the pinned piano gpu-2404 provider in a disposable ARM64 core24 SDK.

No root or device access: unprivileged namespaces and qemu-user binfmt only.
Mesa commit/patch: mesa/source.json. SDK: mesa/snap/core24-sdk.json.
--native-nir executes the architecture-independent Python generator natively.
--resume continues only this attempt's frozen source/recipe and compatible objects.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
INPUTS = REPO / 'mesa'
# The recipe copied into the build root: exactly what the native build uses.
RECIPE = ('tools/build-mesa.py', 'mesa', 'tools/lib/build_root.py', 'tools/lib/deps.py', 'deps/sources.json',
          'LICENSES/oneplus-mesa-tools-GPL-2.0-only.txt')
BINFMT = Path('/proc/sys/fs/binfmt_misc/qemu-aarch64')

# Build-root mechanics are shared; neither package builder imports the other.
_spec = importlib.util.spec_from_file_location('piano_build_root', REPO / 'tools/lib/build_root.py')
root_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(root_tools)
WS = root_tools.WS


def die(message):
    raise SystemExit(f'build-mesa-root: {message}')


def interpreter():
    """The qemu-user binary that runs the build root, for the build record."""
    fields = dict(line.replace(':', '').split(' ', 1) for line in BINFMT.read_text().splitlines()
                  if ' ' in line)
    path = Path(fields['interpreter'].strip()).resolve()
    version = subprocess.run([str(path), '--version'], capture_output=True, text=True).stdout
    return {'path': str(path), 'sha256': root_tools.sha256(path),
            'version': version.splitlines()[0] if version else '', 'flags': fields.get('flags', '').strip()}


def native_nir(build, ninja, log):
    """Execute Mesa's architecture-independent NIR generator with host Python.

    Use a one-target Ninja file so host-side manifest regeneration cannot
    mistake the SDK's pkg-config paths for missing host build dependencies.
    The command and output name are unchanged; Ninja records real completion
    in the shared build log for the subsequent ARM64 compile.
    """
    directory = build / 'out/build'
    target = 'src/compiler/nir/nir_opt_algebraic.c'
    command = subprocess.check_output([ninja, '-C', str(directory), '-t', 'commands', target],
                                      text=True).strip()
    argv = shlex.split(command)
    if '\n' in command or len(argv) != 4 or not argv[1].endswith('/nir_opt_algebraic.py') \
            or argv[2:] != ['--out', target]:
        die('unexpected NIR generation rule; do not execute it as a native helper')
    python = Path(argv[0])
    info = json.loads(subprocess.check_output([str(python), '-c',
        'import json,sys,mako,markupsafe; print(json.dumps(dict('
        'python=sys.version,mako=mako.__version__,markupsafe=markupsafe.__version__)))'], text=True))
    info.update(python_sha256=root_tools.sha256(python),
                ninja_sha256=root_tools.sha256(Path(shutil.which(ninja))),
                ninja_version=subprocess.check_output([ninja, '--version'], text=True).strip(),
                generator_sha256=root_tools.sha256(directory / argv[1]),
                command=command, scope='native build-only Python generator; target compilation stays in ARM64 SDK')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ninja', dir=directory) as recipe:
        recipe.write('rule native_nir\n  command = ' + command + '\n'
                     'build ' + target + ': native_nir\n')
        recipe.flush()
        with log.open('a') as output:
            subprocess.run([ninja, '-C', str(directory), '-f', recipe.name, target], check=True,
                           env=dict(os.environ, PYTHONHASHSEED='0', PYTHONDONTWRITEBYTECODE='1'),
                           stdout=output, stderr=subprocess.STDOUT)
    info['generated_sha256'] = root_tools.sha256(directory / target)
    (build / 'out/native-generator.json').write_text(json.dumps(info, indent=2) + '\n')
    return info


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True,
                        help='new directory for the package and its records')
    parser.add_argument('--work', type=Path, default=WS / 'out/mesa-root-build')
    parser.add_argument('--cache', type=Path, default=WS / 'cache/mesa')
    parser.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--target', choices=('gpu-2404',), default='gpu-2404')
    parser.add_argument('--base-snap', type=Path, required=True, help='Stock mesa-2404 pinned by mesa/snap/mesa-2404.json')
    parser.add_argument('--mesa-source', '--source-archive', type=Path, required=True,
                        help='Official source archive pinned by mesa/source.json')
    parser.add_argument('--resume', action='store_true',
                        help='continue this work directory after a failed build; source/flags must match')
    parser.add_argument('--native-nir', action='store_true',
                        help='run the NIR Python generator on the host using its original Ninja command; '
                             'requires the SDK Python minor version and Mako on the host')
    parser.add_argument('--cross-file', type=Path,
                        help='ARM64 Meson cross file using WORK/root as sysroot; run compilation '
                             'and packaging natively, retaining the pinned target SDK')
    parser.add_argument('--ninja', default='ninja', help='native Ninja executable for --native-nir')
    parser.add_argument('--keep-build', action='store_true', help='Retain unstripped build objects for crash symbolization')
    parser.add_argument('--clean', action='store_true',
                        help='remove the work directory, build root included, after success')
    args = parser.parse_args()
    if args.keep_build and args.clean:
        parser.error('--keep-build and --clean are mutually exclusive')
    if args.cross_file and args.native_nir:
        parser.error('--cross-file already runs all build generators natively')
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    if shutil.which('unshare') is None:
        die('unshare is required')
    if not BINFMT.is_file():
        die('arm64 binfmt (qemu-user-static) is required to run the build root')
    if not root_tools.HOST_CA.is_file():
        die(f'{root_tools.HOST_CA} is required for TLS to the snapshot service')

    stock = json.loads((INPUTS / 'snap/mesa-2404.json').read_text())
    if root_tools.sha256(args.base_snap) != stock['sha256']:
        die('stock mesa-2404 does not match its lock')
    source = json.loads((INPUTS / 'source.json').read_text())
    pinfile = INPUTS / 'snap/core24-sdk.json'
    pins = json.loads(pinfile.read_text())
    recipe_names = RECIPE + ('tools/package-mesa-snap.py', 'packaging/mesa-snap')
    base = root_tools.fetch(pins['build_root']['url'], args.cache / pins['build_root']['archive'],
                            pins['build_root']['sha256'])
    args.cache.mkdir(parents=True, exist_ok=True)
    archive = args.mesa_source.resolve()
    if not archive.is_file() or root_tools.sha256(archive) != source['archive_sha256']:
        die('Official source archive does not match mesa/source.json')
    bootstrap = [(entry, root_tools.fetch(entry['url'], args.cache / entry['file'], entry['sha256']))
                 for entry in pins.get('build_tools', [])]

    output = args.output.resolve()
    if output.exists():
        die(f'{output} exists; select a new --output')
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    with open(work / '.lock', 'w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        log = work / 'build.log'
        if not args.resume:
            log.write_text('')
        root = root_tools.prepare_root(work, pins, base, log)
        build = root / 'build'
        if build.exists() and not args.resume:
            shutil.rmtree(build)
        if args.resume:
            if not (build / 'out/input.json').is_file():
                die('--resume requires a configured build from this work directory')
            if (build / 'debs').exists():
                die('packaging already started; use a fresh work directory')
            identity = json.loads((build / 'out/input.json').read_text())
            if identity['source'] != source or identity['profile'] != args.target \
                    or identity['recipe_sha256'] != root_tools.sha256(build / 'recipe/tools/build-mesa.py'):
                die('the frozen compile recipe/source differs from this attempt')
            if root_tools.sha256(build / 'recipe/mesa/snap' / pinfile.name) != root_tools.sha256(pinfile):
                die('the SDK/tool pins differ from this attempt')
        for name in (() if args.resume else recipe_names):
            path = REPO / name
            if path.is_dir():
                shutil.copytree(path, build / 'recipe' / name,
                                ignore=shutil.ignore_patterns('__pycache__'))
            else:
                (build / 'recipe' / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, build / 'recipe' / name)
        (build / 'inputs').mkdir(exist_ok=True)
        shutil.copyfile(archive, build / 'inputs' / source['archive'])
        shutil.copyfile(args.base_snap, build / 'inputs' / stock['file'])
        for entry, path in (() if args.resume else bootstrap):
            shutil.copyfile(path, build / 'inputs' / entry['file'])
            inside = '/build/inputs/' + entry['file']
            if entry['kind'] == 'deb':
                root_tools.chroot(root, f'dpkg -i {inside}', log=log)
            elif entry['kind'] == 'wheel':
                root_tools.chroot(root, 'rm -rf /opt/piano-meson\n'
                                 f'python3 -m zipfile -e {inside} /opt/piano-meson\n'
                                 "printf '%s\\n' '#!/bin/sh' 'PYTHONPATH=/opt/piano-meson exec python3 -m mesonbuild.mesonmain \"$@\"' > /usr/local/bin/meson\n"
                                 'chmod 755 /usr/local/bin/meson\n', log=log)
            else:
                die('unknown build tool format: ' + entry['kind'])
        mode = 'with the native cross-toolchain' if args.cross_file else 'under qemu-user'
        print(f'  building Mesa {source["version"]} ({args.target} profile) and packaging it '
              f'{mode}; progress in {log}', flush=True)
        packager = 'package-mesa-snap.py'
        native = None
        if args.native_nir:
            if not args.resume:
                root_tools.chroot(root, (
                    f'python3 -u /build/recipe/tools/build-mesa.py --profile {args.target} '
                    f'--source-archive /build/inputs/{source["archive"]} --output /build/out --configure-only\n'),
                    log=log)
            native = native_nir(build, args.ninja, log)
        if args.cross_file:
            # The SDK's pinned Meson is pure Python and can run on the build host.
            host_bin = work / 'host-bin'
            host_bin.mkdir(exist_ok=True)
            meson = host_bin / 'meson'
            meson.write_text('#!/bin/sh\nPYTHONPATH=' + shlex.quote(str(root / 'opt/piano-meson')) +
                             ' exec ' + shlex.quote(sys.executable) +
                             ' -m mesonbuild.mesonmain "$@"\n')
            meson.chmod(0o755)
            env = dict(os.environ, PATH=str(host_bin) + os.pathsep + os.environ['PATH'])
            with log.open('a') as output_log:
                subprocess.run([sys.executable, '-u', str(build / 'recipe/tools/build-mesa.py'),
                                '--profile', args.target, '--jobs', str(args.jobs),
                                '--cross-file', str(args.cross_file.resolve()), '--target-root', str(root),
                                '--source-archive', str(build / 'inputs' / source['archive']),
                                '--output', str(build / 'out')], check=True, env=env,
                               stdout=output_log, stderr=subprocess.STDOUT)
                subprocess.run([sys.executable, str(build / 'recipe/tools' / packager),
                                '--build-output', str(build / 'out'),
                                '--base-snap', str(build / 'inputs' / stock['file']),
                                '--output', str(build / 'debs')], check=True, env=env,
                               stdout=output_log, stderr=subprocess.STDOUT)
        else:
            root_tools.chroot(root, (
                f'python3 -u /build/recipe/tools/build-mesa.py --profile {args.target} --jobs {args.jobs} '
                f'--source-archive /build/inputs/{source["archive"]} --output /build/out\n'
                f'python3 -u /build/recipe/tools/{packager} --build-output /build/out --base-snap /build/inputs/{stock['file']} --output /build/debs\n'),
                log=log)

        installed = root_tools.capture(root, "dpkg-query -W -f='${Package} ${Version}\\n'").splitlines()
        output.mkdir(parents=True)
        manifest = json.loads((build / 'debs/manifest.json').read_text())
        package = build / 'debs' / manifest['package']['file']
        if root_tools.sha256(package) != manifest['package']['sha256']:
            die('the package does not match its manifest')
        for path in (package, build / 'debs/manifest.json', build / 'out/input.json',
                     build / 'out/build-result.json', build / 'out/build/meson-logs/meson-log.txt',
                     build / 'out/build/meson-info/intro-buildoptions.json'):
            shutil.copy2(path, output / path.name)
        # One entry per recipe file, keyed by its repository path.
        frozen = build / 'recipe'
        recipe = {str(path.relative_to(frozen)): root_tools.sha256(path)
                  for name in recipe_names
                  for path in ([frozen / name] if (frozen / name).is_file()
                               else sorted((frozen / name).rglob('*')))
                  if path.is_file() and '__pycache__' not in path.parts}
        recipe['tools/build-mesa-root.py'] = root_tools.sha256(Path(__file__))
        record = {'build_root': pins['build_root'], 'apt_snapshot': pins['apt']['snapshot'],
                  'build_root_pins_sha256': root_tools.sha256(pinfile),
                  'build_tools': pins.get('build_tools', []),
                  'recipe_sha256': recipe, 'interpreter': interpreter(),
                  'build_packages': installed, 'package': manifest['package']}
        if args.cross_file:
            record['cross_file_sha256'] = root_tools.sha256(args.cross_file)
            record['compilation'] = 'native cross-toolchain; pinned ARM64 SDK libraries and headers'
        if native:
            record['native_generator'] = native
        (output / 'build-root.json').write_text(json.dumps(record, indent=2) + '\n')
        if not args.keep_build:
            shutil.rmtree(build)
    if args.clean:
        shutil.rmtree(work)
    print(json.dumps(manifest['package']))


if __name__ == '__main__':
    main()
