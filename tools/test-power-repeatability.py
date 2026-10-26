"""Offline boundaries for the fresh repeatability and battery-extra session."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
HOST_BASH = Path('/bin/bash') if os.name == 'posix' else Path('D:/Program Files/Git/bin/bash.exe')
spec=importlib.util.spec_from_file_location('session',ROOT/'tools/power-repeatability-session.py')
session=importlib.util.module_from_spec(spec);spec.loader.exec_module(session)


class RepeatabilityTests(unittest.TestCase):
    def test_two_independently_position_balanced_halves(self):
        rows=session.build_rows('pilot','repeatability')
        self.assertEqual(18,len(rows))
        self.assertEqual(18,len({r['label'] for r in rows}))
        for half in [rows[:9],rows[9:]]:
            for engine in session.ENGINES:
                self.assertEqual([1,2,3],sorted(r['plannedOrdinal'] for r in half if r['engine']==engine))

    def test_software_target_and_extras_never_share_blocks(self):
        rows=session.build_rows('software','software')
        self.assertEqual(91,len(rows))
        self.assertEqual(3,sum(r.get('validationOnly',False) for r in rows))
        self.assertEqual(24,sum(r['purpose']=='optional-software' for r in rows))
        self.assertEqual(64,sum(r['purpose']=='battery-extra' for r in rows))
        blocks={}
        for r in rows:
            if not r.get('validationOnly'):blocks.setdefault(r['comparisonBlock'],[]).append(r)
        self.assertEqual(22,len(blocks))
        for block in blocks.values():
            self.assertEqual([1,2,3,4],[r['plannedOrdinal'] for r in block])
            order=[r['engine'] for r in block]
            self.assertEqual(order,order[::-1]);self.assertEqual(2,len(set(order)))
            self.assertEqual(1,len({r['purpose'] for r in block}))
        self.assertFalse(any(r['engine']=='vlc' and r['codec']!='av1' for r in rows))

    def actual_extra_gate(self,level,finished=24,qualified=True,levels=None):
        bash=HOST_BASH
        if not bash.exists():self.skipTest('Git Bash not available')
        source=(ROOT/'tools/phone-power-runner.sh').read_text()
        start='    if [ "$PURPOSE" = battery-extra ]; then'
        gate=start+source.split(start,1)[1].split('    if [ "$HARD_STOP_UPTIME" -gt 0 ]',1)[0]
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);(base/'decisions').mkdir()
            script='\n'.join(['BASE='+repr(base.as_posix()),'EXTRA_DECISION=undecided','PURPOSE=battery-extra',
                f'TARGET_SOFTWARE_FINISHED={finished}','REQUIRED_TARGET_SOFTWARE_ROWS=24',
                'TARGET_SOFTWARE_OK='+str(qualified).lower(),'processed_rows=0',
                'read_battery() { :; }','fail() { exit 99; }','status() { echo SKIP; }',
                'for LEVEL in '+' '.join(map(str,levels or [level]))+'; do',gate,'echo RUN','done'])
            result=subprocess.check_output([str(bash),'-c',script],text=True).strip().splitlines()
            return result,json.loads((base/'decisions/battery-extra-entry.json').read_text())

    def test_extra_entry_is_strictly_above_30_and_requires_successful_full_target(self):
        self.assertEqual(['SKIP'],self.actual_extra_gate(30)[0])
        self.assertEqual(['RUN'],self.actual_extra_gate(31)[0])
        self.assertEqual(['SKIP'],self.actual_extra_gate(90,finished=23)[0])
        self.assertEqual(['SKIP'],self.actual_extra_gate(90,qualified=False)[0])

    def test_extra_entry_latches_once_not_a_second_30_percent_stop(self):
        result,decision=self.actual_extra_gate(35,levels=[35,20])
        self.assertEqual(['RUN','RUN'],result)
        self.assertEqual(35,decision['batteryPercent'])

    def test_sd_uses_six_run_means_and_drift_requires_review(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);folder=out/'pilot';folder.mkdir()
            legs=session.build_rows('pilot','repeatability')
            plan={'legs':legs,'repeatabilityRule':{'cvReviewPercent':5,'firstLastHalfDriftReviewPercent':5}}
            session.write(folder/'run-plan.json',plan)
            session.write(out/'session-state.json',{'phase':'running_repeatability','stages':{'repeatability':{'plan':str(folder/'run-plan.json')}}})
            values=[1000,1010,1020,1030,1040,1050]
            indices={e:0 for e in session.ENGINES};rows=[]
            for leg in legs:
                i=indices[leg['engine']];indices[leg['engine']]+=1
                rows.append({'runLabel':leg['label'],'engine':leg['engine'],'qualified':True,'meanPowerMw':values[i],
                             'temperatureMinC':33,'temperatureMaxC':34,'batteryStartPercent':90-i,'batteryEndPercent':89-i})
            audit={'runs':rows,'exit':{'stopReason':'manifest_completed','cleanupSucceeded':True}}
            session.write(folder/'audit.json',audit)
            result=session.review_repeatability(out)
            self.assertTrue(result['continuationAllowed'])
            self.assertAlmostEqual(statistics.stdev(values),result['engines']['exo']['sampleStdevMw'])
            self.assertEqual(6,result['engines']['exo']['n'])
            # A later stage cannot be unlocked for another launch by rerunning the review.
            state=session.read(out/'session-state.json');state['phase']='running_software';session.write(out/'session-state.json',state)
            with self.assertRaises(AssertionError):session.review_repeatability(out)

    def test_duplicate_launch_is_rejected_before_any_device_request(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            session.write(out/'session-state.json',{'phase':'running_software','serial':'test'})
            session.write(out/'device-baseline.json',{'serial':'test'})
            with mock.patch.object(session.subprocess,'check_output') as call:
                with self.assertRaises(AssertionError):session.launch(out,'software','test')
                call.assert_not_called()

    def test_rejected_main_launch_cannot_recover_another_launch_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            state={'phase':'launching_software','serial':'test','launchAttemptId':'other-owner'}
            session.write(out/'session-state.json',state)
            session.write(out/'device-baseline.json',{'serial':'test'})
            args=['session','launch-software','--output',str(out),'--serial','test']
            with mock.patch('sys.argv',args), mock.patch.object(session.subprocess,'check_output') as call:
                with self.assertRaises(AssertionError):session.main()
                call.assert_not_called()
            self.assertEqual(state,session.read(out/'session-state.json'))

    def test_concurrent_main_launch_lock_rejects_without_calling_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'launch.lock').mkdir()
            args=['session','launch-software','--output',str(out),'--serial','test']
            with mock.patch('sys.argv',args), mock.patch.object(session,'launch') as call:
                with self.assertRaises(FileExistsError):session.main()
                call.assert_not_called()

    def test_failed_launch_does_not_signal_foreign_or_unowned_remote_guard(self):
        for owner in (None,'my-guard'):
            with self.subTest(owner=owner), tempfile.TemporaryDirectory() as directory:
                out=Path(directory)
                def fail_launch(out,stage,serial,attempt):
                    state={'phase':'launching_repeatability_guard','launchAttemptId':attempt,'remoteParent':'/test'}
                    if owner:state['guardOwnerId']=owner
                    session.write(out/'session-state.json',state)
                    raise RuntimeError('remote conflict')
                args=['session','launch-repeatability','--output',str(out),'--serial','test']
                with mock.patch('sys.argv',args), mock.patch.object(session,'launch',side_effect=fail_launch), \
                     mock.patch.object(session.subprocess,'check_output',return_value=b'foreign-guard\n') as read, \
                     mock.patch.object(session.subprocess,'run') as signal:
                    with self.assertRaises(RuntimeError):session.main()
                    signal.assert_not_called()
                    self.assertEqual(1 if owner else 0,read.call_count)

    def test_intermediate_failure_keeps_original_recovery_and_only_normal_end_holds(self):
        bash=HOST_BASH
        if not bash.exists():self.skipTest('Git Bash not available')
        source=(ROOT/'tools/phone-power-runner.sh').read_text()
        start='    if [ "${HOLD_FOR_NEXT_STAGE:-false}" = true ]'
        code=start+source.split(start,1)[1].split('    if [ "$LOW_TEST_INSTALLED"',1)[0]
        for reason,level,expected in [('manifest_completed',50,'86400000 0 false'),('battery_below_10_after_leg',9,'600000 2 true'),('main_apk_changed',90,'600000 2 true')]:
            script='\n'.join(['HOLD_FOR_NEXT_STAGE=true','STOP_REASON='+reason,'exit_code=0','CLEANUP_OK=true',
                'ORIGINAL_TIMEOUT=600000','ORIGINAL_BT=2','SLEEP_AT_END=true','SCREEN_TIMEOUT_MS=86400000',
                'RESTORE_TEST_APK=original.apk',f'LEVEL={level}','read_battery() { :; }',code,
                'echo "$ORIGINAL_TIMEOUT $ORIGINAL_BT $SLEEP_AT_END"'])
            result=subprocess.check_output([str(bash),'-c',script],text=True).strip()
            self.assertEqual(expected,result)

    def test_guard_can_be_woken_for_immediate_recovery_without_waiting_deadline(self):
        bash=HOST_BASH
        if not bash.exists():self.skipTest('Git Bash not available')
        source=(ROOT/'tools/power-night-deadline-guard.sh').read_text()
        code='stat_start_id() {'+source.split('stat_start_id() {',1)[1].split('\ncase "$HARD_STOP_EPOCH',1)[0]
        script='\n'.join(['RECOVERY_TRIGGER=deadline','guard_sleep_pid=','guard_sleep_stat=',code,
                          '(sleep 0.15; kill -USR1 $$) &','wait_until_recovery 20','echo "$RECOVERY_TRIGGER"'])
        result=subprocess.check_output([str(bash),'-c',script],text=True,timeout=5).strip()
        self.assertEqual('host_requested',result)

    def test_guard_signal_immediately_before_wait_stops_the_owned_sleeper(self):
        bash=HOST_BASH
        source=(ROOT/'tools/power-night-deadline-guard.sh').read_text()
        code='stat_start_id() {'+source.split('stat_start_id() {',1)[1].split('\ncase "$HARD_STOP_EPOCH',1)[0]
        script='\n'.join(['RECOVERY_TRIGGER=deadline','guard_sleep_pid=','guard_sleep_stat=',code,
            'wait() { kill -USR1 $$; builtin wait "$@"; }','wait_until_recovery 20','echo "$RECOVERY_TRIGGER"'])
        result=subprocess.check_output([str(bash),'-c',script],text=True,timeout=5).strip()
        self.assertEqual('host_requested',result)

    def test_full_finish_late_battery_drop_or_restore_failure_falls_back_and_sleeps(self):
        bash=HOST_BASH
        source=(ROOT/'tools/phone-power-runner.sh').read_text()
        code='restore_session_settings() {'+source.split('restore_session_settings() {',1)[1].split('\ncase "$HARD_STOP_EPOCH',1)[0]
        for failure in ('none','battery','setting'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                base=Path(directory);(base/'active.lock').mkdir()
                script='\n'.join(['set -u','BASE='+repr(base.as_posix()),'STARTED=1','CURRENT_LABEL=','AM_PID=',
                    'NETWORK_PID=','NETWORK_START_ID=','PACKAGE=test','STOP_REASON=manifest_completed','CLEANUP_OK=true',
                    'ORIGINAL_TIMEOUT=600000','ORIGINAL_BT=2','ORIGINAL_BRIGHTNESS=1','ORIGINAL_BRIGHTNESS_MODE=0',
                    'RESTORE_TEST_APK=original.apk','RESTORE_TEST_SHA=original','LOW_TEST_INSTALLED=0',
                    'HOLD_FOR_NEXT_STAGE=true','SLEEP_AT_END=true','SCREEN_TIMEOUT_MS=86400000','LEVEL=50','reads=0',
                    'mode='+failure,'setting_failed=false','actual_timeout=86400000','utc() { echo now; }',
                    'stop_app() { :; }','stop_child_group() { :; }','status() { :; }','sleep() { :; }',
                    'install_test() { echo "$1" >> "$BASE/installed"; }','restore_bluetooth() { :; }',
                    'read_battery() { reads=$((reads+1)); if [ "$mode" = battery ] && [ "$reads" -gt 1 ]; then LEVEL=9; fi; }',
                    '''timed() {
                        case "$*" in
                        'settings put system screen_off_timeout '*) actual_timeout=$5 ;;
                        'settings get system screen_off_timeout') echo "$actual_timeout" ;;
                        'settings get system screen_brightness') echo 1 ;;
                        'settings get system screen_brightness_mode') echo 0 ;;
                        'settings put system screen_brightness 1')
                            if [ "$mode" = setting ] && [ "$setting_failed" = false ]; then setting_failed=true; return 1; fi ;;
                        'input keyevent 223') echo sleep > "$BASE/slept" ;;
                        'dumpsys power') echo 'mWakefulness=Asleep' ;;
                        esac
                    }''',code,':','finish'])
                result=subprocess.run([str(bash),'-c',script],capture_output=True,text=True,timeout=5)
                self.assertEqual(2 if failure=='setting' else 0,result.returncode,result.stderr)
                end=json.loads((base/'exit.json').read_text())
                self.assertEqual(failure!='none',end['sleepVerified'])
                self.assertEqual('86400000' if failure=='none' else '600000',end['screenTimeout'])
                self.assertEqual('0' if failure=='none' else '2',end['bluetooth'])
                if failure!='none':self.assertEqual('original.apk',(base/'installed').read_text().strip())


if __name__=='__main__':unittest.main()
