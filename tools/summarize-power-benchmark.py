#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Integrate raw on-device samples; preserve vendor-sign calibration and run-level uncertainty."""
import argparse
import json
from pathlib import Path
import statistics

def build_identity(row):
    """A cohort name alone cannot bind an APK or a renderer experiment."""
    renderer = row.get('requestedMpvVideoOutput')
    if not renderer and row.get('engine') == 'libmpv':
        renderer = row.get('actualVideoOutput')
    return (row.get('mainApkSha256') or 'legacy-unrecorded',
            row.get('testApkSha256') or 'legacy-unrecorded', renderer or 'legacy-unrecorded')

def summarize(report, discharge_sign):
    samples = report.get('samples', [])
    if len(samples) < 2:
        return None
    intervals = []
    charge_mah = energy_mwh = elapsed_ms = 0.0
    issues = list(report.get('qualityIssues', []))
    if report.get('requestedEngine') == 'libmpv' and report.get('requestedMpvVideoOutput'):
        if any((report.get(endpoint) or {}).get('videoOutput') != report['requestedMpvVideoOutput']
               for endpoint in ('diagnosticsStart', 'diagnosticsEnd')):
            issues.append('mpv_renderer_not_continuously_verified_at_endpoints')
    if report.get('requireHardwareDecoder') and report.get('actualSoftwareDecoder') is not False:
        issues.append('required_hardware_decoder_not_confirmed')
    if report.get('frameProofMethod') == 'display-crop-v1' and any(s.get('screenInteractive') is not True for s in samples):
        issues.append('screen_interactive_not_continuously_verified')
    if report.get('validationOnly'):
        issues.append('functional_validation_only')
    if report.get('allowExternalPower'):
        issues.append('external_power_validation_allowed')
    surface_protocol = report.get('surfaceProtocol', 'current-app')
    if surface_protocol == 'matched-1080p':
        if any(report.get(key) != value for key, value in [
                ('surfaceBufferWidthStart', 1920), ('surfaceBufferHeightStart', 1080),
                ('surfaceBufferWidth', 1920), ('surfaceBufferHeight', 1080)]):
            issues.append('matched_surface_size_not_verified')
        if any(s.get('surfaceBufferWidth') != 1920 or s.get('surfaceBufferHeight') != 1080 for s in samples):
            issues.append('matched_surface_size_changed_or_unobserved')
        if report['measurementSeconds'] >= 120 and (report.get('surfaceMonitoringVersion') != 1 or 'steadySurfaceEvents' not in report):
            issues.append('surface_lifecycle_monitoring_missing')
        if any(event.get('event') != 'changed' or event.get('width') != 1920 or event.get('height') != 1080
               for event in report.get('steadySurfaceEvents', [])):
            issues.append('surface_recreated_or_resized_during_sampling')
    elif surface_protocol != 'current-app':
        issues.append('unknown_surface_protocol')
    for previous, current in zip(samples, samples[1:]):
        dt = current['elapsedMs'] - previous['elapsedMs']
        if not 0 < dt < 2000:
            issues.append('sample_gap')
        if dt <= 0:
            continue
        i = discharge_sign * (previous['currentNowUa'] + current['currentNowUa']) / 2
        if i < 0:
            issues.append('unexpected_current_sign')
        power_mw = discharge_sign * (previous['currentNowUa'] * previous['voltageMv'] + current['currentNowUa'] * current['voltageMv']) / 2e6
        energy_mwh += power_mw * dt / 3_600_000
        charge_mah += i * dt / 3_600_000_000
        elapsed_ms += dt
        intervals.append(power_mw)
    for sample in samples:
        if sample['plugged'] != 0 or sample['batteryStatus'] != 3:
            issues.append('external_supply_or_battery_status')
        if sample['currentNowUa'] == -2147483648 or sample['voltageMv'] <= 0:
            issues.append('unsupported_gauge_value')
        if report.get('controlProtocolVersion', 0) >= 2:
            if sample['temperatureTenthsC'] > 400:
                issues.append('temperature_above_controlled_40c_range')
            if sample.get('windowFocused') is not True:
                issues.append('window_focus_not_continuously_verified')
    if report.get('controlProtocolVersion', 0) >= 2:
        if report.get('cleanupSucceeded') is not True:
            issues.append('device_cleanup_not_verified')
        refresh_rates = {sample.get('displayRefreshRate') for sample in samples}
        if None in refresh_rates or len(refresh_rates) != 1:
            issues.append('display_refresh_rate_not_continuously_fixed')
        if report.get('host', {}).get('transportInterruptedDuringLeg'):
            issues.append('host_transport_interrupted_during_leg')
        if not report.get('validationOnly') and (report['measurementSeconds'] != 240 or
                len(samples) != 241 or abs(elapsed_ms - 240000) > 500):
            issues.append('formal_measurement_interval_not_240_seconds')
    for endpoint in ['environmentBefore', 'environmentAfter']:
        env = report.get(endpoint, {})
        if env.get('airplaneMode') != 1 or env.get('wifiOn') not in [1, 2]:
            issues.append('radio_scenario_changed')
        if env.get('brightnessMode') != 0 or env.get('systemBrightness') != 1 or env.get('windowBrightness') != -1:
            issues.append('brightness_scenario_changed')
        if not (env.get('musicMuted') or env.get('musicVolume') == 0):
            issues.append('audio_scenario_changed')
        if env.get('bluetoothOn') != 0:
            issues.append('bluetooth_not_disabled')
        if report['measurementSeconds'] >= 120:
            display = env.get('displayBrightness', {})
            if 'brightnessMin' not in display or 'adjustedBrightness' not in display or abs(display['adjustedBrightness'] - display['brightnessMin']) >= 1e-6:
                issues.append('physical_minimum_brightness_not_verified')
    if elapsed_ms <= 0:
        return None
    temps = [s['temperatureTenthsC'] / 10 for s in samples]
    counter_key = 'chargeCounterRaw' if 'chargeCounterRaw' in samples[0] else 'chargeCounterUah'
    raw_drop = samples[0][counter_key] - samples[-1][counter_key]
    return {'runLabel': report['runLabel'], 'fixture': report['fixture'], 'engine': report['requestedEngine'],
            'surfaceProtocol': surface_protocol,
            'measurementCohort': report.get('measurementCohort') or 'legacy',
            'mainApkSha256': report.get('mainApkSha256'),
            'testApkSha256': report.get('testApkSha256'),
            'requestedMpvVideoOutput': report.get('requestedMpvVideoOutput'),
            'actualVideoOutput': (report.get('diagnosticsStart') or {}).get('videoOutput'),
            'actualVideoOutputEnd': (report.get('diagnosticsEnd') or {}).get('videoOutput'),
            'engineVersion': (report.get('diagnosticsStart') or {}).get('engineVersion'),
            'controlProtocolVersion': report.get('controlProtocolVersion', 0),
            'comparisonBlock': report.get('host', {}).get('comparisonBlock'),
            'plannedOrdinal': report.get('host', {}).get('plannedOrdinal'),
            'plannedOrder': report.get('host', {}).get('plannedOrder', []),
            'plannedConditions': report.get('host', {}).get('plannedConditions', []),
            'surfaceBufferWidth': report.get('surfaceBufferWidth'), 'surfaceBufferHeight': report.get('surfaceBufferHeight'),
            'startedUtc': report.get('host', {}).get('startedUtc'),
            'mode': report['requestedDecoderMode'], 'actualDecoder': (report.get('diagnosticsStart') or {}).get('decoder'),
            'actualSoftwareDecoder': report.get('actualSoftwareDecoder'),
            'decoderClassificationEvidence': report.get('decoderClassificationEvidence'),
            'decoderClassificationConfidence': report.get('decoderClassificationConfidence'),
            'batteryStratum': report.get('batteryStratum'),
            'fixtureSha256': report['fixtureSha256'], 'seconds': elapsed_ms / 1000,
            'meanPowerMw': energy_mwh * 3_600_000 / elapsed_ms,
            'medianPowerMw': statistics.median(intervals), 'integratedEnergyMwh': energy_mwh,
            'integratedChargeMah': charge_mah, 'rawChargeCounterDrop': raw_drop,
            'rawCounterDropPerIntegratedMah': raw_drop / charge_mah if charge_mah else None,
            'temperatureStartC': temps[0], 'temperatureEndC': temps[-1], 'temperatureMinC': min(temps), 'temperatureMaxC': max(temps),
            'batteryStartPercent': samples[0]['capacityPercent'], 'batteryEndPercent': samples[-1]['capacityPercent'],
            'processCpuMs': samples[-1]['processCpuMs'] - samples[0]['processCpuMs'],
            'playbackAdvanceMs': report.get('playbackAdvanceMs'),
            'refreshRateStart': report.get('environmentBefore', {}).get('refreshRate'),
            'refreshRateEnd': report.get('environmentAfter', {}).get('refreshRate'),
            'samplePowerP05Mw': sorted(intervals)[int((len(intervals)-1)*.05)],
            'samplePowerP95Mw': sorted(intervals)[int((len(intervals)-1)*.95)],
            'qualified': report.get('measurementQualified', False) and not issues,
            'issues': sorted(set(issues)), 'smokeOnly': report.get('host', {}).get('smokeOnly', report['measurementSeconds'] < 120)}


def planned_blocks(rows):
    """Never repair a missing planned pair by silently pairing across blocks."""
    grouped = {}
    for row in rows:
        if row.get('comparisonBlock') and row.get('plannedOrder'):
            key = (row.get('measurementCohort') or '', row['comparisonBlock'], build_identity(row))
            grouped.setdefault(key, []).append(row)
    blocks = []
    for _, members in sorted(grouped.items()):
        members = sorted(members, key=lambda row: row.get('plannedOrdinal') or 0)
        order = members[0]['plannedOrder']
        complete = (len(members) == 4 and [r['plannedOrdinal'] for r in members] == [1, 2, 3, 4]
                    and order in [['exo', 'libmpv', 'libmpv', 'exo'], ['libmpv', 'exo', 'exo', 'libmpv']]
                    and all(r['plannedOrder'] == order for r in members)
                    and [r['engine'] for r in members] == order and all(r['qualified'] for r in members))
        block = {'blockId': members[0]['comparisonBlock'], 'plannedOrder': order, 'complete': complete,
                 'runLabels': [r['runLabel'] for r in members]}
        if complete:
            engine_means = {engine: statistics.mean(r['meanPowerMw'] for r in members if r['engine'] == engine)
                            for engine in ['exo', 'libmpv']}
            pairs = []
            for first, second in [members[:2], members[2:]]:
                a, b = (first, second) if first['engine'] == 'exo' else (second, first)
                pairs.append({'exoRun': a['runLabel'], 'libmpvRun': b['runLabel'],
                              'exoMinusLibmpvMw': a['meanPowerMw'] - b['meanPowerMw'],
                              'startTemperatureDifferenceC': a['temperatureStartC'] - b['temperatureStartC'],
                              'temperatureDriftDifferenceC': (a['temperatureEndC'] - a['temperatureStartC']) -
                                  (b['temperatureEndC'] - b['temperatureStartC'])})
            block.update(engineMeanPowerMw=engine_means,
                exoMinusLibmpvMw=engine_means['exo'] - engine_means['libmpv'], plannedPairs=pairs,
                maxPairStartTemperatureDifferenceC=max(abs(p['startTemperatureDifferenceC']) for p in pairs),
                maxRunTemperatureChangeC=max(abs(r['temperatureEndC'] - r['temperatureStartC']) for r in members))
        blocks.append(block)
    return blocks


def pairwise_planned_blocks(rows):
    """Engine-neutral ABBA blocks; never bridge codecs, cohorts, missing legs or output protocols."""
    grouped = {}
    for row in rows:
        if row.get('comparisonBlock') and row.get('plannedOrder'):
            key = tuple(row.get(k) for k in ('fixture', 'mode', 'surfaceProtocol', 'measurementCohort', 'comparisonBlock'))
            grouped.setdefault((*key, build_identity(row)), []).append(row)
    blocks = []
    for key, members in grouped.items():
        members = sorted(members, key=lambda r: r.get('plannedOrdinal') or 0)
        order = members[0]['plannedOrder']
        engines = sorted(set(order))
        complete = (len(order) == 4 and len(engines) == 2 and order == order[::-1] and
                    len(members) == 4 and [r.get('plannedOrdinal') for r in members] == [1, 2, 3, 4] and
                    [r['engine'] for r in members] == order and
                    all(r.get('plannedOrder') == order and r['qualified'] for r in members))
        block = dict(zip(('fixture', 'mode', 'surfaceProtocol', 'measurementCohort', 'blockId'), key))
        block['buildIdentity'] = dict(zip(('mainApkSha256', 'testApkSha256', 'requestedMpvVideoOutput'), key[-1]))
        block.update(plannedOrder=order, engines=engines, complete=complete,
                     runLabels=[r['runLabel'] for r in members])
        if complete:
            a, b = engines
            means = {e: statistics.mean(r['meanPowerMw'] for r in members if r['engine'] == e) for e in engines}
            pairs = []
            for first, second in (members[:2], members[2:]):
                aa, bb = (first, second) if first['engine'] == a else (second, first)
                pairs.append(dict(runA=aa['runLabel'], runB=bb['runLabel'],
                                  aMinusBMw=aa['meanPowerMw'] - bb['meanPowerMw']))
            block.update(engineMeanPowerMw=means, aMinusBMw=means[a] - means[b], plannedPairs=pairs)
        blocks.append(block)
    return blocks

def controlled_condition_blocks(rows):
    """Explicit same-build comparisons may vary renderer; ordinary groups never do."""
    grouped = {}
    fields = ('fixture', 'fixtureSha256', 'mode', 'surfaceProtocol', 'measurementCohort',
              'comparisonBlock', 'mainApkSha256', 'testApkSha256')
    def condition(row):
        return row['engine'] + ('@' + str(row.get('requestedMpvVideoOutput'))
                                if row['engine'] == 'libmpv' else '')
    for row in rows:
        if row.get('comparisonBlock') and row.get('plannedConditions'):
            grouped.setdefault(tuple(row.get(k) for k in fields), []).append(row)
    result = []
    for key, members in grouped.items():
        members = sorted(members, key=lambda r: r['plannedOrdinal'] if isinstance(r.get('plannedOrdinal'), int) else 0)
        order = members[0]['plannedConditions']
        conditions = sorted(set(order))
        complete = (all(key) and len(order) == 4 and len(conditions) == 2 and order == order[::-1] and
                    len(members) == 4 and [r.get('plannedOrdinal') for r in members] == [1, 2, 3, 4] and
                    len({r['runLabel'] for r in members}) == 4 and
                    [condition(r) for r in members] == order and
                    all(r.get('plannedConditions') == order and r.get('qualified') and
                        (r['engine'] != 'libmpv' or
                         r.get('actualVideoOutput') == r.get('actualVideoOutputEnd') == r.get('requestedMpvVideoOutput'))
                        for r in members))
        block = dict(zip(fields, key))
        block.update(complete=bool(complete), plannedConditions=order, conditions=conditions,
                     runLabels=[r['runLabel'] for r in members])
        if complete:
            a, b = conditions
            means = {c: statistics.mean(r['meanPowerMw'] for r in members if condition(r) == c) for c in conditions}
            block.update(conditionMeanPowerMw=means, a=a, b=b, aMinusBMw=means[a]-means[b],
                         conditionMeanStartTemperatureC={c: statistics.mean(r['temperatureStartC'] for r in members if condition(r)==c) for c in conditions},
                         conditionMeanStartBatteryPercent={c: statistics.mean(r['batteryStartPercent'] for r in members if condition(r)==c) for c in conditions})
        result.append(block)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, default=Path('native/out/power-comparison-20260910'))
    p.add_argument('--discharge-sign', type=int, required=True, choices=[-1, 1])
    args = p.parse_args()
    rows = []
    unreadable = []
    failures = []
    for file in sorted(args.input.glob('*.json')):
        try:
            report = json.loads(file.read_text(encoding='utf-8-sig'))
        except (ValueError, OSError) as failure:
            unreadable.append({'file': file.name, 'error': str(failure)})
            continue
        if isinstance(report, dict) and 'requestedEngine' in report and not report.get('completed'):
            failures.append({key: report.get(key) for key in ['runLabel', 'requestedEngine', 'fixture',
                'requestedDecoderMode', 'stage', 'failureType', 'failureMessage', 'qualityIssues']})
        if isinstance(report, dict) and report.get('completed') and 'requestedEngine' in report:
            row = summarize(report, args.discharge_sign)
            if row:
                rows.append(row)
    groups = {}
    staged_file = args.input / 'staged-fixtures.json'
    staged = {r['file']: r['sha256'] for r in json.loads(staged_file.read_text(encoding='utf-8-sig'))} if staged_file.exists() else {}
    for row in rows:
        if staged.get(row['fixture']) != row['fixtureSha256']:
            row['issues'].append('fixture_not_matched_to_staged_manifest')
            row['qualified'] = False
        if row['smokeOnly'] or not row['qualified']:
            continue
        groups.setdefault((row['fixture'], row['mode'], row['surfaceProtocol'], row['measurementCohort'], build_identity(row)), {}).setdefault(row['engine'], []).append(row)
    comparisons = []
    for (fixture, mode, surface_protocol, cohort, identity), engines in groups.items():
        result = {'fixture': fixture, 'mode': mode, 'surfaceProtocol': surface_protocol, 'measurementCohort': cohort,
                  'buildIdentity': dict(zip(('mainApkSha256', 'testApkSha256', 'requestedMpvVideoOutput'), identity)), 'engines': {}}
        for engine, runs in engines.items():
            powers = [r['meanPowerMw'] for r in runs]
            result['engines'][engine] = {'runs': len(runs), 'meanPowerMw': statistics.mean(powers),
                'minPowerMw': min(powers), 'maxPowerMw': max(powers),
                'sampleStdDevMw': statistics.stdev(powers) if len(powers) > 1 else None,
                'meanStartTemperatureC': statistics.mean(r['temperatureStartC'] for r in runs),
                'actualDecoders': sorted(set(str(r['actualDecoder']) for r in runs))}
        result['replicationSufficientForInitialComparison'] = all(len(engines.get(e, [])) >= 2 for e in ['exo', 'libmpv'])
        if all(engine in result['engines'] for engine in ['exo', 'libmpv']):
            exo, mpv = (result['engines'][engine]['meanPowerMw'] for engine in ['exo', 'libmpv'])
            result['exoMinusLibmpvMw'] = exo - mpv
            result['exoRelativeToLibmpvPercent'] = (exo / mpv - 1) * 100
            all_runs = [r for rr in engines.values() for r in rr]
            blocks = planned_blocks(all_runs)
            result['plannedBlocks'] = blocks
            complete = [block for block in blocks if block['complete']]
            result['completeBalancedBlocks'] = len(complete)
            if complete:
                result['balancedExoMeanPowerMw'] = statistics.mean(b['engineMeanPowerMw']['exo'] for b in complete)
                result['balancedLibmpvMeanPowerMw'] = statistics.mean(b['engineMeanPowerMw']['libmpv'] for b in complete)
                result['balancedExoMinusLibmpvMw'] = statistics.mean(b['exoMinusLibmpvMw'] for b in complete)
                result['balancedExoRelativeToLibmpvPercent'] = (result['balancedExoMeanPowerMw'] / result['balancedLibmpvMeanPowerMw'] - 1) * 100
                result['maxPairedStartTemperatureDifferenceC'] = max(b['maxPairStartTemperatureDifferenceC'] for b in complete)
                abba = [b['exoMinusLibmpvMw'] for b in complete if b['plannedOrder'][0] == 'exo']
                baab = [b['exoMinusLibmpvMw'] for b in complete if b['plannedOrder'][0] == 'libmpv']
                result['bothBalancedOrdersObserved'] = bool(abba and baab)
                result['orderSensitivityMw'] = abs(statistics.mean(abba) - statistics.mean(baab)) / 2 if abba and baab else None
            ordered = sorted(all_runs, key=lambda r: r['startedUtc'] or r['runLabel'])
            pairs = []
            for first, second in ([] if blocks else zip(ordered[::2], ordered[1::2])):
                if first['engine'] == second['engine']:
                    continue
                a, b = (first, second) if first['engine'] == 'exo' else (second, first)
                pairs.append({'exoRun': a['runLabel'], 'libmpvRun': b['runLabel'],
                    'exoMinusLibmpvMw': a['meanPowerMw'] - b['meanPowerMw'],
                    'startTemperatureDifferenceC': a['temperatureStartC'] - b['temperatureStartC']})
            result['adjacentPairs'] = pairs
        comparisons.append(result)
    summary = {'measurement': 'Whole-device battery-side current-times-voltage integration; no external power meter',
        'dischargeSignCalibration': args.discharge_sign,
        'counterNote': 'Raw charge counter kept without assuming API microamp-hour units; no inferred scale is silently applied.',
        'uncertaintyNote': 'P05/P95 describe 1-second power samples, not a confidence interval. Engine summaries use independent leg means.',
        'balancedComparisonNote': 'Planned blocks require all four qualified legs in their declared positions. Order sensitivity is a descriptive drift check, not a confidence interval.',
        'unreadableJsonFiles': unreadable, 'failedReports': failures, 'runs': rows, 'comparisons': comparisons,
        'pairwisePlannedBlocks': pairwise_planned_blocks(rows)}
    (args.input / 'power-summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(comparisons if comparisons else rows, indent=2))

if __name__ == '__main__':
    main()
