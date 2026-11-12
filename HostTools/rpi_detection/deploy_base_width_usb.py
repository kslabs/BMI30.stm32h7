"""Update an audited inactive USB root with the existing complete 1650 bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bmi30_network'))
from deploy import remote
from capture_status import HOSTS

WORK=Path(__file__).resolve().parents[2]/'.tmp/base-width-usb-20260930'
REMOTE=r'''
import copy,csv,hashlib,io,json,os,pathlib,shutil,stat,subprocess,sys,tarfile,time,urllib.request
p=json.load(sys.stdin);v=p['version'];d=pathlib.Path('/home/techaid/Documents')
def run(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def atomic(path,data):
 st=path.stat();tmp=path.with_name(path.name+'.usb-update-new');tmp.write_bytes(data)
 os.chmod(tmp,stat.S_IMODE(st.st_mode));os.chown(tmp,st.st_uid,st.st_gid);tmp.replace(path)
assert run(['hostname']).upper()==p['host']
assert run(['findmnt','-n','-o','SOURCE','/'])=='/dev/mmcblk0p2'
assert run(['lsblk','-dn','-o','TRAN','/dev/sda'])=='usb'
assert run(['lsblk','-dn','-o','SERIAL','/dev/sda'])==p['serial']
assert run(['blkid','-s','UUID','-o','value','/dev/sda2'])==p['uuid']
assert subprocess.run(['findmnt','-rn','-S','/dev/sda2'],stdout=subprocess.DEVNULL).returncode!=0
archive=d/'.tmp/base-width-colors-20260930'/('BMI30-RPi-'+v+'.tar.gz')
assert sha(archive)==p['archive_sha256']
pid=run(['systemctl','show','bmi30-core','-p','MainPID','--value'])
task=d/'.tmp/base-width-usb-20260930';task.mkdir(exist_ok=True)
m=pathlib.Path('/mnt/bmi30-base-width-usb-20260930');assert not m.exists();m.mkdir()
try:
 run(['mount','/dev/sda2',str(m)]);u=m/'home/techaid/Documents'
 assert sha(u/'AGENTS.md')==p['agents_sha256']
 assert sha(u/'switch_bmi30_split_versions.sh')==p['switcher_sha256']
 assert (u/'host/bmi30_split_active_version.env').read_text()==p['active_env']
 assert not (m/'proc/1/root').exists() and not (m/'run/systemd/system').exists() and not (m/'sys/class/pwm').exists()
 assert shutil.disk_usage(m).free>512*1024*1024
 backup=u/'.tmp/base-width-usb-20260930'/('before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,exist_ok=False)
 names=['host/bmi30_config.json','host/bmi30_sel.json','host/bmi30_relay.json','host/bmi30_split_active_version.env','host/bmi30_firmware_release.env','hotspot_info_server.py','docs/BMI30_version_registry_google_sheet.csv']
 original={}
 for name in names:
  path=u/name
  if path.exists():original[name]=path.read_bytes();shutil.copy2(path,backup/path.name)
 old_config=json.loads(original['host/bmi30_config.json']);new_config=copy.deepcopy(old_config)
 with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=4) as r:live=json.load(r)
 width_settings={k:live['detector']['settings'][k] for k in ('base_width_enabled','base_width_max')}
 assert isinstance(width_settings['base_width_enabled'],bool) and 10<=width_settings['base_width_max']<=100
 new_config.update(width_settings);new_config.setdefault('tag_detection',{}).update(width_settings)
 bundle=u/'host/bmi30_split_bundles'/v
 if not bundle.exists():
  with tarfile.open(archive) as tar:
   for member in tar.getmembers():
    name=pathlib.PurePosixPath(member.name)
    assert not name.is_absolute() and '..' not in name.parts and name.parts[0]==v and (member.isfile() or member.isdir())
   tar.extractall(bundle.parent)
  owner=u.stat()
  for path in [bundle,*bundle.rglob('*')]:os.chown(path,owner.st_uid,owner.st_gid,follow_symlinks=False)
 for name,digest in p['hashes'].items():assert sha(bundle/'project'/name)==digest,name
 null=m/'dev/null'
 if not null.exists():os.mknod(null,stat.S_IFCHR|0o666,os.makedev(1,3))
 assert stat.S_ISCHR(null.stat().st_mode) and null.stat().st_rdev==os.makedev(1,3)
 prefix=['chroot',str(m),'/usr/bin/env','BMI30_OFFLINE_ACTIVATION=1','BMI30_RUNTIME_ONLY=1','BMI30_AGENT_BOOTSTRAPPED=1','BMI30_ACTIVATE_PRESERVE_CONFIG=1','BMI30_SPLIT_SELECTED_BY_OVERRIDE=base-width-user-authorized-usb','/bin/bash','/home/techaid/Documents/switch_bmi30_split_versions.sh']
 run(prefix+['--validate',v])
 atomic(u/'host/bmi30_config.json',(json.dumps(new_config,ensure_ascii=False,indent=2)+'\n').encode())
 try:
  result=subprocess.run(prefix+['--activate',v],capture_output=True,text=True)
  (task/'usb-activation.log').write_text(result.stdout+result.stderr)
  assert result.returncode==0,result.stdout+result.stderr
  active=u/'host/bmi30_active_runtime/project'
  for name,digest in p['hashes'].items():assert sha(active/name)==digest,name
  assert json.loads((u/'host/bmi30_config.json').read_text())==new_config
  for name in ('host/bmi30_sel.json','host/bmi30_relay.json'):
   if name in original:assert (u/name).read_bytes()==original[name],name
  assert (u/'host/bmi30_firmware_release.env').read_text()==p['release']
  assert 'BMI30_SPLIT_VERSION='+v in (u/'host/bmi30_split_active_version.env').read_text()
  atomic(u/'hotspot_info_server.py',(active/'hotspot_info_server.py').read_bytes())
  assert sha(m/'usr/local/bin/bmi30-hotspot-info-server.py')==p['hashes']['hotspot_info_server.py']
 except Exception:
  atomic(u/'host/bmi30_config.json',original['host/bmi30_config.json'])
  rollback=subprocess.run(prefix+['--activate','2026-09-30-1135'],capture_output=True,text=True)
  (task/'usb-rollback.log').write_text(rollback.stdout+rollback.stderr)
  raise
 # Copy the current journal and note only the USB installation completed here.
 with (d/'docs/BMI30_version_registry_google_sheet.csv').open(encoding='utf-8-sig',newline='') as f:
  reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
 entry=next(row for row in rows if row[fields[1]]==v)
 entry[fields[11]]+=' USB: установлен на подключённый носитель '+p['board']+'; настройки сохранены, добавлены параметры ширины из eMMC. Запуск с USB не проверялся.'
 buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=fields);writer.writeheader();writer.writerows(rows)
 atomic(u/'docs/BMI30_version_registry_google_sheet.csv',buf.getvalue().encode('utf-8-sig'))
 run(['sync','-f',str(m)])
 assert pid==run(['systemctl','show','bmi30-core','-p','MainPID','--value'])
 result=dict(host=p['host'],board=p['board'],version=v,serial=p['serial'],uuid=p['uuid'],source='/dev/sda2',hashes=p['hashes'],width_settings=width_settings,other_settings_preserved=True,mode=json.loads((u/'host/bmi30_sel.json').read_text())['sel'],live_core_pid_unchanged=pid,boot_tested=False,usb_hostname=(m/'etc/hostname').read_text().strip(),backup=str(backup.relative_to(m)),network_service_installed=(m/'opt/bmi30-network/service.py').is_file())
 (task/'usb-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
finally:
 if subprocess.run(['mountpoint','-q',str(m)]).returncode==0:run(['umount',str(m)])
 m.rmdir()
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('board',choices=('M01','S10'));args=parser.parse_args()
    host=HOSTS[args.board]
    discovery=json.loads((WORK/(host+'-discovery.json')).read_text())
    inspected=json.loads((WORK/(host+'-usb-inspect.json')).read_text())
    info=json.loads((WORK.parent/'base-width-colors-20260930/bundle.json').read_text())
    disk=next(d for d in discovery['disks']['blockdevices'] if d['path']=='/dev/sda' and d['tran']=='usb')
    payload=dict(host=host,board=args.board,serial=disk['serial'],uuid=inspected['uuid'],
                 agents_sha256=hashlib.sha256(inspected['agents']['home/techaid/Documents/AGENTS.md'].encode()).hexdigest(),
                 switcher_sha256=inspected['switcher_sha256'],active_env=inspected['files']['host/bmi30_split_active_version.env'],
                 version=info['version'],archive_sha256=info['sha256'],hashes=info['hashes'],release=info['release'])
    result=json.loads(remote(host,REMOTE,payload,timeout=120))
    (WORK/(args.board+'-usb-result.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':main()
