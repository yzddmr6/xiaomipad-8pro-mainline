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


def restore_commands(version, parts=None, *, separate_rollback=False):
    """Machine-readable recovery recipe for assets-manifest.json, not the body."""
    prefix = asset_prefix(version)
    inputs = ' '.join(parts) if parts and len(parts) > 10 else f'{prefix}-rootfs.img.zst.part*'
    archives = ['boot', 'tools', 'packages']
    if separate_rollback:
        archives.append('rollback')
    return '\n'.join([
        'sha256sum -c SHA256SUMS.assets',
        *(f'unzip {prefix}-{kind}.zip' for kind in archives),
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
        theme_prefix = '主题：' if language == 'zh' else 'Theme: '
        title_suffix = ''
        if summary.startswith(theme_prefix):
            theme, _, summary = summary.partition('\n\n')
            title_suffix = ' — ' + theme.removeprefix(theme_prefix).strip()
        advice_heading = '升级须知' if language == 'zh' else 'Upgrade notes'
        body, has_advice, advice = body.partition(f'\n\n## {advice_heading}\n\n')
        # Keep consecutive top-level updates in one compact Markdown list.
        body = re.sub(r'(^- [^\n]+)\n(?:[ \t]*\n)+(?=- )', r'\1\n', body, flags=re.M)
        result.update({f'title_suffix_{language}': title_suffix,
                       f'upgrade_section_{language}': f'## {advice_heading}\n\n{advice.strip()}\n\n' if has_advice else '',
                       f'summary_{language}': summary.strip(),
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
        if not re.match(rf'^# Ubuntu for Xiaomi Pad 8 Pro {re.escape(version)}(?: — [^\n]+)?\n', half.strip()):
            raise ValueError('release title differs from the manifest')
        advice = '升级须知' if headings[0] == '更新' else 'Upgrade notes'
        expected_headings = headings[:1] + ([advice] if f'## {advice}\n' in half else []) + headings[1:]
        if re.findall(r'^## (.+)$', half, re.M) != expected_headings:
            raise ValueError('release sections are missing or out of order')
        updates = half.split(f'## {headings[0]}\n', 1)[1].split(f'## {headings[1]}\n', 1)[0]
        bullets = [line for line in updates.splitlines() if line.startswith('- ')]
        if not bullets or any(not re.match(r'- \*\*[^*]+\*\*', line) for line in bullets):
            raise ValueError('each release update must start with a bold label')
        if support not in half or 'SHA256SUMS.assets' not in half:
            raise ValueError('release support or download guidance is missing')
        license_lines = [line for line in half.splitlines() if line.startswith(licensing)]
        last = half.strip().splitlines()[-1]
        if len(license_lines) != 1 or 'https://launchpad.net/ubuntu/+source/busybox/' not in license_lines[0] or '/docs/INSTALL' not in last:
            raise ValueError('release body needs licensing, the BusyBox source link and final installation links')


def render_notes(rootfs, stock_build, integration_commit, kernel_source, changes):
    version = rootfs['release_name']
    kernel_name = 'linux-image-' + rootfs['kernel_release']
    # The packaged kernel must exist; the notes name the upstream version only.
    next(e for e in rootfs['packages'] if e['package'] == kernel_name)
    busybox = re.search(r'^package=busybox-static_(\S+)_arm64\.deb$',
                       (REPO / 'tools/fetch-busybox-arm64.sh').read_text(), re.M).group(1)
    notes = string.Template((REPO / 'packaging/release/RELEASE-NOTES.md.in').read_text()).substitute(
        release_name=version, prefix=asset_prefix(version), **change_sections(changes),
        kernel_version=re.sub(r'-piano[0-9]+$', '', rootfs['kernel_release']), stock_build=stock_build,
        repo_url=REPO_URL, kernel_url=kernel_source['url'].removesuffix('.git'),
        busybox_version=busybox)
    validate_notes(notes, version)
    return notes
