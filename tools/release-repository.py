#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create, retain and install complete release batches using local APT repositories.

create runs on the build host. upgrade and rollback run on the target and retain
both package batches and a copy of the previous boot set. They never reboot.
After a completed upgrade or rollback, and again on request with cleanup, the
target keeps only what that transaction can still use: its two batches and
their boot sets, and the kernels they, the running system or a kept boot set
need. Other batches, boot copies, kernels, packages dropped from the releases
and orphaned kernel files are removed after the plan is written to cleanup.json.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile


# A kernel package of one release: the project's or any distribution kernel image,
# module, header or tool package. Distribution kernel metapackages follow none.
KERNEL_PACKAGE = re.compile(r'(?:linux-(?:image(?:-unsigned)?|headers|modules(?:-extra)?|main-modules-[a-z0-9]+|tools'
                            r'|cloud-tools|buildinfo|lib-rust)|erhai-kernel-image)-([0-9].*)')
KERNEL_META = re.compile(r'linux-(?:(?:image|headers|tools)-)?(?:generic|lowlatency|virtual)(?:-.+)?')
MODULE_ROOTS = (Path('/usr/lib/modules'), Path('/lib/modules'))
UNIT_LINKS = Path('/etc/systemd/system')
KERNEL_RETENTION = Path('/usr/libexec/erhai-kernel-retention')
BANNER = re.compile(rb'Linux version ([0-9][A-Za-z0-9.+_~-]*) \(')
UNIT = re.compile(r'[A-Za-z0-9:_.@\\-]+\.(?:service|socket|timer|path|target|mount|automount|swap|slice)')
APT_PURGE = ['apt-get', '-o', 'APT::Get::AutomaticRemove=false', 'purge']
PROJECT_PREFIXES = ('piano-', 'erhai-', 'qcom-npu-', 'gunyahvm', 'linux-image-')
PROJECT_BUILD = re.compile(r'[+~](piano|erhai|sm8750|gunyahvm)')


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def metadata(path):
    raw = subprocess.check_output(['dpkg-deb', '-f', str(path)], text=True)
    return dict(l.split(': ', 1) for l in raw.splitlines() if ': ' in l and not l.startswith(' '))


def index_stanza(repo, deb):
    """The APT index entry dpkg-scanpackages writes for deb, from dpkg-deb alone."""
    digests = [hashlib.md5(usedforsecurity=False), hashlib.sha1(usedforsecurity=False), hashlib.sha256()]
    with deb.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            for digest in digests:
                digest.update(block)
    md5, sha1, sha256 = (d.hexdigest() for d in digests)
    lines = subprocess.check_output(['dpkg-deb', '-f', str(deb)], text=True).rstrip('\n').splitlines()

    def empty(i):  # a field without a value, such as an empty Depends:, which dpkg-scanpackages drops
        return re.fullmatch(r'[^\s:]+:\s*', lines[i]) and not (i + 1 < len(lines) and lines[i + 1][:1].isspace())
    control = '\n'.join(line for i, line in enumerate(lines) if not empty(i))
    return (f'{control}\nFilename: {deb.relative_to(repo)}\nSize: {deb.stat().st_size}\n'
            f'MD5sum: {md5}\nSHA1: {sha1}\nSHA256: {sha256}\n')


def project_package(row):
    """Built by this project: its name or source, or its version marks a project build."""
    return (row['package'].startswith(PROJECT_PREFIXES) or row['source'].startswith(PROJECT_PREFIXES)
            or bool(PROJECT_BUILD.search(row['version'])))


def updated_by_distribution(row, current):
    """A distribution package the system updated past this batch keeps the update."""
    local = current.get(row['package'])
    return (local is not None and local['version'] != row['version']
            and not project_package(local) and not project_package(row)
            and subprocess.run(['dpkg', '--compare-versions', local['version'], 'gt', row['version']]).returncode == 0)


def read_json(path):
    return json.loads(path.read_text())


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def inventory(repo, identifier, product, default_names):
    rows = []
    names = set()
    for p in sorted((repo / 'pool').glob('*.deb')):
        m = metadata(p)
        if m['Package'] in names:
            raise ValueError('duplicate package in release: ' + m['Package'])
        if m['Architecture'] not in ('arm64', 'all'):
            raise ValueError('unexpected architecture: ' + p.name)
        names.add(m['Package'])
        rows.append(dict(package=m['Package'], version=m['Version'], architecture=m['Architecture'],
                         source=m.get('Source', m['Package']).split(' ')[0],
                         file='pool/' + p.name, sha256=sha(p), bytes=p.stat().st_size,
                         install=m['Package'] in default_names))
    if not rows or not set(default_names).issubset(names):
        raise ValueError('empty repository or missing default package')
    atomic_json(repo / 'manifest.json', dict(schema=1, id=identifier, product=product, packages=rows))
    # Snapshots are indexed on the target, and release roots carry no dpkg-dev.
    (repo / 'Packages').write_text('\n'.join(index_stanza(repo, p) for p in sorted((repo / 'pool').glob('*.deb'))))
    shutil.copyfile(__file__, repo / 'install.py')
    (repo / 'install.py').chmod(0o755)
    checker = Path(__file__).with_name('check-host-mesa.py')
    if checker.is_file():
        shutil.copyfile(checker, repo / checker.name)
        (repo / checker.name).chmod(0o755)
    (repo / 'SHA256SUMS').write_text(''.join(
        f'{sha(p)}  {p.relative_to(repo)}\n' for p in sorted(repo.rglob('*'))
        if p.is_file() and p.name != 'SHA256SUMS'))
    return read_json(repo / 'manifest.json')


def verify(repo):
    repo = repo.resolve()
    sums = {}
    for line in (repo / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        p = repo / name
        if not p.resolve().is_relative_to(repo) or p.is_symlink() or sha(p) != digest:
            raise ValueError('repository integrity failure: ' + name)
        sums[name] = digest
    for p in repo.rglob('*'):
        if p.is_file() and p.name != 'SHA256SUMS' and str(p.relative_to(repo)) not in sums:
            raise ValueError('unlisted repository file: ' + str(p))
    m = read_json(repo / 'manifest.json')
    if not re.fullmatch(r'[a-z0-9][a-zA-Z0-9.+_~-]*', m['id']) or m['product'] not in ('piano', 'erhai'):
        raise ValueError('invalid release identity')
    for row in m['packages']:
        if sums.get(row['file']) != row['sha256']:
            raise ValueError('manifest checksum disagreement')
        actual = metadata(repo / row['file'])
        if any(actual[k] != row[k.lower()] for k in ('Package', 'Version', 'Architecture')):
            raise ValueError('manifest package identity disagreement')
    return m


def installed(*, include_unconfigured=False):
    fields = '${db:Status-Abbrev}\t${Package}\t${Version}\t${source:Package}\t${Architecture}\n'
    text = subprocess.check_output(['dpkg-query', '-W', '-f=' + fields], text=True)
    rows = {}
    for line in text.splitlines():
        state, name, version, source, arch = line.split('\t')
        if state.startswith('ii') or (include_unconfigured and state[:1] == 'i'
                                      and state[1:2] in ('U', 'F', 'H', 'W', 't')):
            rows[name] = dict(package=name, version=version, source=source, architecture=arch)
    return rows


def guard(product):
    if os.geteuid():
        raise ValueError('run this command as root on the target')
    compatible = Path('/proc/device-tree/compatible')
    if compatible.exists():
        data = compatible.read_bytes()
        expected = b'xiaomi,piano' if product == 'piano' else b'oneplus,erhai'
        if expected not in data or b'sm8450' in data:
            raise ValueError('release batch does not match this device')
    elif not Path('/etc/release-disposable-test-root').exists():
        raise ValueError('device identity unavailable; refusing installation')


def keep(repo, store, manifest=None):
    m = manifest if manifest is not None else verify(repo)
    dest = store / m['id']
    if dest.exists():
        if verify(dest) != m or sha(dest / 'SHA256SUMS') != sha(repo / 'SHA256SUMS'):
            raise ValueError('release identifier already has different contents')
    else:
        tmp = store / ('.' + m['id'] + '.incoming')
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(repo, tmp)
        verify(tmp)
        tmp.rename(dest)
    return dest, m


def previous_for_installed(previous, old, repo, new, local, store):
    """Retain the exact installed release versions alongside the previous release.

    Project packages must match the supplied previous batch, unless the unit
    already runs the exact build the new batch carries, for example a GunyahVM
    release installed on its own; that archive is copied from the new batch.
    Distribution packages a batch ships, such as the Mesa/libdrm cohort or
    libraries, may have received normal Ubuntu updates since that release; the
    exact installed cohort of each such source is captured via authenticated APT.
    Both go into the snapshot <previous>-before-<new> before anything changes.
    """
    rows = {x['package']: x for x in old['packages']}
    newer = {x['package']: x for x in new['packages']}
    before = {name: row['version'] for name, row in local.items()}
    added_unchanged = {n for n, row in newer.items()
                       if n not in rows and before.get(n) == row['version']}
    missing = {n: before[n] for n in ((rows.keys() | newer.keys()) & before.keys()) - added_unchanged
               if n not in rows or rows[n]['version'] != before[n]}
    if not missing:
        return previous, old
    source_by_name = {x['package']: x['source'] for x in old['packages'] + new['packages']}
    # A new batch can replace a distribution binary with a project build.
    # Use dpkg's source and version for the installed members of each cohort.
    for name in source_by_name.keys() & local.keys():
        source_by_name[name] = local[name]['source']
    distribution = {name for name in missing if not project_package(local[name])}
    carried = {name for name in missing.keys() - distribution if name in newer
               and newer[name]['version'] == missing[name]
               and newer[name]['architecture'] == local[name]['architecture']}
    unknown = {name: missing[name] for name in missing.keys() - carried - distribution}
    if unknown:
        raise ValueError('previous batch is missing exact installed release packages: ' + json.dumps(unknown))
    versions = {}
    for n in sorted(distribution):
        source = source_by_name[n]
        if source in versions and versions[source] != missing[n]:
            raise ValueError('installed source cohort has mixed versions: ' + source)
        versions[source] = missing[n]
    # Every installed member of a captured source must be at the captured version.
    for n, source in source_by_name.items():
        if source in versions and n in before and n not in carried and before[n] != versions[source]:
            raise ValueError('installed source cohort has mixed versions: ' + source)
    identifier = old['id'] + '-before-' + new['id']
    dest = store / identifier
    if dest.exists():
        saved = verify(dest)
        available = {x['package']:x['version'] for x in saved['packages']}
        if all(available.get(n) == v for n, v in missing.items()):
            return dest, saved
        raise ValueError('existing rollback snapshot differs from the installed versions')
    temporary = store / ('.' + identifier + '.incoming')
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(previous, temporary)
    # A carried build replaces its previous row. Packages it breaks were not
    # installed beside it, so a rollback must not try to restore them.
    broken = set()
    for n in carried:
        fields = metadata(repo / newer[n]['file'])
        broken |= relation_names(fields.get('Breaks', '') + ',' + fields.get('Conflicts', ''))
    broken = (broken & rows.keys()) - before.keys() - carried
    for row in old['packages']:
        if source_by_name[row['package']] in versions or row['package'] in carried | broken:
            (temporary / row['file']).unlink()
    for n in sorted(carried):
        shutil.copyfile(repo / newer[n]['file'], temporary / newer[n]['file'])
    with tempfile.TemporaryDirectory(prefix='release-archives-', dir='/var/tmp') as download:
        for name, source in sorted(source_by_name.items()):
            if source not in versions or name in carried:
                continue
            version = versions[source]
            available = subprocess.run(['apt-cache', 'show', name + '=' + version],
                                       capture_output=True, text=True)
            if available.returncode or not available.stdout.strip():
                if name in before:
                    raise ValueError('exact installed distribution package unavailable: ' + name + '=' + version)
                continue  # e.g. automatic debug packages absent from the archive
            run(['apt-get', 'download', name + '=' + version], cwd=download)
        for archive in Path(download).glob('*.deb'):
            shutil.copyfile(archive, temporary / 'pool' / archive.name)
    saved = inventory(temporary, identifier, old['product'],
                      {x['package'] for x in old['packages'] if x['install']} - broken)
    available = {x['package']:x['version'] for x in saved['packages']}
    if any(available.get(n) != v for n, v in missing.items()):
        raise ValueError('incomplete exact rollback capture')
    verify(temporary); temporary.rename(dest)
    return dest, saved


def activate(repo, product):
    # The caller has verified this local immutable batch. Trust applies only to
    # this file: source, never to any network repository.
    p = Path('/etc/apt/sources.list.d') / (product + '-release.list')
    p.write_text(f'deb [trusted=yes] file:{repo} ./\n')
    run(['apt-get', 'update'])


@contextlib.contextmanager
def apt_preferences(product):
    # Only the transition package's ordinary, dpkg-owned Mesa policy is omitted
    # during a complete transaction; all other administrator policy is retained.
    name = 'piano-host-mesa'
    path = '/etc/apt/preferences.d/' + name
    owner = subprocess.run(['dpkg-query', '-S', path], capture_output=True, text=True)
    owned = owner.returncode == 0 and owner.stdout.startswith('piano-mesa: ')
    with tempfile.TemporaryDirectory(prefix='release-preferences-', dir='/var/tmp') as tmp:
        for src in Path('/etc/apt/preferences.d').iterdir():
            if src.is_file() and not (owned and src.name == name):
                shutil.copyfile(src, Path(tmp) / src.name)
        yield ['-o', 'Dir::Etc::preferencesparts=' + tmp]


def solve_and_install(targets, product, *, repair_unpacked=True):
    with apt_preferences(product) as options:
        # Upgrade repairs unpacked dependencies before the targeted transaction.
        # Rollback must replace a broken new postinst, never require it to work.
        # APT will not solve a targeted transaction on an already-broken
        # dependency graph, even if all requested versions are available.
        # Complete the unpacked package's dependencies before switching the
        # remaining batch. A repair must not discard installed applications.
        if repair_unpacked and subprocess.check_output(['dpkg', '--audit'], text=True).strip():
            repair = ['apt-get', *options, '--fix-broken', '--no-remove',
                      '--no-install-recommends', 'install']
            run([*repair[:1], '-s', *repair[1:]])
            run([*repair[:1], '-y', *repair[1:]],
                env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
        command = ['apt-get', *options, '--allow-downgrades', '--no-install-recommends', 'install', *targets]
        run([*command[:1], '-s', *command[1:]])
        run([*command[:1], '-y', *command[1:]], env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
    run(['dpkg', '--configure', '-a'])
    audit = subprocess.check_output(['dpkg', '--audit'], text=True)
    if audit.strip():
        raise ValueError('dpkg audit failed: ' + audit)


def switch_boot(product, directory):
    directory = Path(directory)
    parts = ('boot', 'init_boot', 'vendor_boot')
    wanted = {name: sha(directory / (name + '.img')) for name in parts}
    if product == 'piano':
        suffix = '_a'  # Piano's switcher owns only slot A.
        run(['piano-boot-switch', 'import-ubuntu', '--from', str(directory)])
        run(['piano-boot-switch', 'to-ubuntu', '--only-if-ubuntu'])
    else:
        # The OnePlus switcher resolves the active slot from boot metadata or GPT.
        status = subprocess.check_output(['erhai-switch', 'status'], text=True)
        match = re.search(r'(?m)^.*?slot suffix:\s*(_[ab])\s*$', status)
        if not match:
            raise ValueError('OnePlus switcher did not identify the active slot')
        suffix = match[1]
    nodes = {name: Path('/dev/disk/by-partlabel') / (name + suffix) for name in parts}
    if product == 'erhai':
        # postinst may already have activated this exact triplet. Avoid rotating
        # ubuntu.prev a second time, but compare all three partitions either way.
        registered = Path('/var/lib/erhai/switch/ubuntu')
        if not all((registered / (n + '.img')).is_file() and
                   sha(registered / (n + '.img')) == wanted[n] and sha(nodes[n]) == wanted[n]
                   for n in parts):
            run(['erhai-switch-install-ubuntu-images', str(directory)])
    for name, node in nodes.items():
        actual = sha(node)
        if actual != wanted[name]:
            raise ValueError(f'{node.name} readback differs from {directory.name}: '
                             f'expected {wanted[name]}, read {actual}')


def save_boot(source, destination):
    if destination.exists():
        return
    incoming = destination.with_name('.' + destination.name + '.incoming')
    if incoming.exists():
        shutil.rmtree(incoming)
    shutil.copytree(source, incoming)
    incoming.rename(destination)


def kernel_release(package):
    """Release in a kernel package name such as linux-image-7.4.0-rc2-piano98."""
    match = KERNEL_PACKAGE.fullmatch(package)
    return match[1] if match else None


def boot_release(directory):
    """Kernel release a saved boot set starts: its identity record, else the boot.img banner."""
    for name in ('kernel.json', 'manifest.json', 'meta.json'):
        try:
            record = json.loads((directory / name).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            if isinstance(record.get('kernel_release'), str) and record['kernel_release']:
                return record['kernel_release']
            # Older OnePlus installers recorded only the payload path.
            legacy = re.fullmatch(r'/usr/lib/erhai/boot/([a-z0-9.+-]{1,64})', str(record.get('source', '')))
            if legacy:
                return legacy[1]
    try:
        match = BANNER.search((directory / 'boot.img').read_bytes())
    except OSError:
        return None
    return match[1].decode() if match else None


def size(path):
    try:
        if path.is_dir() and not path.is_symlink():
            return sum(os.lstat(os.path.join(d, f)).st_size for d, _, files in os.walk(path) for f in files)
        return path.lstat().st_size
    except OSError:
        return 0


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def relation_names(field):
    return {re.split(r'[\s(:\[]', choice.strip(), maxsplit=1)[0]
            for part in field.split(',') for choice in part.split('|') if choice.strip()}


def package_states():
    """Every package dpkg knows: its state, dependencies and the packages it replaces."""
    text = subprocess.check_output(['dpkg-query', '-W', '-f=${db:Status-Abbrev}\t${Package}\t'
                                    '${Depends}, ${Pre-Depends}\t${Conflicts}, ${Breaks}, ${Replaces}\n'], text=True)
    rows = {}
    for line in text.splitlines():
        status, name, depends, replaced = line.split('\t')
        rows[name] = dict(status=status.strip(), depends=relation_names(depends),
                          replaced=relation_names(replaced))
    return rows


def owners(paths):
    """Packages whose file lists name each path exactly."""
    found = {str(p): set() for p in paths}
    if found:
        result = subprocess.run(['dpkg-query', '-S', *found], capture_output=True, text=True)
        if result.returncode > 1:  # 1 only means that some paths have no owner
            raise subprocess.CalledProcessError(result.returncode, 'dpkg-query -S', result.stdout, result.stderr)
        for line in result.stdout.splitlines():
            names, separator, path = line.partition(': ')
            if separator and path in found and not line.startswith('diversion '):
                found[path] |= {n.strip().split(':')[0] for n in names.split(',')}
    return found


def changed_conffiles(package):
    """Configuration files of package that the administrator changed; purging would delete them."""
    text = subprocess.run(['dpkg-query', '-W', '-f=${Conffiles}\n', package],
                          capture_output=True, text=True).stdout
    changed = []
    for line in text.splitlines():
        words = line.split()
        while words and words[-1] in ('obsolete', 'remove-on-upgrade'):
            words.pop()
        if len(words) < 2 or not words[0].startswith('/') or words[-1] == 'newconffile':
            continue
        path = Path(' '.join(words[:-1]))
        try:
            if hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest() != words[-1]:
                changed.append(str(path))
        except OSError:
            pass  # already deleted by the administrator: purging cannot remove it
    return changed


def unit_names(package):
    """systemd units a package ships or purges, including those of removed packages."""
    listed = subprocess.run(['dpkg-query', '-L', package], capture_output=True, text=True).stdout
    names = {PurePosixPath(p).name for p in listed.splitlines()
             if '/systemd/system/' in p and UNIT.fullmatch(PurePosixPath(p).name)}
    script = subprocess.run(['dpkg-query', '--control-path', package, 'postrm'],
                            capture_output=True, text=True).stdout.strip()
    if script:
        with contextlib.suppress(OSError):
            for line in Path(script).read_text(errors='replace').splitlines():
                if 'deb-systemd-helper' in line:
                    names |= set(UNIT.findall(line))
    return names


def dangling_links(names, now=True):
    """Enable links under /etc/systemd/system to units named by purged packages, whose unit is gone.

    With now=False, also the links that a purge could leave dangling.
    """
    links = []
    for directory, subdirectories, files in os.walk(UNIT_LINKS):
        for name in subdirectories + files:
            path = Path(directory, name)
            if path.is_symlink() and (not now or not path.exists()) and (
                    name in names or PurePosixPath(os.readlink(path)).name in names):
                links.append(path)
    return sorted(links)


def simulate(packages):
    """Names APT would remove when purging packages, or why it would not just do that."""
    result = subprocess.run([APT_PURGE[0], '-s', *APT_PURGE[1:], *sorted(packages)],
                            capture_output=True, text=True, env=dict(os.environ, LC_ALL='C'))
    if result.returncode:
        return None, (result.stderr.strip().splitlines() or ['APT simulation failed'])[-1]
    installs = re.findall(r'^Inst (\S+)', result.stdout, re.M)
    if installs:
        return None, 'APT would install ' + ', '.join(sorted(installs))
    return {n.split(':')[0] for n in re.findall(r'^(?:Purg|Remv) (\S+)', result.stdout, re.M)}, None


def purgeable(candidates):
    """The candidates APT purges without removing anything else, and why the rest stay."""
    wanted = set(candidates)
    if not wanted or simulate(wanted)[0] == wanted:
        return wanted, {}
    good, refused = set(), {}
    # Removal propagates to dependants, so a candidate whose own purge stays
    # within the candidates can only pull in other such candidates.
    for name in sorted(wanted):
        removed, reason = simulate({name})
        if removed is None or name not in removed:
            refused[name] = reason or 'APT would not purge it'
        elif removed - wanted:
            refused[name] = 'APT would also remove ' + ', '.join(sorted(removed - wanted))
        else:
            good.add(name)
    if good and simulate(good)[0] != good:
        refused.update(dict.fromkeys(good, 'APT would not purge exactly these packages together'))
        good = set()
    return good, refused


def cleanup(product, store, state, manifests=None, dry_run=False, upgrading_from=None):
    """Remove what neither the recorded transaction nor the running system still needs.

    The plan is computed completely and written to cleanup.json before the first
    removal. Every step is idempotent; a rerun plans afresh from what is left.
    A dry run returns the plan, including APT's simulations, and changes nothing.
    """
    if state.get('phase') not in ('upgraded', 'rolled-back'):
        raise ValueError('cleanup needs a completed upgrade or rollback; the transaction is '
                         + str(state.get('phase')))
    ids = (state['to'], state['previous'])
    lost = [i for i in ids if not (manifests or {}).get(i) and not (store / i).is_dir()]
    if lost:
        raise ValueError('the recorded transaction batch ' + ' and '.join(lost) + ' is missing; nothing removed; '
                         'the next whole-release upgrade records a new transaction')
    manifests = {i: (manifests or {}).get(i) or verify(store / i) for i in ids}
    names = {x['package'] for m in manifests.values() for x in m['packages']}
    boot = Path(state['boot'])
    sets = {prefix + i for i in ids for prefix in ('boot-', 'boot-before-')}
    keep = {*ids, *sets, '.lock', 'transaction.json', 'cleanup.json'} | ({boot.name} if boot.parent == store else set())
    plan, skipped, dropped = [], [], {}
    for entry in sorted(store.iterdir()):
        if entry.name in keep:
            continue
        reason = 'not ' + ' or '.join(ids)
        if entry.name.startswith('boot-'):
            reason = 'boot set copy of a batch ' + reason
        elif entry.is_dir() and not entry.is_symlink() and (entry / 'manifest.json').exists():
            try:
                dropped[entry.name] = {x['package'] for x in read_json(entry / 'manifest.json')['packages']}
            except (OSError, ValueError, KeyError, TypeError):
                dropped[entry.name] = set()
                reason += '; its manifest has no package list'
            reason = 'release batch ' + reason
        else:
            reason = 'interrupted copy or temporary file'
        plan.append(dict(kind='store', path=str(entry), bytes=size(entry), reason=reason))

    rows = package_states()
    present = {n for n, r in rows.items() if r['status'] in ('ii', 'rc')}
    config_only = {n for n, r in rows.items() if r['status'] == 'rc'}
    running = os.uname().release
    roots = []
    for root in MODULE_ROOTS:
        if root.is_dir() and all(root.resolve() != r.resolve() for r in roots):
            roots.append(root)
    releases = {p.name for root in roots for p in root.iterdir()
                if p.is_dir() and not p.is_symlink() and not p.name.startswith('.')}
    named = {}
    for n in present:
        if kernel_release(n):
            named.setdefault(kernel_release(n), set()).add(n)
    initrds = {p.name[len('initrd.img-'):]: p for p in Path('/boot').glob('initrd.img-*')
               if p.is_file() and not p.is_symlink()}
    releases |= named.keys() | initrds.keys()
    found = owners([root / r for root in MODULE_ROOTS for r in releases])
    owned = {r: (set().union(*(found[str(root / r)] for root in MODULE_ROOTS)) | named.get(r, set())) & present
             for r in releases}
    kernels = {running} | {r for r in releases if owned[r] & names} | ({kernel_release(n) for n in names} - {None})
    switch = Path('/var/lib') / product / 'switch'
    unknown = []
    switch_sets = [switch / 'ubuntu', switch / 'ubuntu.prev']
    if upgrading_from is not None:
        incoming_kernels = {kernel_release(x['package']) for x in manifests[ids[0]]['packages'] if x['install']} - {None}
        if incoming_kernels and upgrading_from not in incoming_kernels:
            # Importing a different kernel rotates the current set into ubuntu.prev.
            switch_sets = [switch / 'ubuntu']
    for directory in [store / n for n in sorted(sets)] + [boot, *switch_sets]:
        if directory.is_dir():
            release = boot_release(directory)
            if release:
                kernels.add(release)
            else:
                unknown.append(str(directory))
    protected = set().union(*(owned.get(r, set()) for r in (releases if unknown else kernels)))
    if unknown:
        skipped.append(dict(kind='kernels', reason='all kept: no kernel release recorded in ' + ', '.join(unknown)))

    reasons = {}
    for r in sorted(releases - kernels):
        for n in sorted(owned[r] - protected):
            reasons[n] = f'kernel {r}: not running, in neither batch and started by no kept boot set'
    for n in sorted(n for n in present if KERNEL_META.fullmatch(n)):
        reasons[n] = 'distribution kernel metapackage; only the project kernel boots'
    for batch, members in sorted(dropped.items()):
        for n in sorted((members & present) - names - protected):
            reasons.setdefault(n, 'only in removed batch ' + batch)
    sources = {n for n in names if rows.get(n, {}).get('status') == 'ii'}
    sources |= {d for n in sources for d in rows[n]['depends'] if rows.get(d, {}).get('status') == 'ii'}
    for s in sorted(sources):
        for n in sorted((rows[s]['replaced'] & config_only) - names - protected):
            reasons.setdefault(n, 'configuration left after ' + s + ' replaced it')
    for n in sorted(reasons):
        changed = changed_conffiles(n)
        if changed:
            skipped.append(dict(kind='package', package=n,
                                reason=reasons.pop(n) + '; kept for its changed ' + ', '.join(changed)))
    good, refused = purgeable(reasons)
    skipped += [dict(kind='package', package=n, reason=reasons[n] + '; kept: ' + refused[n]) for n in sorted(refused)]
    plan += [dict(kind='package', package=n, reason=reasons[n]) for n in sorted(good)]
    units = set().union(*(unit_names(n) for n in good))

    for r in [] if unknown else sorted(releases - kernels):
        paths = [root / r for root in roots if (root / r).is_dir()] + ([initrds[r]] if r in initrds else [])
        staying = sorted((owned[r] - good) - config_only)
        if staying and paths:
            skipped.append(dict(kind='kernel files', paths=list(map(str, paths)),
                                reason=f'kernel {r} stays installed: ' + ', '.join(staying)))
        elif paths:
            plan += [dict(kind='kernel files', release=r, path=str(p), bytes=size(p),
                          reason=f'kernel {r} is not kept and no remaining package owns it') for p in paths]
    for directory in sorted((Path('/var/lib') / product).glob('rollback-*')):
        named_release = directory.name[len('rollback-'):]
        started = boot_release(directory)
        if directory.is_dir() and not directory.is_symlink() and not {named_release, started} & kernels:
            plan.append(dict(kind='rollback copy', path=str(directory), bytes=size(directory),
                             reason=f'manual boot set copy for kernel {started or named_release}, which is not kept'))
    plan += [dict(kind='unit link', path=str(p), reason='enable link to a unit of a purged package')
             for p in dangling_links(units)]
    transaction = {k: state[k] for k in ('to', 'previous', 'boot', 'phase')}
    if dry_run:
        later = [dict(kind='unit link', path=str(p), reason='removed if the purge leaves it dangling')
                 for p in dangling_links(units, now=False) if str(p) not in {x['path'] for x in plan if 'path' in x}]
        return dict(dry_run=True, product=product, transaction=transaction, running_kernel=running,
                    kept=dict(batches=list(ids), kernels=sorted(kernels)), remove=plan + later, skipped=skipped,
                    freed_bytes=sum(x.get('bytes', 0) for x in plan))

    record = store / 'cleanup.json'
    try:
        runs = read_json(record)['runs']
    except (OSError, ValueError, KeyError, TypeError):
        runs = []
    entry = dict(product=product, transaction=transaction,
                 running_kernel=running, kept=dict(batches=list(ids), kernels=sorted(kernels)),
                 remove=plan, skipped=skipped, status='planned')
    # A rerun that finds nothing new keeps the record of the run that removed things.
    write = bool(plan) or not runs or runs[-1].get('skipped') != skipped
    if write:
        runs = runs[-19:] + [entry]
        atomic_json(record, dict(schema=1, runs=runs))
    try:
        if good:
            run([APT_PURGE[0], '-y', *APT_PURGE[1:], *sorted(good)],
                env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
        listed = {x['path'] for x in plan if x['kind'] == 'unit link'}
        for link in dangling_links(units):
            if str(link) not in listed:
                plan.append(dict(kind='unit link', path=str(link), reason='enable link to a unit of a purged package'))
            link.unlink()
        left = [x for x in plan if x['kind'] == 'kernel files' and Path(x['path']).exists()]
        installed_now = installed().keys()
        found = owners([root / x['release'] for x in left for root in MODULE_ROOTS])
        for item in left:
            r = item['release']
            users = set().union(*(found[str(root / r)] for root in MODULE_ROOTS)) & installed_now
            users |= {n for n in installed_now if kernel_release(n) == r}
            if users:
                item['result'] = 'kept: still owned by ' + ', '.join(sorted(users))
            else:
                remove(Path(item['path']))
        for item in plan:
            if item['kind'] in ('rollback copy', 'store'):
                remove(Path(item['path']))
        if product == 'erhai' and os.access(KERNEL_RETENTION, os.X_OK):
            # Regenerate APT's kernel protection from the kernels now selected.
            run([str(KERNEL_RETENTION), running])
    except (OSError, subprocess.CalledProcessError) as error:
        entry['status'] = 'failed: ' + str(error)
        if write:
            atomic_json(record, dict(schema=1, runs=runs))
        raise
    entry['status'] = 'done'
    if write:
        atomic_json(record, dict(schema=1, runs=runs))
    removed = [x for x in plan if 'result' not in x]
    return dict(cleanup=entry['status'], removed=len(removed), skipped=len(skipped),
                freed_bytes=sum(x.get('bytes', 0) for x in removed), record=str(record))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='action', required=True)
    p = sub.add_parser('create')
    p.add_argument('--id', required=True)
    p.add_argument('--product', choices=('piano', 'erhai'), required=True)
    p.add_argument('--packages', type=Path, action='append', required=True)
    p.add_argument('--default-package', action='append', default=[])
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('verify'); p.add_argument('repository', type=Path)
    for action in ('upgrade', 'preview-upgrade'):
        p = sub.add_parser(action)
        p.add_argument('repository', type=Path)
        p.add_argument('--previous', type=Path, required=True)
    p = sub.add_parser('rollback'); p.add_argument('--product', choices=('piano', 'erhai'), required=True)
    p = sub.add_parser('cleanup', help='remove what earlier releases left behind (also run by upgrade and rollback)')
    p.add_argument('--product', choices=('piano', 'erhai'), required=True)
    p.add_argument('--dry-run', action='store_true', help='print the plan; write, remove and purge nothing')
    a = ap.parse_args()
    if a.action == 'create':
        a.output.mkdir(parents=True, exist_ok=False); (a.output / 'pool').mkdir()
        for directory in a.packages:
            for src in sorted(directory.rglob('*.deb')):
                dest = a.output / 'pool' / src.name
                if dest.exists() and sha(dest) != sha(src):
                    raise ValueError('conflicting package filename')
                shutil.copyfile(src, dest)
        inventory(a.output, a.id, a.product, a.default_package)
        verify(a.output)
        return
    if a.action == 'verify':
        print(json.dumps(verify(a.repository), indent=2)); return
    upgrading = a.action in ('upgrade', 'preview-upgrade')
    new_manifest = verify(a.repository) if upgrading else None
    product = new_manifest['product'] if upgrading else a.product
    guard(product)
    store = Path('/var/lib') / product / 'releases'
    if a.action == 'cleanup' and not (store / 'transaction.json').is_file():
        raise ValueError('no release transaction on this system; nothing to clean up after')
    store.mkdir(parents=True, exist_ok=True)
    with (store / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = store / 'transaction.json'
        if upgrading:
            preview = a.action == 'preview-upgrade'
            repo, m = (a.repository, new_manifest) if preview else keep(a.repository, store, new_manifest)
            previous, old = a.previous, verify(a.previous)
            if old['product'] != product or old['id'] == m['id']:
                raise ValueError('previous release is not a different batch of this product')
            state = read_json(state_path) if state_path.exists() else None
            if not state or state['to'] != m['id']:
                before = installed()
                # Build an exact snapshot before selecting the retained previous ID.
                # Preview has no durable package, boot or transaction side effects.
                with (tempfile.TemporaryDirectory(prefix='release-preview-', dir='/var/tmp')
                      if preview else contextlib.nullcontext(store)) as staging:
                    previous, old = previous_for_installed(previous, old, repo, m, before, Path(staging))
                    if not preview and previous.parent != store:
                        previous, old = keep(previous, store, old)
                managed = {x['package'] for x in old['packages'] + m['packages']}
                boot = Path('/var/lib') / product / 'switch/ubuntu'
                saved = store / ('boot-before-' + m['id'])
                state = dict(product=product, to=m['id'], previous=old['id'], before={n:before[n]['version'] for n in managed & before.keys()},
                             boot=str(saved), phase='prepared')
                if not preview:
                    save_boot(boot, saved)
                    atomic_json(state_path, state)
            else:
                old = verify(store / state['previous'])
            manifests = {m['id']: m, old['id']: old}
            if preview:
                active_boot = Path('/var/lib') / product / 'switch/ubuntu'
                print(json.dumps(cleanup(product, store, dict(state, phase='upgraded'), manifests,
                                         dry_run=True, upgrading_from=boot_release(active_boot)), indent=2))
                return
            current = installed()
            targets = [f'{x["package"]}={x["version"]}' for x in m['packages']
                       if (x['install'] or x['package'] in current) and not updated_by_distribution(x, current)]
            state['phase'] = 'prepared'; atomic_json(state_path, state)
            activate(repo, product)
            solve_and_install(targets, product)
            new_boot = store / ('boot-' + m['id'])
            if not new_boot.exists():
                current_boot = Path('/var/lib') / product / 'switch/ubuntu'
                expected = {kernel_release(x['package']) for x in m['packages'] if x['install']} - {None}
                if expected and boot_release(current_boot) not in expected:
                    raise ValueError('registered boot set does not match the new release kernel')
                save_boot(current_boot, new_boot)
            switch_boot(product, new_boot)
            state['phase'] = 'upgraded'; atomic_json(state_path, state)
        elif a.action == 'cleanup':
            state = read_json(state_path); manifests = None
        else:
            state = read_json(state_path)
            repo = store / state['previous']; old = verify(repo)
            newer = verify(store / state['to'])
            # New-only unpacked/half-configured packages also need removal.
            current = installed(include_unconfigured=True); rows = {x['package']:x for x in old['packages']}
            # PR #21 leaves pre-existing, newly managed packages at the same
            # version out of the snapshot. Rollback must leave them installed.
            unchanged = {r['package'] for r in newer['packages'] if r['package'] not in rows
                         and state['before'].get(r['package']) == r['version']}
            selected = (set(state['before']) - unchanged) | {x['package'] for x in old['packages'] if x['install']}
            # Keep optional packages installed after upgrading when the preceding
            # complete source cohort contains their earlier versions.
            selected |= current.keys() & rows.keys()
            targets = [f'{n}={rows[n]["version"]}' for n in sorted(selected)
                       if not updated_by_distribution(rows[n], current)]
            targets += [x['package'] + '-' for x in newer['packages']
                        if x['package'] in current and x['package'] not in rows and x['package'] not in unchanged
                        and not kernel_release(x['package'])]
            activate(repo, product)
            solve_and_install(targets, product, repair_unpacked=False)
            state['phase'] = 'userspace-restored'; atomic_json(state_path, state)
            switch_boot(product, state['boot'])
            state['phase'] = 'rolled-back'; atomic_json(state_path, state)
            manifests = {old['id']: old, newer['id']: newer}
        print(json.dumps(state, indent=2))
        try:
            print(json.dumps(cleanup(product, store, state, manifests,
                                     dry_run=a.action == 'cleanup' and a.dry_run), indent=2))
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            if a.action == 'cleanup':
                raise
            raise ValueError(f'the {a.action} is complete, but removing what earlier releases left failed: '
                             f'{error}; after fixing it, run: install.py cleanup --product {product}') from error

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
