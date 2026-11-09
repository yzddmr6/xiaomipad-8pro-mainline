#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline package contracts and APT's real PPD provider replacement."""
import importlib.util
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_apt', REPO / 'tools/prepare-ubuntu-apt.py')
prepare_apt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_apt)


class DesktopIntegration(unittest.TestCase):
    def test_built_package_payload_and_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'debs'
            subprocess.run(['python3', str(REPO / 'tools/build-device-debs.py'), '--desktop-only',
                            '--version', '0.5.0~test', '--output', str(out)], check=True,
                           stdout=subprocess.DEVNULL)
            deb = next(out.glob('*.deb'))
            control = subprocess.check_output(['dpkg-deb', '-f', str(deb)], text=True)
            root = out / 'extract'
            subprocess.run(['dpkg-deb', '-R', str(deb), str(root)], check=True)
            conffiles = (root / 'DEBIAN/conffiles').read_text().splitlines()
            self.assertIn('/etc/tuned/piano-ppd.conf', conffiles)
            self.assertFalse((root / 'etc/tuned/ppd.conf').exists())
            self.assertEqual((root / 'etc/tuned/piano-ppd.conf').read_bytes(),
                             (REPO / 'device/power/tuned/ppd.conf').read_bytes())
            profiles = list((root / 'usr/lib/tuned/profiles').glob('*/tuned.conf'))
            self.assertEqual(len(profiles), 3)
            for p in profiles:
                self.assertEqual(p.read_bytes(), (REPO / 'device/power/tuned/profiles' /
                                                 p.parent.name / 'tuned.conf').read_bytes())
            self.assertIn('tuned-ppd (>= 2.25.1)', control)
            self.assertIn('gunyahvm (>= 1.0.0-1)', control)
            self.assertTrue(os.access(root / 'usr/libexec/piano/gunyahvm-favorite', os.X_OK))
            self.assertIn('OnlyShowIn=GNOME;', (root / 'etc/xdg/autostart/piano-gunyahvm-favorite.desktop').read_text())
            dependencies = next(line for line in control.splitlines() if line.startswith('Depends: '))
            for package in ('showtime', 'gstreamer1.0-plugins-base', 'gstreamer1.0-plugins-good',
                            'gstreamer1.0-plugins-bad', 'gstreamer1.0-libav'):
                self.assertEqual(dependencies.split(', ').count(package), 1)
            self.assertFalse(list(root.rglob('__pycache__')))
            helper = 'usr/libexec/firefox-snap-remove'
            self.assertEqual((root / helper).read_bytes(),
                             (REPO / 'packaging/firefox-snap-migration' / helper).read_bytes())
            self.assertFalse((root / 'usr/libexec/piano/firefox-snap-remove').exists())
            self.assertIn('ExecStart=/usr/libexec/firefox-snap-remove /var/lib/piano/firefox-snap-removed',
                          (root / 'usr/lib/systemd/system/piano-firefox-snap-remove.service').read_text())
            self.assertFalse((root / 'usr/local').exists())
            self.assertFalse((root / 'etc/mpv').exists())
            self.assertFalse(list(root.rglob('powerprofilesctl')))
            self.assertIn("power-button-action='lock'", (root / 'usr/share/glib-2.0/schemas/'
                         '50_piano-ubuntu-desktop.gschema.override').read_text())
            for script in ('postinst', 'prerm', 'postrm'):
                subprocess.run(['sh', '-n', str(root / 'DEBIAN' / script)], check=True)

    def test_pins_keep_local_versions_against_future_ubuntu_updates(self):
        # No apt update or network: synthetic index plus the real shipped preferences.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in ('lists/partial', 'empty', 'cache', 'prefs'):
                (d / name).mkdir(parents=True)
            (d / 'sources.list').write_text('deb [trusted=yes] https://example.invalid/ubuntu resolute main\n')
            versions = {'gnome-settings-daemon': '50.0-1ubuntu1+piano2',
                        'gnome-settings-daemon-common': '50.0-1ubuntu1+piano2',
                        'gnome-control-center': '1:50.0-0ubuntu6+piano2',
                        'gnome-control-center-data': '1:50.0-0ubuntu6+piano2'}
            (d / 'status').write_text('\n'.join(
                f'Package: {name}\nStatus: install ok installed\nArchitecture: arm64\n'
                f'Version: {version}\nDescription: synthetic installed package\n'
                for name, version in versions.items()))
            (d / 'lists/example.invalid_ubuntu_dists_resolute_main_binary-arm64_Packages').write_text(
                '\n'.join(f'Package: {name}\nArchitecture: arm64\nVersion: 9:99.0-1ubuntu1\n'
                          f'Description: synthetic security update\nFilename: pool/{name}.deb\n'
                          'Size: 1\nSHA256: ' + '0' * 64 + '\n' for name in versions))
            shutil.copyfile(REPO / 'packaging/piano-ubuntu-desktop/etc/apt/preferences.d/'
                            'piano-power-button-lock', d / 'prefs/piano')
            options = ['-o', 'APT::Architecture=arm64', '-o', f'Dir::State={d}', '-o',
                       f'Dir::State::status={d}/status', '-o', f'Dir::Cache={d}/cache', '-o',
                       f'Dir::Etc::sourcelist={d}/sources.list', '-o', f'Dir::Etc::sourceparts={d}/empty',
                       '-o', 'Dir::Etc::preferences=/dev/null', '-o', f'Dir::Etc::preferencesparts={d}/prefs']
            for name, version in versions.items():
                policy = subprocess.check_output(['apt-cache', *options, 'policy', name], text=True,
                                                 env=dict(os.environ, LC_ALL='C'))
                self.assertIn(f'Candidate: {version}', policy)
                self.assertIn('9:99.0-1ubuntu1 -1', policy)

    def test_no_download_uses_canonical_local_archive_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in ('state/lists/partial', 'empty', 'var/cache/piano-install',
                         'var/cache/apt/archives/partial', 'stage/DEBIAN'):
                (d / name).mkdir(parents=True)
            (d / 'state/status').write_text('')
            (d / 'stage/DEBIAN/control').write_text(
                'Package: piano-apt-probe\nVersion: 1:1.0\nArchitecture: all\n'
                'Maintainer: Test <test@example.invalid>\nDescription: local archive probe\n')
            # Release filenames need not include the version epoch.
            deb = d / 'var/cache/piano-install/piano-apt-probe_1.0_all.deb'
            subprocess.run(['dpkg-deb', '--build', str(d / 'stage'), str(deb)],
                           check=True, stdout=subprocess.DEVNULL)
            fake = d / 'fake-dpkg'
            fake.write_text('#!/bin/sh\nprintf "%s\n" "$*" >>"' + str(d / 'calls') +
                            '"\nexit 0\n')
            fake.chmod(0o755)
            config = d / 'apt.conf'
            config.write_text('Dir::Etc::main "/dev/null";\nDir::Etc::parts "' +
                              str(d / 'empty') + '";\n')
            env = dict(os.environ, LC_ALL='C', APT_CONFIG=str(config),
                       DEBIAN_FRONTEND='noninteractive')
            command = ['apt-get', '-o', 'APT::Architecture=arm64',
                       '-o', 'Dir::Etc::sourcelist=/dev/null',
                       '-o', f'Dir::Etc::sourceparts={d}/empty',
                       '-o', f'Dir::State={d}/state',
                       '-o', f'Dir::State::status={d}/state/status',
                       '-o', f'Dir::Cache={d}/var/cache/apt',
                       '-o', f'Dir::Log={d}', '-o', f'Dir::Bin::dpkg={fake}',
                       '-o', 'Debug::NoLocking=1', '--yes', '--allow-downgrades',
                       '--no-download', '--no-install-recommends', 'install', str(deb)]
            negative = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(negative.returncode, 0)
            self.assertIn('Pathname to install is not absolute', negative.stderr)
            subprocess.run(['/bin/sh', '-ec', '. "$1"; cache_local_debs "$2"',
                            'cache', str(REPO / 'tools/rootfs-image-steps.sh'), str(d)], check=True)
            canonical = d / 'var/cache/apt/archives/piano-apt-probe_1%3a1.0_all.deb'
            self.assertEqual(canonical.read_bytes(), deb.read_bytes())
            positive = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(positive.returncode, 0, positive.stdout + positive.stderr)
            self.assertIn(str(canonical), (d / 'calls').read_text())

    def test_image_provider_removal_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / 'plan'
            script = REPO / 'tools/rootfs-image-steps.sh'
            def check(text, sensor_supplied):
                plan.write_text(text)
                return subprocess.run(['/bin/sh', '-c',
                    '. "$1"; check_userspace_removals "$2" "$3"',
                    'check', str(script), str(plan), str(int(sensor_supplied))]).returncode
            providers = 'Remv power-profiles-daemon [0.30-2]\nRemv iio-sensor-proxy [3.9]\n'
            self.assertEqual(check(providers, True), 0)
            self.assertNotEqual(check(providers, False), 0)
            self.assertEqual(check(providers.replace('iio-sensor-proxy ', 'iio-sensor-proxy:arm64 '), True), 0)
            self.assertNotEqual(check(providers + 'Remv gnome-shell [50]\n', True), 0)

    def test_real_apt_replacement_plan_and_removal_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'status').write_text(
                'Package: power-profiles-daemon\nStatus: install ok installed\n'
                'Priority: optional\nSection: admin\nInstalled-Size: 1\n'
                'Maintainer: Test <test@example.invalid>\nArchitecture: all\nVersion: 0.30-2\n'
                'Description: synthetic old provider\n\n'
                'Package: desktop-consumer\nStatus: install ok installed\n'
                'Priority: optional\nSection: admin\nInstalled-Size: 1\n'
                'Maintainer: Test <test@example.invalid>\nArchitecture: all\nVersion: 1\n'
                'Depends: power-profiles-daemon\nDescription: synthetic consumer\n')
            stage = d / 'stage/DEBIAN'
            stage.mkdir(parents=True)
            (stage / 'control').write_text(
                'Package: tuned-ppd\nVersion: 2.25.1-1ubuntu1\nArchitecture: all\n'
                'Maintainer: Test <test@example.invalid>\n'
                'Conflicts: power-profiles-daemon\nProvides: power-profiles-daemon\n'
                'Description: synthetic new provider\n')
            deb = d / 'tuned-ppd.deb'
            subprocess.run(['dpkg-deb', '--build', str(stage.parent), str(deb)], check=True,
                           stdout=subprocess.DEVNULL)
            for name in ('lists/partial', 'cache/archives/partial', 'empty'):
                (d / name).mkdir(parents=True)
            command = ['apt-get', '-o', 'Dir::Etc::sourcelist=/dev/null', '-o',
                       f'Dir::Etc::sourceparts={d / "empty"}', '-o',
                       f'Dir::State::status={d / "status"}', '-o', f'Dir::State::lists={d / "lists"}',
                       '-o', f'Dir::Cache={d / "cache"}', '-o', 'Debug::NoLocking=1',
                       '--simulate', '--no-download', '--no-install-recommends', 'install', str(deb)]
            plan = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL,
                                           env=dict(os.environ, LC_ALL='C'))
            self.assertEqual(prepare_apt.check_removals(plan, True), ['power-profiles-daemon'])
            with self.assertRaises(ValueError):
                prepare_apt.check_removals(plan, False)
            with self.assertRaises(ValueError):
                prepare_apt.check_removals(plan + '\nRemv desktop-consumer [1]\n', True)
            self.assertIn('Inst tuned-ppd', plan)


if __name__ == '__main__':
    unittest.main()
