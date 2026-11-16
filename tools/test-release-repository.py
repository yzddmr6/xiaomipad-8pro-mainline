# SPDX-License-Identifier: MIT
"""The previous batch must not be asked to retain packages it never managed."""
import importlib.util
from pathlib import Path
import tempfile
import contextlib
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    'release_repository', TOOLS / 'release-repository.py')
release_repository = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_repository)


def row(package, version, source):
    return dict(package=package, version=version, architecture='arm64',
                source=source,
                file='pool/' + package + '_' + version + '_arm64.deb',
                sha256='0' * 64, bytes=1, install=True)


def batch(identifier, rows):
    return dict(schema=1, id=identifier, product='erhai', packages=rows)


DEVICE = row('erhai-device', '0.8.1+b1f68d8c8', 'erhai-device')
# v0.9.0 added these ordinary Ubuntu libraries to its release batch. Qt and
# fcitx5 already had four of them installed at exactly these versions.
ADDED = [
    row('libxcb-render-util0', '0.3.10-1build1', 'libxcb'),
    row('libxcb-ewmh2', '0.4.2-1build1', 'xcb-util-wm'),
    row('libxcb-icccm4', '0.4.2-1build1', 'xcb-util-wm'),
    row('libxcb-xinput0', '1.17.0-2ubuntu1', 'libxcb'),
]


class PreviousBatchCohort(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Path(self.directory.name) / 'releases'
        self.store.mkdir()
        self.previous = Path(self.directory.name) / 'erhai-0.8.1'
        self.old = batch('erhai-0.8.1', [DEVICE])

    def tearDown(self):
        self.directory.cleanup()

    def resolve(self, new_rows, before):
        return release_repository.previous_for_installed(
            self.previous, self.old, self.previous, batch('erhai-0.9.0', new_rows),
            before, self.store)

    def test_new_dependencies_at_new_version_do_not_block_upgrade(self):
        """Regression for erhai v0.8.1 -> v0.9.0.

        The upgrade leaves these packages untouched and a rollback has nothing
        to restore, so no exact .deb is needed in the previous batch.
        """
        before = {'erhai-device': DEVICE['version']}
        before.update({r['package']: r['version'] for r in ADDED})
        got_previous, got_old = self.resolve([DEVICE, *ADDED], before)
        self.assertIs(got_previous, self.previous)
        self.assertIs(got_old, self.old)
        self.assertFalse((self.store / 'erhai-0.8.1-before-erhai-0.9.0').exists())

    def test_other_installed_dependency_version_requires_authenticated_capture(self):
        self.previous.mkdir()
        before = {'erhai-device': DEVICE['version'], 'libxcb-ewmh2': '0.4.1-1'}
        with patch.object(release_repository.subprocess, 'check_output', return_value='libxcb-ewmh2\tarm64\n'), patch.object(release_repository.subprocess, 'run') as archive:
            archive.return_value.returncode = 100
            archive.return_value.stdout = ''
            with self.assertRaisesRegex(ValueError, 'exact installed distribution package unavailable'):
                self.resolve([DEVICE, ADDED[1]], before)
            archive.assert_called_once()

    def test_drifted_release_package_is_still_rejected(self):
        before = {'erhai-device': '0.8.0+other'}
        with patch.object(release_repository.subprocess, 'check_output', return_value='erhai-device\tarm64\n'):
            with self.assertRaisesRegex(ValueError, 'previous batch is missing'):
                self.resolve([DEVICE], before)

    def test_unchanged_cohort_is_returned_untouched(self):
        before = {'erhai-device': DEVICE['version']}
        got_previous, got_old = self.resolve([DEVICE], before)
        self.assertIs(got_previous, self.previous)
        self.assertIs(got_old, self.old)


class RepairPolicy(unittest.TestCase):
    def test_rollback_inventory_includes_broken_new_only_packages(self):
        status = 'ii \tgood\t1\niF \tbad\t2\niU \tnew\t3\nrc \tremoved\t4\niH \thalf\t5\n'
        with patch.object(release_repository.subprocess, 'check_output', return_value=status):
            self.assertEqual(release_repository.installed(), {'good': '1'})
            self.assertEqual(release_repository.installed(include_unconfigured=True),
                             {'good': '1', 'bad': '2', 'new': '3', 'half': '5'})

    def test_upgrade_repairs_unpacked_dependencies(self):
        with patch.object(release_repository, 'apt_preferences', return_value=contextlib.nullcontext([])), patch.object(release_repository, 'run') as run, patch.object(release_repository.subprocess, 'check_output', side_effect=['unpacked dependency', '']):
            release_repository.solve_and_install(['fixture=2'], 'piano')
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(sum('--fix-broken' in c for c in commands), 2)
            self.assertEqual(commands[-1], ['dpkg', '--configure', '-a'])

    def test_rollback_replaces_bad_postinst_before_configuring(self):
        with patch.object(release_repository, 'apt_preferences', return_value=contextlib.nullcontext([])), patch.object(release_repository, 'run') as run, patch.object(release_repository.subprocess, 'check_output', return_value='') as audit:
            release_repository.solve_and_install(['fixture=1'], 'piano', repair_unpacked=False)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertFalse(any('--fix-broken' in c for c in commands))
            self.assertIn('--allow-downgrades', commands[0])
            self.assertEqual(commands[-1], ['dpkg', '--configure', '-a'])
            audit.assert_called_once_with(['dpkg', '--audit'], text=True)


if __name__ == '__main__':
    unittest.main()
