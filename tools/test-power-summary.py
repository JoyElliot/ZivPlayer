# SPDX-License-Identifier: GPL-3.0-or-later
"""Analytic checks for battery trace units and invalid-sample rejection (no device needed)."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('power_summary', Path(__file__).with_name('summarize-power-benchmark.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def report(currents=(1_000_000,) * 3, volts=(4000,) * 3, times=(0, 1000, 2000)):
    environment = dict(airplaneMode=1, wifiOn=2, bluetoothOn=0, brightnessMode=0,
                       systemBrightness=1, windowBrightness=-1, musicMuted=True, musicVolume=0)
    return dict(runLabel='analytic', fixture='synthetic', fixtureSha256='analytic', requestedEngine='exo',
                requestedDecoderMode='automatic', measurementSeconds=2, measurementQualified=True,
                diagnosticsStart={'decoder': 'synthetic'}, environmentBefore=environment, environmentAfter=environment,
                samples=[dict(elapsedMs=t, currentNowUa=i, voltageMv=v, plugged=0, batteryStatus=3,
                              temperatureTenthsC=300, capacityPercent=70, chargeCounterRaw=100,
                              processCpuMs=0) for t, i, v in zip(times, currents, volts)])

class IntegrationTest(unittest.TestCase):
    def test_constant_units(self):
        row = module.summarize(report(), 1)
        self.assertAlmostEqual(row['meanPowerMw'], 4000)
        self.assertAlmostEqual(row['integratedEnergyMwh'], 4000 * 2 / 3600)
        self.assertAlmostEqual(row['integratedChargeMah'], 1000 * 2 / 3600)
        self.assertTrue(row['qualified'])

    def test_hardware_requirement_rejects_software_or_unknown_without_rewriting_history(self):
        for actual in (True, None):
            data = report()
            data.update(requireHardwareDecoder=True, actualSoftwareDecoder=actual)
            self.assertFalse(module.summarize(data, 1)['qualified'])
            data['requireHardwareDecoder'] = False
            self.assertTrue(module.summarize(data, 1)['qualified'])
        data = report()
        data.update(requireHardwareDecoder=True, actualSoftwareDecoder=False)
        self.assertTrue(module.summarize(data, 1)['qualified'])

    def test_display_proof_requires_interactive_screen(self):
        data = report()
        data['frameProofMethod'] = 'display-crop-v1'
        self.assertFalse(module.summarize(data, 1)['qualified'])
        for sample in data['samples']:
            sample['screenInteractive'] = True
        self.assertTrue(module.summarize(data, 1)['qualified'])

    def test_linearly_increasing_current(self):
        row = module.summarize(report(currents=(1_000_000, 2_000_000, 3_000_000)), 1)
        self.assertAlmostEqual(row['meanPowerMw'], 8000)

    def test_standard_negative_discharge_calibration(self):
        row = module.summarize(report(currents=(-1_000_000,) * 3), -1)
        self.assertAlmostEqual(row['meanPowerMw'], 4000)
        self.assertTrue(row['qualified'])

    def test_wrong_sign_and_external_supply_rejected(self):
        data = report()
        data['samples'][1]['plugged'] = 1
        row = module.summarize(data, -1)
        self.assertFalse(row['qualified'])
        self.assertIn('unexpected_current_sign', row['issues'])
        self.assertIn('external_supply_or_battery_status', row['issues'])

    def test_no_time_or_clock_reversal_cannot_pass(self):
        self.assertIsNone(module.summarize(report(times=(0, 0, 0)), 1))
        self.assertFalse(module.summarize(report(times=(0, 1000, 999)), 1)['qualified'])

    def test_scenario_and_gap_rejected(self):
        data = report(times=(0, 1000, 4000))
        data['environmentBefore']['windowBrightness'] = .5
        row = module.summarize(data, 1)
        self.assertFalse(row['qualified'])
        self.assertIn('sample_gap', row['issues'])
        self.assertIn('brightness_scenario_changed', row['issues'])

    def test_charging_validation_can_never_qualify_as_power(self):
        data = report()
        data['validationOnly'] = True
        row = module.summarize(data, 1)
        self.assertFalse(row['qualified'])
        self.assertIn('functional_validation_only', row['issues'])

    def test_matched_surface_requires_actual_endpoint_dimensions(self):
        data = report()
        data['surfaceProtocol'] = 'matched-1080p'
        self.assertFalse(module.summarize(data, 1)['qualified'])
        data.update(surfaceBufferWidthStart=1920, surfaceBufferHeightStart=1080,
                    surfaceBufferWidth=1920, surfaceBufferHeight=1080)
        for row in data['samples']:
            row.update(surfaceBufferWidth=1920, surfaceBufferHeight=1080)
        self.assertTrue(module.summarize(data, 1)['qualified'])
        data['surfaceBufferWidth'] = 2400
        self.assertFalse(module.summarize(data, 1)['qualified'])

    def test_transient_resize_cannot_hide_behind_matching_endpoints(self):
        data = report()
        data.update(surfaceProtocol='matched-1080p', surfaceBufferWidthStart=1920, surfaceBufferHeightStart=1080,
                    surfaceBufferWidth=1920, surfaceBufferHeight=1080)
        for row in data['samples']:
            row.update(surfaceBufferWidth=1920, surfaceBufferHeight=1080)
        data['samples'][1]['surfaceBufferWidth'] = 2400
        self.assertFalse(module.summarize(data, 1)['qualified'])
        data['samples'][1]['surfaceBufferWidth'] = 1920
        data['steadySurfaceEvents'] = [dict(event='destroyed')]
        self.assertFalse(module.summarize(data, 1)['qualified'])

    def test_controlled_protocol_checks_all_temperatures_and_focus_samples(self):
        data = report(currents=(1_000_000,) * 241, volts=(4000,) * 241,
                      times=tuple(range(0, 240001, 1000)))
        data.update(measurementSeconds=240, controlProtocolVersion=2, cleanupSucceeded=True)
        for endpoint in ['environmentBefore', 'environmentAfter']:
            data[endpoint]['displayBrightness'] = {'brightnessMin': .001, 'adjustedBrightness': .001}
        for sample in data['samples']:
            sample.update(windowFocused=True, displayRefreshRate=120, temperatureTenthsC=400)
        self.assertTrue(module.summarize(data, 1)['qualified'])
        data['samples'][120]['temperatureTenthsC'] = 401
        row = module.summarize(data, 1)
        self.assertFalse(row['qualified'])
        self.assertIn('temperature_above_controlled_40c_range', row['issues'])
        data['samples'][120]['temperatureTenthsC'] = 400
        data['samples'][120]['windowFocused'] = False
        self.assertFalse(module.summarize(data, 1)['qualified'])

    def test_balanced_blocks_cancel_linear_order_drift(self):
        for order in [['exo', 'libmpv', 'libmpv', 'exo'], ['libmpv', 'exo', 'exo', 'libmpv']]:
            rows = [dict(comparisonBlock='block', plannedOrdinal=i + 1, plannedOrder=order,
                         engine=engine, qualified=True, runLabel=str(i),
                         meanPowerMw=1000 + 20 * i + (100 if engine == 'exo' else 0),
                         temperatureStartC=30, temperatureEndC=30)
                    for i, engine in enumerate(order)]
            block = module.planned_blocks(rows)[0]
            self.assertTrue(block['complete'])
            self.assertAlmostEqual(100, block['exoMinusLibmpvMw'])
            self.assertFalse(module.planned_blocks(rows[:1] + rows[2:])[0]['complete'])

    def test_missing_planned_legs_are_not_paired_across_blocks(self):
        order = ['exo', 'libmpv', 'libmpv', 'exo']
        rows = [dict(comparisonBlock='one' if i in [0, 3] else 'two', plannedOrdinal=i + 1,
                     plannedOrder=order, engine=engine, qualified=True, runLabel=str(i))
                for i, engine in enumerate(order)]
        self.assertEqual(2, len(module.planned_blocks(rows)))
        self.assertFalse(any(block['complete'] for block in module.planned_blocks(rows)))

    def test_vlc_pairwise_blocks_cancel_drift_and_reject_missing_or_mixed_cohort(self):
        order = ['libmpv', 'vlc', 'vlc', 'libmpv']
        rows = [dict(comparisonBlock='b', fixture='avc', mode='automatic', surfaceProtocol='current-app',
                     measurementCohort='night', plannedOrdinal=i+1, plannedOrder=order, engine=e,
                     qualified=True, runLabel=str(i), meanPowerMw=1000+20*i+(100 if e=='vlc' else 0))
                for i, e in enumerate(order)]
        block = module.pairwise_planned_blocks(rows)[0]
        self.assertTrue(block['complete'])
        self.assertAlmostEqual(-100, block['aMinusBMw'])
        self.assertFalse(module.pairwise_planned_blocks(rows[:3])[0]['complete'])
        rows[-1]['measurementCohort'] = 'other'
        self.assertFalse(any(b['complete'] for b in module.pairwise_planned_blocks(rows)))

    def test_same_cohort_cannot_complete_blocks_across_apks_or_renderers(self):
        order = ['exo', 'libmpv', 'libmpv', 'exo']
        for field in ('mainApkSha256', 'testApkSha256', 'requestedMpvVideoOutput'):
            rows = [dict(comparisonBlock='same', fixture='avc', mode='automatic', surfaceProtocol='current-app',
                         measurementCohort='same', plannedOrdinal=i+1, plannedOrder=order, engine=e,
                         qualified=True, runLabel=str(i), meanPowerMw=1000,
                         temperatureStartC=30, temperatureEndC=30,
                         mainApkSha256='main-a', testApkSha256='test-a', requestedMpvVideoOutput='gpu-next')
                    for i, e in enumerate(order)]
            rows[2][field] = 'different'
            self.assertFalse(any(b['complete'] for b in module.pairwise_planned_blocks(rows)), field)
            self.assertFalse(any(b['complete'] for b in module.planned_blocks(rows)), field)
            self.assertTrue(all('buildIdentity' in b for b in module.pairwise_planned_blocks(rows)))

    def test_renderer_end_must_be_observed_and_match(self):
        data = report()
        data.update(requestedEngine='libmpv', requestedMpvVideoOutput='gpu-next',
                    diagnosticsStart={'decoder':'mediacodec', 'videoOutput':'gpu-next'}, diagnosticsEnd=None)
        self.assertFalse(module.summarize(data, 1)['qualified'])
        data['diagnosticsEnd'] = {'videoOutput':'gpu'}
        self.assertFalse(module.summarize(data, 1)['qualified'])
        data['diagnosticsEnd'] = {'videoOutput':'gpu-next'}
        row = module.summarize(data, 1)
        self.assertTrue(row['qualified'])
        self.assertEqual('gpu-next', row['actualVideoOutputEnd'])

    def test_explicit_renderer_blocks_cancel_drift_without_mixing_apks(self):
        for order in [['libmpv@gpu-next','libmpv@gpu','libmpv@gpu','libmpv@gpu-next'],
                      ['libmpv@gpu','libmpv@gpu-next','libmpv@gpu-next','libmpv@gpu']]:
            rows=[]
            for i, condition in enumerate(order):
                renderer=condition.split('@')[1]
                rows.append(dict(comparisonBlock='render-block', fixture='av1', fixtureSha256='fixture',
                    mode='software', surfaceProtocol='current-app', measurementCohort='new-night',
                    mainApkSha256='main', testApkSha256='test', engine='libmpv',
                    requestedMpvVideoOutput=renderer, actualVideoOutput=renderer, actualVideoOutputEnd=renderer,
                    plannedConditions=order, plannedOrdinal=i+1, runLabel=str(i), qualified=True,
                    temperatureStartC=33, batteryStartPercent=70-i,
                    meanPowerMw=1000+20*i+(100 if renderer=='gpu' else 0)))
            block=module.controlled_condition_blocks(rows)[0]
            self.assertTrue(block['complete'])
            self.assertAlmostEqual(100,block['aMinusBMw'])
            self.assertFalse(module.controlled_condition_blocks(rows[:3])[0]['complete'])
            for field in ['mainApkSha256','testApkSha256','fixtureSha256','measurementCohort','plannedOrdinal','actualVideoOutputEnd']:
                changed=[dict(r) for r in rows]
                changed[2][field]='wrong'
                self.assertFalse(any(b['complete'] for b in module.controlled_condition_blocks(changed)),field)

    def test_known_legacy_renderer_differences_are_not_pooled(self):
        a = dict(engine='libmpv', actualVideoOutput='gpu')
        b = dict(engine='libmpv', actualVideoOutput='gpu-next')
        self.assertNotEqual(module.build_identity(a), module.build_identity(b))
        self.assertNotEqual(module.build_identity(a), module.build_identity(dict(engine='libmpv')))

if __name__ == '__main__':
    unittest.main()
