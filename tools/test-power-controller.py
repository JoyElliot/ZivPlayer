#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regression checks for the unattended device controller; never uses ADB."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('power_controller', Path(__file__).with_name('run-power-benchmark.py'))
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class ScreenGuardTests(unittest.TestCase):
    def test_activity_uses_display_timer_not_attention_or_no_change_lights(self):
        raw = ''' mLastUserActivityTime(excludingAttention)=500
  Display Group User Activity:
    displayGroupId=0
    lastUserActivityTime=469787390 (229103 ms ago)
    lastUserActivityTimeNoChangeLights=999999999
'''
        self.assertEqual(469787390, controller.parse_user_activity(raw))
        self.assertEqual(123, controller.parse_user_activity('  mLastUserActivityTime=123 (1 ms ago)'))

    def test_missing_or_ambiguous_activity_is_rejected(self):
        for raw in ['mLastUserActivityTime(excludingAttention)=100',
                    'lastUserActivityTimeNoChangeLights=100',
                    'lastUserActivityTime=100\nlastUserActivityTime=200']:
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                controller.parse_user_activity(raw)

    def simulate(self, *, unlocked=True, activity_after=200, cleanup_ok=True, leg_error=None, extra_args=()):
        commands = []
        home_count = 0

        def read(command, **kwargs):
            nonlocal home_count
            commands.append(command)
            if command[-4:] == ['settings', 'get', 'system', 'screen_off_timeout']:
                return b'600000\n'
            if command[-3:] == ['dumpsys', 'window', 'policy']:
                return ('  showing=' + str(not unlocked).lower() + '\n').encode()
            if command[-2:] == ['dumpsys', 'power']:
                return f'  mWakefulness=Awake\n  lastUserActivityTime={100 + home_count * (activity_after - 100)}\n'.encode()
            if command[-3:] == ['input', 'keyevent', '3']:
                home_count += 1
                return b''
            if 'force-stop' in command or ('settings' in command and 'put' in command):
                return b''
            raise AssertionError('Unexpected device read: ' + repr(command))

        def run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0)

        cleanup = [{'command': ['am', 'force-stop'], 'success': cleanup_ok},
                   {'wakefulnessAfterCleanup': 'Dozing' if cleanup_ok else 'Awake'}]
        with tempfile.TemporaryDirectory() as temporary:
            arguments = ['run-power-benchmark.py', '--serial', 'offline-device', '--output', temporary,
                         '--prefix', 'guard-test', '--smoke', '--case', 'avc:automatic',
                         '--engine', 'exo', '--sleep-after']
            arguments.extend(extra_args)
            with (patch('sys.argv', arguments), patch.object(controller, 'os', SimpleNamespace(name='test')),
                    patch.object(controller.subprocess, 'check_output', side_effect=read),
                    patch.object(controller.subprocess, 'run', side_effect=run),
                    patch.object(controller, 'finish_device', return_value=cleanup) as finish,
                    patch.object(controller, 'run_leg', return_value={}, side_effect=leg_error) as leg,
                    contextlib.redirect_stdout(io.StringIO())):
                error = None
                try:
                    controller.main()
                except RuntimeError as failure:
                    error = failure
                record = json.loads((Path(temporary) / 'guard-test-exit.json').read_text())
                return commands, record, error, leg.call_count, finish.call_args.args

    def test_locked_phone_never_starts_playback_or_shortens_timeout(self):
        commands, record, error, legs, cleanup_args = self.simulate(unlocked=False)
        self.assertIsNotNone(error)
        self.assertEqual(0, legs)
        self.assertFalse(record['completed'])
        self.assertTrue(cleanup_args[-1])
        self.assertFalse(any(str(controller.SCREEN_TIMEOUT_MS) in command for command in commands))

    def test_unconfirmed_activity_never_shortens_timeout(self):
        commands, record, error, legs, _ = self.simulate(activity_after=100)
        self.assertIsNotNone(error)
        self.assertEqual(0, legs)
        self.assertFalse(any(str(controller.SCREEN_TIMEOUT_MS) in command for command in commands))

    def test_verified_activity_and_cleanup_allow_completion(self):
        commands, record, error, legs, _ = self.simulate()
        self.assertIsNone(error)
        self.assertEqual(1, legs)
        self.assertTrue(record['completed'])
        self.assertTrue(record['deviceCleanupSucceeded'])
        self.assertTrue(any(command[-3:] == ['input', 'keyevent', '3'] for command in commands))
        self.assertTrue(any(str(controller.SCREEN_TIMEOUT_MS) in command for command in commands))

    def test_cleanup_failure_does_not_report_completed(self):
        _, record, error, legs, _ = self.simulate(cleanup_ok=False)
        self.assertEqual(1, legs)
        self.assertIsNotNone(error)
        self.assertFalse(record['completed'])
        self.assertEqual('DeviceCleanupFailed', record['failureType'])

    def test_leg_failure_is_preserved_and_requests_sleep(self):
        _, record, error, legs, cleanup_args = self.simulate(leg_error=RuntimeError('fixture missing'))
        self.assertEqual('fixture missing', str(error))
        self.assertFalse(record['completed'])
        self.assertEqual(0, record['completedLegs'])
        self.assertTrue(cleanup_args[-1])

    def test_recorded_invalid_leg_does_not_abort_remaining_cases(self):
        failure = controller.PlaybackLegError('failed-exo', {'stage': 'prepare_media', 'failureMessage': 'decode failed'})
        _, record, error, legs, _ = self.simulate(leg_error=[failure, {}],
            extra_args=['--continue-on-leg-failure', '--engine', 'libmpv'])
        self.assertEqual(2, legs)
        self.assertEqual(1, record['completedLegs'])
        self.assertEqual(1, len(record['failedLegs']))
        self.assertTrue(record['matrixFinished'])
        self.assertFalse(record['completed'])
        self.assertIn('1 invalid legs', str(error))

    def test_idle_timeout_covers_full_leg_watchdog_and_transfer(self):
        maximum_warmup_and_playback = 350
        watchdog_margin = 150
        result_transfer_margin = 120
        self.assertGreater(controller.SCREEN_TIMEOUT_MS,
            (maximum_warmup_and_playback + watchdog_margin + result_transfer_margin) * 1000)

    def test_idempotent_adb_read_recovers_after_disconnect(self):
        failure = controller.subprocess.CalledProcessError(1, ['adb'], stderr=b'device offline')
        with (patch.object(controller.subprocess, 'check_output', side_effect=[failure, b'recovered']) as read,
              patch.object(controller.subprocess, 'run') as reconnect,
              patch.object(controller.time, 'sleep')):
            self.assertEqual(b'recovered', controller.adb_command('offline-test', 'shell', 'dumpsys', 'power'))
            self.assertEqual(2, read.call_count)
            self.assertEqual(['adb', 'connect', 'offline-test'], reconnect.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
