"""Install a validated portal-only bundle with acquisition continuity checks."""
import argparse
import json
from pathlib import Path
import subprocess

from build_release import VERSION
from deploy import HOSTS, WORK, SSH, CONFIG, remote
from deploy_portal import ORIGINAL

SCRIPT = '''
import ast,datetime,hashlib,json,os,pathlib,shlex,shutil,subprocess,sys,tarfile,time,urllib.request
p=json.load(sys.stdin);version=p['version'];docs=pathlib.Path('/home/techaid/Documents');work=docs/'.tmp/network-20260930'
archive=work/('BMI30-RPi-'+version+'.tar.gz');sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
assert sha(archive)==p['archive_sha256']
bundle=docs/'host/bmi30_split_bundles'/version
if not bundle.exists():
 with tarfile.open(archive) as tf:
  for entry in tf.getmembers():
   path=pathlib.PurePosixPath(entry.name)
   assert not path.is_absolute() and '..' not in path.parts and path.parts[0]==version and (entry.isfile() or entry.isdir())
  tf.extractall(bundle.parent)
subprocess.run(['bash',str(docs/'switch_bmi30_split_versions.sh'),'--validate',version],check=True,stdout=subprocess.PIPE)
active=docs/'host/bmi30_active_runtime/project';project=bundle/'project'
components=['host/BMI30.001.py.2026-09-30-1135','host/BMI30.200.py.2026-09-30-1135','host/BMI30.GUI.001.py.2026-09-30-1135','host/usb_vendor/usb_stream.py']
for component in components:assert sha(active/component)==sha(project/component),'Full activation required: '+component
installed=pathlib.Path('/usr/local/bin/bmi30-hotspot-info-server.py')
assert sha(installed) in p['expected_portal'],'Unexpected portal modification'
portal=(project/'hotspot_info_server.py').read_bytes();ast.parse(portal)
pid=lambda:subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()
before_pid=pid();settings=[docs/'host/bmi30_config.json',docs/'host/bmi30_sel.json',docs/'host/bmi30_relay.json']
before_settings={str(x):sha(x) for x in settings if x.exists()}
env_path=docs/'host/bmi30_split_active_version.env';lines=env_path.read_text().splitlines()
updates={'BMI30_SPLIT_VERSION':version,'BMI30_SPLIT_BUNDLE_ID':version,
 'BMI30_SPLIT_LABEL':'BMI30 group channel, per-device controls and RS485 fallback',
 'BMI30_SPLIT_SELECTED_BY':'portal-only-live-validated',
 'BMI30_SPLIT_SELECTED_AT':datetime.datetime.now().astimezone().isoformat(),
 'BMI30_BUNDLE_ORIGIN':'Based on 2026-09-30-1135; identical acquisition files; portal-only live update'}
env=('\\n'.join(shlex.quote(updates[key]).join([key+'=','']) if (key:=line.split('=',1)[0]) in updates else line for line in lines)+'\\n').encode()
release=(project/'host/bmi30_firmware_release.env').read_bytes()
changes={installed:portal,active/'hotspot_info_server.py':portal,docs/'hotspot_info_server.py':portal,
 active/'host/bmi30_firmware_release.env':release,docs/'host/bmi30_firmware_release.env':release,env_path:env}
backup=pathlib.Path('/var/backups/bmi30-network')/time.strftime('release-%Y%m%d-%H%M%S');backup.mkdir(mode=0o700)
saved={}
for i,path in enumerate(changes):
 if path.exists():shutil.copy2(path,backup/str(i));saved[path]=(backup/str(i),path.stat())
(backup/'paths.json').write_text(json.dumps({str(path):str(pair[0]) for path,pair in saved.items()}))
try:
 for path,data in changes.items():
  st=path.stat() if path.exists() else env_path.stat();temp=path.with_name(path.name+'.bmi30-new');temp.write_bytes(data)
  os.chmod(temp,st.st_mode);os.chown(temp,st.st_uid,st.st_gid);temp.replace(path)
 subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)
 for _ in range(20):
  try:
   with urllib.request.urlopen('http://127.0.0.1/api/status',timeout=2) as r:status=json.load(r)
   break
  except Exception:time.sleep(.3)
 else:raise RuntimeError('Portal did not recover')
 with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3) as r:core=json.load(r)
 assert pid()==before_pid and core['connection']['connected'] and core['connection']['last_frame_age_s']<1
 assert before_settings=={str(x):sha(x) for x in settings if x.exists()},'Live settings changed'
 assert sha(installed)==sha(project/'hotspot_info_server.py')
except Exception:
 for path,(old,st) in saved.items():shutil.copy2(old,path);os.chown(path,st.st_uid,st.st_gid)
 subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)
 raise
print(json.dumps({'host':status['hostname'],'version':version,'portal_sha256':sha(installed),'core_pid':pid(),
 'connection':core['connection'],'settings_preserved':True,'backup':str(backup),'acquisition_unchanged':True}))
'''


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--host',choices=HOSTS);args=parser.parse_args()
    release=json.loads((WORK/'release.json').read_text());name='BMI30-RPi-'+VERSION+'.tar.gz'
    archive=Path('FirmwareReleases/RaspberryPi')/VERSION/name
    for host in ([args.host] if args.host else HOSTS):
        prepared=json.loads(remote(host,"""import pathlib,os,pwd,json,sys,hashlib
p=json.load(sys.stdin);d=pathlib.Path('/home/techaid/Documents/.tmp/network-20260930');d.mkdir(exist_ok=True)
u=pwd.getpwnam('techaid');os.chown(d,u.pw_uid,u.pw_gid);a=d/p['name']
if a.exists():assert hashlib.sha256(a.read_bytes()).hexdigest()==p['sha256'],'Immutable archive differs'
print(json.dumps({'exists':a.exists()}))""",{'name':name,'sha256':release['sha256']}))
        if not prepared['exists']:
            subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',str(archive),
                            host+':/home/techaid/Documents/.tmp/network-20260930/'+name],check=True)
        expected=[ORIGINAL,release['portal_sha256']]
        previous=WORK/(host+'-portal-result.json')
        if previous.exists():expected.append(json.loads(previous.read_text())['sha256'])
        result=json.loads(remote(host,SCRIPT,{'version':VERSION,'archive_sha256':release['sha256'],'expected_portal':expected}))
        (WORK/(host+'-release-result.json')).write_text(json.dumps(result,indent=2))
        print(json.dumps(result),flush=True)
