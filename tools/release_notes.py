# SPDX-License-Identifier: MIT
"""Render and validate bilingual release notes with changes before installation."""
from pathlib import Path
import re
import string

REPO = Path(__file__).resolve().parents[1]
REPO_URL = 'https://github.com/yzddmr6/xiaomipad-8pro-mainline'


def asset_prefix(version):
    if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]*', version):
        raise ValueError('release_name must be a filename-safe version')
    return f'piano-ubuntu-{version}'


def restore_commands(version, parts=None):
    """Machine-readable recovery recipe for assets-manifest.json, not the body."""
    prefix = asset_prefix(version)
    inputs = ' '.join(parts) if parts and len(parts) > 10 else f'{prefix}-rootfs.img.zst.part*'
    return '\n'.join([
        'sha256sum -c SHA256SUMS.assets',
        *(f'unzip {prefix}-{kind}.zip' for kind in ('boot', 'tools', 'packages')),
        'mkdir -p rootfs',
        f'cat {inputs} | zstd -d > rootfs/root.sparse.img',
        'sha256sum -c SHA256SUMS',
    ])


def change_sections(changes):
    halves = re.split(r'^---[ \t]*$', changes, flags=re.M)
    if len(halves) != 2:
        raise ValueError('changes must contain Chinese and English separated by ---')
    result = {}
    for language, half, updates, limits in zip(
            ('zh', 'en'), halves, ('更新', 'Changes'), ('已知限制', 'Known limitations')):
        summary, separator, rest = half.strip().partition(f'\n\n## {updates}\n\n')
        body, last_separator, limitations = rest.partition(f'\n\n## {limits}\n\n')
        if not separator or not last_separator or not all((summary.strip(), body.strip(), limitations.strip())):
            raise ValueError(f'changes ({language}) require a summary, updates and known limitations')
        result.update({f'summary_{language}': summary.strip(),
                       f'updates_{language}': body.strip(), f'limits_{language}': limitations.strip()})
    return result


def validate_notes(notes, version):
    """Reject missing sections, obsolete tables/commands, digests and private paths."""
    asset_prefix(version)
    if re.search(r'(?<!\w)[0-9a-f]{40}(?:[0-9a-f]{24})?(?!\w)|`[0-9a-f]{7,64}`', notes, re.I):
        raise ValueError('release body must not contain hashes')
    if re.search(r'^\s*\|', notes, re.M) or re.search(r'\b\d[\d,.]*\s+(?:bytes?|[KMGT]i?B)\b|\d[\d,]*\s*字节', notes):
        raise ValueError('release body must not contain tables or byte counts')
    if any(token in notes for token in ('```', '<!-- downloads', '<!-- restore',
                                        '/home/', '/tmp/', '/var/tmp/', '/dev/shm/',
                                        'private/', 'out/worktrees/')):
        raise ValueError('release body contains a command block, old marker or internal path')
    halves = re.split(r'^---[ \t]*$', notes, flags=re.M)
    if len(halves) != 2:
        raise ValueError('release body must contain Chinese followed by English')
    for half, headings, licensing, support in zip(halves,
            (['更新', '获取、安装与升级', '已知限制'], ['Changes', 'Get, install and upgrade', 'Known limitations']),
            ('来源与许可：', 'Sources and licensing:'), ('完整功能', 'Full hardware support')):
        if not half.strip().startswith(f'# Ubuntu for Xiaomi Pad 8 Pro {version}\n'):
            raise ValueError('release title differs from the manifest')
        if re.findall(r'^## (.+)$', half, re.M) != headings:
            raise ValueError('release sections are missing or out of order')
        updates = half.split(f'## {headings[0]}\n', 1)[1].split(f'## {headings[1]}\n', 1)[0]
        bullets = [line for line in updates.splitlines() if line.startswith('- ')]
        if not bullets or any(not re.match(r'- \*\*[^*]+\*\*', line) for line in bullets):
            raise ValueError('each release update must start with a bold label')
        if support not in half or 'SHA256SUMS.assets' not in half:
            raise ValueError('release support or download guidance is missing')
        last = half.strip().splitlines()[-1]
        if not last.startswith(licensing) or 'https://launchpad.net/ubuntu/+source/busybox/' not in last:
            raise ValueError('release body must end with licensing and the BusyBox source link')


def render_notes(rootfs, stock_build, integration_commit, kernel_source, changes):
    version = rootfs['release_name']
    kernel_name = 'linux-image-' + rootfs['kernel_release']
    # The packaged kernel must exist; the notes name the upstream version only.
    next(e for e in rootfs['packages'] if e['package'] == kernel_name)
    busybox = re.search(r'^package=busybox-static_(\S+)_arm64\.deb$',
                       (REPO / 'tools/fetch-busybox-arm64.sh').read_text(), re.M).group(1)
    notes = string.Template((REPO / 'packaging/release/RELEASE-NOTES.md.in').read_text()).substitute(
        release_name=version, prefix=asset_prefix(version), **change_sections(changes),
        kernel_version=rootfs['kernel_release'].split('-', 1)[0], stock_build=stock_build,
        repo_url=REPO_URL, kernel_url=kernel_source['url'].removesuffix('.git'),
        busybox_version=busybox)
    validate_notes(notes, version)
    return notes
