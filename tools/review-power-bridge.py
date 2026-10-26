#!/usr/bin/env python3
"""Review a finished six-leg bridge against frozen previous-night independent legs."""
import argparse
import json
from pathlib import Path
import re
import statistics

def review(plan, audit):
    expected = [leg['label'] for leg in plan['legs']]
    rows = audit['runs']
    labels = [row['runLabel'] for row in rows]
    execution_ok = len(expected)==6 and len(labels)==6 and set(labels)==set(expected) and all(row['qualified'] for row in rows)
    engines = {}
    for engine in ('exo','libmpv','vlc'):
        members = [row for row in rows if row['engine']==engine]
        reference = plan['baselineReference']['groups']['avc:'+engine]
        values = [row['meanPowerMw'] for row in members]
        mean = statistics.mean(values) if values else None
        delta = 100*(mean/reference['meanMw']-1) if mean is not None else None
        engines[engine] = dict(currentRuns=len(members),previousRuns=reference['count'],
            previousMeanMw=reference['meanMw'],previousMinMw=reference['minMw'],previousMaxMw=reference['maxMw'],
            previousStdevMw=reference['stdevMw'],currentMeanMw=mean,
            currentMinMw=min(values) if values else None,currentMaxMw=max(values) if values else None,
            currentStdevMw=statistics.stdev(values) if len(values)>1 else None,changePercent=delta,
            passed=len(members)==2 and all(r['qualified'] for r in members) and delta is not None and abs(delta)<=plan['baselineReference']['relativeMeanTolerancePercent'])
    passed = execution_ok and all(group['passed'] for group in engines.values())
    return dict(executionQualified=execution_ok,powerGatePassed=passed,engines=engines,
        recommendedHardwareScope='resume_missing_complete_blocks' if passed else 'fresh_both_orders' if execution_ok else 'diagnose_bridge_before_continuing',
        statisticalEquivalenceEstablished=False,crossDayPoolingAllowed=False,
        note='Two H.264 legs per engine, mirrored ABC/CBA; operational 5 percent mean gate only. Battery, thermal and radio differences are reviewed separately.')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--previous',type=Path,required=True)
    args=p.parse_args()
    folder=args.plan.parent
    plan=json.loads(args.plan.read_text(encoding='utf8'))
    audit=json.loads((folder/'audit.json').read_text(encoding='utf8'))
    result=review(plan,audit)
    reports=[json.loads(path.read_text(encoding='utf8')) for path in (folder/'device-archive/raw').glob('*.json')]
    samples=[sample for report in reports for sample in report.get('samples',[])]
    previous=json.loads((args.previous/'reviewed-audit.json').read_text(encoding='utf8'))
    previous_quality=previous['qualificationReview']
    if samples:
        result['environment']={
            'currentSteadyTemperatureC':[min(s['temperatureTenthsC'] for s in samples)/10,max(s['temperatureTenthsC'] for s in samples)/10],
            'previousSteadyTemperatureC':previous_quality['steadyTemperatureC'],
            'currentSteadyBatteryPercent':[min(s['capacityPercent'] for s in samples),max(s['capacityPercent'] for s in samples)],
            'previousPreflightBatteryPercent':previous_quality['preflightBatteryPercent'],
            'currentVoltageMv':[min(s['voltageMv'] for s in samples),max(s['voltageMv'] for s in samples)],
            'allScreenInteractive':all(s.get('screenInteractive') is True for s in samples),
            'allPhysicallyDischarging':all(s['plugged']==0 and s['batteryStatus']==3 for s in samples),
            'surfaceDimensions':sorted({(s['surfaceBufferWidth'],s['surfaceBufferHeight']) for s in samples}),
            'displayRefreshRates':sorted({s.get('displayRefreshRate') for s in samples}),
        }
        result['environment']['batteryOutsidePreviousNightRange']=any(not previous_quality['preflightBatteryPercent'][0]<=s['capacityPercent']<=previous_quality['preflightBatteryPercent'][1] for s in samples)
    def wifi(path):
        data=path.read_text(encoding='utf8',errors='replace')
        def field(pattern):
            found=re.search(pattern,data)
            return found.group(1) if found else None
        return dict(accessPoint=field(r'BSSID: ([^,]+)'),frequency=field(r'Frequency: (\d+)MHz'),rssi=field(r'RSSI: (-?\d+)'))
    prior_wifi=[wifi(path) for path in (args.previous/'device-archive/control').glob('*.wifi-before.txt')]
    current_wifi=[wifi(path) for path in (folder/'device-archive/control').glob('*.wifi-before.txt')]
    if prior_wifi and current_wifi:
        previous_aps={x['accessPoint'] for x in prior_wifi}
        previous_freqs={x['frequency'] for x in prior_wifi}
        result['wifi']={'sameObservedAccessPoint':all(x['accessPoint'] in previous_aps and x['accessPoint'] is not None for x in current_wifi),
            'sameObservedFrequency':all(x['frequency'] in previous_freqs and x['frequency'] is not None for x in current_wifi),
            'previousRssiDbm':[min(int(x['rssi']) for x in prior_wifi if x['rssi']),max(int(x['rssi']) for x in prior_wifi if x['rssi'])],
            'currentRssiDbm':[min(int(x['rssi']) for x in current_wifi if x['rssi']),max(int(x['rssi']) for x in current_wifi if x['rssi'])]}
    (folder/'bridge-review.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not result['executionQualified']: raise SystemExit(2)

if __name__=='__main__': main()
