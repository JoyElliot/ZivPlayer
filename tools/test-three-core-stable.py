#!/usr/bin/env python3
"""Local safety checks; no ADB or device mutation."""
import datetime as dt
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'tools'/filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

class RepeatSafety(unittest.TestCase):
    def test_priority_and_complementary_complete_blocks(self):
        m = load('repeat_schedule', 'three-core-stable-session.py')
        definitions = m.definitions()
        self.assertEqual([x[0] for x in definitions[:3]], ['n23hevc10check', 'n23hevc10mva', 'n23hevc10mvb'])
        self.assertEqual(m.USER_DEADLINE, '2026-10-23T07:15:00+08:00')
        self.assertEqual(m.OUT.name, 'power-threecore-20261023')
        self.assertNotIn('20261019', m.PARENT)
        labels = []
        orders = {}
        for name, codec, pair, check, reverse in definitions:
            legs = m.legs_for(name, codec, pair, check, reverse)
            labels.extend(x['label'] for x in legs)
            self.assertTrue(all(x['decoderMode']=='automatic' and x['mpvVideoOutput']=='gpu-next' for x in legs))
            if not check:
                order = legs[0]['plannedConditions']; self.assertEqual(len(legs),4)
                self.assertEqual(order, [order[0], order[1], order[1], order[0]])
                self.assertNotEqual(order[0], order[1]); orders[name] = order
        self.assertEqual(len(labels), len(set(labels)))
        for name, order in orders.items():
            if name.endswith('a') and not name.endswith('extra'):
                self.assertEqual(orders[name[:-1]+'b'], [order[1],order[0],order[0],order[1]])

    def test_freeze_keeps_new_cohort_and_restoration_deadline(self):
        m = load('repeat_freeze', 'three-core-stable-session.py')
        n = load('repeat_io', 'renderer-night-session.py')
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder); frozen=out/'frozen-tools';frozen.mkdir()
            (frozen/'phone-power-stable-runner.sh').write_bytes((ROOT/'tools/phone-power-runner.sh').read_bytes())
            m.OUT=out;m.FROZEN=frozen;n.OUT=out;m.night_module=lambda:n;m.report=lambda:None
            state={'stages':[]}
            apks={'app':{'sha256':'a'*64},'androidTest':{'sha256':'b'*64}}
            m.freeze(state, apks, [])
            self.assertEqual(state['phase'],'frozen')
            for stage in state['stages']:
                plan=n.read(stage['plan']);config=(Path(stage['plan']).parent/'config.env').read_text()
                self.assertTrue(plan['measurementCohort'].startswith('threecore-20261023-'))
                self.assertEqual(plan['hardStopLocal'], '2026-10-23T06:35:00+08:00')
                self.assertIn('KEEP_AWAKE_ON_EXIT=true',config)
                self.assertIn('SLEEP_AT_END=false',config)
                self.assertEqual(n.sha(stage['plan']),stage['planSha256'])

    def test_short_window_skips_both_blocks_and_recovers(self):
        m=load('repeat_gate','three-core-stable-session.py')
        with tempfile.TemporaryDirectory() as folder:
            m.OUT=Path(folder)
            state={'phase':'frozen','guardPid':99,'formalStopPid':99,'owner':'owned', 'stages':[
                {'name':'avcmva','status':'pending','preflight':False,'nominalSeconds':1650},
                {'name':'avcmvb','status':'pending','preflight':False,'nominalSeconds':1650}]}
            n=SimpleNamespace(GUARD='/guard',read=lambda p:state,verify_process=lambda p:99,
                adb=lambda *p:b'owned',utc=lambda:'test',launch_stage=Mock(),wait_stage=Mock(),collect_stage=Mock())
            n.record=lambda state,**changes:state.update(changes)
            n.request_recovery=Mock(side_effect=lambda state:state.update(phase='completed'))
            m.request_or_collect_recovery=lambda n,state:n.request_recovery(state)
            m.night_module=lambda:n;m.finalize=Mock();m.os=SimpleNamespace(name='nt',getpid=lambda:123)
            m.DEADLINE=dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=1800)
            import ctypes
            with patch.object(ctypes,'windll',SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=lambda x:1)),create=True):m.run()
            self.assertTrue(all(x['status']=='skipped' for x in state['stages']))
            n.launch_stage.assert_not_called();n.request_recovery.assert_called_once();m.finalize.assert_called_once()
            self.assertFalse((m.OUT/'controller.lock').exists())

    def test_finalizer_rejects_active_sampling_before_adb(self):
        m=load('repeat_finalize','finalize-three-core-stable.py')
        n=SimpleNamespace(read=lambda p:{'phase':'running_stage'},adb=Mock())
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(AssertionError):m.finish(Path(folder),n)
        n.adb.assert_not_called()

    def test_extra_slot_does_not_require_a_missing_sibling(self):
        m=load('repeat_extra','three-core-stable-session.py')
        with tempfile.TemporaryDirectory() as folder:
            m.OUT=Path(folder)
            stage={'name':'vp9mvextra','status':'pending','preflight':False,'nominalSeconds':1650}
            state={'phase':'frozen','guardPid':99,'formalStopPid':99,'owner':'owned','stages':[stage]}
            def collect(state,stage):
                stage.update(status='collected',preflightPassed=0)
                return {'audit':[{}]*4,'qualifiedRows':4,'exit':{'stopReason':'manifest_completed','cleanupSucceeded':True}}
            n=SimpleNamespace(GUARD='/guard',read=lambda p:state,verify_process=lambda p:99,
                adb=lambda *p:b'owned',utc=lambda:'test',launch_stage=Mock(),wait_stage=Mock(),collect_stage=collect)
            n.record=lambda state,**changes:state.update(changes)
            n.request_recovery=Mock(side_effect=lambda state:state.update(phase='completed'))
            m.night_module=lambda:n;m.finalize=Mock();m.os=SimpleNamespace(name='nt',getpid=lambda:123)
            m.request_or_collect_recovery=lambda n,state:n.request_recovery(state)
            m.DEADLINE=dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=1800)
            import ctypes
            with patch.object(ctypes,'windll',SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=lambda x:1)),create=True):m.run()
            self.assertEqual(stage['status'],'collected');n.launch_stage.assert_called_once()

    def test_collects_finished_owned_guard_without_live_guard_requirement(self):
        m=load('repeat_finished_guard','three-core-stable-session.py')
        outcome={'action':'deadline_recovery','keptAwake':True,'cleanupSucceeded':True}
        n=SimpleNamespace(GUARD='/guard',adb=Mock(side_effect=[b'owned',__import__('json').dumps(outcome).encode()]),
            write=Mock(),request_recovery=Mock())
        n.record=lambda state,**changes:state.update(changes)
        state={'owner':'owned'};m.request_or_collect_recovery(n,state)
        self.assertEqual(state['phase'],'completed');n.request_recovery.assert_not_called()

    def test_guard_exit_race_is_reconciled(self):
        m=load('repeat_guard_race','three-core-stable-session.py')
        outcome={'action':'deadline_recovery','keptAwake':True,'cleanupSucceeded':True}
        import subprocess,json
        n=SimpleNamespace(GUARD='/guard',adb=Mock(side_effect=[b'owned',subprocess.CalledProcessError(1,'cat'),b'owned',json.dumps(outcome).encode()]),
            write=Mock(),request_recovery=Mock(side_effect=AssertionError('guard exited')))
        n.record=lambda state,**changes:state.update(changes)
        state={'owner':'owned'};m.request_or_collect_recovery(n,state)
        self.assertEqual(state['phase'],'completed')

    def test_finalizer_rejects_failed_termination_and_uncollected_active_stage(self):
        m=load('repeat_final_gates','finalize-three-core-stable.py')
        for state in [dict(phase='recovery_needs_review',recovery={'cleanupSucceeded':False}),
                      dict(phase='completed',recovery={'cleanupSucceeded':True},stages=[{'status':'running'}])]:
            n=SimpleNamespace(read=lambda p:state,adb=Mock())
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(AssertionError):m.finish(Path(folder),n)
            n.adb.assert_not_called()

class StableRestoration(unittest.TestCase):
    def test_automatic_brightness_is_dynamic_but_other_settings_are_not(self):
        m=load('stable_auto_brightness','finalize-three-core-stable.py')
        expected={'screen_brightness_mode':'1','screen_brightness':'1','screen_off_timeout':'600000'}
        self.assertTrue(m.settings_match(dict(expected,screen_brightness='80'),expected))
        self.assertFalse(m.settings_match(dict(expected,screen_off_timeout='86400000'),expected))
        self.assertFalse(m.settings_match(dict(expected,screen_brightness_mode='0'),expected))
        self.assertFalse(m.settings_match({'screen_brightness_mode':'1'},expected))
        expected['screen_brightness_mode']='0'
        self.assertFalse(m.settings_match(dict(expected,screen_brightness='80'),expected))

    def test_every_frozen_stage_has_battery_and_stability_guards(self):
        m=load('stable_thresholds','three-core-stable-session.py');n=load('stable_io','renderer-night-session.py')
        with tempfile.TemporaryDirectory() as folder:
            m.OUT=Path(folder);m.FROZEN=m.OUT/'frozen-tools';m.FROZEN.mkdir();n.OUT=m.OUT
            (m.FROZEN/'phone-power-stable-runner.sh').write_bytes((ROOT/'tools/phone-power-stable-runner.sh').read_bytes())
            m.night_module=lambda:n;m.report=lambda:None;state={'stages':[]}
            m.freeze(state,{'app':{'sha256':'a'*64},'androidTest':{'sha256':'b'*64}},[])
            for stage in state['stages']:
                config=(Path(stage['plan']).parent/'config.env').read_text()
                for setting in ['MINIMUM_START_BATTERY=15','MINIMUM_SAMPLE_BATTERY=10','MAX_START_TEMPERATURE_TENTHS=350','COOLDOWN_STABLE_POLLS=3','COOLDOWN_INPUT_REFRESH=false']:
                    self.assertIn(setting,config)
                self.assertTrue(all(x['label'].startswith('n23') for x in n.read(stage['plan'])['legs']))

class FinalizerEndToEnd(unittest.TestCase):
    def exercise(self, missing_second_component):
        import io,json,hashlib,tarfile
        m=load('stable_final_success','finalize-three-core-stable.py')
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            def write(path,value): Path(path).write_text(json.dumps(value),encoding='utf8')
            baseline={'settingsBefore':{'screen_off_timeout':'600000','screen_brightness':'1','screen_brightness_mode':'1','bluetooth_on':'2'},'packages':{'main':{'package':'main','sha256':'a'*64},'test':{'package':'test','sha256':'b'*64}},'musicVolume':0,'musicMuted':True}
            write(out/'device-baseline.json',baseline);write(out/'background-control.json',{'packages':[]})
            digest=hashlib.sha256((out/'device-baseline.json').read_bytes()).hexdigest()
            state={'phase':'completed','owner':'owned','baselineSha256':digest,'recovery':{'cleanupSucceeded':True,'awakeGuardStopped':True,'originalTimeoutRestored':True},'statisticsPolicy':'test','stages':[]}
            sample={'elapsedMs':0,'processCpuMs':0,'screenInteractive':True,'windowFocused':True,'isPlaying':True,'thermalStatus':0}
            for i in [1,2]:
                stage=out/f'stage{i}';(stage/'analysis').mkdir(parents=True)
                write(stage/'analysis'/f'leg{i}.json',{'samples':[sample,dict(sample,elapsedMs=1)],'actualHardwareDecoderConfirmed':True,'actualSoftwareDecoder':False,'qualityIssues':[]})
                with tarfile.open(stage/'finished-device.tar','w') as archive:
                    if i==1 or not missing_second_component:
                        value=b'Component: c2.qti.avc.decoder';entry=tarfile.TarInfo(f'logs/leg{i}-codec-end.txt');entry.size=len(value);archive.addfile(entry,io.BytesIO(value))
                state['stages'].append({'name':f'stage{i}','codec':'avc','status':'collected'})
            write(out/'session-state.json',state)
            calls=[]
            def adb(*parts,**kwargs):
                calls.append(parts);key=' '.join(parts)
                if 'awake-guard/owner.txt' in key: value='***'
                elif 'process-stat.txt' in key: value='42 (sh) S'
                elif 'if [ -f /proc/42/stat' in key: value='ABSENT'
                elif 'awake-guard/exit.json' in key: value=json.dumps({'cleanupSucceeded':True,'screenTimeoutMs':600000})
                elif 'night-guard/owner.txt' in key: value='owned'
                elif parts[1:3]==('pm','path'): value='package:/data/app/mock/'+parts[-1]+'/base.apk'
                elif parts[1]=='sha256sum': value=('a'*64 if '/main/' in key else 'b'*64)+'  base.apk'
                elif parts[1:3]==('settings','get'): value='80' if parts[-1]=='screen_brightness' else baseline['settingsBefore'][parts[-1]]
                elif parts[1:3]==('dumpsys','power'): value='mWakefulness=Awake'
                elif parts[1:3]==('dumpsys','window'): value='showing=false'
                elif parts[1:3]==('dumpsys','bluetooth_manager'): value='state: ON'
                elif parts[1:3]==('dumpsys','audio'): value='- STREAM_MUSIC:\n  Muted: true'
                elif parts[1:3]==('cmd','media_session'): value='volume is 0 in range [0..150]'
                else: raise AssertionError(parts)
                return value.encode()
            n=SimpleNamespace(read=lambda p:json.loads(Path(p).read_text()),write=write,sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest(),PARENT='/parent',GUARD='/parent/night-guard',adb=adb,utc=lambda:'2026-10-22T22:40:00+00:00',process_identity=lambda x:x)
            n.record=lambda st,**changes:st.update(changes)
            n.report=lambda:{'blocks':[],'qualifiedFormalRuns':0,'passedPreflightLegs':2}
            result=m.finish(out,n)
            self.assertTrue(result['recoveryVerified'])
            self.assertEqual(result['archivedHardwareComponentsVerified'],not missing_second_component)
            self.assertEqual(result['phase'],'recovery_needs_review' if missing_second_component else 'completed')
            self.assertTrue((out/'morning-report.html').is_file())
            self.assertTrue((out/'descriptive-core-means.json').is_file())
            self.assertTrue(all(c[0]=='shell' for c in calls))
    def test_success_generates_recovery_evidence_and_report(self): self.exercise(False)
    def test_one_missing_codec_stage_cannot_pass_hardware_verification(self): self.exercise(True)

if __name__=='__main__':unittest.main()
