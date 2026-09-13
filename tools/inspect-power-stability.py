#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only drift diagnostics beside, not in place of, the predeclared power qualification."""
import argparse
import json
from pathlib import Path


def window_mean(samples, start_seconds, end_seconds, discharge_sign):
    """Integrate linearly interpolated endpoint power over an exact relative time window."""
    origin = samples[0]['elapsedMs']
    lower, upper = origin + start_seconds * 1000, origin + end_seconds * 1000
    energy_mw_ms = duration_ms = 0.0
    for first, last in zip(samples, samples[1:]):
        t0, t1 = first['elapsedMs'], last['elapsedMs']
        if t1 <= t0:
            raise ValueError('Samples must have a strictly increasing clock.')
        a, b = max(t0, lower), min(t1, upper)
        if b <= a:
            continue
        p0 = discharge_sign * first['currentNowUa'] * first['voltageMv'] / 1e6
        p1 = discharge_sign * last['currentNowUa'] * last['voltageMv'] / 1e6
        pa = p0 + (p1 - p0) * (a - t0) / (t1 - t0)
        pb = p0 + (p1 - p0) * (b - t0) / (t1 - t0)
        energy_mw_ms += (pa + pb) / 2 * (b - a)
        duration_ms += b - a
    return {'seconds': duration_ms / 1000,
            'meanPowerMw': energy_mw_ms / duration_ms if duration_ms else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('native/out/power-comparison-20260910'))
    parser.add_argument('--discharge-sign', type=int, required=True, choices=[-1, 1])
    args = parser.parse_args()
    summary = json.loads((args.input / 'power-summary.json').read_text(encoding='utf-8-sig'))
    diagnostics = []
    for row in summary['runs']:
        if not row['qualified'] or row['smokeOnly']:
            continue
        report = json.loads((args.input / (row['runLabel'] + '.json')).read_text(encoding='utf-8-sig'))
        samples = report['samples']
        quarters = [window_mean(samples, start, start + 60, args.discharge_sign) for start in (0, 60, 120, 180)]
        whole = window_mean(samples, 0, row['seconds'], args.discharge_sign)
        assert abs(whole['meanPowerMw'] - row['meanPowerMw']) < .001
        first, last = quarters[0]['meanPowerMw'], quarters[-1]['meanPowerMw']
        diagnostics.append({
            'runLabel': row['runLabel'], 'surfaceProtocol': row['surfaceProtocol'],
            'measurementCohort': row.get('measurementCohort') or 'legacy',
            'fixture': row['fixture'], 'mode': row['mode'], 'engine': row['engine'],
            'meanPowerMw': whole['meanPowerMw'], 'minuteWindows': quarters,
            'lastMinusFirstMinuteMw': last - first,
            'lastRelativeToFirstMinutePercent': (last / first - 1) * 100,
            'temperatureStartC': row['temperatureStartC'], 'temperatureEndC': row['temperatureEndC'],
            'temperatureDeltaC': row['temperatureEndC'] - row['temperatureStartC'],
            'temperatureMaxC': max(s['temperatureTenthsC'] for s in samples) / 10,
            'temperatureWithin40C': all(s['temperatureTenthsC'] <= 400 for s in samples),
            'thermalStatuses': sorted(set(s['thermalStatus'] for s in samples)),
            'maxGapMs': max(b['elapsedMs'] - a['elapsedMs'] for a, b in zip(samples, samples[1:])),
            'fixtureSha256': row['fixtureSha256'],
            'processCpuMs': row['processCpuMs'],
        })
    result = {'note': 'Descriptive diagnostics only. Minute drift is not a new pass/fail threshold or confidence interval.',
              'dischargeSign': args.discharge_sign, 'runs': diagnostics}
    (args.input / 'power-stability.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(diagnostics, indent=2))


if __name__ == '__main__':
    main()
