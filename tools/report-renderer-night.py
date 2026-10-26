#!/usr/bin/env python3
"""Local-only, conservative reporting for the frozen 2026-10-18 experiment.

The running controller's original progress report remains an operational snapshot.
This report preserves raw block results and only pools blocks within explicit strata.
"""
import datetime as dt
import json
import math
from pathlib import Path
import statistics

OUT = Path(__file__).resolve().parents[1]/'native/out/power-renderer-20261018'
IDENTITY = ('fixture', 'fixtureSha256', 'mode', 'surfaceProtocol',
            'measurementCohort', 'mainApkSha256', 'testApkSha256')

def condition(row):
    return row['engine']+('@'+row['requestedMpvVideoOutput'] if row['engine']=='libmpv' else '')

def stratify(block, rows):
    result = dict(block)
    issues = []
    if not block.get('complete') or len(rows)!=4:
        return dict(result, aggregationEligible=False, aggregationIssues=['incomplete_or_unqualified_block'])
    if any(len({r.get(k) for r in rows})!=1 or rows[0].get(k) is None for k in IDENTITY):
        issues.append('mixed_or_missing_build_fixture_cohort_protocol')
    decoders = {}
    for c in block['conditions']:
        members = [r for r in rows if condition(r)==c]
        actual = {(r.get('actualDecoder'),r.get('actualSoftwareDecoder')) for r in members}
        if len(actual)!=1 or any(d is None or sw is None for d,sw in actual):
            issues.append('decoder_changed_within_condition')
        decoders[c] = sorted(actual,key=str)
    if all(r['engine']=='libmpv' for r in rows) and len({r.get('actualDecoder') for r in rows})!=1:
        issues.append('renderer_comparison_decoder_mismatch')
    if any(r.get('actualSoftwareDecoder') is not (r.get('mode')=='software') for r in rows):
        issues.append('decode_class_mismatch')
    temperatures = [r.get('temperatureStartC') for r in rows]
    batteries = [r.get('batteryStartPercent') for r in rows]
    if any(x is None for x in temperatures+batteries):
        issues.append('missing_start_environment')
        temperature_bands = battery_bands = []
    else:
        # Fixed bins declared before the first formal sample; end temperature is
        # an outcome and is never used to choose a stratum.
        temperature_bands = sorted({math.floor(t) for t in temperatures})
        battery_bands = sorted({min(90,math.floor(b/10)*10) for b in batteries})
        if len(temperature_bands)!=1:issues.append('crosses_1C_start_temperature_strata')
        if len(battery_bands)!=1:issues.append('crosses_10pct_start_battery_strata')
    battery_classes = sorted({str(r.get('batteryStratum')) for r in rows})
    if len(battery_classes)!=1 or 'None' in battery_classes:issues.append('mixed_or_missing_battery_class')
    result.update(aggregationEligible=not issues, aggregationIssues=issues,
                  conditionActualDecoders=decoders, startTemperatureBandsC=temperature_bands,
                  startBatteryBandsPercent=battery_bands, batteryClasses=battery_classes,
                  startTemperaturesC=temperatures, startBatteryPercents=batteries)
    return result

def comparisons(blocks):
    grouped = {}
    for b in blocks:
        if not b.get('aggregationEligible'):continue
        key = tuple(b[k] for k in IDENTITY)+(b['a'],b['b'],
            json.dumps(b['conditionActualDecoders'],sort_keys=True),
            tuple(b['startTemperatureBandsC']),tuple(b['startBatteryBandsPercent']),tuple(b['batteryClasses']))
        grouped.setdefault(key,[]).append(b)
    result = []
    for members in grouped.values():
        first=members[0]; values=[b['aMinusBMw'] for b in members]; count=len(values)
        item={k:first[k] for k in (*IDENTITY,'a','b','conditionActualDecoders',
                                   'startTemperatureBandsC','startBatteryBandsPercent','batteryClasses')}
        item.update(blockCount=count, blockIds=[b['comparisonBlock'] for b in members],
                    meanAMinusBMw=statistics.mean(values), blockDifferencesMw=values,
                    blockDifferenceStdevMw=statistics.stdev(values) if count>1 else None,
                    conditionMeanPowerMw={c:statistics.mean(b['conditionMeanPowerMw'][c] for b in members)
                                          for c in [first['a'],first['b']]})
        result.append(item)
    return result

def main():
    state=json.loads((OUT/'session-state.json').read_text(encoding='utf-8-sig'))
    blocks=[]; qualified=0; preflight=0
    for stage in state['stages']:
        file=Path(stage['plan']).parent/'audit.json'
        if not file.exists():continue
        audit=json.loads(file.read_text(encoding='utf-8-sig'))
        rows={r['runLabel']:r for r in audit['runs']}
        qualified+=sum(r.get('qualified',False) and not r.get('smokeOnly') for r in rows.values())
        preflight+=sum(r.get('preflightPassed',False) for r in audit['audit'])
        for block in audit.get('controlledConditionBlocks',[]):
            blocks.append(stratify(block,[rows[label] for label in block['runLabels'] if label in rows]))
    result=dict(updatedUtc=dt.datetime.now(dt.timezone.utc).isoformat(),phase=state['phase'],
        qualifiedFormalRuns=qualified,passedPreflightLegs=preflight,blocks=blocks,comparisons=comparisons(blocks),
        policy='Full ABBA/BAAB blocks are replicates. Pool only identical APK/fixture/protocol/cohort/actual decoder, battery class, 10 percentage-point start battery band, and 1 C start temperature band. Boundary-crossing blocks remain visible individually. No pooled confidence interval is claimed.',
        limitation='Same new APK renderer/core comparison; not a causal estimate of dependency-upgrade benefit. Power does not establish visual quality. The original progress-report.json is an operational snapshot; use this report for stratified comparisons.')
    target=OUT/'stratified-report.json';temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8');temporary.replace(target)
    lines=['# 功耗实验分层报告','',f'更新时间：{result["updatedUtc"]}；状态：{state["phase"]}。',
           f'已通过预检 {preflight} 轮；正式合格 {qualified} 轮。',
           '', '每个完整 ABBA/BAAB 块才算一次重复；不把每秒采样当重复。跨温度或电量层的块仅逐块列出。',
           '', '| 完整块 | A − B（mW） | 可在同层汇总 | 备注 |','|---|---:|---|---|']
    for block in blocks:
        if not block.get('complete'):continue
        lines.append(f'| {block["comparisonBlock"]} | {block["aMinusBMw"]:.1f} | {block["aggregationEligible"]} | {", ".join(block["aggregationIssues"])} |')
    for c in result['comparisons']:
        lines+=['',f'{c["fixture"]}：{c["a"]} − {c["b"]} = {c["meanAMinusBMw"]:.1f} mW；{c["blockCount"]} 个同层完整块。',
                f'起始温度层 {c["startTemperatureBandsC"]} °C，电量层 {c["startBatteryBandsPercent"]}%。']
    lines+=['','同一新 APK 的渲染器比较不能归因为依赖升级收益。温度、掉帧和画面质量证据需分别说明。',
            '原 progress-report.json 为运行进度快照；定量结论以本分层报告及原始审计为准。']
    (OUT/'stratified-report.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('blocks','comparisons')},ensure_ascii=False))

if __name__=='__main__':main()
