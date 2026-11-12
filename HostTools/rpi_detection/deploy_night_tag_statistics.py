"""Stage/start/inspect one temporary M02-only no-tag statistics job."""
import argparse
import base64
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0,str(ROOT/'.tmp/new-device-20261009'))
from device_remote import remote

REMOTE = r'''
import base64,hashlib,json,math,os,pathlib,pwd,re,socket,subprocess,sys,time
p=json.load(sys.stdin)
assert socket.gethostname()=='BMI30-E29805C7C'
assert pathlib.Path('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
name=p['name'];assert re.fullmatch(r'[a-z0-9-]{1,70}',name)
root=pathlib.Path('/home/techaid/Documents/host/tag_statistics')
directory=root/name
unit='bmi30-tag-stats-'+name
def run(*args):
 q=subprocess.run(args,capture_output=True,text=True,timeout=25)
 return {'rc':q.returncode,'stdout':q.stdout,'stderr':q.stderr}
def read(file):
 path=directory/file
 return json.loads(path.read_text()) if path.exists() else None
action=p['action']
if action=='stage':
 assert not directory.exists(),'Recording directory already exists'
 user=pwd.getpwnam('techaid')
 root.mkdir(mode=0o750,parents=True,exist_ok=True)
 assert root.resolve().parent==pathlib.Path('/home/techaid/Documents/host').resolve()
 os.chown(root,user.pw_uid,user.pw_gid)
 directory.mkdir(mode=0o750);os.chown(directory,user.pw_uid,user.pw_gid)
 source=directory/'source';source.mkdir(mode=0o750);os.chown(source,user.pw_uid,user.pw_gid)
 hashes={}
 for filename,encoded in p['sources'].items():
  assert filename in ('night_tag_statistics.py','remote_tag_capture.py','remote_pass_logger.py','ring_gestures.py')
  data=base64.b64decode(encoded);compile(data,filename,'exec')
  path=source/filename;path.write_bytes(data);os.chmod(path,0o640);os.chown(path,user.pw_uid,user.pw_gid)
  hashes[filename]=hashlib.sha256(data).hexdigest()
 job=p['job']
 if job.get('qualification_duration_s'):job['end_epoch']=time.time()+job['qualification_duration_s']
 disk=os.statvfs(directory);free=disk.f_bavail*disk.f_frsize
 job['reserve_bytes']=3*1024**3
 job['max_bytes']=min(12*1024**3,free-job['reserve_bytes'])
 assert job['max_bytes']>512*1024**2,'Insufficient disk budget'
 job.update(source_sha256=hashes,unit=unit,remote_directory=str(directory),staged_epoch=time.time())
 for filename,value in [('job.json',job),('test-conditions.json',p['conditions'])]:
  path=directory/filename;path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');os.chmod(path,0o640);os.chown(path,user.pw_uid,user.pw_gid)
 print(json.dumps({'staged':True,'directory':str(directory),'unit':unit,'job':job,'available_bytes':free}))
elif action=='start':
 job=read('job.json');assert job
 if job.get('qualification_duration_s'):
  job['end_epoch']=time.time()+job['qualification_duration_s']
  (directory/'job.json').write_text(json.dumps(job,indent=2)+'\n')
 assert 0<job['end_epoch']-time.time()<24*3600
 properties=['Nice=15','IOSchedulingClass=idle','CPUWeight=10','IOWeight=10','MemoryMax=384M',
  'TasksMax=32','TimeoutStopSec=30s','KillMode=control-group','OOMPolicy=stop','Restart=no','UMask=0027',
  'WorkingDirectory='+str(directory/'source'),
  'RuntimeMaxSec='+str(math.ceil(job['end_epoch']-time.time())+60),
  'ExecStopPost=/usr/bin/python3 '+str(directory/'source/night_tag_statistics.py')+' --directory '+str(directory)+' --restore-only']
 args=['systemd-run','--unit='+unit,'--uid=techaid','--gid=techaid','--description=BMI30 M02 no-tag statistics '+name]
 for prop in properties:args+=['--property='+prop]
 args+=['/usr/bin/python3','-u',str(directory/'source/night_tag_statistics.py'),'--directory',str(directory)]
 result=run(*args)
 assert result['rc']==0,result
 print(json.dumps({'started':True,'unit':unit,'directory':str(directory),'result':result}))
elif action=='stop':
 result=run('systemctl','stop',unit)
 print(json.dumps({'stop':result,'results':read('results.json'),'restoration':read('restore-post.json')}))
elif action=='status':
 progress=read('progress.json')
 if progress and progress.get('last_status'):
  status=progress.pop('last_status')
  progress['last_detector_health']={
   'ok':status.get('ok'),'stale':status.get('stale'),'upper_confirm_count':status['detector']['channels']['upper']['confirm_count'],
   'upper_fire_seq':status['detector']['fire_seq0'],'lower_fire_seq':status['detector']['fire_seq1'],
   'stream_stats':status['stream_stats'],'worker':status['detector']['worker']}
 disk=os.statvfs(directory)
 print(json.dumps({'unit':unit,'directory':str(directory),
  'unit_state':run('systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','Result','-p','MainPID','-p','User','-p','Nice','-p','RuntimeMaxUSec','-p','ExecStopPost'),
  'progress':progress,'job':read('job.json'),'results':read('results.json'),
  'restore':read('restore.json'),'restore_post':read('restore-post.json'),
  'available_bytes':disk.f_bavail*disk.f_frsize,
  'journal':run('journalctl','-u',unit,'-n','6','--no-pager','-o','cat')}))
elif action=='kill-test':
 assert name.startswith('qualification-') and read('job.json').get('qualification_duration_s')
 import urllib.request
 def current():return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3))
 end=time.monotonic()+15
 changed=False
 while time.monotonic()<end:
  if current()['detector']['channels']['upper']['confirm_count'] in (3,4):changed=True;break
  time.sleep(.2)
 assert changed,'Qualification never reached a modified confirmation count'
 killed=run('systemctl','kill','--kill-who=main','--signal=KILL',unit)
 assert killed['rc']==0,killed
 end=time.monotonic()+15
 while time.monotonic()<end and not (directory/'restore-post.json').exists():time.sleep(.2)
 result=read('restore-post.json')
 assert result and result['restored'],result
 assert current()['detector']['channels']['upper']['confirm_count']==6
 print(json.dumps({'simulated_supervisor_crash':True,'recovered_by_ExecStopPost':True,'restoration':result}))
else:raise ValueError('Unsupported action')
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('stage','start','status','stop','kill-test'))
    parser.add_argument('--name',required=True)
    parser.add_argument('--end',default='2026-10-20T08:00:00+02:00')
    parser.add_argument('--block-seconds',type=float,default=1800)
    parser.add_argument('--qualification-seconds',type=float)
    args=parser.parse_args()
    if not re.fullmatch('[a-z0-9-]{1,70}',args.name):parser.error('Use a simple unique job name')
    payload={'action':args.action,'name':args.name}
    if args.action=='stage':
        baseline=json.loads((HERE/'recordings/2026-10-19/M02-upper-no-tag-01/summary.json').read_text())['remote_end']['final']
        end=datetime.datetime.fromisoformat(args.end)
        assert end.utcoffset() is not None
        job=dict(end_epoch=end.timestamp(),end_prague=end.isoformat(),block_seconds=args.block_seconds,
            tag_absent_confirmed=True,qualification_duration_s=args.qualification_seconds,
            expected_root=baseline['root'],expected_config_sha256=baseline['files']['BMI30_CONFIG_JSON']['sha256'],
            expected_engine_path=baseline['files']['BMI30_ENGINE_SOURCE']['path'],
            expected_engine_sha256=baseline['files']['BMI30_ENGINE_SOURCE']['sha256'],
            profiles=[6,4,3],schedule='Each 90-minute cycle contains 6,4,3; rotate order across cycles',
            restore_original_confirm_count=6,recording='Full-resolution processed API waveforms at 25Hz maximum; status at 10Hz; not lossless raw ADC capture')
        conditions=dict(board='M02',channel='upper',test_type='no_tag_control',tag_present=False,
            comparison_tag='B51(250/74)',antenna_spacing_cm=110,tag_removed_confirmed_by_user=True,
            user_confirmation='До завтра, 20 октября, 08:00; метка всё время убрана',
            upper_confirm_profiles=[3,4,6],change_only='upper confirmation count',
            objective='Compare false events per hour; select later using independent marked tag-passage detection statistics')
        payload.update(job=job,conditions=conditions,sources={name:base64.b64encode((HERE/name).read_bytes()).decode()
            for name in ('night_tag_statistics.py','remote_tag_capture.py','remote_pass_logger.py','ring_gestures.py')})
    result=remote(REMOTE,payload=payload,timeout=45)
    work=ROOT/'.tmp/tag-capture-20261019/night-jobs'/args.name
    work.mkdir(parents=True,exist_ok=True)
    (work/(args.action+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # The detailed status and restoration snapshots remain in the local audit file.
    if args.action in ('status','stop','kill-test'):
        display={key:value for key,value in result.items() if key not in ('results','restore','restore_post','restoration','journal')}
        for key in ('restore','restore_post','restoration'):
            if result.get(key):display[key]={k:v for k,v in result[key].items() if k!='final_status'}
        if result.get('results'):
            display['results']={k:v for k,v in result['results'].items() if k not in ('blocks','restoration')}
        journal=result.get('journal')
        if journal:
            display['journal']={**journal,'stdout':'\n'.join(line if len(line)<700 else line[:350]+' ... [full line in audit JSON]' for line in journal.get('stdout','').splitlines())}
        print(json.dumps(display,ensure_ascii=False))
    else:print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
