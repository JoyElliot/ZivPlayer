#!/usr/bin/env python3
"""Read-only device verification and a local report after automatic recovery."""
import datetime as dt
import html
import json
import re
import tarfile
from pathlib import Path


def settings_match(actual, expected):
    if set(actual) != set(expected):
        return False
    return all(actual[k]==v for k,v in expected.items()
               if not (k=='screen_brightness' and expected['screen_brightness_mode']=='1'))


def finish(out, n):
    out = Path(out)
    state = n.read(out/'session-state.json')
    assert state['phase']=='completed', 'Failed or unverified termination requires review before device queries.'
    assert state.get('recovery', {}).get('cleanupSucceeded') is True
    assert state['recovery'].get('awakeGuardStopped') is True
    assert state['recovery'].get('originalTimeoutRestored') is True
    assert all(x['status'] in ['collected', 'skipped', 'pending'] for x in state['stages']), 'Reconcile and collect any formerly active stage first.'
    assert n.sha(out/'device-baseline.json')==state['baselineSha256'], 'Original baseline changed.'
    baseline = n.read(out/'device-baseline.json')
    raw = out/'final-verification'
    raw.mkdir(exist_ok=True)
    errors = {}

    def query(name, *parts):
        try:
            text = n.adb('shell', *parts, timeout=20).decode(errors='replace')
            (raw/(name+'.txt')).write_text(text, encoding='utf8')
            return text
        except Exception as error:
            errors[name] = repr(error)
            return ''

    awake_dir=n.PARENT+'/awake-guard'
    awake_owner=query('awake-owner','cat',awake_dir+'/owner.txt').strip()
    awake_saved=query('awake-saved-stat','cat',awake_dir+'/process-stat.txt').strip()
    awake_pid=int(awake_saved.split(' ',1)[0]) if awake_saved else 0
    awake_live=query('awake-live-stat',f'if [ -f /proc/{awake_pid}/stat ]; then cat /proc/{awake_pid}/stat; else echo ABSENT; fi').strip()
    awake_gone=awake_live=='ABSENT' or bool(awake_live and awake_saved and (n.process_identity(awake_live)!=n.process_identity(awake_saved) or awake_live.rsplit(') ',1)[1].split()[0]=='Z'))
    awake_exit_text=query('awake-exit','cat',awake_dir+'/exit.json')
    try:
        awake_exit=json.loads(awake_exit_text)
        awake_restored=awake_exit.get('cleanupSucceeded') is True and str(awake_exit['screenTimeoutMs'])==baseline['settingsBefore']['screen_off_timeout']
    except (ValueError,KeyError): awake_restored=False
    recovery_owner=query('recovery-owner','cat',n.GUARD+'/owner.txt').strip()
    recovery_saved=query('recovery-saved-stat','cat',n.GUARD+'/guard-v2-process-stat.txt').strip()
    recovery_pid=int(recovery_saved.split(' ',1)[0]) if recovery_saved else 0
    recovery_live=query('recovery-live-stat',f'if [ -f /proc/{recovery_pid}/stat ]; then cat /proc/{recovery_pid}/stat; else echo ABSENT; fi').strip()
    recovery_gone=recovery_live=='ABSENT' or bool(recovery_live and recovery_saved and (n.process_identity(recovery_live)!=n.process_identity(recovery_saved) or recovery_live.rsplit(') ',1)[1].split()[0]=='Z'))
    apks = {}
    for key, item in baseline['packages'].items():
        try:
            apk_path=query('apk-'+key+'-path','pm','path',item['package']).strip().removeprefix('package:')
            assert re.fullmatch(r'/data/app/[A-Za-z0-9_~=/.-]+/base.apk',apk_path)
            actual=query('apk-'+key+'-sha256','sha256sum',apk_path).split()[0]
        except Exception as error:
            actual = None
            errors['apk-'+key] = repr(error)
        apks[key] = dict(expected=item['sha256'], actual=actual,
                         matches=actual==item['sha256'])
    packages = []
    for item in n.read(out/'background-control.json')['packages']:
        text = query('package-'+item['package'], 'dumpsys', 'package', item['package'])
        current = text.split('Packages:', 1)[-1]
        match = re.search(r'(?m)^\s*User 0:([^\r\n]*\binstalled=true[^\r\n]*)', current)
        actual = dict(re.findall(r'\b(enabled|stopped|suspended)=([^ ]+)', match[1])) if match else {}
        packages.append(dict(package=item['package'], expected=item['before'], actual=actual,
                             matches=actual==item['before']))
    settings = {}
    system = ['screen_off_timeout', 'screen_brightness', 'screen_brightness_mode']
    for key in baseline['settingsBefore']:
        settings[key] = query('setting-'+key, 'settings', 'get',
                              'system' if key in system else 'global', key).strip()
    power = query('power', 'dumpsys', 'power')
    policy = query('policy', 'dumpsys', 'window', 'policy')
    bluetooth = query('bluetooth', 'dumpsys', 'bluetooth_manager')
    audio = query('audio', 'dumpsys', 'audio')
    volume = query('music-volume', 'cmd', 'media_session', 'volume', '--stream', '3', '--get')
    music = audio.split('- STREAM_MUSIC:', 1)[-1].split('- STREAM_', 1)[0]
    volume_match = re.search(r'volume is (\d+) in range', volume)
    showing = re.search(r'(?m)^\s*showing=(true|false)\s*$', policy)
    checks = dict(recoveryOwner=recovery_owner==state['owner'], recoveryGuardStopped=recovery_gone, awakeGuardStopped=awake_gone and awake_owner==state['awakeOwner'], awakeTimeoutRestored=awake_restored, apks=all(x['matches'] for x in apks.values()),
        backgroundPackages=all(x['matches'] for x in packages),
        settings=settings_match(settings,baseline['settingsBefore']),
        musicVolume=bool(volume_match and int(volume_match[1])==baseline['musicVolume']),
        musicMuted=('Muted: '+str(baseline['musicMuted']).lower()) in music,
        awake=bool(re.search(r'(?m)^\s*mWakefulness=Awake\s*$', power)),
        keyguardNotShowing=bool(showing and showing[1]=='false'),
        bluetoothState=('state: ON' if baseline['settingsBefore']['bluetooth_on']!='0' else 'state: OFF') in bluetooth)
    verification = dict(verifiedUtc=n.utc(), checks=checks, recoveryVerified=all(v for k,v in checks.items() if k not in ['awake','keyguardNotShowing']) and not errors,
        apks=apks, packages=packages, settings=settings, errors=errors,
        brightnessPolicy='Read only. In automatic mode the brightness value is dynamic; verify mode and all other settings.',
        userData='Backed up before installation, not restored from tar or verified item by item.')
    n.write(raw/'verification.json', verification)

    components, quality, cpu_flags, coverage = [], [], [], []
    for stage in state['stages']:
        if stage['status'] != 'collected':
            continue
        folder = out/stage['name']
        component_codec = 'hevc' if stage['codec']=='hevc10' else stage['codec']
        expected_labels={p.stem for p in (folder/'analysis').glob('*.json')}
        observed_labels=[]
        with tarfile.open(folder/'finished-device.tar') as archive:
            for entry in archive:
                if not entry.name.endswith('-codec-end.txt'):
                    continue
                observed_labels.append(Path(entry.name).name.removesuffix('-codec-end.txt'))
                text = archive.extractfile(entry).read().decode('utf8', errors='replace')
                found = sorted({x for x in re.findall(r'Component[:=]\s*([A-Za-z0-9_.-]+)', text)
                                if '.'+component_codec+'.' in x})
                components.append(dict(stage=stage['name'], archiveMember=entry.name,
                    videoComponents=found, expected='c2.qti.'+component_codec+'.decoder',
                    matches=found==['c2.qti.'+component_codec+'.decoder']))
        coverage.append(dict(stage=stage['name'],expectedLabels=sorted(expected_labels),observedLabels=sorted(observed_labels),complete=set(observed_labels)==expected_labels and len(observed_labels)==len(expected_labels)))
        for path in sorted((folder/'analysis').glob('*.json')):
            item = n.read(path)
            samples = item.get('samples', [])
            elapsed = samples[-1]['elapsedMs']-samples[0]['elapsedMs'] if samples else 0
            cpu = samples[-1]['processCpuMs']-samples[0]['processCpuMs'] if samples else 0
            row = dict(stage=stage['name'], analysis=path.name, sampleCount=len(samples),
                hardware=item.get('actualHardwareDecoderConfirmed') is True,
                software=item.get('actualSoftwareDecoder'), qualityIssues=item.get('qualityIssues'),
                screenPlaying=bool(samples) and all(x['screenInteractive'] and x['windowFocused'] and x['isPlaying'] for x in samples),
                thermalStatuses=sorted({x['thermalStatus'] for x in samples}), processCpuDeltaMs=cpu,
                steadyElapsedMs=elapsed)
            quality.append(row)
            if '-libmpv-' in path.name and elapsed>0 and cpu/elapsed>0.8:
                cpu_flags.append(dict(**row, flag='predeclared_mpv_cpu_fraction_above_0.8',
                    action='Preserve and review; do not automatically exclude this leg.'))
    n.write(out/'final-component-coverage.json',coverage)
    n.write(out/'final-hardware-components.json', components)
    n.write(out/'final-quality-observations.json', quality)
    n.write(out/'cpu-observations.json', cpu_flags)
    hardware_ok = bool(components) and all(x['matches'] for x in components) and all(x['complete'] for x in coverage)
    complete = verification['recoveryVerified'] and hardware_ok
    n.record(state, phase='completed' if complete else 'recovery_needs_review',
             finalVerificationUtc=n.utc(), recoveryVerified=verification['recoveryVerified'],
             archivedHardwareComponentsVerified=hardware_ok, cpuFlagCount=len(cpu_flags))
    report = n.report()
    blocks = [x for x in report['blocks'] if x['complete']]
    names = {'exo':'ExoPlayer', 'vlc':'VLC', 'libmpv@gpu-next':'mpv · gpu-next'}
    rows = []
    for block in blocks:
        a, b = block['a'], block['b']
        pa, pb = (block['conditionMeanPowerMw'][c] for c in [a, b])
        layer = '同层' if block['aggregationEligible'] else '跨层单列'
        rows.append('<tr><td>'+html.escape(block['comparisonBlock'])+'</td><td>'+names[a]+' / '+names[b]+
            f'</td><td>{pa/1000:.3f} / {pb/1000:.3f}</td><td>{pa-pb:+.1f}</td><td>'+layer+'</td></tr>')
    summary = dict(generatedUtc=n.utc(), phase=state['phase'], formalRuns=report['qualifiedFormalRuns'],
        preflightLegs=report['passedPreflightLegs'], completeBlocks=len(blocks),
        sameStratumBlocks=sum(x['aggregationEligible'] for x in blocks),
        recovery=state['recovery'], recoveryVerified=verification['recoveryVerified'],
        archivedHardwareComponentsVerified=hardware_ok, cpuFlags=cpu_flags,
        screenTimeoutMs=int(baseline['settingsBefore']['screen_off_timeout']), awakeGuardStoppedAfterRecovery=True,
        skippedStages=[dict(name=x['name'], reason=x.get('skipReason')) for x in state['stages'] if x['status']=='skipped'],
        statisticsPolicy=state['statisticsPolicy'], automationStatus='awaiting_agent_completion_audit')
    n.write(out/'completion-summary.json', summary)
    raw_means={}
    for block in blocks:
        codec=block['fixture'].split('-')[0]
        for core,value in block['conditionMeanPowerMw'].items():
            raw_means.setdefault(codec,{}).setdefault(core,[]).append(value)
    raw_table=[]
    raw_json={}
    for codec,label in [('avc','H.264 / AVC'),('hevc','H.265 / HEVC'),('hevc10','HEVC 10-bit'),('vp9','VP9')]:
        raw_json[codec]={core:dict(meanPowerMw=sum(values)/len(values),completeBlockCount=len(values),formalLegCount=2*len(values)) for core,values in raw_means.get(codec,{}).items()}
        cells=[f"{raw_json[codec][core]['meanPowerMw']/1000:.3f}" if core in raw_json[codec] else '—' for core in ['libmpv@gpu-next','exo','vlc']]
        raw_table.append('<tr><td>'+label+'</td>'+''.join('<td>'+x+'</td>' for x in cells)+'</tr>')
    n.write(out/'descriptive-core-means.json',dict(source='stratified-report.json complete blocks only',values=raw_json,scope='Raw arithmetic descriptive means, unequal temperature/battery strata are not adjusted; never a paired or causal estimate.'))
    status = '已完成恢复核验' if verification['recoveryVerified'] else '恢复核验有待复核项，见记录'
    session_date=out.name.removeprefix('power-threecore-')
    session_date=f'{session_date[:4]}-{session_date[4:6]}-{session_date[6:]}'
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ZivPlayer 硬解续测</title><style>body{font:16px/1.7 system-ui;background:#f5f7fb;color:#192337;max-width:1150px;margin:32px auto;padding:20px}table{border-collapse:collapse;background:white;width:100%}td,th{padding:10px;border-bottom:1px solid #dce2ed;text-align:left}.scroll{overflow:auto}code{word-break:break-all}</style><h1>ZivPlayer 三核心硬解续测 · '+html.escape(session_date)+'</h1>'
    document += f'<p>{report["qualifiedFormalRuns"]} 轮正式采样，{len(blocks)} 个完整四轮块；另有 {report["passedPreflightLegs"]} 轮功能预检。{status}。</p>'
    document += '<p>mpv 固定 gpu-next；本地1080p24素材，90秒预热、240秒采样、1Hz遥测；首腿至少180秒冷却，其余至少60秒，连续3次非升温后入场，上限35°C。低于10%停止采样，低于15%不启动新腿。每个完整ABBA/BAAB块才算一次重复；只合并同APK/素材/协议/cohort/实际解码器、同起始1°C温度层与10个百分点电量层的块。跨层单列，不合并前夜或其他cohort的数据。功耗为整机遥测，不能归因于依赖升级或证明主观画质。</p>'
    document += '<p><strong>待量化误差：</strong>屏幕常亮和后台包暂停、禁用、强停均为本轮受控条件；其影响及残余后台负载尚未单独量化。结果不等同于日常使用功耗或播放器进程单独功耗，不作未经测量的误差扣除。详见 <a href="measurement-limitations.json">测量边界记录</a>。</p>'
    document += '<h2>三核心整机功耗 W</h2><p>本轮完整块中各核心正式轮次的原始算术平均；未校正温度、电量，不能代替下面的严格分层配对结果。“—”表示没有该项完整正式数据。</p><div class="scroll"><table><thead><tr><th>视频标准</th><th>mpv · gpu-next</th><th>ExoPlayer</th><th>VLC</th></tr></thead><tbody>'+''.join(raw_table)+'</tbody></table></div><h2>完整块与分层</h2>'
    document += '<div class="scroll"><table><thead><tr><th>完整块</th><th>A / B</th><th>功耗 W · A / B</th><th>A−B mW</th><th>层级</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    document += f'<p>归档硬件组件核验：{hardware_ok}。按事先阈值发现 {len(cpu_flags)} 项 mpv CPU 时间偏高记录，已保留，未自动剔除。支持的掉帧计数、屏幕播放状态和热状态详见 <a href="final-quality-observations.json">逐轮记录</a>；frame hash 不等同于画质结论。</p>'
    document += '<p>自动恢复记录和只读实机核验分别保存；原用户数据只备份、未覆盖恢复。恢复时停止常亮守护并还原真实锁屏时长；亮度模式为自动时，亮度数值可随环境变化。后续核验只读，不再写亮度。</p><p><a href="stratified-report.md">分层统计报告</a> · <a href="final-verification/verification.json">恢复核验</a> · <a href="completion-summary.json">完成摘要</a> · <a href="cpu-observations.json">CPU观察</a></p></html>'
    (out/'morning-report.html').write_text(document, encoding='utf8')
    return summary
