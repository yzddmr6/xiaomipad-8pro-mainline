#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Monthly availability check; HEAD is not a content-integrity proof."""
import argparse
import concurrent.futures
import subprocess
import urllib.error
import urllib.request
from lib.deps import REPO, inventory, check_hash, check_url


def probe(item):
    label, url = item
    try:
        check_url(url)
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD', headers={'User-Agent':'dependency-health'}), timeout=30) as response:
            return 'OK', label, str(response.status)
    except urllib.error.HTTPError as exc:
        return ('MISSING' if exc.code in (404, 410) else 'HTTP_ERROR'), label, str(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 'TRANSPORT_ERROR', label, str(exc)
    except ValueError as exc:
        return 'INVALID_LOCK', label, str(exc)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--local', action='store_true', help='validate vendored inputs and lock syntax without network')
    a = p.parse_args()
    data = inventory(); tasks = []; failures = []
    for row in data['files']:
        try:
            for url in row.get('upstream', []):check_url(url)
            if row.get('vendored'):
                check_hash(REPO / row['vendored'], row['sha256'])
            elif row['preservation'] == 'D':
                if row.get('upstream'):tasks.append((row['name'] + ' upstream', row['upstream'][0]))
                else:tasks.append((row['name'] + ' archive', row['own_url']))
            else:
                tasks.append((row['name'] + ' owned', row['own_url']))
                if row.get('upstream'):tasks.append((row['name'] + ' upstream', row['upstream'][0]))
        except ValueError as exc:
            failures.append(('INVALID_LOCK', row['name'], str(exc)))
    if not a.local:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            for result in executor.map(probe, tasks):
                print(*result, sep=' | ')
                if result[0] != 'OK':failures.append(result)
        for row in data['git']:
            url = 'https://github.com/yzddmr6/' + row['repository'] + '.git'
            try:
                result = subprocess.run(['git', 'ls-remote', '--exit-code', url, row['own_ref']], capture_output=True, text=True, timeout=45)
                state = 'OK' if result.returncode == 0 and result.stdout.split()[0] == row['commit'] else 'REF_ERROR'
                print(state, row['name'], row['commit'], sep=' | ')
                if state != 'OK':failures.append((state, row['name'], result.stderr))
            except subprocess.TimeoutExpired:
                failures.append(('TRANSPORT_ERROR', row['name'], 'Git timeout'))
    for result in failures:print(*result, sep=' | ')
    if failures:raise SystemExit(1)


if __name__ == '__main__':
    main()
