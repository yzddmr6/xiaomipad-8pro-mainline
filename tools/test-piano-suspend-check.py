#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Small text/cleanup regressions. Never query a real bus, RTC, sysfs or device."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('suspend_check', Path(__file__).with_name('piano-suspend-check.py'))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ParsingAndCleanup(unittest.TestCase):
    def test_connected_charger_recovery(self):
        before = {'supplies': {
            'xiaomi-mca-usb': {'type': 'USB', 'online': '1', 'usb_type': 'PD'},
            'bq27z561-0': {'type': 'Battery', 'capacity': '65',
                           'status': 'Charging', 'current_now': '200000'},
            'bq27z561-1': {'type': 'Battery', 'capacity': '66',
                           'status': 'Charging', 'current_now': '150000'},
        }}
        def sample(online='1', current='200000', capacity='65', status='Charging'):
            return {'supplies': {
                'xiaomi-mca-usb': {'type': 'USB', 'online': online, 'usb_type': 'PD'},
                **{n: p | {'current_now': current, 'capacity': capacity, 'status': status}
                   for n, p in before['supplies'].items() if p['type'] == 'Battery'},
            }}
        self.assertTrue(all(check.charging_recovery_checks(before, sample()).values()))
        # Both readings are valid, but the source disappeared after sleep.
        self.assertFalse(all(check.charging_recovery_checks(before, sample(online='0')).values()))
        for current in ('-100000', '0', None, 'invalid'):
            self.assertFalse(all(check.charging_recovery_checks(before, sample(current=current)).values()))
        self.assertTrue(all(check.charging_recovery_checks(
            before, sample(current='0', capacity='100', status='Full')).values()))
        self.assertFalse(all(check.charging_recovery_checks(
            before, sample(online='0', current='0', capacity='100', status='Full')).values()))
        self.assertFalse(all(check.charging_recovery_checks(before, sample(capacity='invalid')).values()))
        self.assertFalse(all(check.charging_recovery_checks(before, {'supplies': {}}).values()))
        unplugged = sample(online='0')
        self.assertFalse(all(check.charging_recovery_checks(unplugged, sample()).values()))

    def test_suspend_stats_text(self):
        # Individual suspend_stats files are stripped sysfs text, not log lines.
        before = {'success': '4', 'fail': '1', 'failed_suspend': '1',
                  'failed_resume': '0', 'last_failed_dev': ''}
        after = before | {'success': '5'}
        self.assertTrue(check.suspend_delta(before, after)['pass'])
        for changed in ({'success': '4'}, {'success': '6'}, {'failed_resume': '1'},
                        {'fail': '2'}, {'last_failed_dev': 'i2c-controller'}, {'failed_new': '1'}):
            self.assertFalse(check.suspend_delta(before, after | changed)['pass'], changed)
        del after['failed_resume']
        self.assertFalse(check.suspend_delta(before, after)['pass'])

    def test_clocks_distinguish_actual_sleep(self):
        before = {'boottime_ns': 100_000_000_000, 'monotonic_ns': 90_000_000_000, 'uncertainty_ns': 0}
        # 3 awake seconds + 24 asleep seconds: exact 80% boundary for 30s RTC.
        after = before | {'boottime_ns': 127_000_000_000, 'monotonic_ns': 93_000_000_000}
        self.assertTrue(check.sleep_measurement(before, after, 30)['pass'])
        self.assertFalse(check.sleep_measurement(before, after | {'boottime_ns': 126_999_000_000}, 30)['pass'])
        aborted = before | {'boottime_ns': 103_000_000_000, 'monotonic_ns': 93_000_000_000}
        self.assertFalse(check.sleep_measurement(before, aborted, 30)['pass'])
        self.assertFalse(check.sleep_measurement(before, aborted, 180, physical=True)['pass'])
        # A real early physical wake must not be judged against the 180s fallback.
        self.assertTrue(check.sleep_measurement(before, after, 180, physical=True)['pass'])
        noisy = before | {'boottime_ns': 103_000_000_001, 'monotonic_ns': 93_000_000_000, 'uncertainty_ns': 2}
        self.assertFalse(check.sleep_measurement(before, noisy, 180, physical=True)['pass'])

    def test_irq_names_and_physical_fallback(self):
        interrupts = '''           CPU0 CPU1 CPU2
  44:         12    0    3   PMIC arb 2 Edge pm8xxx_rtc_alarm
  83:          0    1    0   gpio 95 Edge nanosic_wn8030_wakeup
  99:          2    0    0   PMIC arb 0 Edge pm8941_pwrkey
 120:          0    0    1   gpio 80 Edge hall_lid
'''
        rtc = check.wake_irq('44\n', interrupts)
        self.assertEqual(rtc['counts'], [12, 0, 3])
        self.assertEqual(check.physical_wake(rtc, 'keyboard'), 'NO_PHYSICAL_WAKE')
        for number, expected in ((83, 'keyboard'), (99, 'pwrkey'), (120, 'lid')):
            self.assertEqual(check.physical_wake(check.wake_irq(str(number), interrupts), expected), 'PASS')
        self.assertEqual(check.physical_wake(check.wake_irq('83', interrupts), 'pwrkey'), 'WRONG_WAKE_SOURCE')
        self.assertIsNone(check.wake_irq('0', interrupts)['name'])
        self.assertEqual(check.physical_wake(check.wake_irq(None, interrupts)), 'UNKNOWN_PHYSICAL_WAKE')

    def test_kernel_log_filter_keeps_original_records(self):
        messages = ['WARNING: CPU: 0 at bq27xxx_battery_poll', 'Call trace:',
                    'i2c: transfer timeout', 'ath12k_pci: failed to resume core: -110',
                    'arm-smmu: Unhandled context fault', 'qca: firmware error',
                    'qcom_iris: command failed', 'msm_dsi: error -22', 'ordinary hardware error']
        rows = [{'MESSAGE': text, 'PRIORITY': '3' if text == messages[-1] else '4', '__CURSOR': str(i)}
                for i, text in enumerate(messages)]
        good = [{'MESSAGE': 'ath12k: firmware booted', 'PRIORITY': '6'},
                {'MESSAGE': 'PM: suspend exit', 'PRIORITY': '6'}]
        self.assertEqual(check.kernel_errors(rows + good), rows)
        self.assertIs(check.kernel_errors(rows)[0], rows[0])
        text = '\n'.join(json.dumps(r) for r in rows + good)
        interval = check.journal_interval(text, '0')
        self.assertTrue(interval['coverage'])
        self.assertEqual(interval['errors'], rows[1:])  # Boundary belongs to the previous interval.
        self.assertFalse(check.journal_interval(text, 'missing-cursor')['coverage'])
        self.assertFalse(check.journal_interval('', '0')['coverage'])
        lost = {'MESSAGE': 'Missed 12 kernel messages', 'PRIORITY': '4'}
        self.assertFalse(check.journal_interval(text + '\n' + json.dumps(lost), '0')['coverage'])

    def test_i2c_negative_and_short_transfer(self):
        text = ''' worker-12 [003] .... 20.001: i2c_result: i2c-6 n=2 ret=2
 worker-12 [003] .... 20.002: i2c_result: i2c-6 n=2 ret=1
 worker-12 [003] .... 20.003: i2c_result: i2c-6 n=1 ret=-121
'''
        result = check.i2c_results(text)
        self.assertEqual(result['transfers'], 3)
        self.assertEqual([r['result'] for r in result['errors']], [1, -121])
        self.assertEqual(check.i2c_results(''), {'transfers': 0, 'errors': []})

    def test_proc_task_and_input_text(self):
        fields = ['D'] + ['0'] * 18 + ['12345'] + ['0'] * 5
        text = '37 (name with ) space) ' + ' '.join(fields)
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            path = proc / '17/task/37/stat'
            path.parent.mkdir(parents=True)
            path.write_text(text)
            self.assertEqual(check.d_tasks(proc), [{'tgid': 17, 'tid': 37, 'comm': 'name with ) space',
                                                   'state': 'D', 'starttime': 12345}])
            path.write_text(text.replace(') D ', ') S '))
            self.assertEqual(check.d_tasks(proc), [])
        inputs = 'N: Name="Piano THP Touchscreen"\nH: Handlers=mouse0 event2\n\nN: Name="Xiaomi Keyboard"\nH: Handlers=kbd event7\n'
        self.assertEqual([r['events'] for r in check.input_devices(inputs)], [['event2'], ['event7']])

    def test_effective_policy_and_owned_cleanup(self):
        text = '[Sleep]\nAllowSuspend=no\n\n# /run/override\n' + check.POLICY_TEXT
        self.assertEqual(check.effective_sleep_config(text)['AllowSuspend'], 'yes')
        self.assertEqual(check.effective_sleep_config(text + '[Sleep]\nSuspendState=mem\n')['SuspendState'], 'mem')
        with tempfile.TemporaryDirectory() as tmp:
            policy = check.TemporaryPolicy(Path(tmp) / 'sleep.conf.d/check.conf')
            policy.create()
            self.assertEqual(policy.path.read_text(), check.POLICY_TEXT)
            other = check.TemporaryPolicy(policy.path)
            with self.assertRaises(FileExistsError):
                other.create()
            other.remove()
            self.assertTrue(policy.path.exists())
            policy.remove()
            policy.remove()
            self.assertFalse(policy.path.exists())

    def test_signals_and_exception_remove_override_and_disarm_rtc(self):
        for sig in (None, signal.SIGINT, signal.SIGTERM):
            for in_flight in (False, True):
                with self.subTest(signal=sig, in_flight=in_flight), tempfile.TemporaryDirectory() as tmp:
                    obj = object.__new__(check.Check)
                    obj.policy = check.TemporaryPolicy(Path(tmp) / 'check.conf')
                    obj.rtc_owned, obj.trace, obj.sleep_in_flight = True, None, in_flight
                    obj.a = SimpleNamespace(rtc='rtc0')
                    obj.result = {'status': 'RUNNING', 'cleanup': []}
                    calls = []
                    obj.run = lambda *args, **kwargs: calls.append(args[0])
                    try:
                        with check.handled_signals(obj):
                            try:
                                obj.policy.create()
                                if sig:
                                    os.kill(os.getpid(), sig)
                                    self.assertTrue(in_flight)
                                    self.assertTrue(obj.policy.path.exists())
                                raise check.StopTest('fixture operation ended')
                            finally:
                                obj.cleanup()
                    except check.StopTest:
                        pass
                    self.assertFalse(obj.policy.path.exists())
                    self.assertEqual(calls, [['rtcwake', '-d', '/dev/rtc0', '-m', 'disable']])
                    self.assertTrue(all(r['status'] == 'PASS' for r in obj.result['cleanup']))

    def test_signal_during_exclusive_create_is_not_a_cleanup_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = check.TemporaryPolicy(Path(tmp) / 'check.conf')
            obj = SimpleNamespace(result={}, sleep_in_flight=False)
            fstat = os.fstat

            def interrupt(fd):
                os.kill(os.getpid(), signal.SIGTERM)
                return fstat(fd)

            with self.assertRaises(check.StopTest), check.handled_signals(obj):
                try:
                    with patch.object(check.os, 'fstat', interrupt):
                        policy.create()
                finally:
                    policy.remove()
            self.assertFalse(policy.path.exists())


if __name__ == '__main__':
    unittest.main()
