#!/usr/bin/env python3
"""Capture this night's baseline, install frozen inputs and stage owned fixtures."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'native/out/power-renderer-20261018'
OLD = ROOT/'native/out/dependency-upgrade-20261017'
ADB = shutil.which('adb') or r'D:\DevTools\Android\Sdk\platform-tools\adb.exe'
SERIAL = '***'
PACKAGE = 'io.github.joyelliot.zivplayer'

def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()

def adb(*parts, timeout=60):
    return subprocess.check_output([ADB,'-s',SERIAL,*map(str,parts)],timeout=timeout)

def save(name, value):
    target=OUT/name
    target.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

def installed(package):
    path=adb('shell','pm','path',package).decode().strip().removeprefix('package:')
    assert re.fullmatch(r'/data/app/[A-Za-z0-9_~=/.-]+/base.apk',path),path
    return {'package':package,'devicePath':path,'sha256':adb('shell','sha256sum',path).decode().split()[0]}

def capture_baseline():
    if (OUT/'device-baseline.json').exists():
        baseline=json.loads((OUT/'device-baseline.json').read_text(encoding='utf8'))
        assert baseline['serial']==SERIAL
        for info in baseline['packages'].values(): assert sha(info['backupPath'])==info['sha256']
        assert sha(baseline['userDataBackup']['path'])==baseline['userDataBackup']['sha256']
        return baseline
    settings=json.loads((OUT/'preparation-settings-before.json').read_text(encoding='utf-8-sig'))
    baseline={'capturedUtc':dt.datetime.now(dt.timezone.utc).isoformat(),'serial':SERIAL,
              'settingsBefore':{k:v for k,v in settings.items() if k not in ('capturedUtc','serial')},'packages':{}}
    (OUT/'backups').mkdir(exist_ok=True)
    for name,package in [('main',PACKAGE),('test',PACKAGE+'.test')]:
        info=installed(package);target=OUT/'backups'/('original-'+name+'.apk')
        candidates=[OLD/'artifacts/test/zivplayer-test-20261017-app.apk',
                    OLD/'device-preflight-20261017/original-test.apk']
        candidate=next((p for p in candidates if p.is_file() and sha(p)==info['sha256']),None)
        if candidate: shutil.copy2(candidate,target)
        else:
            with target.open('wb') as stream:
                subprocess.run([ADB,'-s',SERIAL,'exec-out','cat',info['devicePath']],stdout=stream,check=True,timeout=300)
        assert sha(target)==info['sha256']
        info['backupPath']=str(target);baseline['packages'][name]=info
    adb('shell','am','force-stop',PACKAGE)
    data=OUT/'backups/user-data-before.tar'
    with data.open('wb') as stream:
        subprocess.run([ADB,'-s',SERIAL,'exec-out','run-as',PACKAGE,'tar','-cf','-',
                        'databases','files/datastore','files/mpv','files/playback-resources','app_vlc'],
                       stdout=stream,check=True,timeout=120)
    with tarfile.open(data) as archive: assert len(archive.getmembers())>0
    baseline['userDataBackup']={'path':str(data),'sha256':sha(data)}
    volume=adb('shell','cmd','media_session','volume','--stream','3','--get').decode()
    baseline['musicVolume']=int(re.search(r'volume is (\d+) in range',volume)[1])
    audio=adb('shell','dumpsys','audio').decode(errors='replace')
    section=audio.split('- STREAM_MUSIC:',1)[1].split('- STREAM_',1)[0]
    baseline['musicMuted']='Muted: true' in section
    save('device-baseline.json',baseline)
    print('Fresh APK/user data/settings baseline saved.',flush=True)
    return baseline

def main():
    capture_baseline()
    manifest=json.loads((OLD/'artifact-manifest.json').read_text())['branches']['power']['apks']
    for kind,package in [('app',PACKAGE),('androidTest',PACKAGE+'.test')]:
        info=manifest[kind]
        path=Path(info['path'])
        if info['path'].startswith('/mnt/f/git/zivplayer/'):
            path=ROOT/info['path'].removeprefix('/mnt/f/git/zivplayer/')
        assert sha(path)==info['sha256']
        info['path']=str(path)
        if installed(package)['sha256']==info['sha256']:continue
        result=subprocess.run([ADB,'-s',SERIAL,'install','-r','-t',str(path)],capture_output=True,timeout=420)
        (OUT/f'install-{kind}.log').write_bytes(result.stdout+result.stderr)
        assert result.returncode==0 and b'Success' in result.stdout
        assert installed(package)['sha256']==info['sha256']
        print(kind,'installed and verified',info['sha256'],flush=True)
    save('experiment-apks.json',manifest)
    prior_fixtures={x['file']:x for x in json.loads((OUT/'persistent-fixtures.json').read_text())['fixtures']} if (OUT/'persistent-fixtures.json').exists() else {}
    fixtures=[]
    adb('shell','run-as',PACKAGE,'mkdir','-p','files/power-fixtures')
    for codec in ['av1','avc','hevc','vp9','hevc10']:
        path=ROOT/'native/out/power-fixtures'/f'{codec}-1080p24.mkv'
        expected=sha(path);device='files/power-fixtures/'+path.name
        exists=subprocess.run([ADB,'-s',SERIAL,'shell','run-as',PACKAGE,'test','-e',device],capture_output=True,timeout=20).returncode==0
        if not exists:
            transfer='/data/local/tmp/ziv-renderer-'+uuid.uuid4().hex+'.mkv'
            subprocess.run([ADB,'-s',SERIAL,'push',str(path),transfer],check=True,timeout=300)
            try: adb('shell','run-as',PACKAGE,'cp','-n',transfer,device,timeout=90)
            finally: adb('shell','rm','-f',transfer)
        actual=adb('shell','run-as',PACKAGE,'sha256sum',device,timeout=90).decode().split()[0]
        assert expected==actual,device
        created=prior_fixtures.get(path.name,{}).get('createdByThisNight',not exists)
        fixtures.append({'file':path.name,'bytes':path.stat().st_size,'sha256':actual,'devicePath':device,'createdByThisNight':created})
        save('persistent-fixtures.json',{'fixtures':fixtures})
        print(codec,'fixture verified',flush=True)
    # Only running third-party apps are suspended; keep root control and input tools available.
    packages=adb('shell','pm','list','packages','-3').decode().splitlines()
    processes=adb('shell','ps -A -o ARGS').decode(errors='replace').splitlines()
    (OUT/'processes-before-background-control.txt').write_text('\n'.join(processes),encoding='utf8')
    excluded={PACKAGE,PACKAGE+'.test','com.topjohnwu.magisk','me.weishu.kernelsu','com.rifsxd.ksunext',
              'com.android.deskclock','com.baidu.input_mi'}
    paused=json.loads((OUT/'background-control.json').read_text())['packages'] if (OUT/'background-control.json').exists() else []
    for line in packages:
        package=line.removeprefix('package:').strip()
        if package in excluded or not re.fullmatch(r'[A-Za-z0-9_.]+',package):continue
        if not any(p.strip()==package or p.strip().startswith(package+':') for p in processes):continue
        prior=adb('shell','dumpsys','package',package).decode(errors='replace')
        user=re.search(r'User 0:([^\n]+)',prior)
        if not user or 'suspended=true' in user[1]:continue
        result=adb('shell','su','-c',f'cmd package suspend --user 0 {package}').decode(errors='replace')
        if 'true' not in result.lower():continue
        paused.append({'package':package,'originalSuspended':False})
        save('background-control.json',{'policy':'Suspend already-running third-party apps and kill background processes; no frequency/thermal changes.','packages':paused})
        adb('shell','am','kill','--user','0',package)
    adb('shell','cmd','bluetooth_manager','disable')
    adb('shell','settings','put','system','screen_brightness_mode','0')
    adb('shell','cmd','display','set-brightness','0')
    adb('shell','cmd','media_session','volume','--stream','3','--set','0')
    # User already selected airplane + Wi-Fi; verify real settings instead of merely faking flags.
    assert adb('shell','settings','get','global','airplane_mode_on').decode().strip()=='1'
    assert adb('shell','settings','get','global','wifi_on').decode().strip() in ['1','2']
    assert adb('shell','settings','get','system','screen_brightness').decode().strip()=='1'
    save('prepared.json',{'readyUtc':dt.datetime.now(dt.timezone.utc).isoformat(),'main':installed(PACKAGE),'test':installed(PACKAGE+'.test'),
                          'suspendedBackgroundPackages':paused,'hardwarePriority':True,'keepAwakeUntilUserReturns':True})
    print('Prepared. Background packages paused:',len(paused),flush=True)

if __name__=='__main__': main()
