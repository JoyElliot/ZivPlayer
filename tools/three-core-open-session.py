#!/usr/bin/env python3
"""Hardware three-core repeat experiment, 2026-10-25; open-ended Windows controller."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

ROOT = Path(os.environ.get('ZIV_REPO_ROOT', str(Path(__file__).resolve().parents[1])))
OUT = ROOT/'native/out/power-threecore-20261025'
PARENT = '/data/local/tmp/ziv-power-20261025'
SERIAL = json.loads((OUT/'connection.json').read_text(encoding='utf-8-sig'))['serial'] if (OUT/'connection.json').exists() else ''
SOURCE_FROZEN = ROOT/'native/out/power-threecore-20261023/frozen-tools'
ADB = r'D:\DevTools\Android\Sdk\platform-tools\adb.exe'
DEADLINE = None
RECOVERY = None
USER_DEADLINE = None
PACKAGE = 'io.github.joyelliot.zivplayer'
FROZEN = OUT/'frozen-tools'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def night_module():
    source = FROZEN if (FROZEN/'renderer-night-session.py').exists() else SOURCE_FROZEN
    n = load('night', source/'renderer-night-session.py')
    n.ROOT, n.OUT, n.ADB, n.SERIAL = ROOT, OUT, ADB, SERIAL
    n.PARENT, n.GUARD = PARENT, PARENT+'/night-guard'
    n.DEADLINE, n.RECOVERY_DEADLINE = DEADLINE, RECOVERY
    n.report = report
    def ascii_write(path, value):
        path=Path(path); temp=path.with_suffix(path.suffix+'.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=True,indent=2)+'\n',encoding='utf8'); temp.replace(path)
    n.write = ascii_write
    def record(state,**changes):
        state.update(changes,updatedUtc=n.utc()); n.write(OUT/'session-state.json',state)
        with (OUT/'controller-events.jsonl').open('a',encoding='utf8') as stream:
            stream.write(json.dumps(dict(utc=n.utc(),**changes),ensure_ascii=True)+'\n')
        print(json.dumps(changes,ensure_ascii=True),flush=True)
    n.record=record
    original_verify = n.verify_process
    def verify_owned_process(path):
        pid = original_verify(path)
        directory = path.rsplit('/', 1)[0]
        script = 'guard.sh' if directory.endswith(('/night-guard','/awake-guard')) else 'runner.sh'
        command = n.adb('shell', f'tr "\\000" " " < /proc/{pid}/cmdline').decode()
        uid = n.adb('shell', f"awk '/^Uid:/ {{print $2}}' /proc/{pid}/status").decode().strip()
        assert uid == '2000' and command == f'sh {directory}/{script} {directory} ', (pid,uid,command)
        return pid
    n.verify_process = verify_owned_process
    upload = n.upload
    def upload_or_copy_original(path, remote):
        # A verified host backup already exists. Copy the still-installed original
        # APK locally before replacement, avoiding a second large Wi-Fi transfer.
        baseline_path = OUT/'device-baseline.json'
        if baseline_path.exists() and remote in [n.GUARD+'/original-main.apk', n.GUARD+'/original-test.apk']:
            baseline = n.read(baseline_path)
            key = 'main' if remote.endswith('/original-main.apk') else 'test'
            info = baseline['packages'][key]
            assert n.sha(path)==info['sha256']
            try:
                actual = n.adb('shell', 'sha256sum', info['devicePath']).decode().split()[0]
                if actual==info['sha256']:
                    n.adb('shell', 'cp', info['devicePath'], remote, timeout=60)
                    assert n.adb('shell', 'sha256sum', remote).decode().split()[0]==actual
                    return
            except subprocess.CalledProcessError:
                pass
        upload(path, remote)
    n.upload = upload_or_copy_original
    return n


def launch_stage(n, state, stage):
    plan = n.read(stage['plan']); folder = Path(stage['plan']).parent
    remote = plan['remoteDirectory']
    assert n.sha(stage['plan']) == stage['planSha256']
    assert n.adb('shell','cat',n.GUARD+'/owner.txt').decode().strip() == state['owner']
    n.verify_process(n.GUARD+'/guard-v2-process-stat.txt')
    assert n.package_sha(PACKAGE) == plan['mainApkSha256']
    assert n.package_sha(PACKAGE+'.test') == plan['testApkSha256']
    power=n.adb('shell','dumpsys','power').decode()
    policy=n.adb('shell','dumpsys','window','policy').decode()
    assert re.search(r'^\s*mWakefulness=Awake\s*$',power,re.M)
    assert re.search(r'^\s*showing=false\s*$',policy,re.M)
    assert re.search(r'^\s*mInputRestricted=false\s*$',policy,re.M)
    n.write(folder/'screen-before-launch.json',dict(utc=n.utc(),power=power,policy=policy))
    (folder/'processes-before.txt').write_bytes(n.adb('shell','ps -A -o PID,UID,NAME,ARGS'))
    n.adb('shell','mkdir',remote)
    for name,digest in plan['frozenFiles'].items():
        assert n.sha(folder/name)==digest
        n.upload(folder/name,remote+'/'+name)
    n.upload(folder/'run-plan.json',remote+'/run-plan.json')
    n.adb('shell','sh','-n',remote+'/runner.sh')
    stage['status']='launching'
    n.record(state,phase='launching_stage',activeStage=stage['name'],activePlan=stage['plan'])
    # This short device-local transaction survives host disconnect. Recovery first
    # publishes intent, then takes the same lock before reading the active pointer.
    script=f'''set -eu
j=0
cat /proc/$$/stat > {remote}/launch-process-stat.txt
until ln -s {remote}/launch-process-stat.txt {PARENT}/transition.lock 2>/dev/null; do j=$((j+1)); test "$j" -lt 120; sleep 1; done
trap 'rm -f {PARENT}/transition.lock' EXIT
test ! -e {n.GUARD}/recovery-intent
test ! -d {n.GUARD}/recovery-intent.lock
test ! -e {PARENT}/stop-request.txt
printf '%s\\n' {shlex.quote(remote)} > {n.GUARD}/active-run.next
mv {n.GUARD}/active-run.next {n.GUARD}/active-run.txt
cp {n.GUARD}/active-run.txt {PARENT}/awake-guard/active-run.txt
/system/bin/toybox nohup /system/bin/setsid sh {remote}/runner.sh {remote} </dev/null >{remote}/runner.stdout 2>&1 &
i=0
while test ! -f {remote}/started.json; do
 i=$((i+1)); test "$i" -lt 30; sleep 1
done
cat {remote}/started.json
'''
    n.put_text(folder/'launch.sh',script)
    n.upload(folder/'launch.sh',remote+'/launch.sh')
    started=json.loads(n.adb('shell','sh',remote+'/launch.sh',timeout=170))
    assert n.verify_process(remote+'/runner-process-stat.txt')==started['pid']
    stage.update(status='running',startedUtc=started['startedUtc'])
    n.record(state,phase='running_stage',runnerPid=started['pid'],samplingObserved=False)


def stop():
    n=night_module(); state=n.read(OUT/'session-state.json')
    if state['phase']=='completed':
        print('Session already restored and completed.'); return
    assert n.adb('shell','cat',n.GUARD+'/owner.txt').decode().strip()==state['owner']
    pid=n.verify_process(n.GUARD+'/guard-v2-process-stat.txt')
    assert pid==state['guardPid']
    n.write(OUT/'stop-request.json',dict(requestedUtc=n.utc(),owner=state['owner'],reason='user_requested'))
    n.adb('shell',f'printf user_requested > {PARENT}/stop-request.txt; kill -USR1 {pid}')
    print('Owned session stop requested; runner cancellation and original-state recovery will follow.')


def owner_state(text):
    packages = text.split('Packages:', 1)[1]
    line = re.search(r'(?m)^\s*User 0:([^\r\n]*\binstalled=true[^\r\n]*)', packages)
    assert line, 'Installed user-0 package record missing'
    return {k: v for k, v in re.findall(r'\b(enabled|stopped|suspended)=([^ ]+)', line[1])}


def capture_baseline(n, prep, settings):
    assert not (OUT/'device-baseline.json').exists(), 'Do not recapture an established baseline.'
    backups=OUT/'backups'; backups.mkdir(exist_ok=True)
    baseline=dict(capturedUtc=n.utc(),serial=SERIAL,
        settingsBefore={k:v for k,v in settings.items() if k not in ['capturedUtc','serial']},packages={})
    for key,package in [('main',PACKAGE),('test',PACKAGE+'.test')]:
        info=prep.installed(package); path=backups/('original-'+key+'.apk')
        if path.exists():
            assert n.sha(path)==info['sha256'], 'Partial preparation APK backup no longer matches installation.'
        else:
            with path.open('xb') as stream:
                subprocess.run([ADB,'-s',SERIAL,'exec-out','cat',info['devicePath']],stdout=stream,check=True,timeout=300)
        assert n.sha(path)==info['sha256']
        info['backupPath']=str(path);baseline['packages'][key]=info
    n.adb('shell','am','force-stop',PACKAGE)
    candidates=['databases','files/datastore','files/mpv','files/playback-resources','app_vlc']
    present=[]
    for name in candidates:
        command='test -e /data/user/0/'+PACKAGE+'/'+name+' && printf EXISTS'
        value=n.adb('shell','su -c '+shlex.quote(command),timeout=20).decode().strip()
        if value=='EXISTS':present.append(name)
    assert 'databases' in present and present, 'Expected user-data directories are absent.'
    target=backups/'user-data-before.tar'
    if target.exists():
        # Preserve failed legacy run-as output; it is not a valid user-data backup.
        index=1
        while (backups/f'user-data-backup-failed-r{index}.tar').exists():index+=1
        target.rename(backups/f'user-data-backup-failed-r{index}.tar')
    command='cd /data/user/0/'+PACKAGE+' && tar -cf - '+' '.join(map(shlex.quote,present))
    with target.open('xb') as stream:
        result=subprocess.run([ADB,'-s',SERIAL,'exec-out','su','-c',command],stdout=stream,stderr=subprocess.PIPE,timeout=180)
    (OUT/'user-data-backup.stderr.log').write_bytes(result.stderr)
    assert result.returncode==0
    with tarfile.open(target) as archive:assert archive.getmembers()
    baseline['userDataBackup']=dict(path=str(target),sha256=n.sha(target),directories=present,method='root-readonly-tar',restorePolicy='Backup only; never overwrite current user data.')
    volume=n.adb('shell','cmd','media_session','volume','--stream','3','--get').decode()
    baseline['musicVolume']=int(re.search(r'volume is (\d+) in range',volume)[1])
    audio=n.adb('shell','dumpsys','audio').decode(errors='replace')
    section=audio.split('- STREAM_MUSIC:',1)[1].split('- STREAM_',1)[0]
    baseline['musicMuted']='Muted: true' in section
    n.write(OUT/'device-baseline.json',baseline)
    print('Fresh original APK, user-data and settings backups verified.',flush=True)
    return baseline


def definitions():
    # Remaining nine direct pairs; prior cohorts remain separate evidence.
    groups = [
        ('n25hevcme', 'hevc', ('libmpv@gpu-next', 'exo')),
        ('n25hevc10ev', 'hevc10', ('exo', 'vlc')),
        ('n25avcev', 'avc', ('exo', 'vlc')),
        ('n25vp9ev', 'vp9', ('exo', 'vlc')),
        ('n25hevcev', 'hevc', ('exo', 'vlc')),
        ('n25hevc10me', 'hevc10', ('libmpv@gpu-next', 'exo')),
        ('n25avcmv', 'avc', ('libmpv@gpu-next', 'vlc')),
        ('n25hevcmv', 'hevc', ('libmpv@gpu-next', 'vlc')),
        ('n25vp9mv', 'vp9', ('libmpv@gpu-next', 'vlc')),
    ]
    result, checked = [], set()
    for name, codec, pair in groups:
        if codec not in checked:
            result.append(('n25'+codec+'check', codec, None, True, False))
            checked.add(codec)
        result.extend([(name+'a', codec, pair, False, False),
                       (name+'b', codec, pair, False, True)])
    return result


def legs_for(name, codec, pair, preflight, reverse=False):
    order = ['libmpv@gpu-next', 'exo', 'vlc'] if preflight else (
        [pair[1], pair[0], pair[0], pair[1]] if reverse else
        [pair[0], pair[1], pair[1], pair[0]])
    return [dict(ordinal=i, blockNumber=1, comparisonBlock=f'{name}:{codec}:b1',
        plannedOrdinal=i, codec=codec, decoderMode='automatic', engine=c.partition('@')[0],
        label=f'{name}-b1-{i}-{c.partition("@")[0]}-gpu-next',
        purpose='validation-only' if preflight else 'matrix', mpvVideoOutput='gpu-next',
        plannedOrder=[x.partition('@')[0] for x in order],
        plannedConditions=[] if preflight else order, validationOnly=preflight,
        warmupSeconds=15 if preflight else 90, measurementSeconds=65 if preflight else 240)
        for i, c in enumerate(order, 1)]


def report():
    source = FROZEN if (FROZEN/'report-renderer-night.py').exists() else SOURCE_FROZEN
    r = load('strict_report', source/'report-renderer-night.py')
    r.OUT = OUT
    r.main()
    n = load('report_io', source/'renderer-night-session.py')
    state = n.read(OUT/'session-state.json')
    data = n.read(OUT/'stratified-report.json')
    data['limitation'] = ('Same frozen APK, actual hardware three-core comparison; '
        'not dependency-upgrade benefit. Screen is held awake and selected background packages '
        'are controlled; their effects and residual background activity are unquantified, '
        'so whole-device watts are not normal-use player-only power. No assumed correction. '
        'Small block counts; power does not establish visual quality.')
    limitations = OUT/'measurement-limitations.json'
    if limitations.exists():
        data['measurementLimitations'] = n.read(limitations)
    n.write(OUT/'stratified-report.json', data)
    n.write(OUT/'progress-report.json', dict(updatedUtc=n.utc(), phase=state['phase'],
        activeStage=state.get('activeStage'), qualifiedRuns=data['qualifiedFormalRuns'],
        completeBlocks=sum(b['complete'] for b in data['blocks']), stages=state['stages']))
    text = (OUT/'stratified-report.md').read_text(encoding='utf8')
    text = text.replace('同一新 APK 的渲染器比较', '本轮同一 APK 的播放核心比较')
    (OUT/'stratified-report.md').write_text(text, encoding='utf8')
    return data


def prepare():
    assert os.name == 'nt', 'Use Windows Python so ADB paths and archived plan paths stay consistent.'
    assert not (OUT/'session-state.json').exists(), 'Inspect existing session; do not repeat preparation.'
    n = night_module()
    assert SERIAL, 'A freshly verified connection.json is required.'
    assert n.adb('shell','getprop','ro.serialno').decode().strip() == '***'
    assert n.adb('shell','getprop','ro.product.device').decode().strip() == 'marble'
    n.adb('shell','test ! -e /data/local/tmp/ziv-power-exclusive.lock')
    processes=n.adb('shell','ps -A -o PID,UID,NAME,ARGS').decode(errors='replace')
    n.put_text(OUT/'startup-processes-before.txt',processes)
    assert not re.search(r'sh /data/local/tmp/ziv-power-[0-9]{8}/[^\r\n]*(?:guard|runner)\.sh',processes), 'Existing device experiment process requires identity review.'
    n.adb('shell','mkdir','-p',PARENT)
    prep = load('prepare_baseline', ROOT/'tools/prepare-renderer-night.py')
    prep.ROOT, prep.OUT, prep.ADB, prep.SERIAL = ROOT, OUT, ADB, SERIAL
    settings = dict(capturedUtc=n.utc(), serial=SERIAL)
    for key in ['screen_off_timeout','screen_brightness','screen_brightness_mode','bluetooth_on','airplane_mode_on','wifi_on','stay_on_while_plugged_in']:
        settings[key] = n.adb('shell','settings','get','system' if key in ['screen_off_timeout','screen_brightness','screen_brightness_mode'] else 'global',key).decode().strip()
    battery = n.adb('shell','dumpsys','battery').decode()
    n.put_text(OUT/'preparation-battery-before.txt',battery)
    assert not re.search(r'(?:AC|USB|Wireless|Dock) powered: true',battery)
    assert int(re.search(r'level: (\d+)',battery)[1]) >= 15
    assert n.adb('shell','su','-c','id').decode().startswith('uid=0')
    if (OUT/'preparation-settings-before.json').exists():
        previous=n.read(OUT/'preparation-settings-before.json')
        assert all(settings[k]==v for k,v in previous.items() if k not in ['capturedUtc','serial']), 'Original settings changed during partial preparation.'
        settings=previous
    else:
        n.write(OUT/'preparation-settings-before.json',settings)
    prep.save = lambda name,value:n.write(OUT/name,value)
    baseline = capture_baseline(n,prep,settings)
    artifacts = n.read(ROOT/'native/out/dependency-upgrade-20261017/artifact-manifest.json')['branches']['power']['apks']
    for info in artifacts.values():
        info['path'] = str(ROOT/info['path'].split('/zivplayer/', 1)[1])
        assert n.sha(info['path']) == info['sha256']
    n.write(OUT/'experiment-apks.json', artifacts)
    FROZEN.mkdir()
    for name in ['phone-power-stable-runner.sh', 'power-night-open-guard.sh', 'phone-awake-open-guard.sh',
                 'renderer-night-session.py', 'three-core-open-session.py', 'finalize-three-core-open.py', 'report-renderer-night.py',
                 'collect-power-night.py', 'summarize-power-benchmark.py']:
        shutil.copyfile((ROOT/'tools'/name) if name in ['three-core-open-session.py','finalize-three-core-open.py','phone-power-stable-runner.sh','power-night-open-guard.sh','phone-awake-open-guard.sh'] else SOURCE_FROZEN/name, FROZEN/name)
    collector=FROZEN/'collect-power-night.py'
    collector.write_text(collector.read_text(encoding='utf8').replace("guard_exit.get('action') == 'deadline_recovery'", "guard_exit.get('action') in ['deadline_recovery','session_recovery']"),encoding='utf8')
    shutil.copyfile(SOURCE_FROZEN/'native-source-manifest.toml', FROZEN/'native-source-manifest.toml')
    shutil.copyfile(ROOT/'native/source-manifest.toml',FROZEN/'current-working-native-source-manifest.toml')
    for name in ['PowerPlaybackBenchmarkTest.kt', 'PowerCodecLogCapture.kt']:
        shutil.copyfile(ROOT/'native/out/power-renderer-20261018/frozen-tools'/name, FROZEN/name)
    n.write(OUT/'artifact-provenance.json', dict(artifacts=artifacts,
        measurementSource='20261018 frozen instrument sources; unchanged frozen APK pair',
        nativeManifestSource='20261022 frozen snapshot', currentWorkingTreeIsMeasurementSource=False,
        runnerSha256=n.sha(FROZEN/'phone-power-stable-runner.sh')))
    (FROZEN/'current-source-diff.patch').write_bytes(subprocess.check_output(['git', 'diff', '--binary'], cwd=ROOT))
    background = OUT/'background'; background.mkdir()
    packages = ['app-07-***', 'app-08-***', 'app-02-***', 'app-09-***',
                'app-01-***', 'app-05-***', 'app-10-***', 'app-03-***', 'app-04-***', 'app-06-***']
    processes=n.adb('shell','ps','-A','-o','ARGS').decode(errors='replace').splitlines()
    (OUT/'processes-before-background-control.txt').write_text('\n'.join(processes),encoding='utf8')
    third_party=n.adb('shell','pm','list','packages','-3').decode().splitlines()
    ime=n.adb('shell','settings','get','secure','default_input_method').decode().strip().split('/')[0]
    protected={PACKAGE,PACKAGE+'.test',ime,'com.topjohnwu.magisk','me.weishu.kernelsu','com.rifsxd.ksunext','com.android.deskclock','com.baidu.input_mi'}
    packages += [p.removeprefix('package:').strip() for p in third_party
        if p.removeprefix('package:').strip() not in protected and
        any(x.strip()==p.removeprefix('package:').strip() or x.strip().startswith(p.removeprefix('package:').strip()+':') for x in processes)]
    packages=sorted(set(packages)-protected)
    n.write(OUT/'background-policy.json',dict(selected=packages,protected=sorted(protected),policy='Suspend selected fixed packages and currently running third-party packages; preserve every original enabled/suspended/stopped state. No clock/input/root services or frequency/thermal controls.'))
    records = []
    for package in packages:
        text = n.adb('shell', 'dumpsys', 'package', package).decode(errors='replace')
        (background/(package+'-before.txt')).write_text(text, encoding='utf8')
        fields = owner_state(text)
        records.append(dict(package=package, before=fields,
            suspend=fields['suspended']=='false' and package!='app-05-***', unstop=fields['stopped']=='false',
            disable=package in ['app-10-***', 'app-04-***', 'app-06-***', 'app-05-***', 'app-07-***']
                    and fields['enabled'] in ['0', '1']))
    n.write(OUT/'background-control.json', dict(packages=records))
    guard = OUT/'night-guard'; guard.mkdir()
    shutil.copyfile(FROZEN/'power-night-open-guard.sh', guard/'guard.sh')
    for file, lines in [
        ('background-packages.txt', [x['package'] for x in records if x['suspend']]),
        ('stopped-packages.txt', [x['package'] for x in records if x['unstop']]),
        ('enabled-packages.tsv', [x['package']+'\t'+x['before']['enabled'] for x in records if x['disable']])]:
        n.put_text(guard/file, ''.join(x+'\n' for x in lines))
    owner = uuid.uuid4().hex
    n.put_text(guard/'owner.txt', owner+'\n')
    n.adb('shell',f'mkdir /data/local/tmp/ziv-power-exclusive.lock && printf %s {shlex.quote(owner)} > /data/local/tmp/ziv-power-exclusive.lock/owner.txt')
    settings = baseline['settingsBefore']
    n.env(guard/'config.env', dict(HARD_STOP_EPOCH=0, IDLE_RECOVERY_SECONDS=1800,
        ORIGINAL_TIMEOUT=settings['screen_off_timeout'], RESTORE_ORIGINAL_TIMEOUT='true', AWAKE_GUARD_OWNER='***',
        ORIGINAL_BT=settings['bluetooth_on'], ORIGINAL_BRIGHTNESS=settings['screen_brightness'],
        ORIGINAL_BRIGHTNESS_MODE=settings['screen_brightness_mode'], ORIGINAL_TEST_SHA=baseline['packages']['test']['sha256'],
        MAIN_SHA=artifacts['app']['sha256'], RESTORE_MAIN_APK=n.GUARD+'/original-main.apk',
        RESTORE_MAIN_SHA=baseline['packages']['main']['sha256'], KEEP_AWAKE_ON_EXIT='true',
        REQUIRE_FULL_RECOVERY='true', REQUIRE_BACKGROUND_JOURNALS='true', SESSION_OWNER=owner,
        ORIGINAL_MUSIC_VOLUME=baseline['musicVolume'], ORIGINAL_MUSIC_MUTED=str(baseline['musicMuted']).lower()))
    state = dict(version=1, phase='preparing', serial=SERIAL, owner=owner, stages=[], guardRevision=3,
        deadlineLocal=None, recoveryDeadlineLocal=None, userDeadlineLocal=None, openEnded=True, awakeOwner='***',
        keepAwakeThroughRecovery=True, restoreOriginalTimeoutOnRecovery=True, baselineSha256=n.sha(OUT/'device-baseline.json'),
        guardFiles={p.name:n.sha(p) for p in guard.iterdir()},
        frozenTools={p.name:n.sha(p) for p in FROZEN.iterdir()},
        controllerLogFiles=['controller.stdout.log', 'controller.stderr.log'])
    n.record(state, phase='arming_recovery')
    # Arm independent restoration BEFORE changing APKs, app states or display/audio settings.
    n.deploy_guard(state)
    awake=OUT/'awake-guard'; awake.mkdir()
    shutil.copyfile(FROZEN/'phone-awake-open-guard.sh',awake/'guard.sh')
    n.env(awake/'config.env',dict(HARD_STOP_EPOCH=0,ORIGINAL_TIMEOUT=settings['screen_off_timeout']))
    n.put_text(awake/'owner.txt',state['awakeOwner']+'\n')
    n.adb('shell','mkdir',PARENT+'/awake-guard')
    for name in ['guard.sh','config.env','owner.txt']:
        n.upload(awake/name,PARENT+'/awake-guard/'+name)
    n.adb('shell',f'/system/bin/toybox nohup /system/bin/setsid sh {PARENT}/awake-guard/guard.sh {PARENT}/awake-guard </dev/null >{PARENT}/awake-guard/guard.log 2>&1 &')
    ready=n.wait_json(PARENT+'/awake-guard/ready.json')
    n.verify_process(PARENT+'/awake-guard/process-stat.txt')
    n.record(state,phase='awake_ready',awakePid=ready['pid'])
    n.adb('shell','input','keyevent','224')
    n.adb('shell','wm','dismiss-keyguard')
    for item in records:
        package = item['package']
        if item['suspend']: n.adb('shell', 'su -c '+shlex.quote('cmd package suspend --user 0 '+package))
        if item['disable']: n.adb('shell', 'su -c '+shlex.quote('cmd package disable-user --user 0 '+package))
        n.adb('shell', 'su -c '+shlex.quote('am force-stop --user 0 '+package))
        after = n.adb('shell', 'dumpsys', 'package', package).decode(errors='replace')
        (background/(package+'-after.txt')).write_text(after, encoding='utf8')
        fields = owner_state(after)
        assert not item['suspend'] or fields['suspended']=='true', package
        assert not item['disable'] or fields['enabled']=='3', package
    finish_preparation(n, prep, state, artifacts)


def install_artifact(n, kind, info):
    assert n.sha(info['path'])==info['sha256']
    result=subprocess.run([ADB,'-s',SERIAL,'install','-r','-t','-d',info['path']],capture_output=True,timeout=420)
    (OUT/('install-'+kind+'.log')).write_bytes(result.stdout+result.stderr)
    if result.returncode==0 and b'Success' in result.stdout:return
    assert b'INSTALL_FAILED_VERSION_DOWNGRADE' in result.stdout+result.stderr, 'Unexpected install failure; original data retained.'
    remote=PARENT+'/experiment-'+kind+'.apk'
    n.upload(info['path'],remote)
    assert n.adb('shell','sha256sum',remote).decode().split()[0]==info['sha256']
    output=n.adb('shell','su -c '+shlex.quote('/system/bin/toybox timeout -k 5 180 pm install -r -t -d '+remote),timeout=220)
    (OUT/('root-install-'+kind+'.log')).write_bytes(output)
    assert output.strip()==b'Success', 'Root package-manager install did not succeed.'


def finish_preparation(n, prep, state, artifacts):
    for kind, package in [('app', PACKAGE), ('androidTest', PACKAGE+'.test')]:
        if n.package_sha(package) == artifacts[kind]['sha256']: continue
        install_artifact(n,kind,artifacts[kind])
        assert n.package_sha(package) == artifacts[kind]['sha256']
    fixtures = []
    n.adb('shell', 'run-as', PACKAGE, 'mkdir', '-p', 'files/power-fixtures')
    for codec in ['avc', 'hevc', 'hevc10', 'vp9']:
        path = ROOT/'native/out/power-fixtures'/(codec+'-1080p24.mkv')
        device = 'files/power-fixtures/'+path.name; expected = n.sha(path)
        probe = subprocess.run([ADB, '-s', SERIAL, 'shell', 'run-as', PACKAGE, 'sha256sum', device], capture_output=True, timeout=90)
        if probe.returncode or probe.stdout.decode().split()[0] != expected:
            temporary = PARENT+'/'+path.name
            n.upload(path, temporary); n.adb('shell', 'run-as', PACKAGE, 'cp', temporary, device, timeout=90)
        actual = n.adb('shell', 'run-as', PACKAGE, 'sha256sum', device, timeout=90).decode().split()[0]
        assert actual == expected
        fixtures.append(dict(file=path.name, sha256=expected, bytes=path.stat().st_size, devicePath=device))
    n.write(OUT/'persistent-fixtures.json', dict(fixtures=fixtures))
    n.adb('shell', 'cmd', 'bluetooth_manager', 'disable')
    n.adb('shell', 'settings', 'put', 'system', 'screen_brightness_mode', '0')
    n.adb('shell', 'cmd', 'display', 'set-brightness', '0')
    n.adb('shell', 'cmd', 'media_session', 'volume', '--stream', '3', '--set', '0')
    assert n.adb('shell', 'settings', 'get', 'global', 'airplane_mode_on').decode().strip() == '1'
    assert n.adb('shell', 'settings', 'get', 'global', 'wifi_on').decode().strip() in ['1', '2']
    assert n.adb('shell', 'settings', 'get', 'system', 'screen_brightness').decode().strip() == '1'
    n.write(OUT/'prepared.json', dict(readyUtc=n.utc(), main=prep.installed(PACKAGE), test=prep.installed(PACKAGE+'.test')))
    freeze(state, artifacts, fixtures)


def freeze(state, artifacts, fixtures):
    n = night_module()
    for name, codec, pair, preflight, reverse in definitions():
        folder = OUT/name; folder.mkdir()
        legs = legs_for(name, codec, pair, preflight, reverse)
        keys = ['ordinal', 'blockNumber', 'comparisonBlock', 'plannedOrdinal', 'codec', 'decoderMode', 'engine', 'label', 'purpose', 'mpvVideoOutput']
        n.put_text(folder/'manifest.tsv', ''.join('\t'.join(str(leg[k]) for k in keys)+'\n' for leg in legs))
        n.put_text(folder/'exo-reference.tsv', '')
        shutil.copyfile(FROZEN/'phone-power-stable-runner.sh', folder/'runner.sh')
        config = dict(MANIFEST_SHA=n.sha(folder/'manifest.tsv'), MANIFEST_ROWS=len(legs),
            MAIN_SHA=artifacts['app']['sha256'], LEGACY_TEST_SHA=artifacts['androidTest']['sha256'],
            LOW_TEST_SHA=artifacts['androidTest']['sha256'], EXPECTED_INITIAL_TEST_SHA=artifacts['androidTest']['sha256'],
            LEGACY_TEST_APK=n.GUARD+'/power-test.apk', LOW_TEST_APK=n.GUARD+'/power-test.apk', RESTORE_TEST_APK='',
            COHORT='threecore-20261025-'+artifacts['app']['sha256'][:12]+'-'+artifacts['androidTest']['sha256'][:12],
            MPV_VIDEO_OUTPUT='gpu-next', VALIDATION_ONLY=str(preflight).lower(), SLEEP_AT_END='false',
            KEEP_AWAKE_ON_EXIT='true', HOLD_FOR_NEXT_STAGE='true', RESTORE_SCREEN_TIMEOUT=86400000,
            RESTORE_BLUETOOTH=0, MINIMUM_COOLDOWN=60, FIRST_COOLDOWN_SECONDS=180, COOLDOWN_STABLE_POLLS=3, COOLDOWN_POLL_SECONDS=15,
            COOLDOWN_REQUIRE_NONRISING='true', COOLDOWN_INPUT_REFRESH='false', MAX_START_TEMPERATURE_TENTHS=350,
            MINIMUM_START_BATTERY=15, MINIMUM_SAMPLE_BATTERY=10, WARMUP_SECONDS=15 if preflight else 90,
            MEASURE_SECONDS=65 if preflight else 240, SURFACE_PROTOCOL='current-app', FRAME_PROOF_METHOD='display-crop-v1',
            HARDWARE_ONLY_AUTOMATIC='true', SOFTWARE_ONLY='false', SCREEN_TIMEOUT_MS=86400000,
            HARD_STOP_EPOCH=0, OPTIONAL_BLOCK_BUDGET_SECONDS=1800)
        n.env(folder/'config.env', config)
        plan = dict(schemaVersion=4, buildIdentityProtocol=1, serial=SERIAL, remoteDirectory=PARENT+'/'+name,
            measurementCohort=config['COHORT'], mainApkSha256=config['MAIN_SHA'], testApkSha256=config['LEGACY_TEST_SHA'],
            mpvVideoOutput='gpu-next', legs=legs, fixtures=fixtures, frameProofMethod='display-crop-v1', surfaceProtocol='current-app',
            warmupSeconds=config['WARMUP_SECONDS'], measurementSeconds=config['MEASURE_SECONDS'], minimumCooldownSeconds=60, minimumStartBatteryPercent=15, minimumSampleBatteryPercent=10, stableNonrisingPolls=3,
            maxStartTemperatureTenthsC=350, hardStopLocal=None, hardwareRequired=True, frozenUtc=n.utc(),
            observer='Single passive ADB observer; no extra device queries/transfers during sampling.',
            frozenFiles={x:n.sha(folder/x) for x in ['manifest.tsv', 'config.env', 'runner.sh', 'exo-reference.tsv']})
        n.write(folder/'run-plan.json', plan)
        state['stages'].append(dict(name=name, plan=str(folder/'run-plan.json'), planSha256=n.sha(folder/'run-plan.json'),
            status='pending', preflight=preflight, codec=codec, optional=name.endswith('extra'),
            nominalSeconds=1800 if not preflight else 660))
    n.record(state, phase='frozen', hardwarePriority=True, mpvVideoOutput='gpu-next',
        statisticsPolicy='Unchanged predeclared 1C and 10-percent start strata; complete blocks only; no cross-night pooling.',
        cpuFlagPolicy='Flag mpv process CPU / steady elapsed >0.8; preserve and review, never automatically exclude.')
    report()


def request_or_collect_recovery(n, state):
    # The independent guard may have finished before the host resumes. Its exit
    # belongs to this session only when the remote owner matches our frozen state.
    def reconcile_exit():
        assert n.adb('shell', 'cat', n.GUARD+'/owner.txt').decode().strip()==state['owner']
        try:
            payload = n.adb('shell', 'cat', n.GUARD+'/exit.json', timeout=10)
        except subprocess.CalledProcessError:
            return False
        recovery = json.loads(payload)
        assert recovery['action']=='session_recovery'
        assert recovery.get('keptAwake') is True
        n.write(OUT/'recovery.json', recovery)
        n.record(state, phase='completed' if recovery['cleanupSucceeded'] else 'recovery_needs_review', recovery=recovery)
        return True
    if reconcile_exit():
        return
    try:
        n.request_recovery(state)
    except (AssertionError, subprocess.CalledProcessError):
        # Handle the narrow race between checking exit and checking a live guard.
        if not reconcile_exit():
            raise


def run():
    assert os.name == 'nt', 'Use the same Windows host as preparation.'
    n = night_module()
    state = n.read(OUT/'session-state.json')
    assert state['phase'] == 'frozen', 'Inspect/reconcile existing session, never blindly relaunch.'
    with (OUT/'controller.lock').open('x'):
        pass
    try:
        import ctypes
        assert ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        assert n.verify_process(n.GUARD+'/guard-v2-process-stat.txt') == state['guardPid']
        assert n.adb('shell', 'cat', n.GUARD+'/owner.txt').decode().strip() == state['owner']
        n.verify_process(PARENT+'/awake-guard/process-stat.txt')
        n.record(state, controllerPid=os.getpid(), controllerStartedUtc=n.utc())
        for stage in state['stages']:
            if stage['status'] != 'pending': continue
            if (OUT/'stop-request.json').exists():
                n.record(state,phase='ending_early',reason='user_requested'); break
            launch_stage(n,state,stage); n.wait_stage(state,stage)
            audit = n.collect_stage(state, stage)
            if audit['exit'].get('stopReason') in ['battery_below_start_threshold', 'night_deadline', 'battery_below_10_after_leg', 'user_cancelled', 'signal_cancelled']:
                n.record(state, phase='ending_early', reason=audit['exit']['stopReason']); break
            planned_legs = len(n.read(stage['plan'])['legs'])
            passed = (len(audit['audit'])==planned_legs
                and audit['exit'].get('cleanupSucceeded') is True
                and audit['exit'].get('stopReason')=='manifest_completed'
                and (stage['preflightPassed']==planned_legs if stage['preflight']
                     else audit['qualifiedRows']==planned_legs))
            if not passed:
                n.record(state, phase='needs_review', reviewStage=stage['name'],
                    reason='Inspect immutable failure evidence and existing runner before a named retry. Awake/recovery guards remain active.')
                return
        request_or_collect_recovery(n, state)
        if state['phase']=='completed':
            finalize()
        else:
            report()
    except BaseException as error:
        n.record(state, phase='needs_review', controllerError=repr(error))
        raise
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        (OUT/'controller.lock').unlink()


def finalize():
    assert os.name == 'nt', 'Use the same Windows Python and ADB as the session.'
    source = FROZEN if (FROZEN/'finalize-three-core-open.py').exists() else ROOT/'tools'
    helper = load('finalize_repeat', source/'finalize-three-core-open.py')
    return helper.finish(OUT, night_module())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'report', 'recover', 'finalize', 'validate', 'stop'])
    action = parser.parse_args().action
    os.environ['PATH'] = str(Path(ADB).parent)+os.pathsep+os.environ.get('PATH', '')
    if action == 'prepare': prepare()
    elif action == 'stop': stop()
    elif action == 'run': run()
    elif action == 'report': report()
    elif action == 'finalize': finalize()
    elif action == 'recover':
        n = night_module()
        request_or_collect_recovery(n, n.read(OUT/'session-state.json'))
    else:
        for name, codec, pair, preflight, reverse in definitions():
            legs = legs_for(name, codec, pair, preflight, reverse)
            assert all(x['mpvVideoOutput']=='gpu-next' and x['decoderMode']=='automatic' for x in legs)
            if not preflight:
                order = legs[0]['plannedConditions']
                assert len(legs)==4 and order[0]==order[3] and order[1]==order[2] and order[0]!=order[1]
        assert DEADLINE is None and RECOVERY is None and USER_DEADLINE is None
        assert len(definitions())==22
        print('Open-ended schedule: 9 remaining pairs, 18 complete blocks, 4 preflights; no wall-clock deadline.')


if __name__ == '__main__': main()
