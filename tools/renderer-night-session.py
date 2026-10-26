#!/usr/bin/env python3
"""Frozen hardware-first renderer experiment, with device-local recovery and passive observation."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'native/out/power-renderer-20261018'
ADB = shutil.which('adb') or r'D:\DevTools\Android\Sdk\platform-tools\adb.exe'
SERIAL = '***'
PARENT = '/data/local/tmp/ziv-power-20261018'
GUARD = PARENT+'/night-guard'
PACKAGE = 'io.github.joyelliot.zivplayer'
DEADLINE = dt.datetime.fromisoformat('2026-10-18T09:15:00+08:00')
RECOVERY_DEADLINE = dt.datetime.fromisoformat('2026-10-18T09:30:00+08:00')

def utc(): return dt.datetime.now(dt.timezone.utc).isoformat()
def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()
def write(path,value):
    path=Path(path); temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    temp.replace(path)
def adb(*parts,timeout=60):
    return subprocess.check_output([ADB,'-s',SERIAL,*map(str,parts)],timeout=timeout)
def upload(path,remote):
    subprocess.run([ADB,'-s',SERIAL,'push',str(path),remote],check=True,timeout=240)
def put_text(path,text): Path(path).write_text(text,encoding='utf8',newline='\n')
def env(path,values): put_text(path,''.join(k+'='+shlex.quote(str(v))+'\n' for k,v in values.items()))
def package_sha(package):
    path=adb('shell','pm','path',package).decode().strip().removeprefix('package:')
    assert re.fullmatch(r'/data/app/[A-Za-z0-9_~=/.-]+/base.apk',path),path
    return adb('shell','sha256sum',path).decode().split()[0]
def process_identity(record):
    fields=record.rsplit(') ',1)[1].split()
    return int(fields[2]),int(fields[3]),int(fields[19])
def verify_process(path):
    saved=adb('shell','cat',path,timeout=10).decode().strip()
    pid=int(saved.split(' ',1)[0]); expected=process_identity(saved)
    live=adb('shell','cat',f'/proc/{pid}/stat',timeout=10).decode().strip()
    assert expected[:2]==(pid,pid) and process_identity(live)==expected
    assert live.rsplit(') ',1)[1].split()[0]!='Z'
    return pid
def wait_json(remote,seconds=90):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        result=subprocess.run([ADB,'-s',SERIAL,'shell','cat',remote],capture_output=True,timeout=10)
        if result.returncode==0:
            try:return json.loads(result.stdout)
            except ValueError:pass
        time.sleep(1)
    raise RuntimeError('Startup record missing; investigate the existing launch before retry: '+remote)
def record(state,**changes):
    state.update(changes,updatedUtc=utc());write(OUT/'session-state.json',state)
    with (OUT/'controller-events.jsonl').open('a',encoding='utf8') as stream:
        stream.write(json.dumps(dict(utc=utc(),**changes),ensure_ascii=False)+'\n')
    print(json.dumps(changes,ensure_ascii=False),flush=True)

def build_legs(name,codec,pair,preflight=False):
    orders=[['libmpv@gpu-next','libmpv@gpu','exo','vlc']] if preflight else [
        [pair[0],pair[1],pair[1],pair[0]], [pair[1],pair[0],pair[0],pair[1]]]
    legs=[]
    for block,order in enumerate(orders,1):
        for pos,condition in enumerate(order,1):
            engine,_,renderer=condition.partition('@');renderer=renderer or 'gpu-next'
            legs.append(dict(ordinal=len(legs)+1,blockNumber=block,
                comparisonBlock=f'{name}:{codec}:b{block}',plannedOrdinal=pos,codec=codec,
                decoderMode='software' if codec=='av1' else 'automatic',engine=engine,
                label=f'{name}-b{block}-{pos}-{engine}-{renderer}',
                purpose='validation-only' if preflight else 'matrix',mpvVideoOutput=renderer,
                plannedOrder=[c.partition('@')[0] for c in order],
                plannedConditions=[] if preflight else order,
                validationOnly=preflight,warmupSeconds=15 if preflight else 90,
                measurementSeconds=65 if preflight else 240))
    return legs

def freeze():
    assert not (OUT/'session-state.json').exists(),'A session exists; inspect it instead of replacing it.'
    baseline=read(OUT/'device-baseline.json');prepared=read(OUT/'prepared.json')
    artifacts=read(OUT/'experiment-apks.json');fixtures=read(OUT/'persistent-fixtures.json')['fixtures']
    for kind,key in [('main','app'),('test','androidTest')]:
        assert prepared[kind]['sha256']==artifacts[key]['sha256']==package_sha(PACKAGE+('.test' if kind=='test' else ''))
        assert sha(artifacts[key]['path'])==artifacts[key]['sha256']
        assert sha(baseline['packages'][kind]['backupPath'])==baseline['packages'][kind]['sha256']
    assert sha(baseline['userDataBackup']['path'])==baseline['userDataBackup']['sha256']
    tools_dir=OUT/'frozen-tools';tools_dir.mkdir()
    for name in ['phone-power-runner.sh','power-night-deadline-guard.sh','phone-awake-guard.sh',
                 'renderer-night-session.py','collect-power-night.py','summarize-power-benchmark.py']:
        shutil.copyfile(ROOT/'tools'/name,tools_dir/name)
    shutil.copyfile(ROOT/'native/source-manifest.toml',tools_dir/'native-source-manifest.toml')
    for name in ['PowerPlaybackBenchmarkTest.kt','PowerCodecLogCapture.kt']:
        shutil.copyfile(ROOT/'apps/android/src/androidTest/kotlin/io/github/joyelliot/zivplayer'/name,tools_dir/name)
    (tools_dir/'source-diff.patch').write_bytes(subprocess.check_output(['git','diff','--binary'],cwd=ROOT))
    owner=uuid.uuid4().hex
    stages=[]
    definitions=[('hwpreflight','avc',None,True),
        ('avcmaina','avc',('libmpv@gpu-next','libmpv@gpu'),False),
        ('avcmainb','avc',('libmpv@gpu-next','libmpv@gpu'),False),
        ('hevc','hevc',('libmpv@gpu-next','libmpv@gpu'),False),
        ('hevc10','hevc10',('libmpv@gpu-next','libmpv@gpu'),False),
        ('vp9','vp9',('libmpv@gpu-next','libmpv@gpu'),False),
        ('exoavc','avc',('exo','libmpv@gpu-next'),False),
        ('vlcavc','avc',('libmpv@gpu-next','vlc'),False),
        ('av1optional','av1',('libmpv@gpu-next','libmpv@gpu'),False)]
    for name,codec,pair,preflight in definitions:
        folder=OUT/name;folder.mkdir();remote=PARENT+'/'+name
        legs=build_legs(name,codec,pair,preflight)
        keys=['ordinal','blockNumber','comparisonBlock','plannedOrdinal','codec','decoderMode','engine','label','purpose','mpvVideoOutput']
        put_text(folder/'manifest.tsv',''.join('\t'.join(str(leg[k]) for k in keys)+'\n' for leg in legs))
        put_text(folder/'exo-reference.tsv','')
        shutil.copyfile(tools_dir/'phone-power-runner.sh',folder/'runner.sh')
        config=dict(MANIFEST_SHA=sha(folder/'manifest.tsv'),MANIFEST_ROWS=len(legs),
            MAIN_SHA=prepared['main']['sha256'],LEGACY_TEST_SHA=prepared['test']['sha256'],LOW_TEST_SHA=prepared['test']['sha256'],
            EXPECTED_INITIAL_TEST_SHA=prepared['test']['sha256'],LEGACY_TEST_APK=GUARD+'/power-test.apk',LOW_TEST_APK=GUARD+'/power-test.apk',
            RESTORE_TEST_APK='',COHORT='renderer-20261018-hw-priority-'+prepared['main']['sha256'][:12]+'-'+prepared['test']['sha256'][:12],
            MPV_VIDEO_OUTPUT='gpu-next',VALIDATION_ONLY=str(preflight).lower(),SLEEP_AT_END='false',
            KEEP_AWAKE_ON_EXIT='true',HOLD_FOR_NEXT_STAGE='true',RESTORE_SCREEN_TIMEOUT=86400000,RESTORE_BLUETOOTH=0,
            MINIMUM_COOLDOWN=30,FIRST_COOLDOWN_SECONDS=120,COOLDOWN_POLL_SECONDS=15,COOLDOWN_REQUIRE_NONRISING='true',
            MAX_START_TEMPERATURE_TENTHS=345,MINIMUM_START_BATTERY=20,MINIMUM_SAMPLE_BATTERY=15,
            WARMUP_SECONDS=15 if preflight else 90,MEASURE_SECONDS=65 if preflight else 240,
            SURFACE_PROTOCOL='current-app',FRAME_PROOF_METHOD='display-crop-v1',HARDWARE_ONLY_AUTOMATIC='true',
            SOFTWARE_ONLY=str(codec=='av1').lower(),SCREEN_TIMEOUT_MS=86400000,
            HARD_STOP_EPOCH=int(DEADLINE.timestamp()),OPTIONAL_BLOCK_BUDGET_SECONDS=1650)
        env(folder/'config.env',config)
        plan=dict(schemaVersion=4,buildIdentityProtocol=1,serial=SERIAL,remoteDirectory=remote,
            measurementCohort=config['COHORT'],mainApkSha256=config['MAIN_SHA'],testApkSha256=config['LEGACY_TEST_SHA'],
            mpvVideoOutput='gpu-next',legs=legs,fixtures=fixtures,frameProofMethod='display-crop-v1',surfaceProtocol='current-app',
            warmupSeconds=config['WARMUP_SECONDS'],measurementSeconds=config['MEASURE_SECONDS'],minimumCooldownSeconds=30,
            maxStartTemperatureTenthsC=345,hardStopLocal=DEADLINE.isoformat(),hardwareRequired=codec!='av1',
            frozenUtc=utc(),observer='A passive ADB shell waits for device-local phase files. No host queries or transfers during playback.',
            frozenFiles={n:sha(folder/n) for n in ['manifest.tsv','config.env','runner.sh','exo-reference.tsv']})
        write(folder/'run-plan.json',plan)
        stages.append(dict(name=name,plan=str(folder/'run-plan.json'),planSha256=sha(folder/'run-plan.json'),status='pending',
                           preflight=preflight,optional=codec=='av1',nominalSeconds=(len(legs)*(80 if preflight else 330)+len(legs)*45+90)))
    guard=OUT/'night-guard';guard.mkdir()
    shutil.copyfile(tools_dir/'power-night-deadline-guard.sh',guard/'guard.sh')
    settings=baseline['settingsBefore']
    env(guard/'config.env',dict(HARD_STOP_EPOCH=int(RECOVERY_DEADLINE.timestamp()),ORIGINAL_TIMEOUT=86400000,
        ORIGINAL_BT=settings['bluetooth_on'],ORIGINAL_BRIGHTNESS=settings['screen_brightness'],
        ORIGINAL_BRIGHTNESS_MODE=settings['screen_brightness_mode'],ORIGINAL_TEST_SHA=baseline['packages']['test']['sha256'],
        MAIN_SHA=prepared['main']['sha256'],RESTORE_MAIN_APK=GUARD+'/original-main.apk',RESTORE_MAIN_SHA=baseline['packages']['main']['sha256'],
        KEEP_AWAKE_ON_EXIT='true',REQUIRE_FULL_RECOVERY='true',ORIGINAL_MUSIC_VOLUME=baseline['musicVolume'],
        ORIGINAL_MUSIC_MUTED=str(baseline['musicMuted']).lower()))
    paused=read(OUT/'background-control.json')['packages']
    put_text(guard/'background-packages.txt',''.join(p['package']+'\n' for p in paused if p['originalSuspended'] is False))
    put_text(guard/'owner.txt',owner+'\n')
    state=dict(version=1,phase='frozen',serial=SERIAL,owner=owner,stages=stages,
        hardwarePriority=True,deadlineLocal=DEADLINE.isoformat(),recoveryDeadlineLocal=RECOVERY_DEADLINE.isoformat(),
        keepAwakeUntilUserReturns=True,baselineSha256=sha(OUT/'device-baseline.json'),
        frozenTools={p.name:sha(p) for p in tools_dir.iterdir() if p.is_file()},
        sourceHead=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        guardFiles={p.name:sha(p) for p in guard.iterdir() if p.is_file()})
    record(state,phase='frozen')
    return state

def deploy_guard(state):
    guard=OUT/'night-guard';baseline=read(OUT/'device-baseline.json');artifacts=read(OUT/'experiment-apks.json')
    adb('shell','mkdir',GUARD)
    for name,digest in state['guardFiles'].items():
        assert sha(guard/name)==digest;upload(guard/name,GUARD+'/'+name)
    for name,path in [('original-main.apk',baseline['packages']['main']['backupPath']),
                      ('original-test.apk',baseline['packages']['test']['backupPath']),('power-test.apk',artifacts['androidTest']['path'])]:
        upload(path,GUARD+'/'+name)
        assert adb('shell','sha256sum',GUARD+'/'+name).decode().split()[0]==sha(path)
    adb('shell','sh','-n',GUARD+'/guard.sh')
    record(state,phase='launching_guard')
    adb('shell',f'/system/bin/toybox nohup /system/bin/setsid sh {GUARD}/guard.sh {GUARD} </dev/null >{GUARD}/guard.log 2>&1 &')
    ready=wait_json(GUARD+'/guard-v2-ready.json')
    assert verify_process(GUARD+'/guard-v2-process-stat.txt')==ready['pid']
    record(state,phase='guard_ready',guardPid=ready['pid'])

def launch_stage(state,stage):
    plan=read(stage['plan']);folder=Path(stage['plan']).parent;remote=plan['remoteDirectory']
    assert sha(stage['plan'])==stage['planSha256']
    assert package_sha(PACKAGE)==plan['mainApkSha256'] and package_sha(PACKAGE+'.test')==plan['testApkSha256']
    assert verify_process(GUARD+'/guard-v2-process-stat.txt')==state['guardPid']
    assert adb('shell','cat',GUARD+'/owner.txt').decode().strip()==state['owner']
    power=adb('shell','dumpsys','power').decode();policy=adb('shell','dumpsys','window','policy').decode()
    assert re.search(r'^\s*mWakefulness=Awake\s*$',power,re.M) and re.search(r'^\s*showing=false\s*$',policy,re.M)
    write(folder/'screen-before-launch.json',dict(utc=utc(),power=power,policy=policy))
    (folder/'processes-before.txt').write_bytes(adb('shell','ps -A -o PID,UID,NAME,ARGS'))
    adb('shell','mkdir',remote)
    for name,digest in plan['frozenFiles'].items():
        assert sha(folder/name)==digest;upload(folder/name,remote+'/'+name)
    upload(folder/'run-plan.json',remote+'/run-plan.json')
    adb('shell','sh','-n',remote+'/runner.sh')
    pointer=folder/'active-run.txt';put_text(pointer,remote+'\n')
    for directory in [GUARD,PARENT+'/awake-guard']:
        upload(pointer,directory+'/active-run.next');adb('shell','mv',directory+'/active-run.next',directory+'/active-run.txt')
    stage['status']='launching';record(state,phase='launching_stage',activeStage=stage['name'],activePlan=stage['plan'])
    adb('shell',f'/system/bin/toybox nohup /system/bin/setsid sh {remote}/runner.sh {remote} </dev/null >{remote}/runner.stdout 2>&1 &')
    started=wait_json(remote+'/started.json')
    assert verify_process(remote+'/runner-process-stat.txt')==started['pid']
    stage['status']='running';stage['startedUtc']=started['startedUtc']
    record(state,phase='running_stage',runnerPid=started['pid'],samplingObserved=False)

def wait_stage(state,stage):
    remote=read(stage['plan'])['remoteDirectory']
    # Reading local files and sleeping on the phone produces no periodic host traffic.
    script=('alive() { saved=$(cat "$1") || return 1; pid=${saved%% *}; '
            'live=$(cat /proc/$pid/stat) || return 1; a=${saved##*) }; b=${live##*) }; '
            'set -- $a; shift 19; start=$1; set -- $b; [ "$1" != Z ] || return 1; '
            'shift 19; [ "$start" = "$1" ]; }; '
            f'seen=0; while [ ! -f {remote}/exit.json ] && [ ! -f {GUARD}/exit.json ]; do '
            f'alive {remote}/runner-process-stat.txt || {{ printf "RUNNER_LOST\\n"; exit 42; }}; '
            f'alive {GUARD}/guard-v2-process-stat.txt || {{ printf "GUARD_LOST\\n"; exit 43; }}; '
            f'if [ "$seen" = 0 ] && [ "$(cut -f2 {remote}/status.tsv 2>/dev/null)" = running ]; then '
            'printf "SAMPLING_STARTED\\n"; seen=1; fi; sleep 15; done; printf "PHASE_EXIT\\n"')
    with subprocess.Popen([ADB,'-s',SERIAL,'shell',script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as process:
        for line in process.stdout:
            if line.strip()=='SAMPLING_STARTED':record(state,phase='running_stage',samplingObserved=True,samplingObservedUtc=utc())
        result=process.wait()
        if result:raise RuntimeError('Passive ADB observer disconnected; inspect phase before reconnecting: '+process.stderr.read())

def collect_stage(state,stage):
    folder=Path(stage['plan']).parent
    with (folder/'collection.log').open('wb') as stream:
        subprocess.run([sys.executable,str(OUT/'frozen-tools/collect-power-night.py'),'--plan',stage['plan'],'--serial',SERIAL],
            stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=240)
    audit=read(folder/'audit.json');stage.update(status='collected',finishedUtc=utc(),qualifiedRows=audit['qualifiedRows'],
        preflightPassed=sum(x.get('preflightPassed',False) for x in audit['audit']),exit=audit['exit'])
    record(state,phase='phase_collected',lastCollectedStage=stage['name'])
    report()
    return audit

def request_recovery(state):
    assert adb('shell','cat',GUARD+'/owner.txt').decode().strip()==state['owner']
    pid=verify_process(GUARD+'/guard-v2-process-stat.txt');assert pid==state['guardPid']
    record(state,phase='recovery_requested')
    adb('shell','kill','-USR1',str(pid))
    for _ in range(120):
        result=subprocess.run([ADB,'-s',SERIAL,'shell','cat',GUARD+'/exit.json'],capture_output=True,timeout=15)
        if result.returncode==0:
            recovery=json.loads(result.stdout);write(OUT/'recovery.json',recovery)
            record(state,phase='completed' if recovery['cleanupSucceeded'] else 'recovery_needs_review',recovery=recovery)
            report();return
        time.sleep(5)
    raise RuntimeError('Recovery did not finish within ten minutes; screen guard remains active.')

def run(resume=False):
    lock=OUT/'controller.lock';lock.mkdir()
    state=read(OUT/'session-state.json')
    try:
        if os.name=='nt':
            import ctypes
            assert ctypes.windll.kernel32.SetThreadExecutionState(0x80000001), 'Cannot prevent host sleep.'
        if resume:
            assert state['phase']=='needs_review'
            assert not any(s['status'] in ['launching','running'] for s in state['stages']), 'Reconcile existing device processes first.'
            resolution=read(OUT/'review-resolution.json')
            stage=next(s for s in state['stages'] if s['name']==state['reviewStage'])
            assert resolution['reviewStage']==stage['name'] and resolution['auditSha256']==sha(Path(stage['plan']).parent/'audit.json')
            assert resolution['continue'] is True and resolution['cause'] and resolution['action']
            assert sha(OUT/'review-resolution.json')!=state.get('resolutionAppliedSha256')
            record(state,resolutionAppliedSha256=sha(OUT/'review-resolution.json'),reviewResolution=resolution)
            assert verify_process(GUARD+'/guard-v2-process-stat.txt')==state['guardPid']
        else:
            assert state['phase']=='frozen','Do not relaunch a started session.'
        record(state,controllerPid=os.getpid(),controllerStartedUtc=utc())
        if not resume:deploy_guard(state)
        for stage in state['stages']:
            if stage['status']!='pending':continue
            remaining=(DEADLINE-dt.datetime.now(dt.timezone.utc)).total_seconds()
            if not stage['preflight'] and remaining<stage['nominalSeconds']+120:
                stage.update(status='skipped',skipReason='insufficient_time_for_complete_balanced_phase')
                record(state,phase='phase_skipped',lastSkippedStage=stage['name']);continue
            if (OUT/'pause-before-next-phase').exists():
                record(state,phase='needs_review',reason='Local pause requested; screen remains awake.');return
            launch_stage(state,stage);wait_stage(state,stage);audit=collect_stage(state,stage)
            if audit['exit'].get('stopReason') in ['battery_below_start_threshold','night_deadline','battery_below_10_after_leg']:
                record(state,phase='ending_early',reason=audit['exit']['stopReason']);break
            passed=(stage['preflightPassed']==len(audit['audit'])) if stage['preflight'] else (
                audit['exit'].get('cleanupSucceeded') is True and audit['exit'].get('stopReason')=='manifest_completed')
            severe=[x for x in audit['audit'] if x.get('failure') or x.get('checks',{}).get('completed') is False]
            if not passed or severe or (not stage['preflight'] and audit['qualifiedRows']<len(audit['audit'])-1):
                record(state,phase='needs_review',reason='Playback or audit failure: automatic thread review must diagnose before continuing.',
                       reviewStage=stage['name']);return
        request_recovery(state)
    except BaseException as failure:
        record(state,phase='needs_review',controllerError=repr(failure))
        raise
    finally:
        if os.name=='nt':
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        lock.rmdir()

def report():
    state=read(OUT/'session-state.json');runs=[];blocks=[]
    for stage in state['stages']:
        path=Path(stage['plan']).parent/'audit.json'
        if path.exists():
            audit=read(path);runs+=audit['runs'];blocks+=audit.get('controlledConditionBlocks',[])
    groups={}
    for block in blocks:
        if block['complete']:
            key=(block['fixture'],block['mode'],block['measurementCohort'],block['a'],block['b'])
            groups.setdefault(key,[]).append(block)
    comparisons=[]
    critical={1:12.706,2:4.303,3:3.182,4:2.776,5:2.571,6:2.447,7:2.365}
    for key,members in groups.items():
        values=[b['aMinusBMw'] for b in members];n=len(values);mean=statistics.mean(values)
        sd=statistics.stdev(values) if n>1 else None
        margin=critical.get(n-1,2.306)*sd/(n**.5) if sd is not None else None
        comparisons.append(dict(fixture=key[0],mode=key[1],cohort=key[2],a=key[3],b=key[4],blocks=n,
            meanAMinusBMw=mean,blockDifferenceStdevMw=sd,descriptiveT95IntervalMw=[mean-margin,mean+margin] if margin is not None else None,
            note='Block differences are the replicate unit; small-n t interval is descriptive and assumes independent blocks. One-second samples are not replicates.',
            conditionMeanPowerMw={c:statistics.mean(x['conditionMeanPowerMw'][c] for x in members) for c in [key[3],key[4]]}))
    result=dict(updatedUtc=utc(),phase=state['phase'],hardwarePriority=True,stages=state['stages'],
        qualifiedRuns=sum(bool(r['qualified']) for r in runs),completeBlocks=sum(b['complete'] for b in blocks),comparisons=comparisons,
        limitation='Same new APK, renderer comparison only; cannot isolate dependency-upgrade effects. Battery/temperature strata are not pooled.',
        screenPolicy='Keep awake through preparation, measurement, repair and recovery until user returns.')
    write(OUT/'progress-report.json',result)
    lines=['# 2026-10-18 播放功耗实验阶段记录','',f'更新：{result["updatedUtc"]}；状态：{state["phase"]}',
        '',f'合格正式轮次：{result["qualifiedRuns"]}；完整平衡块：{result["completeBlocks"]}。硬解优先，软解回退不纳入硬解结果。',
        '', '| 阶段 | 状态 | 合格轮次 |','|---|---|---|']
    for s in state['stages']:lines.append(f'| {s["name"]} | {s["status"]} | {s.get("qualifiedRows", "—")} |')
    for c in comparisons:
        lines+=['',f'{c["fixture"]} / {c["mode"]}：{c["a"]} − {c["b"]} = {c["meanAMinusBMw"]:.1f} mW（{c["blocks"]} 个完整块）。']
        if c['a']=='libmpv@gpu' and c['b']=='libmpv@gpu-next':
            lines.append('正值表示 gpu-next 的实测平均整机功耗较低；负值表示较高。')
    lines+=['','统计单位为完整 ABBA/BAAB 块。失败与缺失轮次保留；温度、电量和播放质量仍需结合原始审计解释。',
        '这是同一新 APK 的渲染器比较，不能归因成依赖升级本身的收益。屏幕在结束及修复期间继续保持常亮。']
    put_text(OUT/'progress-report.md','\n'.join(lines)+'\n')
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['freeze','run','resume','report','recover'])
    action=parser.parse_args().action
    if action=='freeze':freeze()
    elif action=='run':run()
    elif action=='resume':run(resume=True)
    elif action=='recover':request_recovery(read(OUT/'session-state.json'))
    else:report()

if __name__=='__main__':main()
