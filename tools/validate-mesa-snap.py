#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check gpu-2404 layout/ELFs and softpipe pixels in a fresh core24 userspace.

No snap daemon, GPU device, display, confinement or hardware acceptance.
The SDK is used only to compile mesa/egl-smoke.c; runtime has no SDK packages.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import shlex

import yaml

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_root', REPO / 'tools/lib/build_root.py')
roots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roots)
spec = importlib.util.spec_from_file_location('packager', REPO / 'tools/package-mesa-snap.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def run(command):
    return subprocess.check_output(list(map(str, command)), text=True, stderr=subprocess.STDOUT,
                                   env=dict(os.environ, LC_ALL="C"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--sdk-root', type=Path, required=True)
    ap.add_argument('--base', type=Path, help='Optional SDK base archive hash check')
    ap.add_argument('--stock-provider', type=Path, required=True)
    ap.add_argument('--gnome-runtime', type=Path, help='GNOME content snap for consumer dependency simulation')
    ap.add_argument('--old-provider', type=Path, help='Previous provider snap for consumer negative control')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--consumer', type=Path, action='append', default=[], help='Frozen consumer .snap (repeatable)')
    ap.add_argument('--core24', type=Path, required=True, help='Actual seeded ARM64 core24 runtime snap')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    pins = json.loads((REPO / 'mesa/snap/core24-sdk.json').read_text())
    if args.base:
        assert roots.sha256(args.base) == pins['build_root']['sha256']
    manifest = json.loads((args.build / 'manifest.json').read_text())
    snap = (args.build / manifest['package']['file']).resolve()
    assert roots.sha256(snap) == manifest['package']['sha256']
    assert manifest['source'] == json.loads((REPO / 'mesa/source.json').read_text())
    (out / 'squashfs.txt').write_text(run(['unsquashfs', '-s', snap]))
    metadata = json.loads(run(['unsquashfs', '-cat', snap, 'meta/snap.yaml']))
    assert metadata['name'] == 'piano-mesa-2404' and metadata['base'] == 'core24'
    assert metadata['architectures'] == ['arm64'] and metadata['confinement'] == 'strict'
    assert metadata['slots'] == {'gpu-2404': {'interface': 'content', 'read': ['$SNAP']}}
    (out / 'snap.json').write_text(json.dumps(metadata, indent=2)+'\n')
    root = out / 'runtime'
    # No SDK/APT libraries enter this root. Skip device nodes/xattrs during extraction;
    # the private runtime below binds only non-GPU random/null nodes.
    (out / 'core24-unpack.log').write_text(run([
        'unsquashfs', '-no-progress', '-no-xattrs', '-excludes', '-d', root, args.core24, 'dev']))
    provider = root / 'test/provider'
    provider.parent.mkdir()
    (out / 'unpack.log').write_text(run(['unsquashfs', '-no-progress', '-d', provider, snap]))
    required = ['bin/gpu-2404-provider-wrapper', 'libdrm', 'drirc.d', 'usr/share/X11/locale', 'X11/XErrorDB']
    assert all((provider / p).exists() for p in required)
    assert os.access(provider / required[0], os.X_OK)
    stock_lock = json.loads((REPO / 'mesa/snap/mesa-2404.json').read_text())
    assert roots.sha256(args.stock_provider) == stock_lock['sha256']
    stock = out / 'stock'
    (out / 'stock-unpack.log').write_text(run(['unsquashfs', '-no-progress', '-d', stock, args.stock_provider]))
    before, after = packager.inventory(stock), packager.inventory(provider)
    missing = sorted(set(before) - set(after))
    stock_paths = {str(p.relative_to(stock)) for p in stock.rglob('*')}
    provider_paths = {str(p.relative_to(provider)) for p in provider.rglob('*')}
    missing_paths = sorted(stock_paths - provider_paths)
    changed = sorted(p for p in before if p in after and before[p] != after[p])
    allowed = {packager.destination(p) for p in
               set(manifest['build']['files_sha256']) | set(manifest['build']['symlinks'])
               if packager.mesa_payload(p)}
    unexpected = sorted(set(changed) - allowed - {'meta/snap.yaml'})
    content_diff = {'stock_files': len(before), 'provider_files': len(after), 'missing': missing,
                    'changed': changed, 'unexpected_changes': unexpected, 'added': sorted(set(after)-set(before)),
                    'stock_paths_including_directories': len(stock_paths), 'missing_paths': missing_paths}
    (out / 'stock-content-diff.json').write_text(json.dumps(content_diff, indent=2)+'\n')
    assert not missing and not missing_paths and not unexpected, content_diff
    stock_metadata = yaml.safe_load((stock / 'meta/snap.yaml').read_text())
    stock_metadata['slots']['gpu-2404']['read'] = ['$SNAP']
    assert {k:v for k,v in metadata.items() if k not in ('name','version')} == {
        k:v for k,v in stock_metadata.items() if k not in ('name','version')}
    consumers = {}
    for consumer in args.consumer:
        text = run(['unsquashfs', '-cat', consumer, 'meta/snap.yaml'])
        (out / (consumer.name + '.yaml')).write_text(text)
        assert 'base: core24' in text and 'target: $SNAP/gpu-2404' in text
        paths = re.findall(r'(?:bind|symlink): \$SNAP/gpu-2404/(\S+)', text)
        assert paths and all((provider / p).exists() for p in paths), paths
        consumers[consumer.name] = {'sha256': roots.sha256(consumer), 'layout': paths}
    # Compile against the core24 SDK; no SDK package is added to the clean runtime.
    sdk = args.sdk_root.resolve()
    shutil.copyfile(REPO / 'mesa/egl-smoke.c', sdk / 'tmp/piano-egl-smoke.c')
    roots.chroot(sdk, 'gcc -O2 -o /tmp/piano-egl-smoke /tmp/piano-egl-smoke.c -lEGL -lGLESv2', log=out / 'compile.log')
    shutil.copyfile(sdk / 'tmp/piano-egl-smoke', root / 'test/piano-egl-smoke')
    (root / 'test/piano-egl-smoke').chmod(0o755)
    shutil.copyfile(root / 'test/piano-egl-smoke', out / 'piano-egl-smoke')
    (out / 'piano-egl-smoke').chmod(0o755)
    for node in ('null', 'random', 'urandom'):
        (root / 'dev').mkdir(exist_ok=True)
        (root / 'dev' / node).touch()
    (root / 'tmp/runtime').mkdir(mode=0o700)
    # Bind only these three non-GPU devices, with a private network namespace.
    wrapper = ('set -eu; for node in null random urandom; do '
               'mount --bind /dev/$node "$1/dev/$node"; done; '
               'mount -t proc proc "$1/proc"; root=$1; shift; '
               'exec chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C QEMU_CPU=max,pauth-impdef=on '
               'XDG_RUNTIME_DIR=/tmp/runtime "$@"')
    prefix = ['timeout', '-k', '5', '120', 'unshare', '--map-root-user', '--mount', '--net',
              '--pid', '--fork', '--kill-child', 'sh', '-c', wrapper, 'sh', str(root),
              '/test/provider/bin/gpu-2404-provider-wrapper']
    log = run([*prefix, '/usr/bin/env', 'LIBGL_ALWAYS_SOFTWARE=1', 'GALLIUM_DRIVER=softpipe',
               '/test/piano-egl-smoke', 'softpipe'])
    (out / 'softpipe.log').write_text(log)
    assert 'renderer=softpipe' in log and 'render_readback=PASS' in log
    negative = subprocess.run([*prefix, '/usr/bin/env', 'LIBGL_ALWAYS_SOFTWARE=1', 'GALLIUM_DRIVER=softpipe',
                               '/test/piano-egl-smoke', 'Adreno'], capture_output=True, text=True)
    (out / 'hardware-negative.log').write_text(negative.stdout + negative.stderr)
    assert negative.returncode != 0 and 'Required renderer missing' in negative.stderr
    elves, highest, needed_glibc = {}, (0, 0), set()
    version_cache = {}
    libs = provider / 'usr/lib/aarch64-linux-gnu'
    for path in sorted(provider.rglob('*')):
        if not path.is_file() or path.is_symlink() or path.read_bytes()[:4] != b'\x7fELF':
            continue
        assert path.name not in ('libc.so.6', 'libm.so.6', 'ld-linux-aarch64.so.1')
        assert 'AArch64' in run(['readelf', '-h', path])
        dynamic = run(['readelf', '-d', path])
        if str(path.relative_to(provider)) in allowed:
            assert 'RPATH' not in dynamic and 'RUNPATH' not in dynamic
        version_info = run(['readelf', '--version-info', path])
        versions = version_info.split('Version needs section', 1)[1] if 'Version needs section' in version_info else ''
        requirements, dependency = {}, None
        for line in versions.splitlines():
            match = re.search(r'File: (\S+)', line)
            if match:
                dependency = match[1]
                requirements[dependency] = set()
            match = re.search(r'Name: (\S+)', line)
            if match and dependency:
                requirements[dependency].add(match[1])
        glibc = [tuple(map(int, v.split('.'))) for v in re.findall(r'Name: GLIBC_([0-9.]+)', versions)]
        # LLVM also requests a version called GLIBC_2.0 from libgcc_s, not libc.
        # ELF version namespaces belong to their named dependency, not their prefix.
        needed_glibc.update(requirements.get('libc.so.6', set()))
        assert 'GLIBC_PRIVATE' not in versions
        highest = max([highest, *glibc])
        relative = str(path.relative_to(root))
        print('ldd', relative, flush=True)
        deps = run([*prefix, '/usr/bin/ldd', '/' + relative])
        assert 'not found' not in deps and '/build/' not in deps, (relative, deps)
        resolved = dict(re.findall(r'(\S+) => (/\S+) \(', deps))
        for dependency, needed in requirements.items():
            location = resolved.get(dependency)
            if location is None and dependency == 'ld-linux-aarch64.so.1':
                location = '/lib/ld-linux-aarch64.so.1'
            assert location, (relative, dependency, deps)
            if location not in version_cache:
                dep_info = run(['readelf', '--version-info', root / location.lstrip('/')])
                definitions = dep_info.split('Version definition section', 1)[-1].split('Version needs section', 1)[0]
                version_cache[location] = set(re.findall(r'Name: (\S+)', definitions))
            assert needed <= version_cache[location], (relative, dependency, sorted(needed-version_cache[location]))
        elves[str(path.relative_to(provider))] = {'sha256': roots.sha256(path), 'dependencies': deps,
                                                  'dependency_versions_verified': {k:sorted(v) for k,v in requirements.items()},
                                                  'max_glibc': '.'.join(map(str, max(glibc, default=(0, 0))))}
    libc = root / 'usr/lib/aarch64-linux-gnu/libc.so.6'
    provided = max(tuple(map(int, v.split('.'))) for v in re.findall(
        r'Name: GLIBC_([0-9.]+)', run(['readelf', '--version-info', libc])))
    assert highest <= provided
    core24 = None
    if args.core24:
        core_libc = out / 'core24-libc.so.6'
        core_libc.write_bytes(subprocess.check_output(['unsquashfs', '-cat', str(args.core24),
                                                       'usr/lib/aarch64-linux-gnu/libc.so.6']))
        assert 'AArch64' in run(['readelf', '-h', core_libc])
        actual = set(re.findall(r'Name: (GLIBC_[\w.]+)', run(['readelf', '--version-info', core_libc])))
        assert needed_glibc <= actual, sorted(needed_glibc - actual)
        core24 = {'snap_sha256': roots.sha256(args.core24), 'libc_sha256': roots.sha256(core_libc),
                  'required_versions': sorted(needed_glibc), 'all_required_versions_provided': True}
    assert elves
    for folder in ('usr/share/glvnd/egl_vendor.d', 'usr/share/vulkan/icd.d'):
        for path in (provider / folder).glob('*.json'):
            name = json.loads(path.read_text())['ICD']['library_path']
            assert name == Path(name).name and (libs / name).exists()
    if args.gnome_runtime:
        # These libraries can be loaded dynamically rather than appear in the
        # executable's DT_NEEDED. Exercise the actual loader as well as ldd.
        shutil.copyfile(REPO / 'tools/fixtures/snap-graphics/dlopen.c', sdk / 'tmp/piano-dlopen.c')
        roots.chroot(sdk, 'gcc -O2 -o /tmp/piano-dlopen /tmp/piano-dlopen.c -ldl', log=out / 'dlopen-compile.log')
        shutil.copyfile(sdk / 'tmp/piano-dlopen', root / 'test/piano-dlopen')
        (root / 'test/piano-dlopen').chmod(0o755)
        gnome = root / 'test/gnome'
        (out / 'gnome-unpack.log').write_text(run(['unsquashfs', '-no-progress', '-d', gnome, args.gnome_runtime]))
        if args.old_provider:
            (out / 'old-provider-unpack.log').write_text(run([
                'unsquashfs', '-no-progress', '-d', root / 'test/old-provider', args.old_provider]))
        app_report = {}
        # Library search directories from the actual GNOME 46 desktop-launch. This
        # includes libproxy's absolute DT_RUNPATH workaround, not host libraries.
        gnome_paths = ['/test/gnome/' + p for p in (
            'lib/aarch64-linux-gnu', 'usr/lib/aarch64-linux-gnu', 'usr/lib', 'lib',
            'usr/lib/aarch64-linux-gnu/libunity', 'usr/lib/aarch64-linux-gnu/pulseaudio',
            'usr/lib/aarch64-linux-gnu/libproxy')]
        for consumer in args.consumer:
            meta = yaml.safe_load(run(['unsquashfs', '-cat', consumer, 'meta/snap.yaml']))
            if meta['name'] not in ('thunderbird', 'snap-store'):
                continue
            name = meta['name']
            app = root / 'test' / name
            (out / (name+'-unpack.log')).write_text(run(['unsquashfs', '-no-progress', '-d', app, consumer]))
            # The application entrypoint and its dlopened graphics/proxy users. ldd on
            # thunderbird-bin alone misses libxul, which the launcher loads later.
            targets = (['usr/lib/thunderbird/thunderbird-bin', 'usr/lib/thunderbird/libxul.so']
                       if name == 'thunderbird' else ['bin/snap-store', 'bin/lib/libflutter_linux_gtk.so'])
            targets = [app / p for p in targets]
            targets += [gnome / 'usr/lib/aarch64-linux-gnu/libproxy.so.1',
                        gnome / 'usr/lib/aarch64-linux-gnu/libproxy/libpxbackend-1.0.so']
            rows = {}
            for variant in (['old-provider', 'provider'] if args.old_provider else ['provider']):
                # Respect consumer -> provider -> GNOME order used by the command chain.
                libpaths = ([f'/test/{name}/usr/lib', f'/test/{name}/usr/lib/aarch64-linux-gnu']
                            if name == 'thunderbird' else [])
                libpaths += [f'/test/{variant}/usr/lib/aarch64-linux-gnu',
                             f'/test/{variant}/usr/lib/aarch64-linux-gnu/vdpau', *gnome_paths]
                paths = ':'.join(libpaths)
                logs = {}
                for target in targets:
                    assert target.is_file(), target
                    rel = '/' + str(target.relative_to(root))
                    target_paths = paths
                    if name == 'thunderbird' and target.name == 'libxul.so':
                        # Mozilla's glue preloads dependentlibs.list from the binary's
                        # directory. ldd on libxul alone does not execute that glue.
                        assert (app / 'usr/lib/thunderbird/dependentlibs.list').is_file()
                        target_paths = '/test/thunderbird/usr/lib/thunderbird:' + paths
                    result = subprocess.run([*prefix[:-1], '/usr/bin/env', 'LD_LIBRARY_PATH='+target_paths,
                                             '/usr/bin/ldd', rel], capture_output=True, text=True)
                    logs[rel] = {'returncode': result.returncode, 'library_path': target_paths, 'output': result.stdout+result.stderr}
                (out / f'{name}-{variant}-ldd.json').write_text(json.dumps(logs, indent=2)+'\n')
                unresolved = sorted(set(re.findall(r'(\S+) => not found', '\n'.join(v['output'] for v in logs.values()))))
                dynamic = subprocess.run([*prefix[:-1], '/usr/bin/env', 'LD_LIBRARY_PATH='+paths,
                                          '/test/piano-dlopen', 'libXfixes.so.3', 'libxml2.so.2',
                                          'libpxbackend-1.0.so'], capture_output=True, text=True)
                (out / f'{name}-{variant}-dlopen.log').write_text(dynamic.stdout+dynamic.stderr)
                if variant == 'provider':
                    assert dynamic.returncode == 0, dynamic.stderr
                    assert not unresolved and all(v['returncode']==0 and 'not found' not in v['output'] for v in logs.values()), logs
                    combined = dynamic.stdout
                    for required_lib in ('libXfixes.so.3', 'libxml2.so.2', 'libpxbackend-1.0.so'):
                        assert re.search(re.escape(required_lib)+r' => /', combined), required_lib
                else:
                    assert 'libXfixes.so.3' in unresolved or 'libxml2.so.2' in unresolved, unresolved
                    assert dynamic.returncode != 0, dynamic.stdout
                rows[variant] = {'unresolved': unresolved, 'elf_targets': list(logs),
                                 'dlopen_returncode': dynamic.returncode, 'dlopen_paths': dynamic.stdout}
            app_report[name] = rows
        (out / 'consumer-dependencies.json').write_text(json.dumps(
            {'gnome_runtime_sha256': roots.sha256(args.gnome_runtime), 'consumers': app_report,
             'scope': 'ldd under qemu-user with actual core24, provider, GNOME and consumer payloads; no app launch or confinement'}, indent=2)+'\n')
    (out / 'elf-layout.json').write_text(json.dumps(elves, indent=2)+'\n')
    report = {'status': 'OFFLINE_SOFTWARE_ONLY_PASS', 'package': {**manifest['package'], 'bytes': snap.stat().st_size},
              'sdk_base': pins['build_root'], 'core24': core24, 'stock_content_diff': content_diff, 'metadata': metadata, 'consumers': consumers,
              'elf_count': len(elves), 'max_glibc_required': '.'.join(map(str, highest)),
              'runtime_glibc_provided': '.'.join(map(str, provided)), 'layout': required,
              'software_renderer': 'softpipe', 'hardware_required_negative': negative.returncode,
              'probe_sha256': roots.sha256(out / 'piano-egl-smoke'),
              'not_verified': ['snap install/connect', 'snap confinement/AppArmor', 'A830 rendering', 'snap-store output']}
    (out / 'validation.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
