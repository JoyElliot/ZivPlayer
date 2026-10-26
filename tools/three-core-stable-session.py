#!/usr/bin/env python3
"""Hardware three-core repeat experiment, 2026-10-23; Windows controller."""
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
import uuid

ROOT = Path(os.environ.get('ZIV_REPO_ROOT', str(Path(__file__).resolve().parents[1])))
OUT = ROOT/'native/out/power-threecore-20261023'
PARENT = '/data/local/tmp/ziv-power-20261023'
SERIAL = json.loads((OUT/'connection.json').read_text(encoding='utf-8-sig'))['serial'] if (OUT/'connection.json').exists() else '***'
SOURCE_FROZEN = ROOT/'native/out/power-threecore-20261022/frozen-tools'
ADB = r'D:\DevTools\Android\Sdk\platform-tools\adb.exe'
DEADLINE = dt.datetime.fromisoformat('2026-10-23T06:35:00+08:00')
RECOVERY = dt.datetime.fromisoformat('2026-10-23T06:40:00+08:00')
USER_DEADLINE = '2026-10-23T07:15:00+08:00'
if (OUT/'recovery-budget.json').exists():
    _budget=json.loads((OUT/'recovery-budget.json').read_text(encoding='utf-8-sig'))
    DEADLINE=dt.datetime.fromisoformat(_budget['samplingStopLocal'])
    RECOVERY=dt.datetime.fromisoformat(_budget['recoveryStartLocal'])
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


def owner_state(text):
    packages = text.split('Packages:', 1)[1]
    line = re.search(r'(?m)^\s*User 0:([^\r\n]*\binstalled=true[^\r\n]*)', packages)
    assert line, 'Installed user-0 package record missing'
    return {k: v for k, v in re.findall(r'\b(enabled|stopped|suspended)=([^ ]+)', line[1])}


def definitions():
    # Each priority comparison receives complementary complete blocks in this new
    # cohort. Do not count the previous night's blocks as repeats in this cohort.
    groups = [
        ('n23hevc10mv', 'hevc10', ('libmpv@gpu-next', 'vlc')),
        ('n23avcme', 'avc', ('libmpv@gpu-next', 'exo')),
        ('n23vp9me', 'vp9', ('libmpv@gpu-next', 'exo')),
        ('n23hevcme', 'hevc', ('libmpv@gpu-next', 'exo')),
        ('n23hevc10ev', 'hevc10', ('exo', 'vlc')),
        ('n23avcev', 'avc', ('exo', 'vlc')),
    ]
    result, checked = [], set()
    for name, codec, pair in groups:
        if codec not in checked:
            result.append(('n23'+codec+'check', codec, None, True, False))
            checked.add(codec)
        result.extend([(name+'a', codec, pair, False, False),
                       (name+'b', codec, pair, False, True)])
    # A remaining full-block slot can strengthen an already repeated comparison.
    for name, codec, pair in [groups[4], groups[5], groups[0]]:
        result.append((name+'extra', codec, pair, False, False))
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
        'not dependency-upgrade benefit. Small block counts; power does not establish visual quality.')
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
    n.verify_process(PARENT+'/awake-guard/process-stat.txt')
    prep = load('prepare_baseline', ROOT/'tools/prepare-renderer-night.py')
    prep.ROOT, prep.OUT, prep.ADB, prep.SERIAL = ROOT, OUT, ADB, SERIAL
    baseline = prep.capture_baseline()
    artifacts = n.read(ROOT/'native/out/dependency-upgrade-20261017/artifact-manifest.json')['branches']['power']['apks']
    for info in artifacts.values():
        info['path'] = str(ROOT/info['path'].split('/zivplayer/', 1)[1])
        assert n.sha(info['path']) == info['sha256']
    n.write(OUT/'experiment-apks.json', artifacts)
    FROZEN.mkdir()
    for name in ['phone-power-stable-runner.sh', 'power-night-stable-guard.sh', 'phone-awake-stable-guard.sh', 'power-formal-stop-guard.sh',
                 'renderer-night-session.py', 'three-core-stable-session.py', 'finalize-three-core-stable.py', 'report-renderer-night.py',
                 'collect-power-night.py', 'summarize-power-benchmark.py']:
        shutil.copyfile((ROOT/'tools'/name) if name in ['three-core-stable-session.py','finalize-three-core-stable.py','phone-power-stable-runner.sh','power-night-stable-guard.sh','phone-awake-stable-guard.sh','power-formal-stop-guard.sh'] else SOURCE_FROZEN/name, FROZEN/name)
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
                'app-01-***', 'app-10-***', 'app-03-***', 'app-04-***', 'app-06-***']
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
            suspend=fields['suspended']=='false', unstop=fields['stopped']=='false',
            disable=package in ['app-10-***', 'app-04-***', 'app-06-***']
                    and fields['enabled'] in ['0', '1']))
    n.write(OUT/'background-control.json', dict(packages=records))
    guard = OUT/'night-guard'; guard.mkdir()
    shutil.copyfile(FROZEN/'power-night-stable-guard.sh', guard/'guard.sh')
    for file, lines in [
        ('background-packages.txt', [x['package'] for x in records if x['suspend']]),
        ('stopped-packages.txt', [x['package'] for x in records if x['unstop']]),
        ('enabled-packages.tsv', [x['package']+'\t'+x['before']['enabled'] for x in records if x['disable']])]:
        n.put_text(guard/file, ''.join(x+'\n' for x in lines))
    owner = uuid.uuid4().hex
    n.put_text(guard/'owner.txt', owner+'\n')
    settings = baseline['settingsBefore']
    journal_entries=sum(x['suspend']+x['unstop']+x['disable'] for x in records)
    reserve=1700+20*journal_entries
    global DEADLINE,RECOVERY
    RECOVERY=min(RECOVERY,dt.datetime.fromisoformat(USER_DEADLINE)-dt.timedelta(seconds=reserve))
    DEADLINE=min(DEADLINE,RECOVERY-dt.timedelta(minutes=5))
    assert DEADLINE>dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=35), 'Insufficient safe recovery budget.'
    n.DEADLINE,n.RECOVERY_DEADLINE=DEADLINE,RECOVERY
    n.write(OUT/'recovery-budget.json',dict(samplingStopLocal=DEADLINE.isoformat(),recoveryStartLocal=RECOVERY.isoformat(),userDeadlineLocal=USER_DEADLINE,conservativeReserveSeconds=reserve,includesFinalVerificationSeconds=300,journalEntries=journal_entries,perCommandTimeoutSeconds=8,killGraceSeconds=2))
    n.env(guard/'config.env', dict(HARD_STOP_EPOCH=int(RECOVERY.timestamp()), ORIGINAL_TIMEOUT=settings['screen_off_timeout'], RESTORE_ORIGINAL_TIMEOUT='true', AWAKE_GUARD_OWNER='***',
        ORIGINAL_BT=settings['bluetooth_on'], ORIGINAL_BRIGHTNESS=settings['screen_brightness'],
        ORIGINAL_BRIGHTNESS_MODE=settings['screen_brightness_mode'], ORIGINAL_TEST_SHA=baseline['packages']['test']['sha256'],
        MAIN_SHA=artifacts['app']['sha256'], RESTORE_MAIN_APK=n.GUARD+'/original-main.apk',
        RESTORE_MAIN_SHA=baseline['packages']['main']['sha256'], KEEP_AWAKE_ON_EXIT='true',
        REQUIRE_FULL_RECOVERY='true', REQUIRE_BACKGROUND_JOURNALS='true',
        ORIGINAL_MUSIC_VOLUME=baseline['musicVolume'], ORIGINAL_MUSIC_MUTED=str(baseline['musicMuted']).lower()))
    state = dict(version=1, phase='preparing', serial=SERIAL, owner=owner, stages=[], guardRevision=3,
        deadlineLocal=DEADLINE.isoformat(), recoveryDeadlineLocal=RECOVERY.isoformat(), userDeadlineLocal=USER_DEADLINE,
        keepAwakeThroughRecovery=True, restoreOriginalTimeoutOnRecovery=True, baselineSha256=n.sha(OUT/'device-baseline.json'),
        guardFiles={p.name:n.sha(p) for p in guard.iterdir()},
        frozenTools={p.name:n.sha(p) for p in FROZEN.iterdir()},
        controllerLogFiles=['controller.stdout.log', 'controller.stderr.log'])
    n.record(state, phase='arming_recovery')
    # Arm independent restoration BEFORE changing APKs, app states or display/audio settings.
    n.deploy_guard(state)
    arm_formal_stop(n,state,artifacts['app']['sha256'])
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
    for kind, package in [('app', PACKAGE), ('androidTest', PACKAGE+'.test')]:
        if n.package_sha(package) == artifacts[kind]['sha256']: continue
        result = subprocess.run([ADB, '-s', SERIAL, 'install', '-r', '-t', artifacts[kind]['path']],
            capture_output=True, timeout=420)
        (OUT/('install-'+kind+'.log')).write_bytes(result.stdout+result.stderr)
        assert result.returncode == 0 and b'Success' in result.stdout
        assert n.package_sha(package) == artifacts[kind]['sha256']
    experiment_path=n.adb('shell','pm','path',PACKAGE).decode().strip()
    assert re.fullmatch(r'package:/data/app/[A-Za-z0-9_~=/.-]+/base.apk',experiment_path)
    assert n.package_sha(PACKAGE)==artifacts['app']['sha256']
    n.put_text(OUT/'formal-stop/experiment-apk-path.txt',experiment_path+'\n')
    n.upload(OUT/'formal-stop/experiment-apk-path.txt',PARENT+'/formal-stop/experiment-apk-path.txt')
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


def arm_formal_stop(n,state,main_sha):
    folder=OUT/'formal-stop';folder.mkdir()
    remote=PARENT+'/formal-stop'
    shutil.copyfile(FROZEN/'power-formal-stop-guard.sh',folder/'guard.sh')
    n.env(folder/'config.env',dict(HARD_STOP_EPOCH=int(DEADLINE.timestamp())-20,OWNER=state['owner'],MAIN_SHA=main_sha))
    n.adb('shell','mkdir',remote)
    for name in ['guard.sh','config.env']:
        n.upload(folder/name,remote+'/'+name)
    n.adb('shell','sh','-n',remote+'/guard.sh')
    n.adb('shell',f'/system/bin/toybox nohup /system/bin/setsid sh {remote}/guard.sh {remote} </dev/null >{remote}/guard.log 2>&1 &')
    ready=n.wait_json(remote+'/ready.json')
    assert n.verify_process(remote+'/process-stat.txt')==ready['pid']
    n.record(state,formalStopPid=ready['pid'],formalStopEpoch=int(DEADLINE.timestamp())-20)


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
            COHORT='threecore-20261023-'+artifacts['app']['sha256'][:12]+'-'+artifacts['androidTest']['sha256'][:12],
            MPV_VIDEO_OUTPUT='gpu-next', VALIDATION_ONLY=str(preflight).lower(), SLEEP_AT_END='false',
            KEEP_AWAKE_ON_EXIT='true', HOLD_FOR_NEXT_STAGE='true', RESTORE_SCREEN_TIMEOUT=86400000,
            RESTORE_BLUETOOTH=0, MINIMUM_COOLDOWN=60, FIRST_COOLDOWN_SECONDS=180, COOLDOWN_STABLE_POLLS=3, COOLDOWN_POLL_SECONDS=15,
            COOLDOWN_REQUIRE_NONRISING='true', COOLDOWN_INPUT_REFRESH='false', MAX_START_TEMPERATURE_TENTHS=350,
            MINIMUM_START_BATTERY=15, MINIMUM_SAMPLE_BATTERY=10, WARMUP_SECONDS=15 if preflight else 90,
            MEASURE_SECONDS=65 if preflight else 240, SURFACE_PROTOCOL='current-app', FRAME_PROOF_METHOD='display-crop-v1',
            HARDWARE_ONLY_AUTOMATIC='true', SOFTWARE_ONLY='false', SCREEN_TIMEOUT_MS=86400000,
            HARD_STOP_EPOCH=int(DEADLINE.timestamp()), OPTIONAL_BLOCK_BUDGET_SECONDS=1800)
        n.env(folder/'config.env', config)
        plan = dict(schemaVersion=4, buildIdentityProtocol=1, serial=SERIAL, remoteDirectory=PARENT+'/'+name,
            measurementCohort=config['COHORT'], mainApkSha256=config['MAIN_SHA'], testApkSha256=config['LEGACY_TEST_SHA'],
            mpvVideoOutput='gpu-next', legs=legs, fixtures=fixtures, frameProofMethod='display-crop-v1', surfaceProtocol='current-app',
            warmupSeconds=config['WARMUP_SECONDS'], measurementSeconds=config['MEASURE_SECONDS'], minimumCooldownSeconds=60, minimumStartBatteryPercent=15, minimumSampleBatteryPercent=10, stableNonrisingPolls=3,
            maxStartTemperatureTenthsC=350, hardStopLocal=DEADLINE.isoformat(), hardwareRequired=True, frozenUtc=n.utc(),
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
        assert recovery['action']=='deadline_recovery'
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
        assert n.verify_process(PARENT+'/formal-stop/process-stat.txt')==state['formalStopPid']
        n.record(state, controllerPid=os.getpid(), controllerStartedUtc=n.utc())
        for stage in state['stages']:
            if stage['status'] != 'pending': continue
            remaining = (DEADLINE-dt.datetime.now(dt.timezone.utc)).total_seconds()
            if stage['name'].endswith('a') and not stage['name'].endswith('extra') and not stage['preflight']:
                sibling = next(x for x in state['stages'] if x['name']==stage['name'][:-1]+'b')
                if remaining < stage['nominalSeconds']+sibling['nominalSeconds']+90:
                    for item in [stage, sibling]:
                        item.update(status='skipped', skipReason='insufficient_time_for_complementary_pair')
                        n.record(state, phase='phase_skipped', lastSkippedStage=item['name'])
                    continue
            if remaining < stage['nominalSeconds']+90:
                stage.update(status='skipped', skipReason='insufficient_time_for_complete_stage')
                n.record(state, phase='phase_skipped', lastSkippedStage=stage['name']); continue
            n.launch_stage(state, stage); n.wait_stage(state, stage)
            audit = n.collect_stage(state, stage)
            if audit['exit'].get('stopReason') in ['battery_below_start_threshold', 'night_deadline', 'battery_below_10_after_leg']:
                n.record(state, phase='ending_early', reason=audit['exit']['stopReason']); break
            passed = (stage['preflightPassed']==len(audit['audit'])) if stage['preflight'] else (
                audit['qualifiedRows']==len(audit['audit']) and audit['exit'].get('cleanupSucceeded') is True
                and audit['exit'].get('stopReason')=='manifest_completed')
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
    source = FROZEN if (FROZEN/'finalize-three-core-stable.py').exists() else ROOT/'tools'
    helper = load('finalize_repeat', source/'finalize-three-core-stable.py')
    return helper.finish(OUT, night_module())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'report', 'recover', 'finalize', 'validate'])
    action = parser.parse_args().action
    os.environ['PATH'] = str(Path(ADB).parent)+os.pathsep+os.environ.get('PATH', '')
    if action == 'prepare': prepare()
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
        assert DEADLINE < RECOVERY < dt.datetime.fromisoformat(USER_DEADLINE)
        print('Frozen schedule, complete blocks, fixed renderer and recovery reserve validated.')


if __name__ == '__main__': main()
