"""Guarded S09/M10 test controller. Device tokens and private backups stay on Pi."""
import argparse
import base64
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / '.tmp/update-two-20261027'))
import manage
_ssh_args = manage.ssh_args
def direct_ssh_args(board):
    if board != 'M10':
        return _ssh_args(board)
    return ['C:/Windows/System32/OpenSSH/ssh.exe','-F',str(Path.home()/'.ssh/bmi30_config'),
            '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5',
            '-o','ServerAliveInterval=5','-o','ServerAliveCountMax=12','BMI30-D5EB8F26C']
manage.ssh_args = direct_ssh_args
remote = manage.remote

WORK = ROOT / '.tmp/legacy-menu-20261027'

PUBLISH = r'''
import pathlib,json,sys,base64,hashlib,subprocess
P=pathlib.Path;p=json.load(sys.stdin);D=P('/home/techaid/Documents')
raw=base64.b64decode(p['archive']);assert hashlib.sha256(raw).hexdigest()==p['info']['ARCHIVE_SHA256']
folder=P('/var/tmp/bmi30-legacy-publication')/p['info']['FIRMWARE_BUNDLE_ID'];folder.mkdir(parents=True,exist_ok=True,mode=0o700)
archive=folder/p['info']['ARCHIVE_NAME'];marker=folder/'bmi30_latest.env'
if archive.exists():assert archive.read_bytes()==raw,'Release identity conflict'
else:archive.write_bytes(raw)
marker.write_bytes(base64.b64decode(p['marker']))
r=subprocess.run(['bash',str(D/'utilities/server_firmware.sh'),'publish','--marker',str(marker),'--archive',str(archive)],capture_output=True,text=True,timeout=210)
if r.returncode:raise RuntimeError('Publication failed: '+r.stdout[-900:]+r.stderr[-400:])
result=json.loads(r.stdout)
print(json.dumps({'publication':result.get('publication'),'marker':result.get('marker'),'staged_archive':str(archive)}))
'''

START = r'''
import json,pathlib,subprocess,sys
p=json.load(sys.stdin);D=pathlib.Path('/home/techaid/Documents')
r=subprocess.run(['systemd-run','--quiet','--collect','--unit='+p['unit'],'/bin/bash',str(D/'utilities/portal_firmware_operation.sh'),p['action']],capture_output=True,text=True,timeout=10)
assert r.returncode==0,'Cannot start standard menu operation'
print(json.dumps({'started':True,'unit':p['unit'],'action':p['action']}))
'''

STATUS = r'''
import pathlib,json,subprocess,urllib.request,hashlib,shlex
P=pathlib.Path;D=P('/home/techaid/Documents')
def api(port,path='/api/status'):
 o=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 try:
  with o.open('http://127.0.0.1:'+str(port)+path,timeout=3) as r:return json.load(r)
 except Exception as e:return {'error':type(e).__name__}
def env(path):
 result={}
 for line in path.read_text().splitlines():
  if '=' in line and not line.startswith('#'):
   k,v=line.split('=',1);a=shlex.split(v);result[k]=a[0] if a else ''
 return result
active=env(D/'host/bmi30_split_active_version.env');release=env(D/'host/bmi30_firmware_release.env')
c=api(8765);n=api(8791,'/status');portal=api(80)
hashes={}
for key in ('BMI30_CORE_PATH','BMI30_ENGINE_SOURCE','BMI30_GUI_PATH','BMI30_PORTAL_PATH'):
 path=P(active[key]);path=path if path.is_absolute() else D/path
 hashes[key]=hashlib.sha256(path.read_bytes()).hexdigest()
operation=D/'.bmi30_cloud_sync/portal_operation.json'
component=P('/var/lib/bmi30-legacy-menu')/active.get('BMI30_SPLIT_BUNDLE_ID','')/'state.json'
print(json.dumps({'id':portal.get('hostname'),'active_bundle':active.get('BMI30_SPLIT_BUNDLE_ID'),
'firmware_version':release.get('BMI30_FIRMWARE_VERSION'),'component':json.loads(component.read_text()) if component.is_file() else None,
'operation':json.loads(operation.read_text()) if operation.is_file() else None,'network_version':n.get('version'),'legacy':n.get('legacy_recovery'),
'core':{k:c.get(k) for k in ('ok','stale','connection','mode','sync','stream_stats')},'worker':(c.get('detector') or {}).get('worker'),
'detector_settings':(c.get('detector') or {}).get('settings'),'network_nodes':[{'id':x.get('id'),'age_s':x.get('age_s'),'internet':x.get('internet')} for x in n.get('nodes',[])],
'hashes':hashes,'portal_installed_sha256':hashlib.sha256(P('/usr/local/bin/bmi30-hotspot-info-server.py').read_bytes()).hexdigest()}))
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['publish','update','rollback','status'])
    parser.add_argument('--board',choices=['M10','S09'],default='M10')
    parser.add_argument('--bundle')
    args=parser.parse_args()
    if args.action=='publish':
        folder=ROOT/'FirmwareReleases/RaspberryPi'/args.bundle
        record=json.loads((folder/'release.json').read_text())
        payload={'info':record['marker'],'archive':base64.b64encode((folder/record['marker']['ARCHIVE_NAME']).read_bytes()).decode(),
                 'marker':base64.b64encode((folder/'bmi30_latest.env').read_bytes()).decode()}
        result=remote(args.board,PUBLISH,payload,timeout=230)
        (folder/'publication.json').write_text(json.dumps(result,indent=2)+'\n')
        record['published']=True
        (folder/'release.json').write_text(json.dumps(record,indent=2)+'\n')
    elif args.action in ('update','rollback'):
        payload={'action':args.action,'unit':'bmi30-menu-test-'+args.board.lower()+'-'+args.action+'-'+uuid.uuid4().hex[:8]}
        result=remote(args.board,START,payload,timeout=20)
    else:
        result=remote(args.board,STATUS,timeout=30)
    name=args.board+'-'+args.action+'-'+uuid.uuid4().hex[:8]+'.json'
    (WORK/name).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'board':args.board,'action':args.action,'report':str(WORK/name),'result':result}))


if __name__=='__main__':
    main()
