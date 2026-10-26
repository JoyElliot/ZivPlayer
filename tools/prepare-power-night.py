#!/usr/bin/env python3
"""Freeze the hardware rerun, conditional Exo follow-up and time-gated AV1 supplement."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'io.github.joyelliot.zivplayer'
CODECS = ('avc', 'hevc', 'hevc10', 'vp9')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build_legs(name, codecs=CODECS, full_matrix=False):
    legs = []
    def add(codec, mode, engine, purpose, block, pos, order, pair):
        label = f'{name}-{pair}-b{block}-{codec}-{mode}-{pos}-{engine}'
        legs.append(dict(ordinal=len(legs)+1, blockNumber=block,
                         comparisonBlock=f'{name}:{pair}:{codec}:{mode}:b{block}',
                         plannedOrdinal=pos, codec=codec, decoderMode=mode, engine=engine,
                         label=label, purpose=purpose, plannedOrder=order))
    if not full_matrix:
        for codec in codecs:
            add(codec, 'automatic', 'exo', 'exo-screen', 1, 1, ['exo'], 'screen')
    for pair, codecs, mode, purpose, engines in (
        ('mpv-vlc', codecs, 'automatic', 'matrix', ('libmpv', 'vlc')),
        ('exo-mpv', codecs, 'automatic', 'matrix' if full_matrix else 'exo-conditional', ('exo', 'libmpv')),
        ('av1-exo-mpv', ('av1',), 'software', 'av1-conditional', ('exo', 'libmpv')),
        ('av1-mpv-vlc', ('av1',), 'software', 'av1-conditional', ('libmpv', 'vlc')),
    ):
        a, b = engines
        for block, order in enumerate(([a, b, b, a], [b, a, a, b]), 1):
            for codec in codecs:
                for pos, engine in enumerate(order, 1):
                    add(codec, mode, engine, purpose, block, pos, order, pair)
    return legs


def cohort_name(name, cutoff, main_sha, test_sha, renderer):
    return f'phone-{cutoff:%Y%m%d}-{name}-{main_sha[:12]}-{test_sha[:12]}-{renderer}'


def verify_fixture_coverage(legs, fixtures):
    required = {leg['codec']+'-1080p24.mkv' for leg in legs}
    present = {row['file'] for row in fixtures}
    assert required <= present, 'Stage and verify missing fixtures: '+', '.join(sorted(required-present))


def snapshot_sources(destination):
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT)
    with tarfile.open(destination, 'w:gz', dereference=False) as archive:
        for name in sorted(set(names.decode().split('\0'))-{''}):
            path = ROOT/name
            if path.is_file() and not path.is_symlink():
                archive.add(path, arcname=name, recursive=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--serial', required=True)
    p.add_argument('--name', default='night01')
    p.add_argument('--av1-before', required=True, help='Timezone-aware ISO date, such as 2026-09-21T07:00:00+08:00')
    p.add_argument('--deploy', action='store_true')
    p.add_argument('--codecs', nargs='+', choices=CODECS, default=list(CODECS))
    p.add_argument('--full-matrix', action='store_true', help='Measure new engine builds without historical Exo screening/skips.')
    p.add_argument('--mpv-video-output', choices=('gpu-next', 'gpu'), default='gpu-next')
    p.add_argument('--original-test-apk', type=Path, help='The backed-up test APK from this device baseline.')
    p.add_argument('--artifact-report', type=Path, required=True, help='Successful verify-android-artifact.py report for the new main APK.')
    args = p.parse_args()
    assert args.name.isascii() and args.name.isalnum() and len(args.name) <= 20, 'Use 1-20 ASCII letters or digits for --name.'
    cutoff = dt.datetime.fromisoformat(args.av1_before)
    assert cutoff.tzinfo is not None
    out = args.output.resolve()
    folder = out / args.name
    folder.mkdir(exist_ok=False)
    baseline = json.loads((out/'device-baseline.json').read_text(encoding='utf8'))
    fixtures = json.loads((out/'persistent-fixtures.json').read_text(encoding='utf8'))['fixtures']
    remote = f'/data/local/tmp/ziv-power-{cutoff:%Y%m%d}/{args.name}'
    test_sha = sha(out/'power-test.apk')
    old_test = args.original_test_apk or ROOT/'native/out/power-comparison-20260913/artifacts/legacy-test.apk'
    assert sha(old_test) == baseline['packages']['test']['sha256']
    artifact = json.loads(args.artifact_report.read_text(encoding='utf8'))
    assert artifact['apkSha256'] == baseline['packages']['main']['sha256'], 'Artifact audit must match the installed main APK.'
    source_summary = ROOT/'native/out/power-comparison-20260913/phone01/analysis-input/power-summary.json'
    historical = [] if args.full_matrix else json.loads(source_summary.read_text(encoding='utf8'))['runs']
    refs = {}
    for codec in (() if args.full_matrix else args.codecs):
        rows = [r for r in historical if r['engine']=='exo' and r['mode']=='automatic'
                and r['fixture']==codec+'-1080p24.mkv' and r['qualified'] and r['actualSoftwareDecoder'] is False]
        assert len(rows) >= 3
        values = [r['meanPowerMw'] for r in rows]
        refs[codec] = dict(minMw=min(values), maxMw=max(values), runs=[r['runLabel'] for r in rows])
    (folder/'exo-reference.tsv').write_text(''.join(f'{c}\t{v["minMw"]:.9f}\t{v["maxMw"]:.9f}\n' for c,v in refs.items()), encoding='utf8', newline='\n')
    legs = build_legs(args.name, tuple(dict.fromkeys(args.codecs)), args.full_matrix)
    verify_fixture_coverage(legs, fixtures)
    keys = ('ordinal','blockNumber','comparisonBlock','plannedOrdinal','codec','decoderMode','engine','label','purpose')
    (folder/'manifest.tsv').write_text(''.join('\t'.join(str(leg[k]) for k in keys)+'\n' for leg in legs), encoding='utf8', newline='\n')
    shutil.copyfile(ROOT/'tools/phone-power-runner.sh', folder/'runner.sh')
    shutil.copyfile(ROOT/'apps/android/src/androidTest/kotlin/io/github/joyelliot/zivplayer/PowerPlaybackBenchmarkTest.kt', folder/'PowerPlaybackBenchmarkTest.kt')
    shutil.copyfile(ROOT/'apps/android/src/androidTest/kotlin/io/github/joyelliot/zivplayer/PowerCodecLogCapture.kt', folder/'PowerCodecLogCapture.kt')
    shutil.copyfile(args.artifact_report, folder/'artifact-verification.json')
    snapshot_sources(folder/'source-snapshot.tar.gz')
    config = dict(MANIFEST_SHA=sha(folder/'manifest.tsv'), MANIFEST_ROWS=str(len(legs)),
        MAIN_SHA=baseline['packages']['main']['sha256'], LEGACY_TEST_SHA=test_sha, LOW_TEST_SHA=test_sha,
        EXPECTED_INITIAL_TEST_SHA=test_sha, LEGACY_TEST_APK=remote+'/power-test.apk', LOW_TEST_APK=remote+'/power-test.apk',
        RESTORE_TEST_APK=remote+'/original-test.apk', RESTORE_TEST_SHA=baseline['packages']['test']['sha256'],
        COHORT=cohort_name(args.name, cutoff, baseline['packages']['main']['sha256'], test_sha, args.mpv_video_output),
        MPV_VIDEO_OUTPUT=args.mpv_video_output, VALIDATION_ONLY='false', SLEEP_AT_END='true',
        MINIMUM_COOLDOWN='90', WARMUP_SECONDS='90', MEASURE_SECONDS='240', SURFACE_PROTOCOL='current-app',
        FRAME_PROOF_METHOD='display-crop-v1', HARDWARE_ONLY_AUTOMATIC='true', SCREEN_TIMEOUT_MS='86400000',
        RESTORE_SCREEN_TIMEOUT=baseline['settingsBefore']['screen_off_timeout'], RESTORE_BLUETOOTH=baseline['settingsBefore']['bluetooth_on'],
        AV1_BEFORE_EPOCH=str(int(cutoff.timestamp())))
    (folder/'config.env').write_text(''.join(k+'='+shlex.quote(v)+'\n' for k,v in config.items()), encoding='utf8', newline='\n')
    plan = dict(schemaVersion=3, buildIdentityProtocol=1, mpvVideoOutput=args.mpv_video_output,
        nativeSourceManifestSha256=sha(ROOT/'native/source-manifest.toml'),
        frozenUtc=dt.datetime.now(dt.timezone.utc).isoformat(), serial=args.serial,
        remoteDirectory=remote, measurementCohort=config['COHORT'], legs=legs, fixtures=fixtures,
        mainApkSha256=config['MAIN_SHA'], testApkSha256=test_sha, originalTestApkSha256=config['RESTORE_TEST_SHA'],
        warmupSeconds=90, measurementSeconds=240, minimumCooldownSeconds=90, maxStartTemperatureC=36,
        maxTemperatureC=40, screenTimeoutMs=86400000, surfaceProtocol='current-app', frameProofMethod='display-crop-v1',
        baselineReference=dict(path=None if args.full_matrix else str(source_summary),
            sha256=None if args.full_matrix else sha(source_summary), codecs=refs,
            rule='Full new-build matrix; historical screening disabled.' if args.full_matrix else 'One qualified 240s Exo screening leg per hardware codec. If its mean is within the prior September 13 qualified independent-leg min/max, skip that codec Exo follow-up; otherwise run two complete Exo/mpv ABBA and BAAB blocks.',
            interpretation='Observed-range screening only, not statistical equivalence. New APK and measurement method; no pooling with historical cohorts, no dependency-only attribution.'),
        conditionalAv1=dict(beforeLocal=cutoff.isoformat(), beforeEpoch=int(cutoff.timestamp()),
            rule='Evaluate once after all hardware rows (including required Exo follow-up); start AV1 software only if earlier than cutoff. Once started finish both pairwise balanced comparisons even if cutoff passes.'),
        observer='No host transfers during sampling. Collect only after exit.json or a granted collection window.',
        stopPolicy='Physical discharge only; stop starting legs below 10%, per-sample guard 5%; preserve failures. Restore original test APK/settings and sleep on finish.',
        sourceHead=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT).decode().strip())
    plan['frozenFiles']={n:sha(folder/n) for n in ('manifest.tsv','config.env','runner.sh','PowerPlaybackBenchmarkTest.kt',
        'PowerCodecLogCapture.kt','source-snapshot.tar.gz','artifact-verification.json','exo-reference.tsv')}
    for name in ('prepare-power-night.py', 'collect-power-night.py', 'summarize-power-benchmark.py'):
        shutil.copyfile(ROOT/'tools'/name, folder/name)
        plan['frozenFiles'][name] = sha(folder/name)
    (folder/'run-plan.json').write_text(json.dumps(plan,indent=2)+'\n', encoding='utf8')
    (folder/'source-diff.patch').write_bytes(subprocess.check_output(['git','diff','--binary'],cwd=ROOT))
    if args.deploy:
        adb=['adb','-s',args.serial]
        for package, expected in ((PACKAGE,config['MAIN_SHA']),(PACKAGE+'.test',test_sha)):
            path=subprocess.check_output(adb+['shell','pm','path',package],timeout=30).decode().strip().removeprefix('package:')
            installed=subprocess.check_output(adb+['shell','sha256sum',path],timeout=60).decode().split()[0]
            assert installed==expected, 'Install and validate the frozen APK before deployment: '+package
        subprocess.run(adb+['shell','mkdir','-p',remote.rsplit('/',1)[0]],check=True)
        subprocess.run(adb+['shell','mkdir',remote],check=True)  # Refuse any stale same-name run.
        for n in ('manifest.tsv','config.env','runner.sh','exo-reference.tsv','run-plan.json'):
            subprocess.run(adb+['push',str(folder/n),remote+'/'+n],check=True)
        for path,name in ((out/'power-test.apk','power-test.apk'),(old_test,'original-test.apk')):
            subprocess.run(adb+['push',str(path),remote+'/'+name],check=True)
        subprocess.run(adb+['shell','sh','-n',remote+'/runner.sh'],check=True)
    hardware = [leg for leg in legs if leg['decoderMode']=='automatic']
    minimum_hardware = len(hardware) if args.full_matrix else len(hardware)-8*len(set(args.codecs))
    print(json.dumps(dict(folder=str(folder),remote=remote,plannedRows=len(legs),minimumHardwareRows=minimum_hardware,
                         maximumHardwareRows=len(hardware),av1OptionalRows=16,planSha256=sha(folder/'run-plan.json')),indent=2))


if __name__ == '__main__':
    main()
