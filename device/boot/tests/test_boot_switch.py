# SPDX-License-Identifier: MIT
"""piano-boot-switch with temporary files standing in for slot-A partitions."""
import contextlib
import fcntl
import io
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import fakepiano as fp
from fakepiano import slot

tool = fp.load_tool('piano-boot-switch')
LIST_INHIBITORS = ['busctl', '--system', '--json=short', 'call',
                   'org.freedesktop.login1', '/org/freedesktop/login1',
                   'org.freedesktop.login1.Manager', 'ListInhibitors']
INHIBITED = 'Call to Reboot failed: Operation denied due to active block inhibitor\n'


class Base(unittest.TestCase):
    occupant = 'ubuntu'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = fp.FakePiano(self.tmp.name, occupant=self.occupant)
        self.store = self.p.root / 'var/lib/piano/switch'
        self.inhibitors = []
        capture = self.p.host.capture

        def commands(argv):
            result = capture(argv)
            if argv == LIST_INHIBITORS:
                result.stdout = json.dumps({'type': 'a(ssssuu)', 'data': [self.inhibitors]})
            return result
        patcher = mock.patch.object(self.p.host, 'capture', side_effect=commands)
        self.capture = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, env or {}, clear=False):
            rc = tool.main(list(argv), host=self.p.host)
        self.stderr = err.getvalue()
        return rc, json.loads(out.getvalue())

    def runs(self):
        return [e[1] for e in self.p.host.events if e[0] == 'run']

    def assert_slot(self, name):
        self.assertEqual(self.p.slot_content(), self.p.sets[name])

    def refused(self, code, *argv):
        before = self.p.slot_content()
        rc, report = self.run_tool(*argv)
        self.assertEqual((rc, report['result'], report['code']), (1, 'refused', code), report)
        self.assertEqual(self.p.slot_content(), before)
        return report


class Switch(Base):
    def test_to_android_writes_reads_back_and_records(self):
        rc, report = self.run_tool('to-android')
        self.assertEqual((rc, report['result'], report['rollback']), (0, 'written', 'ubuntu'), report)
        self.assert_slot('android')
        state = json.loads((self.store / 'state.json').read_text())
        self.assertEqual((state['result'], state['occupant'], state['command']), ('written', 'android', 'to-android'))
        log = (self.store / 'switch.log').read_text()
        self.assertIn('Slot A now holds store/android', log)
        self.assertEqual(self.runs(), [], 'no reboot without --reboot/--desktop')
        self.assertTrue(any(e[0] == 'log' for e in self.p.host.events))

    def test_desktop_reboots_after_success(self):
        rc, report = self.run_tool('to-android', '--desktop')
        self.assertEqual(rc, 0)
        self.assertEqual(self.runs(), [LIST_INHIBITORS, ['systemctl', 'reboot']])

    def test_already_target_is_unchanged_and_still_reboots_for_desktop(self):
        self.p.fill_slot(self.p.sets['android'])
        rc, report = self.run_tool('to-android', '--desktop')
        self.assertEqual((rc, report['result']), (0, 'unchanged'))
        self.assertEqual(self.runs(), [LIST_INHIBITORS, ['systemctl', 'reboot']])

    def test_to_ubuntu_from_android(self):
        self.p.fill_slot(self.p.sets['android'])
        rc, report = self.run_tool('to-ubuntu', '--reboot')
        self.assertEqual((rc, report['result'], report['rollback']), (0, 'written', 'android'))
        self.assert_slot('ubuntu')
        self.assertEqual(self.runs(), [LIST_INHIBITORS, ['systemctl', 'reboot']])

    def test_occupant_ignores_slot_attributes(self):
        # The former bit-exact attribute snapshot refused after every ABL decrement.
        for a in (0x3f, 0x37, 0x2f, 0x77):
            self.p.set_boot_attrs(a=a)
            rc, report = self.run_tool('status')
            self.assertEqual((rc, report['occupant']), (0, 'ubuntu'), a)
        self.assertEqual(self.run_tool('to-android')[0], 0)

    def test_dry_run_writes_nothing(self):
        rc, report = self.run_tool('to-android', '--dry-run', '--desktop')
        self.assertEqual((rc, report['result']), (0, 'would-write'))
        self.assert_slot('ubuntu')
        self.assertEqual(self.runs(), [])

    def test_kernel_update_rewrites_previous_ubuntu(self):
        self.p.fill_slot(self.p.sets['ubuntu.prev'])
        rc, report = self.run_tool('to-ubuntu', '--only-if-ubuntu')
        self.assertEqual((rc, report['result'], report['rollback']), (0, 'written', 'ubuntu.prev'))
        self.assert_slot('ubuntu')
        self.assertEqual(self.runs(), [])

    def test_kernel_update_leaves_android_in_place(self):
        self.p.fill_slot(self.p.sets['android'])
        rc, report = self.run_tool('to-ubuntu', '--only-if-ubuntu')
        self.assertEqual((rc, report['result']), (0, 'skipped'))
        self.assert_slot('android')

    def test_kernel_update_refuses_unknown_occupant(self):
        self.p.fill_slot(fp.image_set('ubuntu', 'unregistered'))
        self.refused('occupant-unknown', 'to-ubuntu', '--only-if-ubuntu')

    def test_signals_blocked_during_writes(self):
        seen = []
        real = tool.write_set

        def check(*a):
            seen.append(slot.signal.SIGTERM in slot.signal.pthread_sigmask(slot.signal.SIG_BLOCK, []))
            real(*a)
        with mock.patch.object(tool, 'write_set', check):
            self.assertEqual(self.run_tool('to-android')[0], 0)
        self.assertEqual(seen, [True])
        self.assertNotIn(slot.signal.SIGTERM, slot.signal.pthread_sigmask(slot.signal.SIG_BLOCK, []))


class Reboot(Base):
    def setUp(self):
        super().setUp()
        self.inhibitors = [
            ['shutdown:sleep', 'gnome-session-s', 'user session inhibited', 'block', 1000, 123],
            ['handle-lid-switch', 'gsd-power', 'GNOME handling lid', 'block', 1000, 124],
            ['handle-power-key', 'gsd-media-keys', 'GNOME handling keys', 'block', 1000, 125],
            ['shutdown', 'delay-only', 'Saving state', 'delay', 1000, 126],
        ]
        self.reboot_stderr = INHIBITED
        self.reboot_rc = 1
        commands = self.capture.side_effect

        def results(argv):
            result = commands(argv)
            if argv[:2] == ['systemctl', 'reboot']:
                result.returncode = self.reboot_rc
                result.stderr = self.reboot_stderr
            return result
        self.capture.side_effect = results

    def test_inhibited_reboot_keeps_android_and_reports_real_error_and_next_boot(self):
        rc, report = self.run_tool('to-android', '--reboot')
        self.assertEqual((rc, report['result']), (1, 'written'))
        self.assert_slot('android')
        self.assertIn(INHIBITED, self.stderr)
        self.assertEqual(self.stderr.count(INHIBITED), 1)
        self.assertIn('Slot A now holds the android set', self.stderr)
        self.assertIn('the next boot will be Android', self.stderr)
        self.assertIn('reboot manually', self.stderr)
        self.assertIn('sudo piano-boot-switch to-android --reboot --force', self.stderr)
        self.assertNotIn('nothing was written', self.stderr)
        self.assertIn('who=gnome-session-s; why=user session inhibited', self.stderr)
        for other in ('gsd-power', 'gsd-media-keys', 'delay-only'):
            self.assertNotIn(other, self.stderr)
        self.assertLess(self.stderr.index('who=gnome-session-s'), self.stderr.index(INHIBITED))
        self.assertEqual(self.runs(), [LIST_INHIBITORS, ['systemctl', 'reboot']])
        self.assertEqual(json.loads((self.store / 'state.json').read_text())['occupant'], 'android')
        self.assertIn('the next boot will be Android', (self.store / 'switch.log').read_text())

    def test_force_retry_ignores_inhibitors_without_rewriting_slot(self):
        self.assertEqual(self.run_tool('to-android', '--reboot')[0], 1)
        self.p.host.events.clear()
        self.reboot_rc, self.reboot_stderr = 0, ''
        with mock.patch.object(tool, 'write_set', side_effect=AssertionError('must not rewrite')):
            rc, report = self.run_tool('to-android', '--reboot', '--force')
        self.assertEqual((rc, report['result']), (0, 'unchanged'))
        self.assert_slot('android')
        self.assertEqual(self.runs(), [LIST_INHIBITORS, ['systemctl', 'reboot', '-i']])
        self.assertIn('who=gnome-session-s; why=user session inhibited', self.stderr)
        self.assertIn('unsaved work may be lost', self.stderr)

    def test_force_still_reports_reboot_failure_to_desktop_caller(self):
        self.reboot_stderr = 'Failed to connect to bus: No such file or directory\n'
        rc, report = self.run_tool('to-android', '--desktop', '--force',
                                   env={'PKEXEC_UID': str(os.getuid())})
        self.assertEqual((rc, report['result']), (1, 'written'))
        self.assert_slot('android')
        self.assertIn(self.reboot_stderr, self.stderr)
        self.assertEqual(self.runs()[:2], [LIST_INHIBITORS, ['systemctl', 'reboot', '-i']])
        notification = self.runs()[-1]
        self.assertIn('Notify', notification)
        self.assertIn(self.reboot_stderr, notification[-4])
        self.assertIn('the next boot will be Android', notification[-4])

    def test_success_still_surfaces_systemctl_stderr_verbatim(self):
        self.reboot_rc, self.reboot_stderr = 0, 'A warning with trailing spaces  \n\n'
        self.assertEqual(self.run_tool('to-android', '--reboot')[0], 0)
        self.assertIn(self.reboot_stderr, self.stderr)

    def test_missing_systemctl_reports_written_state(self):
        commands = self.capture.side_effect

        def missing(argv):
            if argv[0] == 'systemctl':
                raise FileNotFoundError('systemctl not found')
            return commands(argv)
        self.capture.side_effect = missing
        rc, report = self.run_tool('to-android', '--reboot')
        self.assertEqual((rc, report['result']), (1, 'written'))
        self.assert_slot('android')
        self.assertIn('systemctl not found', self.stderr)
        self.assertIn('the next boot will be Android', self.stderr)

    def test_listing_failure_is_visible_and_systemctl_still_decides(self):
        commands = self.capture.side_effect

        def broken_list(argv):
            result = commands(argv)
            if argv == LIST_INHIBITORS:
                result.returncode, result.stderr = 1, 'Could not get active inhibitors\n'
            return result
        self.capture.side_effect = broken_list
        rc, report = self.run_tool('to-android', '--reboot')
        self.assertEqual((rc, report['result']), (1, 'written'))
        self.assertIn('Could not get active inhibitors\n', self.stderr)
        self.assertIn('Could not list shutdown block inhibitors', self.stderr)
        self.assertIn(INHIBITED, self.stderr)
        self.assertEqual(self.runs(), [LIST_INHIBITORS, ['systemctl', 'reboot']])

    def test_ubuntu_reboot_failure_names_ubuntu(self):
        self.p.fill_slot(self.p.sets['android'])
        rc, report = self.run_tool('to-ubuntu', '--reboot')
        self.assertEqual((rc, report['result']), (1, 'written'))
        self.assert_slot('ubuntu')
        self.assertIn('the next boot will be Ubuntu', self.stderr)

    def test_force_dry_run_never_reboots(self):
        for occupant, expected in (('ubuntu', 'would-write'), ('android', 'unchanged')):
            with self.subTest(occupant=occupant):
                self.p.fill_slot(self.p.sets[occupant])
                rc, report = self.run_tool('to-android', '--dry-run', '--desktop', '--force')
                self.assertEqual((rc, report['result']), (0, expected))
                self.assert_slot(occupant)
                self.assertEqual(self.runs(), [])


class Rollback(Base):
    def corrupt_readback(self, bad):
        real = slot.digest

        def digest(path, size):
            if path == self.p.lun4.part_node(bad):
                with open(path, 'rb') as f:
                    if f.read(4096) == self.p.sets['android'][bad[:-2]][:4096]:
                        return '0' * 64
            return real(path, size)
        return mock.patch.object(slot, 'digest', digest)

    def test_readback_failure_restores_the_occupant(self):
        with self.corrupt_readback('vendor_boot_a'):
            rc, report = self.run_tool('to-android', '--desktop', env={'PKEXEC_UID': str(os.getuid())})
        self.assertEqual((rc, report['result']), (1, 'rolled-back'), report)
        self.assert_slot('ubuntu')
        state = json.loads((self.store / 'state.json').read_text())
        self.assertEqual((state['result'], state['occupant']), ('rolled-back', 'ubuntu'))
        runs = self.runs()
        self.assertNotIn(['systemctl', 'reboot'], runs)
        self.assertEqual(len(runs), 1)
        user = pwd.getpwuid(os.getuid()).pw_name
        self.assertEqual(runs[0][:4], ['busctl', '--user', f'--machine={user}@.host', 'call'])
        self.assertIn('Notify', runs[0])
        self.assertIn('store/ubuntu was written back', runs[0][-4])

    def test_write_error_restores_the_occupant(self):
        real = slot.pwrite_all
        calls = []

        def fail_second(fd, data, offset):
            calls.append(offset)
            if len(calls) == 2:
                raise OSError(5, 'Input/output error')
            real(fd, data, offset)
        with mock.patch.object(slot, 'pwrite_all', fail_second):
            rc, report = self.run_tool('to-android')
        self.assertEqual((rc, report['result']), (1, 'rolled-back'))
        self.assert_slot('ubuntu')

    def test_failed_rollback_is_reported(self):
        with mock.patch.object(slot, 'pwrite_all', side_effect=OSError(5, 'Input/output error')):
            rc, report = self.run_tool('to-android', '--desktop')
        self.assertEqual((rc, report['result']), (1, 'rollback-failed'))
        self.assertIn('recover from Fastboot', report['message'])
        self.assertEqual(self.runs(), [], 'no PKEXEC_UID: no notification, and never a reboot')

    def test_cli_failure_without_desktop_does_not_notify(self):
        with self.corrupt_readback('boot_a'):
            rc, report = self.run_tool('to-android', env={'PKEXEC_UID': str(os.getuid())})
        self.assertEqual((rc, self.runs()), (1, []))
        self.assertIn('ERROR', (self.store / 'switch.log').read_text())


class Refusals(Base):
    def test_unknown_occupant(self):
        self.p.fill_slot(fp.image_set('android', 'other-android'))
        report = self.refused('occupant-unknown', 'to-android', '--desktop')
        self.assertIn('nothing was written', report['message'])

    def test_target_set_digest(self):
        img = self.store / 'android/boot.img'
        img.write_bytes(img.read_bytes()[:-1] + b'\x01')
        self.refused('set-digest', 'to-android')

    def test_target_set_size(self):
        img = self.store / 'android/init_boot.img'
        img.write_bytes(img.read_bytes() + b'\0')
        self.refused('set-size', 'to-android')

    def test_rollback_set_must_verify_before_the_first_write(self):
        img = self.store / 'ubuntu/vendor_boot.img'
        img.write_bytes(img.read_bytes()[:-1] + b'\x01')
        self.refused('set-digest', 'to-android')

    def test_android_set_with_ubuntu_marker(self):
        marked = fp.image_set('ubuntu', 'fake-android')
        self.p.store_set('android', marked)
        self.refused('set-kind', 'to-android')

    def test_ubuntu_set_without_marker(self):
        self.p.fill_slot(self.p.sets['android'])
        self.p.store_set('ubuntu', fp.image_set('android', 'not-ubuntu'))
        self.refused('set-kind', 'to-ubuntu')

    def test_malformed_sums(self):
        (self.store / 'android/SHA256SUMS').write_text('nonsense\n')
        self.refused('set-sums-invalid', 'to-android')

    def test_slot_b_and_unselected_slot(self):
        self.p.cmdline(fp.UBUNTU_CMDLINE + ' androidboot.slot_suffix=_b')
        self.refused('not-slot-a', 'to-android')
        self.p.cmdline(fp.UBUNTU_CMDLINE + ' ' + fp.ABL_APPEND)
        self.p.set_boot_attrs(a=0x37, b=0x06)
        self.refused('slot-a-not-selected', 'to-android')

    def test_serial(self):
        self.p.cmdline(fp.UBUNTU_CMDLINE.replace(fp.SERIAL, 'other'))
        self.refused('serial-mismatch', 'to-android')

    def test_serial_from_bootconfig_only(self):
        # The address- and serial-free release boot set: ABL passes the serial only in bootconfig.
        release = fp.UBUNTU_CMDLINE.replace(f' androidboot.serialno={fp.SERIAL}', '')
        self.p.cmdline(release + ' androidboot.verifiedbootstate=orange')
        self.refused('serial-mismatch', 'to-android')
        (self.p.root / 'proc/bootconfig').write_text(
            f'androidboot.serialno = "{fp.SERIAL}"\nandroidboot.slot_suffix = "_a"\n')
        rc, report = self.run_tool('status')
        self.assertEqual(rc, 0, report)
        self.assertEqual(report['occupant'], 'ubuntu')
        (self.p.root / 'proc/bootconfig').write_text('androidboot.serialno = "other"\n')
        self.refused('serial-mismatch', 'to-android')

    def test_serial_conflict(self):
        (self.p.root / 'proc/bootconfig').write_text('androidboot.serialno = "other"\n')
        self.refused('serial-conflict', 'to-android')

    def test_malformed_map(self):
        (self.p.root / 'etc/piano/boot-partitions.json').write_text('[{"label": "boot_a"}')
        self.refused('invalid-input', 'to-android')

    def test_map_mismatch(self):
        rows = json.loads((self.p.root / 'etc/piano/boot-partitions.json').read_text())
        rows[0]['start_bytes'] += 4096
        (self.p.root / 'etc/piano/boot-partitions.json').write_text(json.dumps(rows))
        self.refused('map-mismatch', 'to-android')

    def test_mounted(self):
        mount = self.p.root / 'proc/self/mountinfo'
        boot = next(i for i, r in enumerate(self.p.lun4.rows, 1) if r.name == 'boot_a')
        mount.write_text(mount.read_text() + f'30 22 8:{4 * 16 + boot} / /mnt rw - vfat /dev/x rw\n')
        self.refused('partition-mounted', 'to-android')

    def test_expiring_debug_session(self):
        self.p.cmdline(fp.UBUNTU_CMDLINE + ' piano.debug=1 piano.debug_timeout=150')
        self.refused('session-expiring', 'to-android')
        # Read-only commands stay available.
        self.assertEqual(self.run_tool('status')[0], 0)

    def test_lock_shared_with_marker(self):
        with open(self.p.root / 'run/piano-boot-switch.lock', 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.refused('locked', 'to-android')

    def test_backup_gpt_damage_is_reported_not_blocking(self):
        self.p.lun4.write_gpt(after_seal=lambda b: b['backup_header'].__setitem__(40, 1))
        rc, report = self.run_tool('status')
        self.assertEqual(report['backup_gpt']['code'], 'gpt-header-crc')
        self.assertEqual(self.run_tool('to-android')[0], 0)


class StatusVerify(Base):
    def test_status_reports_stale_android_set_by_image(self):
        ksu = dict(self.p.sets['android'])
        ksu['init_boot'] = fp.boot_image(fp.SIZES['init_boot'], '', 'ksu')
        self.p.fill_slot(ksu)
        rc, report = self.run_tool('status')
        self.assertEqual((rc, report['occupant'], report['slot_content']), (0, 'unknown', 'android'))
        self.assertEqual(report['store']['android']['matches_slot'],
                         {'boot.img': True, 'init_boot.img': False, 'vendor_boot.img': True})
        self.assertEqual(report['slot_sha256']['init_boot.img'], fp.sha(ksu['init_boot']))
        rc, report = self.run_tool('verify')
        self.assertEqual((rc, report['code']), (1, 'verify-failed'))

    def test_status_lists_each_set_and_stray_entries(self):
        for stray in ('ubuntu-debug', 'ubuntu-debug-fixed', 'ubuntu-original-v4'):
            self.p.store_set(stray, fp.image_set('ubuntu', stray))
        (self.store / '.lock').touch()
        (self.store / '.build-x1').mkdir()
        rc, report = self.run_tool('status')
        self.assertEqual(report['stray'], ['ubuntu-debug', 'ubuntu-debug-fixed', 'ubuntu-original-v4'])
        self.assertEqual(set(report['store']), {'android', 'ubuntu', 'ubuntu.prev'})
        self.assertEqual({n: v['equals_slot'] for n, v in report['store'].items()},
                         {'android': False, 'ubuntu': True, 'ubuntu.prev': False})
        self.assertEqual(report['store']['ubuntu']['sha256']['boot.img'], fp.sha(self.p.sets['ubuntu']['boot']))
        # A stray set identical to the slot is never taken as the occupant.
        self.p.fill_slot(fp.image_set('ubuntu', 'ubuntu-debug'))
        self.assertEqual(self.run_tool('status')[1]['occupant'], 'unknown')
        self.refused('occupant-unknown', 'to-android')

    def test_status_names_the_mainline_controller(self):
        rc, report = self.run_tool('status')
        self.assertEqual((rc, report['controller'], report['disk'], report['scsi']),
                         (0, '1d84000.ufs', 'sde', '0:0:0:4'))

    def test_verify_passes(self):
        rc, report = self.run_tool('verify')
        self.assertEqual((rc, report['occupant'], report['android'], report['ubuntu']),
                         (0, 'ubuntu', 'verified', 'verified'), report)

    def test_status_reports_last_state(self):
        self.run_tool('to-android')
        rc, report = self.run_tool('status')
        self.assertEqual((report['occupant'], report['last']['result']), ('android', 'written'))


class Import(Base):
    occupant = 'android'

    def test_import_android_from_slot_replaces_stale_set(self):
        ksu = dict(self.p.sets['android'])
        ksu['init_boot'] = fp.boot_image(fp.SIZES['init_boot'], '', 'ksu')
        self.p.fill_slot(ksu)
        rc, report = self.run_tool('import-android')
        self.assertEqual((rc, report['result'], report['source']), (0, 'imported', 'slot-a'), report)
        self.assertEqual((self.store / 'android/init_boot.img').read_bytes(), ksu['init_boot'])
        sums = (self.store / 'android/SHA256SUMS').read_text().splitlines()
        self.assertEqual(sums, [f'{fp.sha(ksu[n])}  {n}.img' for n in ('boot', 'init_boot', 'vendor_boot')])
        self.assertEqual(self.run_tool('status')[1]['occupant'], 'android')
        self.assertFalse((self.store / '.android.new').exists() or (self.store / '.android.old').exists())

    def test_import_android_from_directory(self):
        ksu = dict(self.p.sets['android'])
        ksu['init_boot'] = fp.boot_image(fp.SIZES['init_boot'], '', 'ksu')
        source = self.p.image_dir('tmp-ksu', ksu)
        rc, report = self.run_tool('import-android', '--from', str(source))
        self.assertEqual((rc, report['result']), (0, 'imported'), report)
        self.assertEqual((self.store / 'android/init_boot.img').read_bytes(), ksu['init_boot'])
        # Slot A still holds the old Android set, which is no longer in the store.
        self.assertEqual(self.run_tool('status')[1]['occupant'], 'unknown')

    def test_import_android_refuses_ubuntu_content(self):
        self.p.fill_slot(fp.image_set('ubuntu', 'host-release'))
        before = (self.store / 'android/SHA256SUMS').read_text()
        self.refused('ubuntu-content', 'import-android')
        self.assertEqual((self.store / 'android/SHA256SUMS').read_text(), before)
        source = self.p.image_dir('tmp-ubuntu', fp.image_set('ubuntu', 'y'))
        self.refused('ubuntu-content', 'import-android', '--from', str(source))

    def test_import_android_from_directory_checks_sizes_and_digests(self):
        source = self.p.image_dir('tmp-bad', self.p.sets['android'])
        (source / 'boot.img').write_bytes(b'short')
        self.refused('set-size', 'import-android', '--from', str(source))
        shutil.rmtree(source)
        source = self.p.image_dir('tmp-bad', self.p.sets['android'])
        (source / 'SHA256SUMS').write_text((source / 'SHA256SUMS').read_text().replace('a', 'b', 1))
        self.refused('set-digest', 'import-android', '--from', str(source))

    def test_import_unchanged(self):
        rc, report = self.run_tool('import-android')
        self.assertEqual((rc, report['result']), (0, 'unchanged'))


class ImportUbuntu(Base):
    def setUp(self):
        super().setUp()
        self.release = fp.image_set('ubuntu', 'host-release')
        self.p.fill_slot(self.release)

    def test_registers_running_slot_set(self):
        old_ubuntu = (self.store / 'ubuntu/SHA256SUMS').read_text()
        rc, report = self.run_tool('import-ubuntu')
        self.assertEqual((rc, report['result']), (0, 'imported'), report)
        self.assertEqual((self.store / 'ubuntu.prev/SHA256SUMS').read_text(), old_ubuntu)
        self.assertEqual(self.run_tool('status')[1]['occupant'], 'ubuntu')
        # The marker now accepts the slot.
        marker = fp.load_tool('piano-slot-success')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(marker.main(['--mark'], host=self.p.host), 0)

    def test_from_directory(self):
        self.p.fill_slot(self.p.sets['android'])
        source = self.p.image_dir('tmp-release', self.release)
        rc, report = self.run_tool('import-ubuntu', '--from', str(source))
        self.assertEqual((rc, report['result']), (0, 'imported'), report)
        self.assertEqual((self.store / 'ubuntu/boot.img').read_bytes(), self.release['boot'])

    def test_previous_source_promotes_then_switches(self):
        current = self.p.sets['ubuntu']
        previous = self.p.sets['ubuntu.prev']
        self.p.fill_slot(current)
        source = self.p.image_dir('rollback-copy', previous)
        rc, report = self.run_tool('import-ubuntu', '--from', str(source))
        self.assertEqual((rc, report['result']), (0, 'imported'), report)
        self.assertEqual(self.p.slot_content(), current, 'import must not write the slot')
        for name in slot.NAMES:
            self.assertEqual((self.store / 'ubuntu' / (name + '.img')).read_bytes(), previous[name])
            self.assertEqual((self.store / 'ubuntu.prev' / (name + '.img')).read_bytes(), current[name])
        self.assertEqual(self.run_tool('to-ubuntu')[0], 0)
        self.assertEqual(self.p.slot_content(), previous, 'the documented rollback must select the old set')
        self.assertEqual(self.run_tool('verify')[0], 0)
        self.assertEqual(self.run_tool('import-ubuntu', '--from', str(source))[1]['result'], 'unchanged')

    def test_previous_source_dry_run_preserves_both_sets(self):
        self.p.fill_slot(self.p.sets['ubuntu'])
        before = {str(p.relative_to(self.store)): p.read_bytes()
                  for p in self.store.rglob('*') if p.is_file() and p.name != '.lock'}
        rc, report = self.run_tool('import-ubuntu', '--from', str(self.store / 'ubuntu.prev'), '--dry-run')
        self.assertEqual((rc, report['result']), (0, 'would-import'), report)
        after = {str(p.relative_to(self.store)): p.read_bytes()
                 for p in self.store.rglob('*') if p.is_file() and p.name != '.lock'}
        self.assertEqual(after, before)
        self.assert_slot('ubuntu')

    def test_refuses_debug_session(self):
        self.p.cmdline(fp.UBUNTU_CMDLINE + ' piano.debug=1 piano.debug_timeout=900')
        self.refused('not-booted-from-slot', 'import-ubuntu')

    def test_refuses_android_content(self):
        self.p.fill_slot(self.p.sets['android'])
        self.p.store_set('android', fp.image_set('android', 'unrelated'))
        self.refused('wrong-content', 'import-ubuntu')


class Cli(unittest.TestCase):
    def test_capture_retains_stderr_and_status_from_a_real_child(self):
        host = slot.Host()
        result = host.capture([sys.executable, '-c',
                               f'import sys; sys.stderr.write({INHIBITED!r}); sys.exit(1)'])
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, '', INHIBITED))

    def test_force_requires_a_reboot_request(self):
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(io.StringIO()):
            tool.main(['to-android', '--force'], host=slot.Host('/nonexistent'))
        self.assertEqual(ctx.exception.code, 2)

    @unittest.skipIf(os.geteuid() == 0, 'checks the unprivileged refusal')
    def test_standalone_script_runs_and_requires_root(self):
        for script, argv in (('piano-boot-switch', ['status']), ('piano-slot-success', ['--check'])):
            r = subprocess.run([sys.executable, str(fp.BOOT / script), *argv], capture_output=True, text=True)
            self.assertEqual((r.returncode, json.loads(r.stdout)['code']), (1, 'not-root'), r.stderr)
            r = subprocess.run([sys.executable, str(fp.BOOT / script), '--help'], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)

    def test_subcommand_required(self):
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(io.StringIO()):
            tool.main([], host=slot.Host('/nonexistent'))
        self.assertEqual(ctx.exception.code, 2)

    def test_only_if_ubuntu_is_a_to_ubuntu_option(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            tool.main(['to-android', '--only-if-ubuntu'], host=slot.Host('/nonexistent'))


if __name__ == '__main__':
    unittest.main()
