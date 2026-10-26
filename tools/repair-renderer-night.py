#!/usr/bin/env python3
"""Record the first idle-cooling failure and create a distinct revised night attempt."""
import importlib.util
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

spec=importlib.util.spec_from_file_location('night',Path(__file__).with_name('renderer-night-session.py'))
n=importlib.util.module_from_spec(spec);spec.loader.exec_module(n)

def user_state(text):
    found=re.search(r'(?m)^\s*User 0:([^\r\n]*\binstalled=true[^\r\n]*)',text)
    assert found,'Installed owner state missing'
    return found[1]

def main():
    state=n.read(n.OUT/'session-state.json')
    assert state['phase']=='needs_review' and state['reviewStage']=='hwpreflight'
    assert not any(s['status'] in ['running','launching'] for s in state['stages'])
    audit=n.read(n.OUT/'hwpreflight/audit.json')
    assert audit['exit']['stopReason']=='cooling_deadline_expired' and not audit['runs']
    repair=n.OUT/'repair-r2'
    continuing='--continue-background' in sys.argv
    if continuing:
        assert repair.exists() and state.get('guardRevision')==2
        assert n.verify_process(n.GUARD+'/guard-v2-process-stat.txt')==state['guardPid']
        n.verify_process(n.PARENT+'/awake-guard/process-stat.txt')
        if not (repair/'stopped-packages-first-parser.json').exists():
            shutil.copyfile(repair/'stopped-packages.json',repair/'stopped-packages-first-parser.json')
    else:
        repair.mkdir()
        n.write(repair/'session-state-before.json',state)
    # Only packages actually running in the recorded baseline and explicitly
    # controlled this night; the assistant is included after observed idle CPU.
    packages=[p['package'] for p in n.read(n.OUT/'background-control.json')['packages']]+['app-06-***']
    stopped=[]
    for package in packages:
        before=repair/(package+'-before.txt')
        if continuing:prior=before.read_text(encoding='utf8')
        else:
            prior=n.adb('shell','dumpsys','package',package).decode(errors='replace')
            before.write_text(prior,encoding='utf8')
        if 'stopped=false' in user_state(prior):stopped.append(dict(package=package,originalStopped=False,status='planned'))
    n.write(repair/'stopped-packages.json',stopped)
    n.put_text(repair/'stopped-packages.txt',''.join(p['package']+'\n' for p in stopped))
    if continuing:
        # Replace only the restoration journal before stopping any additional package.
        n.upload(repair/'stopped-packages.txt',n.GUARD+'/stopped-packages.next')
        n.adb('shell','mv',n.GUARD+'/stopped-packages.next',n.GUARD+'/stopped-packages.txt')
    else:
        deploy_guard(state,repair)
    for item in stopped:
        package=item['package']
        output=n.adb('shell','su -c '+shlex.quote(f'am force-stop --user 0 {package}'))
        (repair/(package+'-stop-output.txt')).write_bytes(output)
        for attempt in range(10):
            after=n.adb('shell','dumpsys','package',package).decode(errors='replace')
            if 'stopped=true' in user_state(after):break
            time.sleep(.3)
        (repair/(package+'-after.txt')).write_text(after,encoding='utf8')
        item['status']='stopped_verified' if 'stopped=true' in user_state(after) else 'stop_requested_flag_unconfirmed'
        n.write(repair/'stopped-packages.json',stopped)
    processes=n.adb('shell','ps -A -o ARGS').decode(errors='replace')
    (repair/'processes-after.txt').write_text(processes,encoding='utf8')
    remaining=[line.strip() for line in processes.splitlines() if any(line.strip()==x['package'] or line.strip().startswith(x['package']+':') for x in stopped)]
    assert not remaining,remaining
    create_plans(state)
    print('Revision 2 prepared; resume the controller.',flush=True)

def deploy_guard(state,repair):
    shutil.copyfile(n.ROOT/'tools/power-night-deadline-guard.sh',repair/'guard-r2.sh')
    n.upload(repair/'guard-r2.sh',n.GUARD+'/guard-r2.sh')
    n.upload(repair/'stopped-packages.txt',n.GUARD+'/stopped-packages.txt')
    n.adb('shell','sh','-n',n.GUARD+'/guard-r2.sh')
    # Validate the documented reversal before stopping any app.
    n.adb('shell',"su -c 'cmd package unstop --user 0 app-06-***'")
    old_pid=n.verify_process(n.GUARD+'/guard-v2-process-stat.txt')
    assert old_pid==state['guardPid']
    assert n.adb('shell','cat',n.GUARD+'/owner.txt').decode().strip()==state['owner']
    for name in ['guard-v2-ready.json','guard-v2-process-stat.txt']:
        (repair/('old-'+name)).write_bytes(n.adb('shell','cat',n.GUARD+'/'+name))
    # The awake guard is independent and remains running through this replacement.
    n.verify_process(n.PARENT+'/awake-guard/process-stat.txt')
    n.adb('shell',f'kill -TERM -- -{old_pid}')
    for _ in range(30):
        group=subprocess.run([n.ADB,'-s',n.SERIAL,'shell',f'kill -0 -- -{old_pid}'],capture_output=True)
        if group.returncode:break
        time.sleep(.2)
    assert group.returncode,'Old guard process group remained; no second guard launched.'
    n.adb('shell',f'if [ -d {n.GUARD}/active-v2.lock ]; then rmdir {n.GUARD}/active-v2.lock; fi')
    for name in ['guard-v2-ready.json','guard-v2-process-stat.txt']:
        n.adb('shell','mv',n.GUARD+'/'+name,n.GUARD+'/'+name+'.r1')
    n.adb('shell',f'/system/bin/toybox nohup /system/bin/setsid sh {n.GUARD}/guard-r2.sh {n.GUARD} </dev/null >{n.GUARD}/guard-r2.log 2>&1 &')
    ready=n.wait_json(n.GUARD+'/guard-v2-ready.json')
    assert n.verify_process(n.GUARD+'/guard-v2-process-stat.txt')==ready['pid']
    n.record(state,guardPid=ready['pid'],guardRevision=2,
             guardRevisionSha256=n.sha(repair/'guard-r2.sh'),stoppedPackagesJournal=str(repair/'stopped-packages.json'))

def create_plans(state):
    new_stages=[]
    for old in list(state['stages']):
        if old['status'] not in ['pending','collected']:continue
        assert old['name']=='hwpreflight' or old['status']=='pending'
        name=old['name']+'r2';folder=n.OUT/name;folder.mkdir();plan=n.read(old['plan']);old_folder=Path(old['plan']).parent
        for leg in plan['legs']:
            leg['label']=leg['label'].replace(old['name']+'-',name+'-',1)
            leg['comparisonBlock']=leg['comparisonBlock'].replace(old['name']+':',name+':',1)
        keys=['ordinal','blockNumber','comparisonBlock','plannedOrdinal','codec','decoderMode','engine','label','purpose','mpvVideoOutput']
        n.put_text(folder/'manifest.tsv',''.join('\t'.join(str(leg[k]) for k in keys)+'\n' for leg in plan['legs']))
        n.put_text(folder/'exo-reference.tsv','')
        shutil.copyfile(n.ROOT/'tools/phone-power-runner.sh',folder/'runner.sh')
        config={k:shlex.split(v)[0] for k,v in (line.split('=',1) for line in (old_folder/'config.env').read_text().splitlines())}
        config.update(MANIFEST_SHA=n.sha(folder/'manifest.tsv'),MAX_START_TEMPERATURE_TENTHS='360',COOLDOWN_INPUT_REFRESH='false',COHORT=config['COHORT']+'-r2-idle-controls')
        n.env(folder/'config.env',config)
        plan.update(remoteDirectory=n.PARENT+'/'+name,measurementCohort=config['COHORT'],maxStartTemperatureTenthsC=360,
                    frozenUtc=n.utc(),revision=2,supersedesPlanSha256=old['planSha256'],
                    revisionReason='No playback occurred: 34.5 C entry gate was below observed 35.5 C awake idle. Restore established 36 C gate; remove repeated Home input and stop selected background packages with recovery journal.',
                    frozenFiles={x:n.sha(folder/x) for x in ['manifest.tsv','config.env','runner.sh','exo-reference.tsv']})
        n.write(folder/'run-plan.json',plan)
        new_stages.append(dict(old,name=name,plan=str(folder/'run-plan.json'),planSha256=n.sha(folder/'run-plan.json'),status='pending',revision=2))
        for field in ['startedUtc','finishedUtc','exit','qualifiedRows','preflightPassed']:
            new_stages[-1].pop(field,None)
        if old['status']=='pending':old.update(status='superseded',supersededBy=name)
    state['stages']+=new_stages
    n.record(state,protocolRevision=2,controllerLogFiles=['controller-resume-r2.stdout.log','controller-resume-r2.stderr.log'])
    n.write(n.OUT/'review-resolution.json',dict(reviewStage='hwpreflight',auditSha256=n.sha(n.OUT/'hwpreflight/audit.json'),
        cause='Initial cooling gate remained below the measured awake idle temperature; all four tests were never started.',
        action='Preserve failed attempt; create distinct revision-2 labels/plans with established 36 C entry, same 40 C measurement/thermal guards and no repeated cooldown Home. Stop journaled nonessential apps and install verified unstop recovery.',
        **{'continue':True}))
    n.report()

def control_restarting_packages():
    state=n.read(n.OUT/'session-state.json')
    assert state['phase']=='needs_review' and state['guardRevision']==2
    repair=n.OUT/'repair-r3';repair.mkdir()
    packages=['app-10-***','app-04-***','app-06-***']
    enabled=[]
    for package in packages:
        baseline=user_state((n.OUT/'repair-r2'/(package+'-before.txt')).read_text(encoding='utf8'))
        mode=re.search(r'\benabled=(\d+)\b',baseline)[1]
        assert mode=='0',(package,mode)
        enabled.append(dict(package=package,originalEnabled=int(mode)))
    n.write(repair/'enabled-packages.json',enabled)
    n.put_text(repair/'enabled-packages.tsv',''.join(x['package']+'\t'+str(x['originalEnabled'])+'\n' for x in enabled))
    n.upload(repair/'enabled-packages.tsv',n.GUARD+'/enabled-packages.tsv')
    config=n.adb('shell','cat',n.GUARD+'/config.env').decode().replace('\r','')
    n.put_text(repair/'config.env',config+'\nREQUIRE_BACKGROUND_JOURNALS=true\n')
    n.upload(repair/'config.env',n.GUARD+'/config.env')
    # deploy_guard records its revision-2-compatible interface; revision 3 adds enabled-state restoration.
    shutil.copyfile(n.OUT/'repair-r2/stopped-packages.txt',repair/'stopped-packages.txt')
    deploy_guard(state,repair)
    n.record(state,guardRevision=3,enabledPackagesJournal=str(repair/'enabled-packages.json'))
    for item in enabled:
        package=item['package']
        n.adb('shell','su -c '+shlex.quote(f'cmd package disable-user --user 0 {package}'))
        n.adb('shell','su -c '+shlex.quote(f'am force-stop --user 0 {package}'))
        after=n.adb('shell','dumpsys','package',package).decode()
        assert 'enabled=3' in user_state(after),package
        (repair/(package+'-after.txt')).write_text(after,encoding='utf8')
    processes=n.adb('shell','ps -A -o ARGS').decode()
    (repair/'processes-after.txt').write_text(processes,encoding='utf8')
    assert not [p for p in processes.splitlines() if any(p==x or p.startswith(x+':') for x in packages)]
    create_plans(state)
    print('Revision 2 sampling plans prepared with revision 3 background recovery.',flush=True)

if __name__=='__main__':
    if '--control-restarting' in sys.argv:control_restarting_packages()
    else:main()
