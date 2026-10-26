#!/usr/bin/env python3
"""Launch the next frozen phase only after the bridge has been archived and reviewed."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def identity(stat):
    fields=stat.rsplit(') ',1)[1].split()
    return int(fields[2]),int(fields[3]),int(fields[19])

def require_time(deadline, remaining=120):
    assert (deadline-dt.datetime.now(dt.timezone.utc)).total_seconds()>remaining, 'Insufficient time before hard stop.'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--bridge',type=Path,required=True)
    p.add_argument('--previous',type=Path,required=True)
    p.add_argument('--serial',required=True)
    p.add_argument('--name',default='resume22')
    args=p.parse_args()
    assert args.name.isalnum() and len(args.name)<=20
    out=args.output.resolve();bridge=args.bridge.resolve();previous=args.previous.resolve()
    readiness=json.loads((out/'continuation-ready.json').read_text())
    assert readiness['ready'] is True
    for name,expected in readiness['toolSha256'].items():
        assert sha(ROOT/'tools'/name)==expected, 'Reviewed continuation tool changed: '+name
    bridge_plan=json.loads((bridge/'run-plan.json').read_text())
    review=json.loads((bridge/'bridge-review.json').read_text())
    audit=json.loads((bridge/'audit.json').read_text())
    assert review['executionQualified'], 'Diagnose the bridge before launching any continuation.'
    assert audit['exit']['stopReason']=='manifest_completed' and audit['exit']['cleanupSucceeded']
    deadline=dt.datetime.fromisoformat(bridge_plan['hardStopLocal'])
    require_time(deadline,2520)
    assert not (out/args.name).exists(), 'Never deploy or launch a duplicate phase; inspect its existing records.'
    adb=['adb','-s',args.serial]
    def call(*parts,timeout=60): return subprocess.check_output(adb+list(parts),timeout=timeout)
    def wait_json(path, seconds=15):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            result=subprocess.run(adb+['shell','cat',path],capture_output=True,timeout=5)
            if result.returncode==0:
                try: return json.loads(result.stdout)
                except json.JSONDecodeError: pass
            time.sleep(.5)
        raise RuntimeError('Startup record not ready: '+path)
    def verify_session(stat_path, expected_pid):
        saved=call('shell','cat',stat_path,timeout=5).decode().strip()
        assert int(saved.split(' ',1)[0])==expected_pid
        expected=identity(saved)
        assert expected[:2]==(expected_pid,expected_pid), 'Process has no isolated session.'
        live=call('shell','cat',f'/proc/{expected_pid}/stat',timeout=5).decode().strip()
        assert identity(live)==expected and live.rsplit(') ',1)[1].split()[0]!='Z', 'Process identity is no longer live.'
        return expected
    # Device work is now outside every completed bridge leg.
    current_exit=json.loads(call('shell','cat',bridge_plan['remoteDirectory']+'/exit.json'))
    assert current_exit==audit['exit']
    guard_remote=bridge_plan['remoteDirectory'].rsplit('/',1)[0]+'/night-guard'
    guard_local=out/'night-guard'
    original_stat=call('shell','cat',guard_remote+'/guard-process-stat.txt').decode().strip()
    guard_pid=int(original_stat.split(' ',1)[0])
    saved_identity=identity(original_stat)
    assert saved_identity[:2]==(guard_pid,guard_pid)
    live_stat=call('shell','cat',f'/proc/{guard_pid}/stat').decode().strip()
    assert identity(live_stat)==saved_identity, 'Guard PID identity changed; do not terminate another process.'
    (guard_local/'v1-process-stat-at-upgrade.txt').write_text(original_stat+'\n')
    # Start and verify the replacement in its own lock/session before stopping the original guard.
    guard_v2=guard_local/'guard-v2.sh'
    guard_v2.write_text((ROOT/'tools/power-night-deadline-guard.sh').read_text(),newline='\n')
    subprocess.run(adb+['push',str(guard_v2),guard_remote+'/guard-v2.sh'],check=True,timeout=60)
    call('shell','sh','-n',guard_remote+'/guard-v2.sh')
    require_time(deadline)
    call('shell',f'test ! -e {guard_remote}/guard-v2-ready.json')
    call('shell',f'/system/bin/toybox nohup /system/bin/setsid sh {guard_remote}/guard-v2.sh {guard_remote} </dev/null >{guard_remote}/guard-v2.log 2>&1 &')
    ready=wait_json(guard_remote+'/guard-v2-ready.json')
    assert ready['version']==2
    guard_pid_v2=int(ready['pid'])
    new_identity=verify_session(guard_remote+'/guard-v2-process-stat.txt',guard_pid_v2)
    assert (guard_pid_v2,new_identity[2])!=(guard_pid,saved_identity[2])
    # Recheck the original identity after the upload/ready wait, immediately before signalling it.
    verify_session(guard_remote+'/guard-process-stat.txt',guard_pid)
    call('shell',f'kill -TERM -- -{guard_pid}')
    time.sleep(1)
    live=subprocess.run(adb+['shell','cat',f'/proc/{guard_pid}/stat'],capture_output=True,timeout=30)
    if live.returncode==0 and identity(live.stdout.decode())==saved_identity:
        call('shell',f'kill -KILL -- -{guard_pid}')
        time.sleep(1)
    live=subprocess.run(adb+['shell','cat',f'/proc/{guard_pid}/stat'],capture_output=True,timeout=30)
    assert live.returncode!=0 or identity(live.stdout.decode())!=saved_identity, 'Old guard did not stop.'
    group=subprocess.run(adb+['shell',f'kill -0 -- -{guard_pid}'],capture_output=True,timeout=5)
    assert group.returncode!=0, 'Old guard group has not stopped.'
    call('shell',f'if [ -d {guard_remote}/active.lock ]; then rmdir {guard_remote}/active.lock; fi')
    verify_session(guard_remote+'/guard-v2-process-stat.txt',guard_pid_v2)
    (guard_local/'upgrade-record.json').write_text(json.dumps(dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),oldPid=guard_pid,newPid=guard_pid_v2,newIdentity=new_identity,ready=ready,guardV2Sha256=sha(guard_v2),phase='bridge exited; before continuation'),indent=2)+'\n')
    command=[sys.executable,'-B',str(ROOT/'tools/prepare-power-continuation.py'),
        '--output',str(out),'--previous',str(previous),'--serial',args.serial,
        '--stage','continuation','--name',args.name,'--deadline',bridge_plan['hardStopLocal'],
        '--restore-by',bridge_plan['restoreByLocal'],'--deploy']
    if not review['powerGatePassed']: command.append('--repeat-full-hardware')
    require_time(deadline,2520)
    subprocess.run(command,check=True)
    folder=out/args.name
    plan=json.loads((folder/'run-plan.json').read_text())
    remote=plan['remoteDirectory']
    require_time(deadline,2520)
    device_epoch=int(call('shell','date','+%s').decode().strip())
    assert int(deadline.timestamp())-device_epoch>2520, 'Device deadline is too close.'
    verify_session(guard_remote+'/guard-v2-process-stat.txt',guard_pid_v2)
    pointer=folder/'active-run.txt';pointer.write_text(remote+'\n',newline='\n')
    subprocess.run(adb+['push',str(pointer),guard_remote+'/active-run.next'],check=True)
    call('shell','mv',guard_remote+'/active-run.next',guard_remote+'/active-run.txt')
    assert call('shell','cat',guard_remote+'/active-run.txt').decode().strip()==remote
    call('shell','touch',guard_remote+'/final-stage.started')
    state=dict(phase='launching',bridgeReviewSha256=sha(bridge/'bridge-review.json'),plan=str(folder/'run-plan.json'),
        remoteDirectory=remote,serial=args.serial,plannedRows=len(plan['legs']),repeatFullHardware=plan['repeatFullHardware'],
        hardStopLocal=plan['hardStopLocal'],restoreByLocal=plan['restoreByLocal'],
        nextPhoneAccessNotBeforeLocal='2026-09-22T07:10:00+08:00')
    (out/'continuation-state.json').write_text(json.dumps(state,indent=2)+'\n')
    require_time(deadline,2400)
    call('shell',f'/system/bin/toybox nohup /system/bin/setsid sh {remote}/runner.sh {remote} </dev/null >{remote}/runner.stdout 2>&1 &')
    startup_begin=time.monotonic()
    started=wait_json(remote+'/started.json')
    runner_identity=verify_session(remote+'/runner-process-stat.txt',int(started['pid']))
    assert int(started['pid'])!=guard_pid_v2
    assert time.monotonic()-startup_begin<45, 'Startup verification exceeded its quiet-window allowance.'
    state.update(phase='running',started=started,runnerIdentity=runner_identity,guardPid=guard_pid_v2,observedUtc=dt.datetime.now(dt.timezone.utc).isoformat(),
        observation='Startup records and process identity read within 45 seconds of launch, before the minimum 90-second first cooldown completes; no later host access during sampling')
    (out/'continuation-state.json').write_text(json.dumps(state,indent=2)+'\n')
    print(json.dumps(state,indent=2))

if __name__=='__main__': main()
