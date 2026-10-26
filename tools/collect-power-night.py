#!/usr/bin/env python3
"""Archive a finished device-controlled night and audit each leg without changing raw evidence."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile


def load(path):
    return json.loads(path.read_text(encoding='utf8'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--serial', required=True)
    args = p.parse_args()
    plan = load(args.plan)
    base = ['adb', '-s', args.serial]
    remote = plan['remoteDirectory']
    assert re.fullmatch(r'/data/local/tmp/ziv-power-[0-9]{8}/[A-Za-z0-9_-]+', remote)
    folder = args.plan.parent
    final = subprocess.run(base+['shell','cat',remote+'/exit.json'],capture_output=True,timeout=30)
    exit_source = 'runner'
    if final.returncode == 0:
        exit_record = json.loads(final.stdout)
    else:
        # A guard may have forcibly stopped an unresponsive runner before it wrote exit.json.
        # Do not invent a successful runner exit: retain the distinct guard record and verify inactivity.
        guard = remote.rsplit('/',1)[0]+'/night-guard'
        guard_exit = json.loads(subprocess.check_output(base+['shell','cat',guard+'/exit.json'],timeout=30))
        pointer = subprocess.check_output(base+['shell','cat',guard+'/active-run.txt'],timeout=30).decode().strip()
        assert pointer == remote and guard_exit.get('action') == 'deadline_recovery', 'No matching completed deadline recovery.'
        saved = subprocess.check_output(base+['shell','cat',remote+'/runner-process-stat.txt'],timeout=30).decode().strip()
        pid = int(saved.split(' ',1)[0])
        saved_fields = saved.rsplit(') ',1)[1].split()
        assert [int(saved_fields[2]),int(saved_fields[3])] == [pid,pid], 'Saved runner session identity is invalid.'
        live = subprocess.run(base+['shell','cat',f'/proc/{pid}/stat'],capture_output=True,timeout=30)
        if live.returncode == 0:
            assert saved.rsplit(') ',1)[1].split()[19] != live.stdout.decode().rsplit(') ',1)[1].split()[19], 'Runner still alive.'
        group = subprocess.run(base+['shell',f'kill -0 -- -{pid}'],capture_output=True,timeout=30)
        assert group.returncode != 0, 'Runner process group still active; collection must wait.'
        exit_source = 'deadline_guard_after_missing_runner_exit'
        exit_record = dict(guard_exit,stopReason='deadline_guard_forced_stop',runnerExitCode=None)
        (folder/'guard-exit.json').write_text(json.dumps(guard_exit,indent=2)+'\n',encoding='utf8')
    archive_file = folder/'finished-device.tar'
    if not archive_file.exists():
        names = ['raw','control','decisions','exo-reference.tsv','exit.json','started.json','events.tsv','status.tsv','manifest.tsv','config.env',
                 'network-link-events.txt','network-link-error.txt','runner-process-stat.txt',
                 'active-am-process-stat.txt','network-process-stat.txt','runner.stdout','cancel']
        present = set(subprocess.check_output(base+['shell','ls','-1',remote],timeout=30).decode().splitlines())
        selected = [name for name in names if name in present]
        assert {'manifest.tsv','config.env','exo-reference.tsv'} <= set(selected), 'Frozen input files are missing.'
        # exec-out argument handling differs from shell; avoid nested shell quoting altogether.
        data = subprocess.check_output(base+['exec-out','tar','-C',remote,'-cf','-',*selected],timeout=120)
        with tarfile.open(fileobj=io.BytesIO(data)) as check:
            check.getmembers()
        archive_file.write_bytes(data)
        (folder/'finished-device.sha256').write_text(hashlib.sha256(data).hexdigest(),encoding='ascii')
    data = archive_file.read_bytes()
    assert hashlib.sha256(data).hexdigest() == (folder/'finished-device.sha256').read_text(encoding='ascii')
    dest = folder/'device-archive'
    dest.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(dest,filter='data')
    for name in ('manifest.tsv','config.env','exo-reference.tsv'):
        assert hashlib.sha256((dest/name).read_bytes()).hexdigest()==plan['frozenFiles'][name], 'Wrong frozen run: '+name
    analysis = folder/'analysis'
    analysis.mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location('summary',Path(__file__).with_name('summarize-power-benchmark.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    collection_issues = []
    events_path = dest/'events.tsv'
    if not events_path.is_file(): collection_issues.append('missing_events')
    if not (dest/'decisions').is_dir(): collection_issues.append('missing_decisions')
    events = events_path.read_text(encoding='utf8').splitlines() if events_path.is_file() else []
    skipped = {f[3]:f[4] for line in events if len(f:=line.split('\t'))==5 and f[1]=='leg_skipped'}
    audits, rows = [], []
    fixtures = {r['file']:r['sha256'] for r in plan['fixtures']}
    for leg in plan['legs']:
        label = leg['label']; path = dest/'raw'/f'{label}.json'
        if label in skipped:
            audits.append(dict(label=label, skipped=skipped[label])); continue
        instrument_path = dest/'raw'/f'{label}.instrumentation.log'
        missing = []
        if not path.is_file() or not (dest/'control'/f'{label}.control.json').is_file(): missing.append('missing_report_or_control')
        if not instrument_path.is_file(): missing.append('missing_instrumentation_log')
        if missing:
            audits.append(dict(label=label, qualified=False, issues=collection_issues+missing)); continue
        report = load(path); control = load(dest/'control'/f'{label}.control.json')
        expected_warmup = leg.get('warmupSeconds', plan.get('warmupSeconds', 90))
        expected_measure = leg.get('measurementSeconds', plan.get('measurementSeconds', 240))
        validation_only = leg.get('validationOnly', False)
        expected_renderer = leg.get('mpvVideoOutput', plan.get('mpvVideoOutput'))
        trace_path = dest/'raw'/f'{label}.jsonl'
        trace = [json.loads(x) for x in trace_path.read_text(encoding='utf8').splitlines()] if trace_path.exists() else []
        steady = [x for x in trace if x.get('phase')=='steady']
        instrument = instrument_path.read_text(encoding='utf8',errors='replace')
        checks = dict(
            archiveEvidence=not collection_issues,
            completed=report.get('completed') is True and report.get('cleanupSucceeded') is True,
            buildIdentity=(report.get('mainApkSha256') == plan['mainApkSha256'] and
                report.get('testApkSha256') == plan['testApkSha256']) if plan.get('buildIdentityProtocol') == 1 else True,
            renderer=(report.get('requestedMpvVideoOutput') == expected_renderer and
                (leg['engine'] != 'libmpv' or all((report.get(endpoint) or {}).get('videoOutput') == expected_renderer
                    for endpoint in ('diagnosticsStart', 'diagnosticsEnd')))) if plan.get('buildIdentityProtocol') == 1 else True,
            testIdentity=control.get('testApkSha256')==plan['testApkSha256'],
            requestedIdentity=report.get('runLabel')==label and report.get('requestedEngine')==leg['engine'] and
                report.get('requestedDecoderMode')==leg['decoderMode'] and report.get('measurementCohort')==plan['measurementCohort'],
            decodeClass=report.get('actualSoftwareDecoder') is (leg['decoderMode']=='software'),
            hardwareGuard=leg['decoderMode']=='software' or report.get('requireHardwareDecoder') is True,
            trace=steady==report.get('samples') and len(steady)==expected_measure+1 and len(trace)==expected_warmup+expected_measure+1,
            fixture=report.get('fixtureSha256')==fixtures.get(leg['codec']+'-1080p24.mkv'),
            protocol=report.get('surfaceProtocol')=='current-app' and report.get('frameProofMethod')==plan['frameProofMethod'] and
                report.get('warmupSeconds')==expected_warmup and report.get('measurementSeconds')==expected_measure and
                report.get('validationOnly',False) is validation_only,
            control=control.get('exitCode')==0 and control.get('finalResultSeen') is True and not control.get('commandTimedOut') and
                'OK (1 test)' in instrument and 'INSTRUMENTATION_CODE: -1' in instrument,
            processAndCooling=control.get('freshProcessVerified') is True and
                control.get('cooldownSeconds',0)>=leg.get('minimumCooldownSeconds',plan.get('minimumCooldownSeconds',90)) and
                control.get('preflightBattery',{}).get('temperatureTenthsC',999)<=plan.get('maxStartTemperatureTenthsC',360),
            plannedBlock=(control.get('comparisonBlock')==leg['comparisonBlock'] and
                control.get('plannedOrdinal')==leg['plannedOrdinal']) if leg.get('plannedConditions') else True,
            network=control.get('networkMonitorAlive') is True and control.get('networkStartBytes')==control.get('networkEndBytes'),
            awake=bool(trace) and all(x.get('screenInteractive') is True for x in trace),
            geometry=all(x.get('surfaceBufferWidth')==1920 and x.get('surfaceBufferHeight')==1080 for x in steady),
            screenRate=bool(steady) and all(abs(x.get('displayRefreshRate',0)-120)<.01 for x in steady),
        )
        report['host']=dict(control,plannedOrder=leg['plannedOrder'],
                            plannedConditions=leg.get('plannedConditions',[]),smokeOnly=validation_only)
        report['qualityIssues']=list(report.get('qualityIssues',[]))+['audit_'+k for k,v in checks.items() if not v]
        report['measurementQualified']=report.get('measurementQualified') is True and all(checks.values())
        low_battery = control.get('preflightBattery',{}).get('level',0)<28 or any(x.get('capacityPercent',0)<25 for x in trace)
        report['batteryStratum']='below-historical-battery-range' if low_battery else 'normal'
        if low_battery:
            report['originalMeasurementCohort']=report['measurementCohort']
            report['measurementCohort']+=':low-battery'
        row = module.summarize(report,1)
        if row: rows.append(row)
        audits.append(dict(label=label,checks=checks,qualified=bool(row and row['qualified']),
                           validationOnly=validation_only,preflightPassed=validation_only and all(checks.values()) and report.get('playbackValidationPassed') is True,
                           meanPowerMw=row['meanPowerMw'] if row else None,failure=report.get('failureMessage')))
        (analysis/(label+'.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    result=dict(exit=exit_record,exitSource=exit_source,plannedRows=len(plan['legs']),skippedRows=len(skipped),
        qualifiedRows=sum(x.get('qualified',False) for x in audits),audit=audits,runs=rows,
        pairwisePlannedBlocks=module.pairwise_planned_blocks(rows),
        controlledConditionBlocks=module.controlled_condition_blocks(rows),
        archiveSha256=hashlib.sha256(data).hexdigest(), collectionIssues=collection_issues,
        conditionalDecisions={p.name:p.read_text(encoding='utf8') for p in (dest/'decisions').glob('*') if p.is_file()},
        note='Independent-leg means and balanced pairwise blocks only. Historical cohorts are never pooled; P05/P95 are not confidence intervals.')
    (folder/'audit.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('audit','runs','pairwisePlannedBlocks')},indent=2))


if __name__=='__main__': main()
