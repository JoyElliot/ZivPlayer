#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run opt-in, same-APK steady-playback legs over an already connected wireless ADB transport."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

PACKAGE = 'io.github.joyelliot.zivplayer'
RUNNER = PACKAGE + '.test/androidx.test.runner.AndroidJUnitRunner'
DEVICE_RESULTS = 'files/power-results/'
CASES = [('avc', 'automatic'), ('hevc', 'automatic'), ('hevc10', 'automatic'),
         ('vp9', 'automatic'), ('av1', 'automatic'), ('avc', 'software'),
         ('hevc', 'software'), ('vp9', 'software')]
SCREEN_TIMEOUT_MS = 900000


class PlaybackLegError(RuntimeError):
    """A recorded playback/quality failure; safe to diagnose while other cases run."""
    def __init__(self, label, report):
        self.label, self.report = label, report
        super().__init__(label + ': ' + str(report.get('failureMessage') or report.get('qualityIssues')))


def adb_command(serial, *parts, timeout=30):
    # These commands are reads or idempotent setup/cleanup. Instrumentation itself
    # is never retried here: a broken transport does not prove it stopped running.
    for attempt in range(3):
        try:
            return subprocess.check_output(['adb', '-s', serial] + list(parts), timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            try:
                subprocess.run(['adb', 'connect', serial], capture_output=True, timeout=15)
            except subprocess.TimeoutExpired:
                pass
            time.sleep(1)


def screen_state(serial):
    raw = adb_command(serial, 'shell', 'dumpsys', 'power').decode(errors='replace')
    policy = adb_command(serial, 'shell', 'dumpsys', 'window', 'policy').decode(errors='replace')
    wake = re.search(r'^\s*mWakefulness=(\S+)', raw, re.M)
    return {'wakefulness': wake.group(1) if wake else 'unavailable',
            'unlocked': bool(re.search(r'^\s*showing=false\s*$', policy, re.M)),
            'lastUserActivity': parse_user_activity(raw)}


def refresh_idle_screen(serial):
    before = screen_state(serial)
    if not before['unlocked'] or before['wakefulness'] != 'Awake':
        raise RuntimeError('The phone must be manually unlocked before foreground testing.')
    adb_command(serial, 'shell', 'input', 'keyevent', '3')
    deadline = time.monotonic() + 3
    while True:
        after = screen_state(serial)
        if after['unlocked'] and after['wakefulness'] == 'Awake' and after['lastUserActivity'] > before['lastUserActivity']:
            return {'before': before['lastUserActivity'], 'after': after['lastUserActivity']}
        if time.monotonic() >= deadline:
            raise RuntimeError('HOME did not refresh unlocked-screen user activity.')
        time.sleep(0.2)


def cooldown_wait(serial, seconds):
    while seconds > 0:
        interval = min(30, seconds)
        time.sleep(interval)
        seconds -= interval
        refresh_idle_screen(serial)

def parse_user_activity(raw):
    # Android versions expose this either globally or under Display Group User
    # Activity. Do not substitute the attention-service or no-change-lights timer.
    values = {int(value) for value in re.findall(
        r'^\s*(?:mLastUserActivityTime|lastUserActivityTime)=(\d+)\b', raw, re.M)}
    if len(values) != 1:
        raise RuntimeError('Cannot unambiguously verify the phone user-activity timer.')
    return values.pop()

def battery(serial):
    raw = adb_command(serial, 'shell', 'dumpsys', 'battery', timeout=20).decode().replace('\r', '')
    def number(name):
        match = re.search(r'^  ' + re.escape(name) + r': (-?\d+)$', raw, re.M)
        return int(match.group(1)) if match else None
    plugged = any(re.search(r'^  ' + kind + r' powered: true$', raw, re.M) for kind in ['AC', 'USB', 'Wireless', 'Dock'])
    return {'plugged': plugged, 'status': number('status'), 'level': number('level'),
            'temperatureTenthsC': number('temperature'), 'voltageMv': number('voltage')}

def run_leg(args, codec, mode, engine, label):
    prefix = ['adb', '-s', args.serial]
    def command(*parts):
        return adb_command(args.serial, *parts)
    report_path = args.output / (label + '.json')
    if report_path.exists():
        raise RuntimeError('Output already exists; choose a new prefix instead of overwriting: ' + label)
    screen_before = refresh_idle_screen(args.serial)
    command('shell', 'am', 'force-stop', PACKAGE)
    command('shell', 'settings', 'put', 'system', 'screen_brightness_mode', '0')
    command('shell', 'cmd', 'display', 'set-brightness', '0')
    if command('shell', 'settings', 'get', 'global', 'bluetooth_on').strip() != b'0':
        command('shell', 'cmd', 'bluetooth_manager', 'disable')
        for _ in range(10):
            if command('shell', 'settings', 'get', 'global', 'bluetooth_on').strip() == b'0':
                break
            time.sleep(1)
        else:
            raise RuntimeError('Bluetooth did not turn off before the controlled leg.')
    if subprocess.run(prefix + ['shell', 'pidof', PACKAGE], capture_output=True, timeout=20).stdout.strip():
        raise RuntimeError('The previous application process is still alive.')
    if subprocess.run(prefix + ['shell', 'run-as', PACKAGE, 'test', '-e', DEVICE_RESULTS + label + '.json'], timeout=20).returncode == 0:
        raise RuntimeError('The device already has this run label; choose a new prefix.')
    before = battery(args.serial)
    if not args.allow_external_power and (before['plugged'] or before['status'] != 3):
        raise RuntimeError('Physically unplug external supply before power measurements.')
    if before['level'] is None or before['level'] < 28:
        raise RuntimeError('Battery is too low to start another controlled leg.')
    if not args.smoke:
        deadline = time.monotonic() + args.cooldown_limit
        cooldown_wait(args.serial, args.minimum_cooldown)
        while True:
            before = battery(args.serial)
            if before['plugged'] or before['status'] != 3:
                raise RuntimeError('External supply appeared during cooldown.')
            if before['temperatureTenthsC'] is not None and before['temperatureTenthsC'] <= args.max_start_temperature * 10:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Temperature did not return to the configured starting range.')
            print(f'Cooling: {before}', flush=True)
            cooldown_wait(args.serial, 30)
    print(f'Start {label}: {before}', flush=True)
    screen_before = refresh_idle_screen(args.serial)
    command('shell', 'cmd', 'display', 'set-brightness', '0')
    warmup = args.warmup if not args.smoke else 8
    duration = args.duration if not args.smoke else 15
    cmd = prefix + ['shell', 'am', 'instrument', '-w', '-r',
        '-e', 'powerBenchmark', 'true', '-e', 'engine', engine,
        '-e', 'fixture', codec, '-e', 'decoderMode', mode,
        '-e', 'surfaceProtocol', args.surface_protocol,
        '-e', 'measurementCohort', args.cohort,
        '-e', 'sleepOnFailure', 'false',
        '-e', 'validationOnly', str(args.smoke).lower(),
        '-e', 'allowExternalPower', str(args.allow_external_power).lower(),
        '-e', 'runLabel', label, '-e', 'warmupSeconds', str(warmup), '-e', 'measureSeconds', str(duration),
        '-e', 'class', PACKAGE + '.PowerPlaybackBenchmarkTest', RUNNER]
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # Allow result recovery even if the local ADB client survived until watchdog.
    result_deadline = time.monotonic() + warmup + duration + 150 + 120
    process = None
    watchdog = None
    saw_instrumentation_end = False
    try:
        with (args.output / (label + '.log')).open('w', encoding='utf-8') as log:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            watchdog = threading.Timer(warmup + duration + 150, process.kill)
            watchdog.daemon = True
            watchdog.start()
            for line in process.stdout:
                log.write(line)
                log.flush()
                if 'INSTRUMENTATION_CODE:' in line:
                    saw_instrumentation_end = True
                if label + ':' in line or 'OK (' in line or 'FAILURES' in line or 'Process crashed' in line:
                    print(line.rstrip(), flush=True)
            exit_code = process.wait()
        # If the ADB stream broke, the on-device test may still be sampling. Wait
        # for its final JSON before any HOME/stop command. A recovered trace is
        # retained but excluded because transport recovery added measurement work.
        transport_interrupted = not saw_instrumentation_end
        while transport_interrupted and time.monotonic() < result_deadline:
            try:
                payload = command('exec-out', 'run-as', PACKAGE, 'cat', DEVICE_RESULTS + label + '.json')
                candidate = json.loads(payload.decode('utf-8-sig'))
                if 'completed' in candidate:
                    break
            except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
            time.sleep(min(5, max(0, result_deadline - time.monotonic())))
        for suffix in ['json', 'jsonl']:
            try:
                (args.output / (label + '.' + suffix)).write_bytes(command('exec-out', 'run-as', PACKAGE,
                    'cat', DEVICE_RESULTS + label + '.' + suffix))
            except subprocess.CalledProcessError:
                if suffix == 'json':
                    raise RuntimeError('The device did not publish its result: ' + label)
        raw_report = report_path.read_text(encoding='utf-8-sig')
        try:
            report = json.loads(raw_report)
        except ValueError:
            (args.output / (label + '.device-result.txt')).write_text(raw_report, encoding='utf-8')
            (args.output / (label + '.exit-info.txt')).write_bytes(command('shell', 'dumpsys', 'activity', 'exit-info', PACKAGE))
            report = {'schemaVersion': 1, 'runLabel': label, 'requestedEngine': engine,
                      'requestedDecoderMode': mode, 'fixture': codec + '-1080p24.mkv',
                      'completed': False, 'measurementQualified': False,
                      'failureType': 'DeviceResultUnavailable',
                      'failureMessage': 'Instrumentation exited without a valid result; see instrumentation log and package exit-info.'}
        report['host'] = {'startedUtc': started, 'finishedUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          'exitCode': exit_code, 'smokeOnly': args.smoke, 'preflightBattery': before,
                          'wirelessAdb': True, 'freshProcessVerified': True, 'cooldownLimitSeconds': args.cooldown_limit,
                          'screenActivityBeforeLeg': screen_before, 'temporaryScreenTimeoutMs': SCREEN_TIMEOUT_MS,
                          'transportInterruptedDuringLeg': transport_interrupted,
                          'comparisonBlock': f'{args.prefix}-{codec}-{mode}',
                          'plannedOrdinal': int(label.rsplit('-', 2)[1]),
                          'plannedOrder': (['exo', 'libmpv', 'libmpv', 'exo'] if args.block == 1 else ['libmpv', 'exo', 'exo', 'libmpv'])
                              if not args.smoke and not args.engine else [],
                          'blockNumber': args.block}
        if transport_interrupted:
            report.setdefault('qualityIssues', []).append('host_transport_interrupted_during_leg')
            report['measurementQualified'] = False
            report['playbackValidationPassed'] = False
        if report.get('controlProtocolVersion', 0) >= 2 and report.get('cleanupSucceeded') is not True:
            report.setdefault('qualityIssues', []).append('device_cleanup_not_verified')
            report['measurementQualified'] = False
            report['playbackValidationPassed'] = False
        report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        report['host']['screenActivityAfterLeg'] = refresh_idle_screen(args.serial)
        report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    finally:
        import sys
        primary_failure = sys.exception()
        if watchdog:
            watchdog.cancel()
        if process and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired) as process_cleanup_error:
                (args.output / (label + '.process-cleanup-error.txt')).write_text(str(process_cleanup_error), encoding='utf-8')
                if primary_failure is None:
                    primary_failure = process_cleanup_error
        try:
            command('shell', 'am', 'force-stop', PACKAGE)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as cleanup_error:
            (args.output / (label + '.cleanup-error.txt')).write_text(str(cleanup_error), encoding='utf-8')
            # Preserve the original failure when unwinding; the outer cleanup
            # still retries stopping the application and verifies the screen.
            if primary_failure is None:
                raise
        if primary_failure is not None and sys.exception() is None:
            raise primary_failure
    passed = report.get('playbackValidationPassed') if args.smoke else report.get('measurementQualified')
    if exit_code != 0 or not report.get('completed') or not passed:
        raise PlaybackLegError(label, report)
    print(f"Done {label}: decoder={report['diagnosticsStart'].get('decoder')}, qualified={report['measurementQualified']}", flush=True)
    return report

def finish_device(args, restore_timeout, should_sleep):
    commands = [['shell', 'am', 'force-stop', PACKAGE]]
    if restore_timeout and restore_timeout.isdigit():
        commands.append(['shell', 'settings', 'put', 'system', 'screen_off_timeout', restore_timeout])
    if should_sleep:
        commands.append(['shell', 'input', 'keyevent', '223'])
    records = []
    if not should_sleep:
        try:
            records.append({'screenActivityBeforeRestore': refresh_idle_screen(args.serial), 'success': True})
        except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            records.append({'screenActivityBeforeRestore': str(error), 'success': False})
    for parts in commands:
        for attempt in range(2):
            try:
                result = subprocess.run(['adb', '-s', args.serial] + parts, capture_output=True, text=True, timeout=25)
                if result.returncode == 0:
                    records.append({'command': parts, 'success': True})
                    break
                failure = result.stderr.strip() or result.stdout.strip()
            except subprocess.TimeoutExpired:
                failure = 'ADB command timeout'
            if attempt == 0:
                try:
                    subprocess.run(['adb', 'connect', args.serial], capture_output=True, timeout=20)
                except subprocess.TimeoutExpired:
                    pass
            else:
                records.append({'command': parts, 'success': False, 'error': failure})
    if restore_timeout and restore_timeout.isdigit():
        try:
            restored = adb_command(args.serial, 'shell', 'settings', 'get', 'system', 'screen_off_timeout').decode().strip()
            records.append({'restoredScreenTimeout': restored, 'success': restored == restore_timeout})
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            records.append({'restoredScreenTimeout': str(error), 'success': False})
    if should_sleep:
        try:
            deadline = time.monotonic() + 8
            while True:
                raw = adb_command(args.serial, 'shell', 'dumpsys', 'power', timeout=20).decode(errors='replace')
                state = re.search(r'^\s*mWakefulness=(\S+)', raw, re.M)
                wakefulness = state.group(1) if state else 'unavailable'
                if wakefulness in {'Dozing', 'Asleep'} or time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
            records.append({'wakefulnessAfterCleanup': wakefulness})
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            records.append({'wakefulnessAfterCleanup': 'unavailable'})
    return records

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--serial', required=True)
    p.add_argument('--output', type=Path, default=Path('native/out/power-comparison-20260910'))
    p.add_argument('--prefix', required=True)
    p.add_argument('--cohort', default='matched-persistent-v1', help='Keep restarted protocols separate in summaries')
    p.add_argument('--sleep-after', action='store_true', help='Sleep after successful completion; failures always request sleep')
    p.add_argument('--continue-on-leg-failure', action='store_true', help='Record playback/quality failures and test remaining cases; never accept invalid power data')
    p.add_argument('--leave-awake-on-failure', action='store_true', help='For supervised diagnosis: restore idle timeout without deliberately locking the phone')
    p.add_argument('--guard-only', action='store_true', help='Validate idle display control without starting playback')
    p.add_argument('--screen-guard-seconds', type=int, default=180, help='Guard-only validation duration')
    p.add_argument('--case', action='append', help='fixture:automatic or fixture:software; repeat to narrow the matrix')
    p.add_argument('--engine', choices=['exo', 'libmpv'], action='append', help='Limit smoke/individual legs; default both')
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--allow-external-power', action='store_true', help='Functional smoke validation only; never accepts power results')
    p.add_argument('--surface-protocol', choices=['matched-1080p', 'current-app'], default='matched-1080p')
    p.add_argument('--block', type=int, default=1, choices=[1, 2])
    p.add_argument('--warmup', type=int, default=90)
    p.add_argument('--duration', type=int, default=240)
    p.add_argument('--minimum-cooldown', type=float, default=45)
    p.add_argument('--max-start-temperature', type=float, default=34)
    p.add_argument('--cooldown-limit', type=float, default=900)
    args = p.parse_args()
    if args.allow_external_power and not args.smoke:
        p.error('External power is permitted only with --smoke; no power measurement can use it.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', args.prefix):
        p.error('prefix must be a short filename-safe label')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', args.cohort):
        p.error('cohort must be a short filename-safe label')
    if not (5 <= args.warmup <= 110 and 10 <= args.duration <= 240 and args.warmup + args.duration <= 350):
        p.error('Warmup and steady interval must fit within the 360-second fixture.')
    if args.screen_guard_seconds < 0 or args.screen_guard_seconds > 900:
        p.error('Screen guard validation must be between 0 and 900 seconds.')
    args.output.mkdir(parents=True, exist_ok=True)
    selected = [tuple(item.split(':', 1)) for item in args.case] if args.case else CASES
    if any(item not in CASES + [('hevc10', 'software')] for item in selected):
        p.error('Unknown fixture/decoder case')
    completed = []
    failed = []
    exit_record = {'cohort': args.cohort, 'prefix': args.prefix, 'completed': False}
    restore_timeout = None
    windows_power = None
    try:
        if os.name == 'nt':
            import ctypes
            windows_power = ctypes.windll.kernel32.SetThreadExecutionState
            windows_power.argtypes = [ctypes.c_uint]
            windows_power.restype = ctypes.c_uint
            if not windows_power(0x80000001):  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED, not DISPLAY_REQUIRED.
                raise RuntimeError('Unable to keep the measurement host running while unattended.')
            exit_record['hostSystemSleepPrevented'] = True
        restore_timeout = adb_command(args.serial, 'shell', 'settings', 'get', 'system', 'screen_off_timeout', timeout=20).decode().strip()
        exit_record['screenUserActivityVerified'] = refresh_idle_screen(args.serial)
        adb_command(args.serial, 'shell', 'am', 'force-stop', PACKAGE)
        # This must outlast a full playback leg plus watchdog/transfer overhead.
        # Otherwise clearing KEEP_SCREEN_ON can immediately expire the idle timer.
        adb_command(args.serial, 'shell', 'settings', 'put', 'system', 'screen_off_timeout', str(SCREEN_TIMEOUT_MS))
        exit_record['temporaryScreenTimeoutMs'] = SCREEN_TIMEOUT_MS
        if args.guard_only:
            cooldown_wait(args.serial, args.screen_guard_seconds)
            exit_record['guardValidatedSeconds'] = args.screen_guard_seconds
        for codec, mode in ([] if args.guard_only else selected):
            if args.smoke or args.engine:
                engines = args.engine or ['exo', 'libmpv']
            else:
                engines = ['exo', 'libmpv', 'libmpv', 'exo'] if args.block == 1 else ['libmpv', 'exo', 'exo', 'libmpv']
            for ordinal, engine in enumerate(engines, 1):
                label = f'{args.prefix}-{codec}-{mode}-{ordinal}-{engine}'
                try:
                    completed.append(run_leg(args, codec, mode, engine, label))
                except PlaybackLegError as error:
                    failed.append({'label': error.label, 'failureMessage': str(error),
                                   'stage': error.report.get('stage'), 'qualityIssues': error.report.get('qualityIssues')})
                    (args.output / (args.prefix + '-failures.json')).write_text(json.dumps(failed, indent=2) + '\n', encoding='utf-8')
                    print('Recorded invalid leg; excluded from power comparison: ' + str(error), flush=True)
                    if not args.continue_on_leg_failure:
                        raise
                    refresh_idle_screen(args.serial)
        exit_record['matrixFinished'] = True
        if failed:
            raise RuntimeError(f'Matrix finished with {len(failed)} invalid legs; inspect saved evidence.')
        exit_record['completed'] = True
    except BaseException as error:
        exit_record.update(failureType=type(error).__name__, failureMessage=str(error))
        raise
    finally:
        exit_record.update(completedLegs=len(completed), failedLegs=failed, finishedUtc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        try:
            should_sleep = args.sleep_after or (not exit_record['completed'] and not args.leave_awake_on_failure)
            exit_record['deviceCleanup'] = finish_device(args, restore_timeout, should_sleep)
            cleanup = exit_record['deviceCleanup']
            cleanup_passed = all(item.get('success', True) for item in cleanup)
            if should_sleep:
                cleanup_passed = cleanup_passed and any(
                    item.get('wakefulnessAfterCleanup') in {'Dozing', 'Asleep'} for item in cleanup)
            exit_record['deviceCleanupSucceeded'] = cleanup_passed
            if not cleanup_passed:
                exit_record['completed'] = False
                if 'failureType' not in exit_record:
                    exit_record.update(failureType='DeviceCleanupFailed',
                        failureMessage='Playback ended, but device stop/restore/sleep was not fully verified.')
        finally:
            if windows_power:
                windows_power(0x80000000)
        (args.output / (args.prefix + '-exit.json')).write_text(json.dumps(exit_record, indent=2) + '\n', encoding='utf-8')
    if not exit_record['completed']:
        raise RuntimeError(exit_record['failureMessage'])
    print(f'Completed {len(completed)} legs. Smoke-only={args.smoke}', flush=True)

if __name__ == '__main__':
    main()
