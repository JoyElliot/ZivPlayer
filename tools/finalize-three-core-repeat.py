#!/usr/bin/env python3
"""Read-only device verification and a local report after automatic recovery."""
import datetime as dt
import html
import json
import re
import tarfile
from pathlib import Path


def finish(out, n):
    out = Path(out)
    state = n.read(out/'session-state.json')
    assert state['phase']=='completed', 'Failed or unverified termination requires review before device queries.'
    assert state.get('recovery', {}).get('cleanupSucceeded') is True
    assert all(x['status'] in ['collected', 'skipped', 'pending'] for x in state['stages']), 'Reconcile and collect any formerly active stage first.'
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

    apks = {}
    for key, item in baseline['packages'].items():
        try:
            actual = n.package_sha(item['package'])
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
    checks = dict(apks=all(x['matches'] for x in apks.values()),
        backgroundPackages=all(x['matches'] for x in packages),
        settings=settings==baseline['settingsBefore'],
        musicVolume=bool(volume_match and int(volume_match[1])==baseline['musicVolume']),
        musicMuted=('Muted: '+str(baseline['musicMuted']).lower()) in music,
        awake=bool(re.search(r'(?m)^\s*mWakefulness=Awake\s*$', power)),
        keyguardNotShowing=bool(showing and showing[1]=='false'),
        bluetoothState=('state: ON' if baseline['settingsBefore']['bluetooth_on']!='0' else 'state: OFF') in bluetooth)
    verification = dict(verifiedUtc=n.utc(), checks=checks, recoveryVerified=all(checks.values()) and not errors,
        apks=apks, packages=packages, settings=settings, errors=errors,
        brightnessPolicy='Read and report only; never overwrite a later user brightness change.',
        userData='Backed up before installation, not restored from tar or verified item by item.')
    n.write(raw/'verification.json', verification)

    components, quality, cpu_flags = [], [], []
    for stage in state['stages']:
        if stage['status'] != 'collected':
            continue
        folder = out/stage['name']
        component_codec = 'hevc' if stage['codec']=='hevc10' else stage['codec']
        with tarfile.open(folder/'finished-device.tar') as archive:
            for entry in archive:
                if not entry.name.endswith('-codec-end.txt'):
                    continue
                text = archive.extractfile(entry).read().decode('utf8', errors='replace')
                found = sorted({x for x in re.findall(r'Component[:=]\s*([A-Za-z0-9_.-]+)', text)
                                if '.'+component_codec+'.' in x})
                components.append(dict(stage=stage['name'], archiveMember=entry.name,
                    videoComponents=found, expected='c2.qti.'+component_codec+'.decoder',
                    matches=found==['c2.qti.'+component_codec+'.decoder']))
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
    n.write(out/'final-hardware-components.json', components)
    n.write(out/'final-quality-observations.json', quality)
    n.write(out/'cpu-observations.json', cpu_flags)
    hardware_ok = bool(components) and all(x['matches'] for x in components)
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
        screenTimeoutMs=86400000, awakeGuardKeptUntilUserReturns=True,
        skippedStages=[dict(name=x['name'], reason=x.get('skipReason')) for x in state['stages'] if x['status']=='skipped'],
        statisticsPolicy=state['statisticsPolicy'], automationStatus='awaiting_agent_completion_audit')
    n.write(out/'completion-summary.json', summary)
    status = '已完成恢复核验' if verification['recoveryVerified'] else '恢复核验有待复核项，见记录'
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ZivPlayer 10-22 续测</title><style>body{font:16px/1.7 system-ui;background:#f5f7fb;color:#192337;max-width:1150px;margin:32px auto;padding:20px}table{border-collapse:collapse;background:white;width:100%}td,th{padding:10px;border-bottom:1px solid #dce2ed;text-align:left}.scroll{overflow:auto}code{word-break:break-all}</style><h1>ZivPlayer 三核心硬解续测 · 2026-10-22</h1>'
    document += f'<p>{report["qualifiedFormalRuns"]} 轮正式采样，{len(blocks)} 个完整四轮块；另有 {report["passedPreflightLegs"]} 轮功能预检。{status}。</p>'
    document += '<p>mpv 固定 gpu-next；本地1080p24素材，90秒预热、240秒采样、1Hz遥测。每个完整ABBA/BAAB块才算一次重复；只合并同APK/素材/协议/cohort/实际解码器、同起始1°C温度层与10个百分点电量层的块。跨层单列，不混入10-19的数据。功耗为整机遥测，不能归因于依赖升级或证明主观画质。</p>'
    document += '<div class="scroll"><table><thead><tr><th>完整块</th><th>A / B</th><th>功耗 W · A / B</th><th>A−B mW</th><th>层级</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    document += f'<p>归档硬件组件核验：{hardware_ok}。按事先阈值发现 {len(cpu_flags)} 项 mpv CPU 时间偏高记录，已保留，未自动剔除。支持的掉帧计数、屏幕播放状态和热状态详见 <a href="final-quality-observations.json">逐轮记录</a>；frame hash 不等同于画质结论。</p>'
    document += '<p>自动恢复记录和只读实机核验分别保存；原用户数据只备份、未覆盖恢复。常亮守护保留至用户回来；若用户已接管或亮度变化，后续核验不再写亮度。</p><p><a href="stratified-report.md">分层统计报告</a> · <a href="final-verification/verification.json">恢复核验</a> · <a href="completion-summary.json">完成摘要</a> · <a href="cpu-observations.json">CPU观察</a></p></html>'
    (out/'morning-report.html').write_text(document, encoding='utf8')
    return summary
