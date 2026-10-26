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
        m = load('repeat_schedule', 'three-core-repeat-session.py')
        definitions = m.definitions()
        self.assertEqual([x[0] for x in definitions[:3]], ['avccheck', 'avcmva', 'avcmvb'])
        self.assertEqual(m.USER_DEADLINE, '2026-10-22T07:15:00+08:00')
        self.assertEqual(m.OUT.name, 'power-threecore-20261022')
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
        m = load('repeat_freeze', 'three-core-repeat-session.py')
        n = load('repeat_io', 'renderer-night-session.py')
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder); frozen=out/'frozen-tools';frozen.mkdir()
            (frozen/'phone-power-runner.sh').write_bytes((ROOT/'tools/phone-power-runner.sh').read_bytes())
            m.OUT=out;m.FROZEN=frozen;n.OUT=out;m.night_module=lambda:n;m.report=lambda:None
            state={'stages':[]}
            apks={'app':{'sha256':'a'*64},'androidTest':{'sha256':'b'*64}}
            m.freeze(state, apks, [])
            self.assertEqual(state['phase'],'frozen')
            for stage in state['stages']:
                plan=n.read(stage['plan']);config=(Path(stage['plan']).parent/'config.env').read_text()
                self.assertTrue(plan['measurementCohort'].startswith('threecore-20261022-'))
                self.assertEqual(plan['hardStopLocal'], '2026-10-22T06:35:00+08:00')
                self.assertIn('KEEP_AWAKE_ON_EXIT=true',config)
                self.assertIn('SLEEP_AT_END=false',config)
                self.assertEqual(n.sha(stage['plan']),stage['planSha256'])

    def test_short_window_skips_both_blocks_and_recovers(self):
        m=load('repeat_gate','three-core-repeat-session.py')
        with tempfile.TemporaryDirectory() as folder:
            m.OUT=Path(folder)
            state={'phase':'frozen','guardPid':99,'owner':'owned', 'stages':[
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
        m=load('repeat_finalize','finalize-three-core-repeat.py')
        n=SimpleNamespace(read=lambda p:{'phase':'running_stage'},adb=Mock())
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(AssertionError):m.finish(Path(folder),n)
        n.adb.assert_not_called()

    def test_extra_slot_does_not_require_a_missing_sibling(self):
        m=load('repeat_extra','three-core-repeat-session.py')
        with tempfile.TemporaryDirectory() as folder:
            m.OUT=Path(folder)
            stage={'name':'vp9mvextra','status':'pending','preflight':False,'nominalSeconds':1650}
            state={'phase':'frozen','guardPid':99,'owner':'owned','stages':[stage]}
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
        m=load('repeat_finished_guard','three-core-repeat-session.py')
        outcome={'action':'deadline_recovery','keptAwake':True,'cleanupSucceeded':True}
        n=SimpleNamespace(GUARD='/guard',adb=Mock(side_effect=[b'owned',__import__('json').dumps(outcome).encode()]),
            write=Mock(),request_recovery=Mock())
        n.record=lambda state,**changes:state.update(changes)
        state={'owner':'owned'};m.request_or_collect_recovery(n,state)
        self.assertEqual(state['phase'],'completed');n.request_recovery.assert_not_called()

    def test_guard_exit_race_is_reconciled(self):
        m=load('repeat_guard_race','three-core-repeat-session.py')
        outcome={'action':'deadline_recovery','keptAwake':True,'cleanupSucceeded':True}
        import subprocess,json
        n=SimpleNamespace(GUARD='/guard',adb=Mock(side_effect=[b'owned',subprocess.CalledProcessError(1,'cat'),b'owned',json.dumps(outcome).encode()]),
            write=Mock(),request_recovery=Mock(side_effect=AssertionError('guard exited')))
        n.record=lambda state,**changes:state.update(changes)
        state={'owner':'owned'};m.request_or_collect_recovery(n,state)
        self.assertEqual(state['phase'],'completed')

    def test_finalizer_rejects_failed_termination_and_uncollected_active_stage(self):
        m=load('repeat_final_gates','finalize-three-core-repeat.py')
        for state in [dict(phase='recovery_needs_review',recovery={'cleanupSucceeded':False}),
                      dict(phase='completed',recovery={'cleanupSucceeded':True},stages=[{'status':'running'}])]:
            n=SimpleNamespace(read=lambda p:state,adb=Mock())
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(AssertionError):m.finish(Path(folder),n)
            n.adb.assert_not_called()

if __name__=='__main__':unittest.main()
