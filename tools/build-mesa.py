#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Adapted for piano, 2026-10-11; see mesa/snap/provenance.json.
"""Build pinned Freedreno inputs into an isolated staging tree, never the live root."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[1]
INPUTS = REPO / 'mesa'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, **kwargs):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def version(command):
    return subprocess.check_output(command, text=True).splitlines()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--configure-only', action='store_true',
                        help='prepare the verified source and Ninja rules without compiling')
    parser.add_argument('--profile', choices=('probe', 'desktop', 'gpu-2404'), default='probe')
    parser.add_argument('--cross-file', type=Path,
                        help='Meson cross file for an ARM64 SDK; compilation runs on the build host')
    parser.add_argument('--target-root', type=Path,
                        help='Debian SDK sysroot used by --cross-file, for recording target packages')
    args = parser.parse_args()
    if bool(args.cross_file) != bool(args.target_root):
        parser.error('--cross-file and --target-root must be supplied together')
    if not args.cross_file and platform.machine() not in ('aarch64', 'arm64'):
        parser.error('use a native arm64 environment or an ARM64 Meson cross file and SDK root')
    if not 1 <= args.jobs <= 8:
        parser.error('--jobs must be between 1 and 8')
    lock = json.loads((INPUTS / 'source.json').read_text())
    archive = args.source_archive.resolve()
    if sha(archive) != lock['archive_sha256']:
        parser.error('source archive does not match the pinned SHA-256')
    patches = [{'path': lock['patch'], 'sha256': lock['patch_sha256']},
               *lock.get('additional_patches', [])]
    for patch in patches:
        if sha(REPO / patch['path']) != patch['sha256']:
            parser.error('patch does not match the pinned SHA-256: ' + patch['path'])
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with (out / '.lock').open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        content = args.profile == 'gpu-2404'
        prefix = '/usr' if content else '/usr/lib/piano/mesa'
        libdir = 'lib/aarch64-linux-gnu' if content else 'lib'
        drivers = 'freedreno,softpipe' if content else 'freedreno'
        # -O2, as Debian and Ubuntu build Mesa. GCC 13's -O3 loop unswitching
        # on switch statements miscompiles the GLSL compiler on ARM64:
        # ir_constant (src/compiler/glsl/ir.cpp) takes the default case for
        # float16 values, so mediump constants are lost and Flutter's
        # Impeller renderer draws no text. GCC 14 is not affected. Meson keeps
        # --buildtype's -O3 if the level is given as -Doptimization, so pass
        # --optimization and check the configured value below.
        flags = [f'--prefix={prefix}', f'--libdir={libdir}', '--buildtype=release',
                 '--optimization=2', '--wrap-mode=nofallback',
                 '-Db_ndebug=true' if content else '-Db_ndebug=false', '-Db_lto=false',
                 f'-Dgallium-drivers={drivers}', '-Degl=enabled',
                 '-Dgbm=enabled', '-Dgles1=disabled', '-Dgles2=enabled',
                 '-Dllvm=disabled', '-Dgallium-va=disabled', '-Dvideo-codecs=[]',
                 '-Dvalgrind=disabled', '-Dbuild-tests=false']
        if args.profile in ('desktop', 'gpu-2404'):
            # Turnip's loader manifest is staged under the private prefix and
            # names the private library by absolute path; where the loader
            # finds it is the package's decision. SPIRV-Tools is optional in
            # Mesa and pinned off, so an installed spirv-tools cannot add a
            # runtime dependency.
            flags += ['-Dplatforms=wayland,x11', '-Dglx=dri', '-Dglvnd=enabled',
                      '-Dvulkan-drivers=freedreno', '-Dspirv-tools=disabled']
            if not content:
                flags += [f'-Dc_link_args=-Wl,-rpath,{prefix}/lib',
                          f'-Dcpp_link_args=-Wl,-rpath,{prefix}/lib']
        else:
            flags += ['-Dplatforms=[]', '-Dglx=disabled', '-Dglvnd=disabled',
                      '-Dvulkan-drivers=[]']
        packages = subprocess.check_output(
            ['dpkg-query', '-W', '-f=${Package} ${Version}\\n'], text=True)
        identity = {'source': lock, 'flags': flags, 'profile': args.profile,
                    'recipe_sha256': sha(__file__),
                    'native_packages': packages.splitlines(),
                    'tools': {name: version([name, '--version']) for name in ('gcc', 'g++', 'meson', 'ninja')}}
        if args.cross_file:
            identity['cross_file_sha256'] = sha(args.cross_file)
            identity['target_packages'] = subprocess.check_output(
                ['dpkg-query', '--admindir=' + str(args.target_root.resolve() / 'var/lib/dpkg'),
                 '-W', '-f=${Package} ${Version}\\n'], text=True).splitlines()
        identity_file = out / 'input.json'
        if identity_file.exists() and json.loads(identity_file.read_text()) != identity:
            parser.error('build inputs changed; select a new output directory')
        src = out / ('mesa-' + lock['version'])
        if not identity_file.exists():
            if src.exists():
                parser.error('unidentified source tree exists in output directory')
            with tarfile.open(archive) as tar:
                epoch = int(tar.getmember('mesa-' + lock['version'] + '/VERSION').mtime)
                tar.extractall(out, filter='data')
            for patch in patches:
                run(['patch', '-p1', '--batch', '--fuzz=0', '-i', REPO / patch['path']], cwd=src)
            identity_file.write_text(json.dumps(identity, indent=2) + '\n')
            (out / 'source-date-epoch').write_text(str(epoch))
        # Mesa's isaspec generator orders instruction fields by iterating a
        # Python set (src/compiler/isaspec/isa.py), so the generated ir3
        # encoders, and the libgallium and Turnip code built from them, change
        # with the string hash seed unless it is fixed.
        env = dict(os.environ, SOURCE_DATE_EPOCH=(out / 'source-date-epoch').read_text().strip(),
                   PYTHONHASHSEED='0',
                   CFLAGS=f'-ffile-prefix-map={src}=/mesa', CXXFLAGS=f'-ffile-prefix-map={src}=/mesa')
        build = out / 'build'
        if not (build / 'build.ninja').exists():
            cross = ['--cross-file', args.cross_file.resolve()] if args.cross_file else []
            run(['meson', 'setup', build, src, *flags, *cross], env=env)
        machines = json.loads(subprocess.check_output(
            ['meson', 'introspect', '--machines', str(build)], text=True))
        if machines['host']['cpu_family'] != 'aarch64':
            parser.error('the configured Mesa target must be ARM64')
        options = {option['name']: option['value'] for option in json.loads(subprocess.check_output(
            ['meson', 'introspect', '--buildoptions', str(build)], text=True))}
        if options['optimization'] != '2':
            parser.error(f"Mesa is configured at -O{options['optimization']}, not -O2")
        if args.configure_only:
            return
        run(['meson', 'compile', '-C', build, '-j', args.jobs], env=env)
        stage = out / 'stage'
        run(['meson', 'install', '-C', build, '--no-rebuild', '--destdir', stage], env=env)
        files = {str(path.relative_to(stage)): sha(path)
                 for path in sorted(stage.rglob('*')) if path.is_file() and not path.is_symlink()}
        symlinks = {str(path.relative_to(stage)): os.readlink(path)
                    for path in sorted(stage.rglob('*')) if path.is_symlink()}
        result = {'status': 'OFFLINE_BUILD_ONLY', 'version': lock['version'],
                  'profile': args.profile, 'prefix': prefix,
                  'input_sha256': sha(identity_file), 'files_sha256': files,
                  'symlinks': symlinks}
        (out / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'status': result['status'], 'staged_files': len(files)}), flush=True)


if __name__ == '__main__':
    main()
