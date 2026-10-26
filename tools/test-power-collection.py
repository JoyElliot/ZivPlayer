"""An incomplete device archive must remain auditable and never qualify a leg."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import copy

spec = importlib.util.spec_from_file_location('collection', Path(__file__).with_name('collect-power-night.py'))
collection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collection)


class IncompleteArchiveTest(unittest.TestCase):
    def test_planned_hardware_renderer_blocks_reject_wrong_renderer_or_software(self):
        order=['libmpv@gpu-next','libmpv@gpu','libmpv@gpu','libmpv@gpu-next']
        scenario=dict(airplaneMode=1,wifiOn=2,bluetoothOn=0,brightnessMode=0,systemBrightness=1,
                      windowBrightness=-1,musicMuted=True,musicVolume=0)
        for fault in (None,'renderer_start','renderer_end','software','unknown_decoder','ordinal','missing_leg'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                folder=Path(tmp);files={'manifest.tsv':b'manifest','config.env':b'config','exo-reference.tsv':b'',
                    'events.tsv':b'', 'decisions/record':b''};legs=[]
                for pos,condition in enumerate(order,1):
                    renderer=condition.partition('@')[2];label=f'leg{pos}'
                    leg=dict(label=label,engine='libmpv',decoderMode='automatic',codec='avc',
                        comparisonBlock='block',plannedOrdinal=pos,plannedOrder=['libmpv']*4,
                        plannedConditions=order,mpvVideoOutput=renderer,warmupSeconds=1,measurementSeconds=2)
                    legs.append(leg)
                    if fault=='missing_leg' and pos==1:continue
                    samples=[dict(phase='steady',elapsedMs=t*1000,currentNowUa=500000,voltageMv=4000,
                        plugged=0,batteryStatus=3,temperatureTenthsC=300,capacityPercent=70,
                        chargeCounterRaw=100,processCpuMs=t,screenInteractive=True,
                        surfaceBufferWidth=1920,surfaceBufferHeight=1080,displayRefreshRate=120) for t in range(3)]
                    report=dict(runLabel=label,requestedEngine='libmpv',requestedDecoderMode='automatic',
                        mainApkSha256='main',testApkSha256='test',fixture='avc-1080p24.mkv',fixtureSha256='fixture',
                        measurementCohort='cohort',requestedMpvVideoOutput=renderer,
                        diagnosticsStart=dict(videoOutput=renderer),diagnosticsEnd=dict(videoOutput=renderer),
                        actualSoftwareDecoder=False,requireHardwareDecoder=True,completed=True,cleanupSucceeded=True,
                        measurementQualified=True,playbackValidationPassed=True,validationOnly=False,
                        surfaceProtocol='current-app',frameProofMethod='display-crop-v1',warmupSeconds=1,measurementSeconds=2,
                        samples=samples,environmentBefore=scenario,environmentAfter=scenario)
                    control=dict(preflightBattery=dict(level=70,temperatureTenthsC=300),testApkSha256='test',
                        exitCode=0,finalResultSeen=True,commandTimedOut=False,freshProcessVerified=True,
                        cooldownSeconds=31,comparisonBlock='block',plannedOrdinal=pos,
                        networkMonitorAlive=True,networkStartBytes=0,networkEndBytes=0)
                    if pos==1:
                        if fault=='renderer_start':report['diagnosticsStart']['videoOutput']='gpu'
                        if fault=='renderer_end':report['diagnosticsEnd']['videoOutput']='gpu'
                        if fault=='software':report['actualSoftwareDecoder']=True
                        if fault=='unknown_decoder':report['actualSoftwareDecoder']=None
                        if fault=='ordinal':control['plannedOrdinal']=2
                    warmup=copy.deepcopy(samples[0]);warmup['phase']='warmup'
                    files[f'raw/{label}.json']=json.dumps(report).encode()
                    files[f'raw/{label}.jsonl']='\n'.join(json.dumps(s) for s in [warmup,*samples]).encode()
                    files[f'raw/{label}.instrumentation.log']=b'OK (1 test)\nINSTRUMENTATION_CODE: -1\n'
                    files[f'control/{label}.control.json']=json.dumps(control).encode()
                archive=folder/'finished-device.tar'
                with tarfile.open(archive,'w') as output:
                    for name,raw in files.items():
                        info=tarfile.TarInfo(name);info.size=len(raw);output.addfile(info,io.BytesIO(raw))
                (folder/'finished-device.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest())
                plan=dict(remoteDirectory='/data/local/tmp/ziv-power-20261018/night',
                    fixtures=[dict(file='avc-1080p24.mkv',sha256='fixture')],buildIdentityProtocol=1,
                    mainApkSha256='main',testApkSha256='test',measurementCohort='cohort',
                    frameProofMethod='display-crop-v1',minimumCooldownSeconds=30,maxStartTemperatureTenthsC=345,
                    frozenFiles={n:hashlib.sha256(files[n]).hexdigest() for n in ('manifest.tsv','config.env','exo-reference.tsv')},legs=legs)
                path=folder/'run-plan.json';path.write_text(json.dumps(plan))
                with patch('sys.argv',['collect-power-night.py','--plan',str(path),'--serial','mock']), \
                     patch.object(collection.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'{}')), \
                     contextlib.redirect_stdout(io.StringIO()):collection.main()
                audit=json.loads((folder/'audit.json').read_text())
                self.assertEqual(4 if fault is None else 3,audit['qualifiedRows'])
                self.assertEqual(fault is None,any(b['complete'] for b in audit['controlledConditionBlocks']))

    def test_missing_logs_are_reported_without_aborting_collection(self):
        for with_instrumentation in (False, True):
            with self.subTest(with_instrumentation=with_instrumentation), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                files = {'manifest.tsv': b'manifest', 'config.env': b'config', 'exo-reference.tsv': b'',
                         'raw/leg.json': b'{}', 'control/leg.control.json': b'{"preflightBattery":{"level":100}}'}
                if with_instrumentation:
                    files['raw/leg.instrumentation.log'] = b'OK (1 test)\nINSTRUMENTATION_CODE: -1\n'
                archive = folder/'finished-device.tar'
                with tarfile.open(archive, 'w') as output:
                    for name, raw in files.items():
                        info = tarfile.TarInfo(name); info.size = len(raw)
                        output.addfile(info, io.BytesIO(raw))
                (folder/'finished-device.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest())
                plan = dict(remoteDirectory='/data/local/tmp/ziv-power-20261018/night', fixtures=[],
                            testApkSha256='test', measurementCohort='cohort', frameProofMethod='display-crop-v1',
                            frozenFiles={name: hashlib.sha256(files[name]).hexdigest()
                                         for name in ('manifest.tsv', 'config.env', 'exo-reference.tsv')},
                            legs=[dict(label='leg', engine='libmpv', decoderMode='automatic',
                                       plannedOrder=['libmpv'], codec='avc')])
                plan_path = folder/'run-plan.json'; plan_path.write_text(json.dumps(plan))
                with patch('sys.argv', ['collect-power-night.py', '--plan', str(plan_path), '--serial', 'mock']), \
                        patch.object(collection.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'{}')), \
                        contextlib.redirect_stdout(io.StringIO()):
                    collection.main()
                audit = json.loads((folder/'audit.json').read_text())
                self.assertEqual(['missing_events', 'missing_decisions'], audit['collectionIssues'])
                self.assertEqual(0, audit['qualifiedRows'])
                self.assertFalse(audit['audit'][0]['qualified'])
                if not with_instrumentation:
                    self.assertIn('missing_instrumentation_log', audit['audit'][0]['issues'])


if __name__ == '__main__': unittest.main()
