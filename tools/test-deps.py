#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline checks of the dependency trust and fallback boundaries."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib import deps


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache/input'
        self.owned = self.root / 'owned'
        self.owned.mkdir()
        self.upstream = self.root / 'upstream-v1.tar'
        self.upstream.write_bytes(b'locked source')
        self.sha = hashlib.sha256(self.upstream.read_bytes()).hexdigest()
        self.filename = self.sha + '-source-v1.tar'
        self.row = {'name': 'test', 'filename': self.filename, 'sha256': self.sha,
                    'upstream': [self.upstream.as_uri()], 'own_url': (self.root / 'absent-v1').as_uri()}
        self.env = patch.dict(os.environ, {'DEPS_ARCHIVE': str(self.owned), 'DEPS_OFFLINE': '1', 'DEPS_OWN_ONLY': '0'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.inv = patch.object(deps, 'inventory', return_value={'files': [self.row]})
        self.inv.start(); self.addCleanup(self.inv.stop)

    def fetch(self):
        return deps.fetch(self.upstream.as_uri(), self.cache, self.sha)

    def test_cache_hit_without_any_source(self):
        self.cache.parent.mkdir(); self.cache.write_bytes(self.upstream.read_bytes()); self.upstream.unlink()
        with patch.object(deps.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            self.assertEqual(self.fetch().read_bytes(), b'locked source')

    def test_owned_copy_before_upstream(self):
        (self.owned / self.filename).write_bytes(self.upstream.read_bytes()); self.upstream.unlink()
        self.assertEqual(self.fetch().read_bytes(), b'locked source')

    def test_unavailable_copy_falls_back_to_pinned_upstream(self):
        self.assertEqual(self.fetch().read_bytes(), b'locked source')

    def test_bad_owned_hash_stops_without_upstream(self):
        (self.owned / self.filename).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.fetch()
        self.assertFalse(self.cache.exists())
        self.assertFalse(list(self.cache.parent.glob('*.part-*')))

    def test_bad_cache_hash_is_not_replaced(self):
        self.cache.parent.mkdir(); self.cache.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.fetch()
        self.assertEqual(self.cache.read_bytes(), b'corrupt')

    def test_refuses_floating_inputs_even_on_cache_hit(self):
        self.cache.parent.mkdir(); self.cache.write_bytes(self.upstream.read_bytes())
        for ref in ('main', 'latest', 'v1.0', '0123'):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                deps.fetch(self.upstream.as_uri(), self.cache, self.sha, ref=ref)
        for path in ('main/file', 'releases/latest/a', 'archive/master.tar.gz', 'releases/download/dev/a.zip'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                deps.fetch('https://example.invalid/' + path, self.cache, self.sha)

    def test_snapshot_main_is_a_component_not_a_git_branch(self):
        deps.check_url('https://snapshot.ubuntu.com/ubuntu/20261107T000000Z/pool/main/g/gcc/a.deb')
        deps.check_url('https://snapshot.debian.org/archive/debian/20261107T000000Z/pool/main/a/a.tar.xz')

    def test_own_only_never_uses_upstream(self):
        with patch.dict(os.environ, {'DEPS_OWN_ONLY': '1'}), self.assertRaises(RuntimeError):
            self.fetch()


if __name__ == '__main__':
    unittest.main()
