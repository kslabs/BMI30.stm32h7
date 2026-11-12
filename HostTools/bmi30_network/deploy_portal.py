"""Validate/render or install only the portal; acquisition services stay alive."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

from deploy import HOSTS, WORK, remote
from patch_group_portal import patch

ORIGINAL = "46426ef68c49a3986a0607c682333fb8d7d5bc8be3bfad1faf77b54bb06afa4c"
SCRIPT = '''
import ast,base64,hashlib,json,os,pathlib,runpy,shutil,subprocess,sys,time,urllib.request
p=json.load(sys.stdin);source=base64.b64decode(p['source']);ast.parse(source)
docs=pathlib.Path('/home/techaid/Documents');work=docs/'.tmp/network-20260930';work.mkdir(exist_ok=True)
candidate=work/'portal-candidate.py';candidate.write_bytes(source)
module=runpy.run_path(str(candidate))
html=module['render_portal_page']('BMI30 validation','admin',request_host='127.0.0.1')
(work/'portal-candidate.html').write_bytes(html)
assert b'Group channel' in html and b'/api/group-command' in html
if not p['install']:
 print(json.dumps({'ok':True,'html':base64.b64encode(html).decode()}));sys.exit()
paths=[docs/'host/bmi30_active_runtime/project/hotspot_info_server.py',pathlib.Path('/usr/local/bin/bmi30-hotspot-info-server.py'),docs/'hotspot_info_server.py']
pid=lambda:subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()
before_pid=pid();backup=pathlib.Path('/var/backups/bmi30-network')/time.strftime('portal-%Y%m%d-%H%M%S');backup.mkdir(mode=0o700)
assert hashlib.sha256(paths[1].read_bytes()).hexdigest() in p['expected'],'Portal changed since inspection'
saved={}
for i,path in enumerate(paths):
 if path.exists():
  shutil.copy2(path,backup/str(i));saved[path]=(backup/str(i),path.stat())
try:
 for path in paths:
  st=path.stat() if path.exists() else paths[0].stat()
  temp=path.with_name(path.name+'.bmi30-new');temp.write_bytes(source);os.chmod(temp,st.st_mode);os.chown(temp,st.st_uid,st.st_gid);temp.replace(path)
 subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)
 for _ in range(20):
  try:
   with urllib.request.urlopen('http://127.0.0.1/api/status',timeout=2) as response:status=json.load(response)
   break
  except Exception:time.sleep(.3)
 else:raise RuntimeError('Portal did not recover')
 assert pid()==before_pid,'Acquisition owner unexpectedly changed'
 with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3) as response:core=json.load(response)
 assert core['connection']['connected'] and core['connection']['last_frame_age_s']<1
except Exception:
 for path,(old,st) in saved.items():shutil.copy2(old,path);os.chown(path,st.st_uid,st.st_gid)
 subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)
 raise
print(json.dumps({'ok':True,'host':status['hostname'],'sha256':hashlib.sha256(source).hexdigest(),'backup':str(backup),'core_pid':before_pid,'connection':core['connection']}))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', choices=HOSTS, default=HOSTS[-1])
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    source = patch((WORK/'portal.py').read_text(encoding='utf-8')).encode()
    (WORK/'portal-candidate.py').write_bytes(source)
    result_file = WORK/(args.host+'-portal-result.json')
    expected=[ORIGINAL]
    if result_file.exists():
        expected.append(json.loads(result_file.read_text())['sha256'])
    result=json.loads(remote(args.host,SCRIPT,{'source':base64.b64encode(source).decode(),'install':args.install,'expected':expected}))
    if args.install:
        result_file.write_text(json.dumps(result,indent=2))
    else:
        (WORK/'portal-candidate.html').write_bytes(base64.b64decode(result.pop('html')))
    print(json.dumps(result))


if __name__=='__main__':
    main()
