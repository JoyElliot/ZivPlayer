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

if __name__ == '__main__':
    unittest.main()
