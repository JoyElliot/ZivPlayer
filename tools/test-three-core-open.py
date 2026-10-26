"""Host-only regression checks for the open-ended session; no ADB."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tools'/file)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class OpenSession(unittest.TestCase):
    def test_remaining_pairs_and_unique_complementary_orders(self):
        m=load('open_pairs','three-core-open-session.py')
        rows=m.definitions()
        self.assertEqual(len(rows),22)
        self.assertEqual([r[0] for r in rows if not r[3]][:6],['n25hevcmea','n25hevcmeb','n25hevc10eva','n25hevc10evb','n25avceva','n25avcevb'])
        labels=[];orders={};pairs=set()
        for name,codec,pair,preflight,reverse in rows:
            legs=m.legs_for(name,codec,pair,preflight,reverse)
            labels.extend(x['label'] for x in legs)
            if not preflight:
                orders[name]=legs[0]['plannedConditions'];pairs.add((codec,tuple(sorted(pair))))
                self.assertEqual(len(legs),4)
        self.assertEqual(len(pairs),9);self.assertEqual(len(labels),84)
        self.assertEqual(len(labels),len(set(labels)))
        for name,order in orders.items():
            self.assertEqual(order,[order[0],order[1],order[1],order[0]])
            if name.endswith('a'):self.assertEqual(orders[name[:-1]+'b'],[order[1],order[0],order[0],order[1]])

    def test_frozen_plans_disable_clock_limit_preserve_guards(self):
        m=load('open_freeze','three-core-open-session.py');n=load('open_io','renderer-night-session.py')
        with tempfile.TemporaryDirectory() as d:
            m.OUT=Path(d);m.FROZEN=m.OUT/'frozen-tools';m.FROZEN.mkdir();n.OUT=m.OUT
            (m.FROZEN/'phone-power-stable-runner.sh').write_bytes((ROOT/'tools/phone-power-stable-runner.sh').read_bytes())
            m.night_module=lambda:n;m.report=lambda:None
            state={'stages':[]};m.freeze(state,{'app':{'sha256':'a'*64},'androidTest':{'sha256':'b'*64}},[])
            for stage in state['stages']:
                plan=n.read(stage['plan']);config=(Path(stage['plan']).parent/'config.env').read_text()
                self.assertIsNone(plan['hardStopLocal'])
                self.assertTrue(plan['measurementCohort'].startswith('threecore-20261025-'))
                for setting in ['HARD_STOP_EPOCH=0','MINIMUM_START_BATTERY=15','MINIMUM_SAMPLE_BATTERY=10','MAX_START_TEMPERATURE_TENTHS=350','COOLDOWN_STABLE_POLLS=3','COOLDOWN_INPUT_REFRESH=false','SLEEP_AT_END=false']:
                    self.assertIn(setting,config)
                self.assertEqual(n.sha(stage['plan']),stage['planSha256'])

    def test_completed_stop_does_not_touch_device(self):
        m=load('open_stop','three-core-open-session.py')
        n=SimpleNamespace(read=lambda p:{'phase':'completed'},adb=Mock())
        m.night_module=lambda:n;m.stop();n.adb.assert_not_called()

    def test_finalize_rejects_live_stage_without_device_io(self):
        m=load('open_final','finalize-three-core-open.py')
        n=SimpleNamespace(read=lambda p:{'phase':'running_stage'},adb=Mock())
        with self.assertRaises(AssertionError):m.finish(Path('/unused'),n)
        n.adb.assert_not_called()

    def test_automatic_brightness_and_original_manual_value(self):
        m=load('open_settings','finalize-three-core-open.py')
        expected={'screen_brightness_mode':'1','screen_brightness':'1','screen_off_timeout':'600000'}
        self.assertTrue(m.settings_match(dict(expected,screen_brightness='100'),expected))
        expected['screen_brightness_mode']='0'
        self.assertFalse(m.settings_match(dict(expected,screen_brightness='100'),expected))

if __name__=='__main__':unittest.main()
