#!/usr/bin/env python3
"""September 27 completion: fresh repeatability and the remaining complete software blocks."""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import statistics
import subprocess
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
PACKAGE = 'io.github.joyelliot.zivplayer'
ENGINES = ('exo', 'libmpv', 'vlc')
PRIOR = REPO/'native/out/power-comparison-20260926'
CONTROLLER = Path(__file__).name
HK = dt.timezone(dt.timedelta(hours=8))
DEADLINE = dt.datetime(2026,9,28,1,0,tzinfo=HK)
REQUIRED_INDEPENDENT_CHECKS = frozenset({
    'integralsRecomputed','all18Qualified','actualHardwareDecoders',
    'fullHdGeometry','traceAndTiming','screenAwakeFocusedPlaying',
    'networkEvidenceReviewed','temperatureAndPhysicalSupply',
    'batteryStrataReviewed','sixTripletsAndSampleSD',
    'historicalEnvironmentReviewed','allFrozenIdentitiesVerified'})


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build_rows(name, stage):
    rows = []

    def add(codec, order, block, pair, purpose, validation=False):
        for pos, engine in enumerate(order, 1):
            mode = 'automatic' if stage == 'repeatability' else 'software'
            row = dict(ordinal=len(rows)+1, blockNumber=block,
                       comparisonBlock=f'{name}:{pair}:{codec}:{mode}:b{block}',
                       plannedOrdinal=pos, codec=codec, decoderMode=mode, engine=engine,
                       label=f'{name}-{pair}-b{block}-{codec}-{mode}-{pos}-{engine}',
                       purpose=purpose, plannedOrder=list(order))
            if validation:
                row.update(validationOnly=True, warmupSeconds=15, measurementSeconds=65)
            rows.append(row)

    if stage == 'repeatability':
        # Each engine occupies each of the three positions twice. Fixed before data.
        orders = [('exo','libmpv','vlc'), ('libmpv','vlc','exo'), ('vlc','exo','libmpv'),
                  ('exo','vlc','libmpv'), ('vlc','libmpv','exo'), ('libmpv','exo','vlc')]
        for index, order in enumerate(orders, 1):
            add('avc', order, 1, f'repeat{index}', 'cross-day-bridge')
    else:
        for engine in ENGINES:
            add('av1', [engine], 1, 'preflight-'+engine, 'software-preflight', True)
        # Recreate complete blocks: the prior first AVC leg was observer-excluded.
        # Prior qualified legs remain in their own night's cohorts, never spliced.
        for cycle in (1,2):
            pairs = [('avc','sw-exo-mpv','exo','libmpv')] if cycle == 1 else [
                                     ('av1','sw-exo-mpv','exo','libmpv'),
                                      ('av1','sw-mpv-vlc','libmpv','vlc'),
                                      ('hevc','sw-exo-mpv','exo','libmpv'),
                                      ('avc','sw-exo-mpv','exo','libmpv')]
            for codec, pair, a, b in pairs:
                for block, order in enumerate(([a,b,b,a], [b,a,a,b]), 1):
                    add(codec, order, block, f'remaining{cycle}-{pair}', 'battery-extra')
    return rows


def prepare(out, serial, watchdog):
    baseline = read(out/'device-baseline.json')
    assert baseline['serial'] == serial
    assert baseline['settingsBefore']['screen_brightness'] == '1'
    assert baseline['settingsBefore']['screen_brightness_mode'] == '0'
    assert sha(out/'user-data-before.tar') == baseline['userDataArchiveSha256']
    assert sha(out/'original-test.apk') == baseline['packages']['test']['sha256']
    previous = PRIOR/'software26'
    old = read(previous/'run-plan.json')
    assert read(PRIOR/'session-state.json')['phase']=='completed'
    assert read(PRIOR/'session-state.json')['phoneWorkComplete'] is True
    assert sha(out/'power-test.apk') == old['testApkSha256']
    assert baseline['packages']['main']['sha256'] == old['mainApkSha256']
    day = dt.datetime.fromisoformat(baseline['utc']).astimezone(dt.timezone(dt.timedelta(hours=8))).strftime('%Y%m%d')
    assert day == '20260927', 'This controller is bound to the reviewed September 27 remainder.'
    assert watchdog == DEADLINE, 'Wrong night emergency recovery deadline.'
    assert watchdog.tzinfo and 12*3600 < (watchdog-dt.datetime.now(dt.timezone.utc)).total_seconds() <= 26*3600
    parent = '/data/local/tmp/ziv-power-'+day
    state = dict(schemaVersion=1, phase='prepared', serial=serial, remoteParent=parent,
                 watchdogLocal=watchdog.isoformat(), originalSettings=baseline['settingsBefore'],
                 userDeadline=None, noMorningCutoff=True, stages={},
                 baselineSha256=sha(out/'device-baseline.json'))
    fixtures = read(out/'persistent-fixtures.json')['fixtures']
    for stage, name in [('repeatability','repeat27'), ('software','software27')]:
        folder=out/name
        folder.mkdir(exist_ok=False)
        remote=parent+'/'+name
        rows=build_rows(name,stage)
        if stage=='software':
            old_remaining=[r for r in old['legs'] if r['ordinal']>=52]
            formal=[r for r in rows if not r.get('validationOnly')]
            assert len(old_remaining)==len(formal)==40
            for new,prior in zip(formal,old_remaining):
                assert all(new[k]==prior[k] for k in ('codec','decoderMode','engine','blockNumber','plannedOrdinal','plannedOrder'))
                new['priorNightOrigin']=dict(ordinal=prior['ordinal'],label=prior['label'],
                    reason='observer_excluded_full_block_replacement' if prior['ordinal']==52 else 'not_executed')
        keys=('ordinal','blockNumber','comparisonBlock','plannedOrdinal','codec','decoderMode','engine','label','purpose')
        (folder/'manifest.tsv').write_text(''.join('\t'.join(str(r[k]) for k in keys)+'\n' for r in rows),encoding='utf-8',newline='\n')
        shutil.copyfile(REPO/'tools/phone-power-runner.sh',folder/'runner.sh')
        shutil.copyfile(previous/'PowerPlaybackBenchmarkTest.kt',folder/'PowerPlaybackBenchmarkTest.kt')
        # Collector requires this frozen file, although this plan has no legacy Exo gate.
        (folder/'exo-reference.tsv').write_text('',encoding='utf-8')
        first=stage=='repeatability'
        config=dict(MANIFEST_SHA=sha(folder/'manifest.tsv'),MANIFEST_ROWS=str(len(rows)),
            MAIN_SHA=baseline['packages']['main']['sha256'],LEGACY_TEST_SHA=sha(out/'power-test.apk'),
            LOW_TEST_SHA=sha(out/'power-test.apk'),EXPECTED_INITIAL_TEST_SHA=sha(out/'power-test.apk'),
            LEGACY_TEST_APK=remote+'/power-test.apk',LOW_TEST_APK=remote+'/power-test.apk',
            RESTORE_TEST_APK=remote+'/original-test.apk',
            RESTORE_TEST_SHA=baseline['packages']['test']['sha256'],COHORT=f'phone-{day}-{stage}-v1',
            VALIDATION_ONLY='false',SLEEP_AT_END='true',HOLD_FOR_NEXT_STAGE='true' if first else 'false',
            MINIMUM_COOLDOWN='90',WARMUP_SECONDS='90',MEASURE_SECONDS='240',
            SURFACE_PROTOCOL='current-app',FRAME_PROOF_METHOD='display-crop-v1',
            HARDWARE_ONLY_AUTOMATIC='true',SCREEN_TIMEOUT_MS='86400000',
            RESTORE_SCREEN_TIMEOUT=baseline['settingsBefore']['screen_off_timeout'],
            RESTORE_BLUETOOTH=baseline['settingsBefore']['bluetooth_on'],
            HARD_STOP_EPOCH=str(int(watchdog.timestamp())),OPTIONAL_BLOCK_BUDGET_SECONDS='2400',REQUIRED_TARGET_SOFTWARE_ROWS='0')
        (folder/'config.env').write_text(''.join(k+'='+shlex.quote(v)+'\n' for k,v in config.items()),encoding='utf-8',newline='\n')
        for filename in ['collect-power-night.py','summarize-power-benchmark.py',CONTROLLER]:
            shutil.copyfile(REPO/'tools'/filename,folder/filename)
        plan=dict(schemaVersion=4,stage=stage,frozenUtc=dt.datetime.now(dt.timezone.utc).isoformat(),serial=serial,
            remoteDirectory=remote,measurementCohort=config['COHORT'],legs=rows,fixtures=fixtures,
            mainApkSha256=config['MAIN_SHA'],testApkSha256=config['LEGACY_TEST_SHA'],originalTestApkSha256=config['RESTORE_TEST_SHA'],
            warmupSeconds=90,measurementSeconds=240,minimumCooldownSeconds=90,maxStartTemperatureC=36,maxTemperatureC=40,
            screenTimeoutMs=86400000,surfaceProtocol='current-app',frameProofMethod='display-crop-v1',
            watchdogLocal=watchdog.isoformat(),userDeadline=None,
            repeatabilityRule=dict(expectedRunsPerEngine=6,cvReviewPercent=5.0,firstLastHalfDriftReviewPercent=5.0,
                sampleUnit='One qualified 240-second leg mean, not the 241 correlated per-second samples.',
                interpretation='Operational variability review only; never statistical equivalence or proof of decoder-only power. No automatic full hardware matrix rerun.'),
            scope='18 H264 hardware repeatability legs, then 3 AV1 short validations and exactly 40 formal software remainder candidates: prior extra1 AVC Exo/mpv 8 and prior extra2 32. The observer-excluded AVC leg is replaced as part of an entirely new 8-leg pair; no cross-night splicing, no rerun of the prior 24 qualified target legs, and no additional batches.',
            extraSoftwareRule='The user directly authorized these 40 remaining formal legs after prior target24 completion. REQUIRED_TARGET_SOFTWARE_ROWS=0 means no new target phase in this night, not a claim that 24 new targets passed. Enter the finite remainder once with battery strictly above 30 percent, after host repeatability review and independent audit; AV1 additionally requires this night software preflights. Once admitted, continue the finite list until completion or existing guards; below 10 percent never start another leg and restore/sleep. Full host qualification remains pending until collection. Preserve partial blocks without combining nights or low-battery strata.',
            stopPolicy='Complete the finite queue; no early-morning cutoff. Physical discharge and existing temperature/battery failure guards remain; 24-hour watchdog is emergency recovery only.',
            previousPlanSha256=sha(previous/'run-plan.json'),
            historicalRepeatabilitySha256=sha(PRIOR/'repeat26/repeatability-review.json'),
            independentReviewRequiredChecks=sorted(REQUIRED_INDEPENDENT_CHECKS),
            sourceHead=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO).decode().strip())
        plan['frozenFiles']={p.name:sha(p) for p in folder.iterdir() if p.is_file()}
        write(folder/'run-plan.json',plan)
        state['stages'][stage]=dict(name=name,plan=str(folder/'run-plan.json'),planSha256=sha(folder/'run-plan.json'),plannedRows=len(rows))
    guard=out/'night-guard';guard.mkdir(exist_ok=False)
    shutil.copyfile(REPO/'tools/power-night-deadline-guard.sh',guard/'guard.sh')
    settings=baseline['settingsBefore']
    config=dict(HARD_STOP_EPOCH=str(int(watchdog.timestamp())),ORIGINAL_TEST_SHA=baseline['packages']['test']['sha256'],
                MAIN_SHA=baseline['packages']['main']['sha256'],ORIGINAL_TIMEOUT=settings['screen_off_timeout'],
                ORIGINAL_BT=settings['bluetooth_on'],ORIGINAL_BRIGHTNESS=settings['screen_brightness'],
                ORIGINAL_BRIGHTNESS_MODE=settings['screen_brightness_mode'])
    (guard/'config.env').write_text(''.join(k+'='+shlex.quote(v)+'\n' for k,v in config.items()),encoding='utf-8',newline='\n')
    state['guardFrozenFiles']={p.name:sha(p) for p in guard.iterdir() if p.is_file()}
    state['toolSha256']={name:sha(REPO/'tools'/name) for name in [CONTROLLER,'collect-power-night.py','summarize-power-benchmark.py']}
    write(out/'session-state.json',state)
    return state


def review_repeatability(out):
    state=read(out/'session-state.json')
    assert state['phase'] in ('running_repeatability','repeatability_reviewed'), 'Cannot reset an active or attempted software launch through review.'
    assert state.get('activeStage') != 'software' and not state.get('softwareLaunchAttemptUtc')
    folder=Path(state['stages']['repeatability']['plan']).parent
    plan=read(folder/'run-plan.json');audit=read(folder/'audit.json')
    expected={r['label'] for r in plan['legs']}
    rows=audit['runs']
    execution_ok=(len(rows)==18 and {r['runLabel'] for r in rows}==expected and all(r['qualified'] for r in rows)
                  and audit['exit']['stopReason']=='manifest_completed' and audit['exit']['cleanupSucceeded'])
    groups={}
    for engine in ENGINES:
        legs=[r for r in rows if r['engine']==engine]
        values=[r['meanPowerMw'] for r in legs]
        avg=statistics.mean(values) if values else None
        sd=statistics.stdev(values) if len(values)>1 else None
        cv=100*sd/avg if sd is not None and avg else None
        drift=100*(statistics.mean(values[3:])/statistics.mean(values[:3])-1) if len(values)==6 else None
        groups[engine]=dict(n=len(values),meanMw=avg,sampleStdevMw=sd,cvPercent=cv,
            minMw=min(values) if values else None,maxMw=max(values) if values else None,
            firstHalfVsLastHalfChangePercent=drift,runLabels=[r['runLabel'] for r in legs],valuesMw=values,
            temperatureRangeC=[min(r['temperatureMinC'] for r in legs),max(r['temperatureMaxC'] for r in legs)] if legs else None,
            batteryRangePercent=[min(r['batteryEndPercent'] for r in legs),max(r['batteryStartPercent'] for r in legs)] if legs else None)
    rule=plan['repeatabilityRule']
    variability_ok=all(g['n']==6 and g['cvPercent'] is not None and g['cvPercent']<=rule['cvReviewPercent']
                       and abs(g['firstHalfVsLastHalfChangePercent'])<=rule['firstLastHalfDriftReviewPercent'] for g in groups.values())
    result=dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),sourceAuditSha256=sha(folder/'audit.json'),
        planSha256=sha(folder/'run-plan.json'),executionQualified=execution_ok,variabilityWithinOperationalLimits=variability_ok,
        continuationAllowed=execution_ok and variability_ok,engines=groups,
        statisticalEquivalenceEstablished=False,historicalCohortsPooled=False,
        note='Sample SD uses independent-run means with n-1 denominator. The 5 percent CV/drift flags are prespecified operational review thresholds, not confidence intervals. Qualification or variability failure requires diagnosis; never discard outliers or automatically run a full hardware matrix.')
    historical=read(PRIOR/'repeat26/repeatability-review.json')
    assert sha(PRIOR/'repeat26/repeatability-review.json')==plan['historicalRepeatabilitySha256']
    result['historicalComparison']={}
    for engine in ENGINES:
        fresh=groups[engine];old=historical['engines'][engine]
        if fresh['meanMw'] is None or fresh['sampleStdevMw'] is None:continue
        delta=fresh['meanMw']-old['meanMw']
        result['historicalComparison'][engine]=dict(
            referenceNight='2026-09-26',meanDeltaMw=delta,meanDeltaPercent=100*delta/old['meanMw'],
            withinReferenceOneSD=abs(delta)<=old['sampleStdevMw'],
            withinBothNightsOneSD=abs(delta)<=min(old['sampleStdevMw'],fresh['sampleStdevMw']),
            note='Descriptive flag only. Outside one SD does not invalidate data, prove a fault, or establish non-equivalence. Compare environment, battery strata, decoder and protocol; keep every qualified leg and each night separate.')
    result['independentAuditRequiredBeforeSoftware']=True
    write(folder/'repeatability-review.json',result)
    state.update(phase='repeatability_reviewed',repeatabilityReviewSha256=sha(folder/'repeatability-review.json'),continuationAllowed=result['continuationAllowed'])
    write(out/'session-state.json',state)
    return result


def identity(stat):
    fields=stat.rsplit(') ',1)[1].split()
    return int(fields[2]),int(fields[3]),int(fields[19])


def launch(out, stage, serial, attempt_id=None):
    state=read(out/'session-state.json')
    baseline=read(out/'device-baseline.json')
    assert stage in ('repeatability','software') and serial==state['serial']==baseline['serial']
    required='prepared' if stage=='repeatability' else 'repeatability_reviewed'
    assert state['phase']==required, 'Existing launch/running state must be investigated; never launch twice.'
    assert sha(out/'device-baseline.json')==state['baselineSha256']
    assert sha(out/'user-data-before.tar')==baseline['userDataArchiveSha256']
    assert sha(out/'original-test.apk')==baseline['packages']['test']['sha256']
    for name, digest in state['toolSha256'].items():
        assert sha(REPO/'tools'/name)==digest,'Reviewed tool changed: '+name
    folder=Path(state['stages'][stage]['plan']).parent
    plan=read(folder/'run-plan.json')
    assert sha(folder/'run-plan.json')==state['stages'][stage]['planSha256']
    for name,digest in plan['frozenFiles'].items():assert sha(folder/name)==digest,name
    watchdog=dt.datetime.fromisoformat(plan['watchdogLocal'])
    assert watchdog==DEADLINE
    # Nominal complete queue duration plus startup/cooling margin, not merely 4h.
    nominal=sum(r.get('warmupSeconds',90)+r.get('measurementSeconds',240)+90 for r in plan['legs'])
    required_seconds=max(4*3600,nominal+60*60)
    assert (watchdog-dt.datetime.now(dt.timezone.utc)).total_seconds()>required_seconds, 'Insufficient full-stage budget; restore and review, never silently launch a truncated plan.'
    if stage=='software':
        repeat=Path(state['stages']['repeatability']['plan']).parent
        review=read(repeat/'repeatability-review.json')
        assert sha(repeat/'repeatability-review.json')==state['repeatabilityReviewSha256']
        assert sha(repeat/'audit.json')==review['sourceAuditSha256']
        assert review['continuationAllowed'] is True
        independent=read(repeat/'independent-review.json')
        assert independent['sourceAuditSha256']==sha(repeat/'audit.json')
        assert independent['sourceReviewSha256']==sha(repeat/'repeatability-review.json')
        assert independent['continuationAllowed'] is True
        assert independent['historicalComparisonReviewed'] is True
        assert REQUIRED_INDEPENDENT_CHECKS<=independent['checks'].keys()
        assert all(value is True for value in independent['checks'].values())
        state['independentReviewSha256']=sha(repeat/'independent-review.json')
    adb=['adb','-s',serial]
    def call(*parts,timeout=60):return subprocess.check_output(adb+list(parts),timeout=timeout)
    def session(stat_path,pid):
        saved=call('shell','cat',stat_path,timeout=10).decode().strip()
        expected=identity(saved)
        assert int(saved.split(' ',1)[0])==pid and expected[:2]==(pid,pid)
        live=call('shell','cat',f'/proc/{pid}/stat',timeout=10).decode().strip()
        assert identity(live)==expected and live.rsplit(') ',1)[1].split()[0]!='Z'
        return expected
    def owned_guard():
        assert call('shell','cat',guard+'/owner.txt',timeout=10).decode().strip()==state['guardOwnerId']
        assert session(guard+'/guard-v2-process-stat.txt',state['guardPid'])==tuple(state['guardIdentity'])
    def wait_record(path):
        until=time.monotonic()+15
        while time.monotonic()<until:
            record=subprocess.run(adb+['shell','cat',path],capture_output=True,timeout=5)
            if record.returncode==0:
                try:return json.loads(record.stdout)
                except json.JSONDecodeError:pass
            time.sleep(.5)
        raise RuntimeError('Startup record missing; inspect existing process before any retry: '+path)
    def upload(path,remote):subprocess.run(adb+['push',str(path),remote],check=True,timeout=180)
    def package_hash(pkg):
        package_path=call('shell','pm','path',pkg).decode().strip()
        assert re.fullmatch(r'package:/data/app/[^\s]+/base.apk',package_path)
        return call('shell','sha256sum',package_path[8:]).decode().split()[0]
    assert package_hash(PACKAGE)==plan['mainApkSha256']
    parent=state['remoteParent'];remote=plan['remoteDirectory'];guard=parent+'/night-guard'
    if stage=='repeatability':
        assert package_hash(PACKAGE+'.test')==baseline['packages']['test']['sha256']
        state.update(phase='launching_repeatability_guard',launchAttemptId=attempt_id,guardLaunchAttemptUtc=dt.datetime.now(dt.timezone.utc).isoformat())
        write(out/'session-state.json',state)
        call('shell','mkdir',parent)
        state['guardOwnerId']=attempt_id
        write(out/'session-state.json',state)
        call('shell','mkdir',guard)
        owner=out/'night-guard/owner.txt'
        owner.write_text(attempt_id+'\n',encoding='utf-8',newline='\n')
        upload(owner,guard+'/owner.txt')
        for name,digest in state['guardFrozenFiles'].items():
            assert sha(out/'night-guard'/name)==digest
            upload(out/'night-guard'/name,guard+'/'+name)
        upload(out/'original-test.apk',guard+'/original-test.apk')
        call('shell','sh','-n',guard+'/guard.sh')
        # Record the mutation before starting any detached process, making failures non-retryable by default.
        call('shell',f'/system/bin/toybox nohup /system/bin/setsid sh {guard}/guard.sh {guard} </dev/null >{guard}/guard.log 2>&1 &')
        ready=wait_record(guard+'/guard-v2-ready.json');assert ready['version']==2
        state['guardPid']=ready['pid'];state['guardIdentity']=session(guard+'/guard-v2-process-stat.txt',ready['pid'])
        write(out/'session-state.json',state)
        write(out/'night-guard/launch-record.json',dict(ready=ready,identity=state['guardIdentity']))
        subprocess.run(adb+['install','-r','-t',str(out/'power-test.apk')],check=True,timeout=180)
    else:
        rp=read(Path(state['stages']['repeatability']['plan']))
        actual_exit=json.loads(call('shell','cat',rp['remoteDirectory']+'/exit.json'))
        assert actual_exit==read(Path(state['stages']['repeatability']['plan']).parent/'audit.json')['exit']
        owned_guard()
        state.update(phase='launching_software',launchAttemptId=attempt_id,softwareLaunchAttemptUtc=dt.datetime.now(dt.timezone.utc).isoformat())
        write(out/'session-state.json',state)
    assert package_hash(PACKAGE+'.test')==plan['testApkSha256']
    assert package_hash(PACKAGE)==plan['mainApkSha256']
    call('shell','mkdir',remote)
    for name in ['manifest.tsv','config.env','runner.sh','exo-reference.tsv','run-plan.json']:
        upload(folder/name,remote+'/'+name)
    for name in ['power-test.apk','original-test.apk']:upload(out/name,remote+'/'+name)
    for name in ['manifest.tsv','config.env','runner.sh','exo-reference.tsv','run-plan.json']:
        assert call('shell','sha256sum',remote+'/'+name).decode().split()[0]==sha(folder/name)
    call('shell','sh','-n',remote+'/runner.sh')
    pointer=folder/'active-run.txt';pointer.write_text(remote+'\n',encoding='utf-8',newline='\n')
    upload(pointer,guard+'/active-run.next')
    call('shell','mv',guard+'/active-run.next',guard+'/active-run.txt')
    if stage=='software':call('shell','touch',guard+'/final-stage.started')
    assert call('shell','cat',guard+'/active-run.txt').decode().strip()==remote
    owned_guard()
    state.update(phase='launching_'+stage,activeStage=stage,activeRemote=remote,launchAttemptUtc=dt.datetime.now(dt.timezone.utc).isoformat())
    write(out/'session-state.json',state)
    call('shell',f'/system/bin/toybox nohup /system/bin/setsid sh {remote}/runner.sh {remote} </dev/null >{remote}/runner.stdout 2>&1 &')
    startup=time.monotonic()
    started=wait_record(remote+'/started.json')
    run_identity=session(remote+'/runner-process-stat.txt',started['pid'])
    assert time.monotonic()-startup<45
    # Earliest next observation is deliberately beyond the nominal queue duration.
    quiet_minutes=155 if stage=='repeatability' else 345
    state.update(phase='running_'+stage,started=started,runnerIdentity=run_identity,
                 observedUtc=dt.datetime.now(dt.timezone.utc).isoformat(),
                 nextExitCheckNotBeforeUtc=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=quiet_minutes)).isoformat())
    write(out/'session-state.json',state)
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','review','launch-repeatability','launch-software'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--serial')
    parser.add_argument('--watchdog',help='Timezone-aware emergency recovery limit, never the old morning deadline')
    args=parser.parse_args();out=args.output.resolve()
    if args.action=='prepare':result=prepare(out,args.serial,dt.datetime.fromisoformat(args.watchdog))
    elif args.action=='review':
        # Review writes state too; serialize it against launching either phase.
        lock=out/'launch.lock'
        lock.mkdir(exist_ok=False)
        try:result=review_repeatability(out)
        finally:lock.rmdir()
    else:
        # Exclusive local ownership covers the read/check/write interval as well.
        lock=out/'launch.lock'
        lock.mkdir(exist_ok=False)
        attempt_id=uuid.uuid4().hex
        try:
            result=launch(out,args.action.removeprefix('launch-'),args.serial,attempt_id)
        except Exception as failure:
            state=read(out/'session-state.json')
            # Only reconcile this failed launch. A rejected duplicate must not stop a healthy run.
            if state.get('launchAttemptId')==attempt_id and str(state.get('phase','')).startswith('launching_'):
                state['lastLaunchError']=str(failure)
                state['launchFailureUtc']=dt.datetime.now(dt.timezone.utc).isoformat()
                adb=['adb','-s',args.serial]
                guard=state['remoteParent']+'/night-guard'
                try:
                    owner_id=state.get('guardOwnerId')
                    assert owner_id, 'No guard ownership: remote directory may belong to another session.'
                    owner=subprocess.check_output(adb+['shell','cat',guard+'/owner.txt'],timeout=10).decode().strip()
                    assert owner==owner_id, 'Guard ownership mismatch: do not signal another session.'
                    saved=subprocess.check_output(adb+['shell','cat',guard+'/guard-v2-process-stat.txt'],timeout=10).decode().strip()
                    pid=int(saved.split(' ',1)[0]);expected=identity(saved)
                    live=subprocess.check_output(adb+['shell','cat',f'/proc/{pid}/stat'],timeout=10).decode().strip()
                    assert expected[:2]==(pid,pid) and identity(live)==expected
                    subprocess.run(adb+['shell','kill','-USR1',str(pid)],check=True,timeout=10)
                    state.update(phase='recovery_requested_after_launch_failure',recoveryGuardPid=pid,
                                 recoveryNote='Immediate device-local recovery requested; verify exit and restored state, never assume success.')
                except Exception as recovery_error:
                    state['recoveryRequestError']=str(recovery_error)
                    state['requiresImmediateRecoveryReview']=True
                write(out/'session-state.json',state)
            raise
        finally:
            lock.rmdir()
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
