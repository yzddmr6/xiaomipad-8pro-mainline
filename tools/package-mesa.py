#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Package the shared, verified ARM64 desktop Mesa build without device access."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import shlex
import subprocess

REPO = Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mesa-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--dependency-root', type=Path,
                        help='ARM64 SDK used for symbol-version dependency scanning; native root by default')
    args = parser.parse_args()
    build, output = args.mesa_build.resolve(), args.output.resolve()
    source = json.loads((REPO / 'mesa/source.json').read_text())
    result = json.loads((build / 'build-result.json').read_text())
    identity = json.loads((build / 'input.json').read_text())
    manifest = json.loads((build / 'manifest.json').read_text())
    if (result['profile'] != 'desktop' or result['input_sha256'] != sha(build / 'input.json')
            or result['target_compilers_sha256'] != sha(build / 'target-compilers.json')
            or manifest['build_input_sha256'] != result['input_sha256']
            or manifest['files'] != result['files_sha256']
            or identity['source'] != source or identity['recipe_sha256'] != sha(REPO / 'tools/build-mesa.py')):
        parser.error('incompatible or unverified desktop build')
    output.mkdir(parents=True, exist_ok=True)
    stage = output / 'stages/piano-mesa'
    subprocess.run(['python3', str(REPO / 'tools/prepare-mesa-initramfs.py'),
                    '--mesa-build', str(build), '--stage', str(stage)], check=True,
                   stdout=subprocess.DEVNULL)
    prefix = stage / source['prefix'].lstrip('/')
    ldconf = stage / 'etc/ld.so.conf.d/00-piano-mesa.conf'
    ldconf.parent.mkdir(parents=True)
    ldconf.write_text(source['prefix'] + '/lib\n')
    icds = list((prefix / 'share/vulkan/icd.d').glob('*.json'))
    if len(icds) != 1:
        parser.error('expected exactly one Turnip ICD')
    icd = stage / 'usr/share/vulkan/icd.d/piano_freedreno.json'
    icd.parent.mkdir(parents=True)
    icd.symlink_to('/' + str(icds[0].relative_to(stage)))
    # dpkg's symbols databases provide the minimum ABI versions. Private Mesa
    # libraries are shipped together and have no distribution shlibs entries.
    scan = output / 'dependency-scan'
    (scan / 'debian').mkdir(parents=True)
    (scan / 'debian/control').write_text('Source: piano-mesa\n\nPackage: piano-mesa\nArchitecture: arm64\n')
    configured = json.loads((build / 'configured.json').read_text())
    sysroots = [x.split('=', 1)[1] for x in configured['compilers']['host']['c']['exelist']
                if x.startswith('--sysroot=')]
    root = (args.dependency_root.resolve() if args.dependency_root
            else Path(sysroots[0]) if sysroots else Path('/'))
    libraries = [p for p in prefix.rglob('*') if p.is_file() and not p.is_symlink()
                 and p.read_bytes()[:4] == b'\x7fELF']
    private_shlibs = []
    for library in libraries:
        dynamic = subprocess.check_output(['readelf', '-d', str(library)], text=True)
        match = re.search(r'\(SONAME\).*\[([^]]+)\]', dynamic)
        if match:
            soname = re.fullmatch(r'(.+)\.so\.(.+)', match[1])
            if soname:
                private_shlibs.append(f'{soname[1]} {soname[2]} piano-mesa')
    (scan / 'debian/shlibs.local').write_text('\n'.join(sorted(set(private_shlibs))) + '\n')
    if root == Path('/'):
        command = ['dpkg-shlibdeps', '-O', '--ignore-missing-info', '-xpiano-mesa', '-l' + str(prefix / 'lib'),
                   *['-e' + str(p) for p in libraries]]
    else:
        # Run the target's dpkg tools inside its own root: host dpkg cannot
        # resolve ownership of sysroot-prefixed libraries using target lists.
        inside = '/tmp/piano-mesa-shlibdeps'
        mountpoint = root / inside.lstrip('/')
        mountpoint.mkdir(parents=True, exist_ok=True)
        (scan / 'package').mkdir()
        library_dir = inside + '/package' + source['prefix'] + '/lib'
        scan_args = ['dpkg-shlibdeps', '-O', '--ignore-missing-info', '-xpiano-mesa', '-l' + library_dir,
                     *['-e' + inside + '/package/' + str(p.relative_to(stage)) for p in libraries]]
        script = ('set -eu; mount --bind "$1" "$2/package"; mount --rbind "$2" "$3"; '
                  'exec chroot "$4" /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C '
                  'QEMU_CPU=max,pauth-impdef=on /bin/sh -c "$5"')
        command = ['unshare', '--map-root-user', '--mount', '--net', '--pid', '--fork', '--kill-child',
                   'sh', '-c', script, 'sh', str(stage), str(scan), str(mountpoint), str(root),
                   'cd ' + shlex.quote(inside) + ' && ' + shlex.join(scan_args)]
    proc = subprocess.run(command, cwd=scan, text=True, capture_output=True,
                          env=dict(os.environ, QEMU_CPU='max,pauth-impdef=on'))
    (scan / 'scan.log').write_text(proc.stdout + proc.stderr)
    proc.check_returncode()
    if not proc.stdout.startswith('shlibs:Depends='):
        raise RuntimeError('dependency scan returned no Depends')
    depends = proc.stdout.strip().split('=', 1)[1]
    if 'libc6' not in depends:
        raise RuntimeError('dependency scan did not resolve target libc')
    control = stage / 'DEBIAN'
    control.mkdir()
    (control / 'control').write_text(
        f'Package: piano-mesa\nVersion: {args.version}\nArchitecture: arm64\n'
        'Maintainer: yzddmr6 <46088090+yzddmr6@users.noreply.github.com>\n'
        'Section: libs\nPriority: optional\n'
        f'Depends: {depends}\n'
        'Description: Shared Adreno 830 Freedreno and Turnip drivers\n'
        ' Private Mesa libraries selected through the system dynamic linker.\n')
    for name, cases in [('postinst', 'configure'), ('postrm', 'remove|purge|disappear')]:
        p = control / name
        p.write_text(f'#!/bin/sh\nset -e\ncase "$1" in {cases}) ldconfig ;; esac\n')
        p.chmod(0o755)
    doc = stage / 'usr/share/doc/piano-mesa'
    doc.mkdir(parents=True)
    shutil.copy2(build / ('mesa-' + source['version']) / 'docs/license.rst', doc / 'copyright')
    provenance = {'source': source, 'build': result,
                  'configured': {k: configured[k] for k in
                                 ('optimization', 'b_ndebug', 'target_compile_commands_checked')},
                  'compilers': {lang: {k: compiler[k] for k in ('id', 'version', 'full_version')}
                                for lang, compiler in configured['compilers']['host'].items()},
                  'depends': depends, 'packager_sha256': sha(Path(__file__))}
    (doc / 'build.json').write_text(json.dumps(provenance, indent=2) + '\n')
    package = output / f'piano-mesa_{args.version}_arm64.deb'
    subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '-z1', '--build', str(stage), str(package)],
                   check=True, env=dict(os.environ, SOURCE_DATE_EPOCH=(build / 'source-date-epoch').read_text().strip(),
                                        DPKG_DEB_THREADS_MAX=str(os.cpu_count() or 1)))
    receipt = {'file': package.name, 'sha256': sha(package), 'depends': provenance['depends'],
               'files': {str(p.relative_to(stage)): sha(p) for p in sorted(stage.rglob('*'))
                         if p.is_file() and not p.is_symlink() and control not in p.parents}}
    (output / 'mesa-package.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'file': package.name, 'sha256': receipt['sha256']}))


if __name__ == '__main__':
    main()
