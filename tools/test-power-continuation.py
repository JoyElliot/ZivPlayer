"""Offline policy checks for the overnight continuation and its deadline decisions."""
import datetime as dt
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tools'/file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
prepare=module('continuation','prepare-power-continuation.py')
bridge=module('bridge','review-power-bridge.py')
collector=module('collector','collect-power-night.py')
launcher=module('launcher','launch-power-continuation.py')

class ContinuationTests(unittest.TestCase):
    def test_mirrored_bridge_keeps_six_distinct_non_pairwise_legs(self):
        rows=prepare.build_legs('bridge','bridge')
        self.assertEqual(['exo','libmpv','vlc','vlc','libmpv','exo'],[r['engine'] for r in rows])
        self.assertEqual(6,len({r['label'] for r in rows}))
        self.assertTrue(all(r['plannedOrdinal']==1 and len(r['plannedOrder'])==1 for r in rows))

    def test_hardware_continuation_replaces_whole_baab_blocks(self):
        rows=prepare.build_legs('resume','continuation')
        hardware=[r for r in rows if r['decoderMode']=='automatic']
        self.assertEqual(16,len(hardware))
        for codec in prepare.CODECS:
            legs=[r for r in hardware if r['codec']==codec]
            self.assertEqual(['libmpv','exo','exo','libmpv'],[r['engine'] for r in legs])
            self.assertEqual([1,2,3,4],[r['plannedOrdinal'] for r in legs])

    def test_optional_scope_does_not_claim_unsupported_vlc_software(self):
        rows=prepare.build_legs('resume','continuation')
        self.assertEqual(51,len(rows))
        formal=[r for r in rows if r['purpose']=='optional-software']
        self.assertEqual(['av1']*16+['hevc']*8+['avc']*8,[r['codec'] for r in formal])
        self.assertFalse(any(r['engine']=='vlc' and r['codec']!='av1' for r in formal))
        preflight=[r for r in rows if r['purpose']=='software-preflight']
        self.assertEqual(3,len(preflight))
        self.assertTrue(all(r['validationOnly'] and r['measurementSeconds']==65 for r in preflight))

    def test_every_formal_block_is_complete_and_single_night(self):
        rows=prepare.build_legs('resume','continuation')
        blocks={}
        for row in rows:
            if row['purpose']!='software-preflight': blocks.setdefault(row['comparisonBlock'],[]).append(row)
        self.assertEqual(12,len(blocks))
        for legs in blocks.values():
            self.assertEqual([1,2,3,4],[r['plannedOrdinal'] for r in legs])
            order=[r['engine'] for r in legs]
            self.assertEqual(order,order[::-1])
            self.assertEqual(2,len(set(order)))

    def test_large_drift_option_adds_complete_hardware_counter_orders(self):
        rows=prepare.build_legs('resume','continuation',True)
        self.assertEqual(32,sum(r['decoderMode']=='automatic' for r in rows))
        self.assertEqual(67,len(rows))

    def test_hong_kong_deadline_is_this_morning(self):
        self.assertEqual(1790031900,int(dt.datetime.fromisoformat('2026-09-22T07:05:00+08:00').timestamp()))
        self.assertEqual(900,int((dt.datetime.fromisoformat('2026-09-22T07:20:00+08:00')-dt.datetime.fromisoformat('2026-09-22T07:05:00+08:00')).total_seconds()))

    def test_bridge_gate_rejects_qualifier_failure_or_missing_repeat(self):
        legs=prepare.build_legs('bridge','bridge')
        refs={f'avc:{e}':dict(count=2,meanMw=1200,minMw=1190,maxMw=1210,stdevMw=10) for e in ('exo','libmpv','vlc')}
        plan={'legs':legs,'baselineReference':{'groups':refs,'relativeMeanTolerancePercent':5}}
        rows=[dict(runLabel=r['label'],engine=r['engine'],meanPowerMw=1200,qualified=True) for r in legs]
        self.assertTrue(bridge.review(plan,{'runs':rows})['powerGatePassed'])
        rows[0]['qualified']=False
        self.assertFalse(bridge.review(plan,{'runs':rows})['executionQualified'])
        rows[0]['qualified']=True
        self.assertFalse(bridge.review(plan,{'runs':rows[:-1]})['executionQualified'])

    def test_large_power_change_requests_same_night_both_orders_not_pooling(self):
        legs=prepare.build_legs('bridge','bridge')
        refs={f'avc:{e}':dict(count=2,meanMw=1200,minMw=1190,maxMw=1210,stdevMw=10) for e in ('exo','libmpv','vlc')}
        plan={'legs':legs,'baselineReference':{'groups':refs,'relativeMeanTolerancePercent':5}}
        rows=[dict(runLabel=r['label'],engine=r['engine'],meanPowerMw=1400,qualified=True) for r in legs]
        result=bridge.review(plan,{'runs':rows})
        self.assertEqual('fresh_both_orders',result['recommendedHardwareScope'])
        self.assertFalse(result['crossDayPoolingAllowed'])
        self.assertFalse(result['statisticalEquivalenceEstablished'])

    def test_actual_shell_cancel_function_distinguishes_deadline(self):
        bash=Path('D:/Program Files/Git/bin/bash.exe')
        if not bash.exists(): self.skipTest('Git Bash unavailable')
        source=(ROOT/'tools/phone-power-runner.sh').read_text()
        function='cancelled() {'+source.split('cancelled() {',1)[1].split('\nstop_app()',1)[0]
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp).as_posix()
            def run(extra):
                script='BASE='+repr(base)+'\nHARD_STOP_UPTIME=0\nSTOP_REASON=initial\n'+function+'\n'+extra
                return subprocess.check_output([str(bash),'-c',script],text=True).strip()
            self.assertEqual('no',run('if cancelled; then echo "$STOP_REASON"; else echo no; fi'))
            (Path(temp)/'cancel').write_bytes(b'night_deadline\n')
            self.assertEqual('night_deadline',run('cancelled; echo "$STOP_REASON"'))
            (Path(temp)/'cancel').write_text('')
            self.assertEqual('user_cancelled',run('cancelled; echo "$STOP_REASON"'))
            (Path(temp)/'cancel').unlink()
            self.assertEqual('night_deadline',run('HARD_STOP_UPTIME=1; cancelled; echo "$STOP_REASON"'))

    def run_block_gate(self, remaining, *, software=False, validated=()):
        bash=Path('D:/Program Files/Git/bin/bash.exe')
        if not bash.exists(): self.skipTest('Git Bash unavailable')
        source=(ROOT/'tools/phone-power-runner.sh').read_text()
        start='    if [ "$HARD_STOP_UPTIME" -gt 0 ] && { [ "$PURPOSE" = optional-software ]'
        gate=start+source.split(start,1)[1].split('    if [ "$PURPOSE" = exo-conditional ]',1)[0]
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);(base/'decisions').mkdir()
            for engine in validated: (base/'decisions'/f'software-av1-{engine}-validated').touch()
            manifest=''.join(f'{pos}\t1\ttestblock\t{pos}\tav1\tsoftware\t{engine}\tleg{pos}\toptional-software\n' for pos,engine in enumerate(['exo','libmpv','libmpv','exo'],1))
            (base/'manifest.tsv').write_text(manifest,newline='\n')
            script='\n'.join(['BASE='+repr(base.as_posix()),f'HARD_STOP_UPTIME={100+remaining}',
                'OPTIONAL_BLOCK_BUDGET_SECONDS=2400','SKIPPED_BUDGET_BLOCK=','SKIPPED_BLOCK_REASON=',
                'PURPOSE='+('optional-software' if software else 'matrix'),'CODEC=av1','BLOCK_ID=testblock',
                'processed_rows=0','uptime_s() { echo 100; }','status() { echo "$1:$2"; }','fail() { exit 99; }',
                'for POSITION in 1 2 3 4; do','ORDINAL=$POSITION',gate,'echo RUN:$POSITION','done'])
            return subprocess.check_output([str(bash),'-c',script],text=True).strip().splitlines()

    def test_actual_shell_block_budget_never_starts_a_partial_new_block(self):
        accepted=self.run_block_gate(2400)
        self.assertEqual(['RUN:1','RUN:2','RUN:3','RUN:4'],accepted)
        rejected=self.run_block_gate(2399)
        self.assertEqual(['leg_skipped:insufficient_time_for_complete_block']*4,rejected)

    def test_actual_shell_software_gate_requires_both_engines(self):
        self.assertEqual(['leg_skipped:software_preflight_not_passed']*4,self.run_block_gate(4000,software=True,validated=['exo']))
        self.assertEqual(['RUN:1','RUN:2','RUN:3','RUN:4'],self.run_block_gate(4000,software=True,validated=['exo','libmpv']))

    def test_guard_never_signals_reused_or_wrong_group_pid(self):
        bash=Path('D:/Program Files/Git/bin/bash.exe')
        if not bash.exists(): self.skipTest('Git Bash unavailable')
        source=(ROOT/'tools/power-night-deadline-guard.sh').read_text()
        function='stop_recorded_group() {'+source.split('stop_recorded_group() {',1)[1].split('\nmkdir ',1)[0]
        def stat(start,group=123):
            return '123 (sh) '+' '.join(['S','1',str(group),'123']+['0']*15+[str(start)])+'\n'
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            (base/'saved').write_text(stat(100),newline='\n')
            for live,expected,group_exists in [(stat(200),0,False),(stat(100,456),1,False),(stat(200),1,True)]:
                (base/'live').write_text(live,newline='\n')
                script='\n'.join(['BASE='+repr(base.as_posix()),function,
                    'cat() { case "$1" in /proc/*/stat) command cat "$BASE/live" ;; *) command cat "$@" ;; esac; }',
                    'kill() { if [ "$1" = -0 ]; then return '+('0' if group_exists else '1')+'; fi; echo UNEXPECTED_SIGNAL; return 99; }','sleep() { :; }',
                    'stop_recorded_group "$BASE/saved"; echo RESULT:$?'])
                output=subprocess.check_output([str(bash),'-c',script],text=True).strip()
                self.assertEqual(f'RESULT:{expected}',output)

    def test_launcher_rejects_expired_deadline_and_parses_session(self):
        with self.assertRaises(AssertionError): launcher.require_time(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1))
        sample='123 (a name) '+' '.join(['S','1','123','123']+['0']*15+['999'])
        self.assertEqual((123,123,999),launcher.identity(sample))

    def run_collector_without_runner_exit(self, *, live=False, wrong_pointer=False):
        frozen={'manifest.tsv':b'', 'config.env':b'MANIFEST_ROWS=0\n', 'exo-reference.tsv':b''}
        plan=dict(remoteDirectory='/data/local/tmp/ziv-power-20260922/test',legs=[],fixtures=[],
                  frozenFiles={name:hashlib.sha256(data).hexdigest() for name,data in frozen.items()})
        saved='123 (sh) '+' '.join(['S','1','123','123']+['0']*15+['999'])
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w') as archive:
            for name,data in frozen.items():
                entry=tarfile.TarInfo(name);entry.size=len(data);archive.addfile(entry,io.BytesIO(data))
            for name in ('raw','control','decisions'):
                entry=tarfile.TarInfo(name);entry.type=tarfile.DIRTYPE;archive.addfile(entry)
            archive.addfile(tarfile.TarInfo('events.tsv'),io.BytesIO())
        def run(cmd, **kwargs):
            if cmd[-1].endswith('/exit.json'): return subprocess.CompletedProcess(cmd,1,b'',b'missing')
            if cmd[-1]=='/proc/123/stat': return subprocess.CompletedProcess(cmd,0 if live else 1,saved.encode() if live else b'',b'')
            if cmd[-1]=='kill -0 -- -123': return subprocess.CompletedProcess(cmd,1,b'',b'')
            self.fail('Unexpected mocked command: '+str(cmd))
        def output(cmd, **kwargs):
            last=cmd[-1]
            if last.endswith('/night-guard/exit.json'): return b'{"action":"deadline_recovery","cleanupSucceeded":true}'
            if last.endswith('/active-run.txt'): return (plan['remoteDirectory']+('-wrong' if wrong_pointer else '')).encode()
            if last.endswith('/runner-process-stat.txt'): return saved.encode()
            if 'ls' in cmd: return b'manifest.tsv\nconfig.env\nexo-reference.tsv\nraw\ncontrol\ndecisions\nevents.tsv\n'
            if 'exec-out' in cmd:
                self.assertIn('tar',cmd)
                self.assertNotIn('sh',cmd)
                return payload.getvalue()
            self.fail('Unexpected mocked command: '+str(cmd))
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'run-plan.json';path.write_text(json.dumps(plan))
            with mock.patch.object(sys,'argv',['collect','--plan',str(path),'--serial','offline']),mock.patch.object(collector.subprocess,'run',side_effect=run),mock.patch.object(collector.subprocess,'check_output',side_effect=output),contextlib.redirect_stdout(io.StringIO()):
                collector.main()
            return json.loads((Path(temp)/'audit.json').read_text())

    def test_guard_finished_collection_retains_distinct_forced_stop(self):
        result=self.run_collector_without_runner_exit()
        self.assertEqual('deadline_guard_after_missing_runner_exit',result['exitSource'])
        self.assertEqual('deadline_guard_forced_stop',result['exit']['stopReason'])
        self.assertEqual(0,result['qualifiedRows'])

    def test_guard_collection_cannot_archive_live_runner(self):
        with self.assertRaisesRegex(AssertionError,'Runner still alive'): self.run_collector_without_runner_exit(live=True)

    def test_guard_collection_requires_matching_active_phase(self):
        with self.assertRaisesRegex(AssertionError,'matching completed'): self.run_collector_without_runner_exit(wrong_pointer=True)

    def test_guard_replacement_launch_failure_preserves_original_guard(self):
        stat='123 (sh) '+' '.join(['S','1','123','123']+['0']*15+['999'])
        exit_record=dict(stopReason='manifest_completed',cleanupSucceeded=True)
        commands=[]
        def output(cmd,**kwargs):
            commands.append(cmd)
            if cmd[-1].endswith('/exit.json'): return json.dumps(exit_record).encode()
            if cmd[-1].endswith('/guard-process-stat.txt') or cmd[-1]=='/proc/123/stat': return stat.encode()
            if 'nohup' in cmd[-1]: raise RuntimeError('simulated replacement launch failure')
            return b''
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp); bridge_dir=out/'bridge';bridge_dir.mkdir();(out/'night-guard').mkdir()
            deadline=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=2)).isoformat()
            records={'run-plan.json':dict(hardStopLocal=deadline,remoteDirectory='/data/local/tmp/ziv-power-20260922/bridge'),
                     'bridge-review.json':dict(executionQualified=True), 'audit.json':dict(exit=exit_record)}
            for name,data in records.items(): (bridge_dir/name).write_text(json.dumps(data))
            (out/'continuation-ready.json').write_text('{"ready":true,"toolSha256":{}}')
            argv=['launch','--output',str(out),'--bridge',str(bridge_dir),'--previous',str(out/'previous'),'--serial','offline']
            with mock.patch.object(sys,'argv',argv),mock.patch.object(launcher.subprocess,'check_output',side_effect=output),mock.patch.object(launcher.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')):
                with self.assertRaisesRegex(RuntimeError,'replacement launch failure'): launcher.main()
            self.assertFalse(any('kill -TERM' in str(cmd) or 'kill -KILL' in str(cmd) for cmd in commands))

if __name__=='__main__': unittest.main()
