#!/usr/bin/env python3
"""Freeze a separate cross-day bridge or complete blocks for the following night."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'io.github.joyelliot.zivplayer'
CODECS = ('avc', 'hevc', 'hevc10', 'vp9')

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def build_legs(name, stage, repeat_full_hardware=False):
    rows = []
    def add(codec, mode, engines, block, pair, purpose):
        for pos, engine in enumerate(engines, 1):
            rows.append(dict(ordinal=len(rows)+1, blockNumber=block,
                comparisonBlock=f'{name}:{pair}:{codec}:{mode}:b{block}',
                plannedOrdinal=pos, codec=codec, decoderMode=mode, engine=engine,
                label=f'{name}-{pair}-b{block}-{codec}-{mode}-{pos}-{engine}',
                purpose=purpose, plannedOrder=engines))
    if stage == 'bridge':
        # Mirrored ABC/CBA gives two independent legs per engine without a day-spanning block.
        for i, engine in enumerate(('exo','libmpv','vlc','vlc','libmpv','exo')):
            add('avc','automatic',[engine],1 if i<3 else 2,'bridge-'+engine,'cross-day-bridge')
        return rows
    for codec in CODECS:
        add(codec,'automatic',['libmpv','exo','exo','libmpv'],2,'exo-mpv','matrix')
        if repeat_full_hardware:
            add(codec,'automatic',['exo','libmpv','libmpv','exo'],1,'exo-mpv','matrix')
    for engine in ('exo','libmpv','vlc'):
        add('av1','software',[engine],1,'preflight-'+engine,'software-preflight')
        rows[-1].update(validationOnly=True,warmupSeconds=15,measurementSeconds=65)
    # AV1 includes all engines. The unchanged test APK cannot classify native VLC HEVC/AVC software.
    schedule = [('av1',(1,2)),('hevc',(1,2)),('avc',(1,2))]
    for codec, blocks in schedule:
        for block in blocks:
            pairs=[('sw-exo-mpv','exo','libmpv')]
            if codec=='av1': pairs.append(('sw-mpv-vlc','libmpv','vlc'))
            for pair, a, b in pairs:
                order = [a,b,b,a] if block==1 else [b,a,a,b]
                add(codec,'software',order,block,pair,'optional-software')
    return rows

def baseline_reference(previous):
    audit = json.loads((previous/'reviewed-audit.json').read_text())
    complete_labels = {label for block in audit['pairwisePlannedBlocks'] if block['complete'] for label in block['runLabels']}
    refs = {}
    for codec in CODECS:
        for engine in ('exo','libmpv','vlc'):
            runs = [r for r in audit['runs'] if r['runLabel'] in complete_labels and r['qualified'] and r['engine']==engine
                    and r['fixture']==codec+'-1080p24.mkv' and r['mode']=='automatic']
            values = [r['meanPowerMw'] for r in runs]
            if values:
                refs[codec+':'+engine] = dict(count=len(values),meanMw=statistics.mean(values),
                    minMw=min(values),maxMw=max(values),stdevMw=statistics.stdev(values) if len(values)>1 else None,
                    runLabels=[r['runLabel'] for r in runs],
                    temperatureMinC=min(r['temperatureMinC'] for r in runs),temperatureMaxC=max(r['temperatureMaxC'] for r in runs),
                    batteryMinPercent=min(r['batteryEndPercent'] for r in runs),batteryMaxPercent=max(r['batteryStartPercent'] for r in runs))
    return dict(previousReviewedAuditSha256=sha(previous/'reviewed-audit.json'),groups=refs,
        rule='Bridge requires two qualified H.264 legs per engine and each engine mean within 5 percent of the previous complete-block mean. This is an operational check, not a statistical equivalence test. Temperature, battery, radio and software differences are reported separately; no cross-day pooling.',
        relativeMeanTolerancePercent=5.0,temperatureDifferenceReviewC=2.0)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--previous',type=Path,required=True)
    p.add_argument('--serial',required=True)
    p.add_argument('--stage',choices=('bridge','continuation'),required=True)
    p.add_argument('--name',required=True)
    p.add_argument('--deadline',required=True)
    p.add_argument('--restore-by',required=True)
    p.add_argument('--repeat-full-hardware',action='store_true')
    p.add_argument('--deploy',action='store_true')
    args=p.parse_args()
    assert args.name.isalnum() and len(args.name)<=20
    deadline=dt.datetime.fromisoformat(args.deadline)
    restore_by=dt.datetime.fromisoformat(args.restore_by)
    assert deadline.tzinfo and restore_by.tzinfo and restore_by>deadline
    out=args.output.resolve(); previous=args.previous.resolve(); folder=out/args.name
    folder.mkdir(exist_ok=False)
    baseline=json.loads((out/'device-baseline.json').read_text())
    fixtures=json.loads((out/'persistent-fixtures.json').read_text())['fixtures']
    old_plan=json.loads((previous/'run-plan.json').read_text())
    remote=f'/data/local/tmp/ziv-power-{deadline:%Y%m%d}/{args.name}'
    test=out/'power-test.apk'; old_test=ROOT/'native/out/power-comparison-20260913/artifacts/legacy-test.apk'
    assert sha(test)==old_plan['testApkSha256']
    assert baseline['packages']['main']['sha256']==old_plan['mainApkSha256']
    assert sha(old_test)==baseline['packages']['test']['sha256']
    rows=build_legs(args.name,args.stage,args.repeat_full_hardware)
    keys=('ordinal','blockNumber','comparisonBlock','plannedOrdinal','codec','decoderMode','engine','label','purpose')
    (folder/'manifest.tsv').write_text(''.join('\t'.join(str(r[k]) for k in keys)+'\n' for r in rows),newline='\n')
    # Keep bridge measurement/controller byte-identical to the previous night.
    runner=previous/'runner.sh' if args.stage=='bridge' else ROOT/'tools/phone-power-runner.sh'
    (folder/'runner.sh').write_text(runner.read_text(encoding='utf8'),encoding='utf8',newline='\n')
    shutil.copyfile(previous/'PowerPlaybackBenchmarkTest.kt',folder/'PowerPlaybackBenchmarkTest.kt')
    shutil.copyfile(previous/'exo-reference.tsv',folder/'exo-reference.tsv')
    refs=baseline_reference(previous)
    (folder/'bridge-reference.json').write_text(json.dumps(refs,indent=2)+'\n')
    bridge=args.stage=='bridge'
    config=dict(MANIFEST_SHA=sha(folder/'manifest.tsv'),MANIFEST_ROWS=str(len(rows)),
        MAIN_SHA=baseline['packages']['main']['sha256'],LEGACY_TEST_SHA=sha(test),LOW_TEST_SHA=sha(test),
        EXPECTED_INITIAL_TEST_SHA=sha(test),LEGACY_TEST_APK=remote+'/power-test.apk',LOW_TEST_APK=remote+'/power-test.apk',
        RESTORE_TEST_APK='' if bridge else remote+'/original-test.apk',RESTORE_TEST_SHA=sha(old_test),
        COHORT=f'phone-{deadline:%Y%m%d}-hardware-v1',VALIDATION_ONLY='false',SLEEP_AT_END='false' if bridge else 'true',
        MINIMUM_COOLDOWN='90',WARMUP_SECONDS='90',MEASURE_SECONDS='240',SURFACE_PROTOCOL='current-app',
        FRAME_PROOF_METHOD='display-crop-v1',HARDWARE_ONLY_AUTOMATIC='true',SCREEN_TIMEOUT_MS='86400000',
        RESTORE_SCREEN_TIMEOUT='86400000' if bridge else baseline['settingsBefore']['screen_off_timeout'],
        RESTORE_BLUETOOTH='0' if bridge else baseline['settingsBefore']['bluetooth_on'],
        STOP_STARTING_EPOCH=str(int(deadline.timestamp())),OPTIONAL_BLOCK_BUDGET_SECONDS='2400',
        HARD_STOP_EPOCH=str(int(deadline.timestamp())))
    (folder/'config.env').write_text(''.join(k+'='+shlex.quote(v)+'\n' for k,v in config.items()),newline='\n')
    plan=dict(schemaVersion=3,frozenUtc=dt.datetime.now(dt.timezone.utc).isoformat(),serial=args.serial,
        stage=args.stage,remoteDirectory=remote,measurementCohort=config['COHORT'],legs=rows,fixtures=fixtures,
        mainApkSha256=config['MAIN_SHA'],testApkSha256=config['LEGACY_TEST_SHA'],originalTestApkSha256=config['RESTORE_TEST_SHA'],
        warmupSeconds=90,measurementSeconds=240,minimumCooldownSeconds=90,maxStartTemperatureC=36,maxTemperatureC=40,
        screenTimeoutMs=86400000,surfaceProtocol='current-app',frameProofMethod='display-crop-v1',
        baselineReference=refs,previousPlanSha256=sha(previous/'run-plan.json'),
        repeatFullHardware=args.repeat_full_hardware,hardStopLocal=deadline.isoformat(),restoreByLocal=restore_by.isoformat(),
        optionalSoftwareRule='AV1 short preflight per engine, then both orders for Exo/mpv and mpv/VLC only if both block engines passed preflight; then HEVC both orders Exo/mpv, then AVC both orders Exo/mpv. VLC HEVC/AVC native software is not classifiable by the unchanged test APK and is not scheduled. Only start an optional complete four-leg block with 40 minutes remaining. Hard deadline overrides unfinished legs.',
        observer='No host activity during playback; bridge is collected only after its exit, all later data only after exit or granted collection window.',
        stopPolicy='Physical discharge; restore original test/settings and sleep at final completion/deadline. Bridge holds 24h timeout and test APK for continuation; night-level deadline recovery remains active.',
        sourceHead=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip())
    for name in ('collect-power-night.py','summarize-power-benchmark.py','prepare-power-continuation.py'):
        shutil.copyfile(ROOT/'tools'/name,folder/name)
    plan['frozenFiles']={f.name:sha(f) for f in folder.iterdir() if f.is_file()}
    (folder/'run-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    if args.deploy:
        adb=['adb','-s',args.serial]
        for package,expected in ((PACKAGE,config['MAIN_SHA']),(PACKAGE+'.test',config['LEGACY_TEST_SHA'])):
            path=subprocess.check_output(adb+['shell','pm','path',package],timeout=30).decode().strip().removeprefix('package:')
            actual=subprocess.check_output(adb+['shell','sha256sum',path],timeout=60).decode().split()[0]
            assert actual==expected,package
        subprocess.run(adb+['shell','mkdir','-p',remote.rsplit('/',1)[0]],check=True)
        subprocess.run(adb+['shell','mkdir',remote],check=True)
        for path in folder.iterdir():
            if path.name in ('manifest.tsv','config.env','runner.sh','exo-reference.tsv','bridge-reference.json','run-plan.json'):
                subprocess.run(adb+['push',str(path),remote+'/'+path.name],check=True)
        for path,name in ((test,'power-test.apk'),(old_test,'original-test.apk')):
            subprocess.run(adb+['push',str(path),remote+'/'+name],check=True)
        subprocess.run(adb+['shell','sh','-n',remote+'/runner.sh'],check=True)
    print(json.dumps(dict(folder=str(folder),plannedRows=len(rows),stage=args.stage,planSha256=sha(folder/'run-plan.json')),indent=2))

if __name__=='__main__': main()
