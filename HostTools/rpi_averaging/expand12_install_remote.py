"""Install a validated canonical bundle on active USB and guarded inactive eMMC."""
import base64,hashlib,io,json,os,pathlib,shlex,shutil,subprocess,sys,tarfile,time,urllib.request
p=json.load(sys.stdin);i=p['before'];r=p['release'];P=pathlib.Path;v=r['version']
M=P('/mnt/bmi30-average12-emmc');D=P('/home/techaid/Documents');A=D/'host/bmi30_active_runtime/project'
def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def api():return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=5))
def atomic(f,raw):
 s=f.stat();t=f.with_name(f.name+'.avg12-tmp');t.write_bytes(raw);os.chown(t,s.st_uid,s.st_gid);os.chmod(t,s.st_mode&0o777);t.replace(f)
assert run('hostname')==i['hostname']
assert P('/proc/device-tree/serial-number').read_text()==i['serial']
assert run('findmnt','-n','-o','SOURCE','/')==i['root']=='/dev/sda2'
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==i['usb_serial']
assert P('/sys/block/mmcblk0/device/cid').read_text()==i['emmc_cid']
assert run('blkid','-s','UUID','-o','value','/dev/mmcblk0p2')==i['emmc_uuid']
assert subprocess.run(['findmnt','-rn','-S','/dev/mmcblk0p2'],stdout=subprocess.DEVNULL).returncode!=0
fsck=subprocess.run(['e2fsck','-f','-n','/dev/mmcblk0p2'],capture_output=True,text=True);assert fsck.returncode==0,fsck.stdout[-1500:]+fsck.stderr[-1500:]
archive=base64.b64decode(p['archive']);assert hashlib.sha256(archive).hexdigest()==r['archive_sha256']
before=api();assert before['ok'] and not before['stale'] and before['connection']['connected']
assert not before['player_recorder']['recording'] and not before['player_recorder']['playback_active']
for n,path in i['paths'].items():assert sha(A/path)==i['hashes'][n],n
M.mkdir(exist_ok=False);roots={};snapshots={};persistent={};paused=False;committed=False
try:
 run('mount','/dev/mmcblk0p2',str(M))
 assert (M/'etc/hostname').read_text().strip()==i['hostname']
 for label,root in [('usb',P('/')),('emmc',M)]:
  d=root/'home/techaid/Documents';a=d/'host/bmi30_active_runtime/project';env=d/'host/bmi30_split_active_version.env';b=d/'host/bmi30_split_bundles'/v
  assert 'BMI30_SPLIT_VERSION=2026-10-13-1534\n' in env.read_text()
  for n,path in i['paths'].items():assert sha(a/path)==i['hashes'][n],(label,n)
  if not b.exists():
   with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as t:
    for member in t.getmembers():
     rel=P(member.name);assert not rel.is_absolute() and '..' not in rel.parts and rel.parts[0]==v and (member.isdir() or member.isfile())
    t.extractall(b.parent)
  assert sha(b/'SHA256SUMS')==r['bundle_checksums_sha256']
  if label=='usb':run('bash',str(d/'switch_bmi30_split_versions.sh'),'--validate',v)
  else:run('chroot',str(root),'/bin/bash','/home/techaid/Documents/switch_bmi30_split_versions.sh','--validate',v)
  targets={a/path:(b/'project'/path).read_bytes() for n,path in r['paths'].items()}
  portal=(b/'project/hotspot_info_server.py').read_bytes()
  targets.update({d/'hotspot_info_server.py':portal,root/'usr/local/bin/bmi30-hotspot-info-server.py':portal})
  manifest=(b/'project/host/bmi30_firmware_release.env').read_bytes()
  targets.update({d/'host/bmi30_firmware_release.env':manifest,a/'host/bmi30_firmware_release.env':manifest})
  updates={'BMI30_SPLIT_VERSION':v,'BMI30_SPLIT_BUNDLE_ID':v,'BMI30_SPLIT_LABEL':'Raspberry averaging 1-12',
   'BMI30_SPLIT_SELECTED_BY':'validated-averaging12-update','BMI30_SPLIT_SELECTED_AT':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
   'BMI30_BUNDLE_ORIGIN':'Raspberry rolling averaging 1-12; centered readout'}
  targets[env]=('\n'.join(line.split('=',1)[0]+'='+shlex.quote(updates[line.split('=',1)[0]]) if line.split('=',1)[0] in updates else line for line in env.read_text().splitlines())+'\n').encode()
  backup=root/'var/backups/bmi30-averaging12'/v;backup.mkdir(parents=True,mode=0o700,exist_ok=False)
  for f in targets:
   assert f.is_file() and not f.is_symlink()
   snapshots[f]=f.read_bytes();dest=backup/f.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dest)
  roots[label]={'root':root,'d':d,'a':a,'b':b,'targets':targets,'backup':backup}
 run('systemctl','stop','bmi30-core','bmi30-hotspot-info');paused=True
 for label,entry in roots.items():
  for folder in [entry['d']/ 'host',entry['root']/'usr/local/bin/host']:
   if folder.exists():
    for f in folder.iterdir():
     if f.is_file() and not f.is_symlink() and f.suffix in ('.json','.npz','.npy'):persistent[f]=f.read_bytes()
  for f,raw in entry['targets'].items():atomic(f,raw)
  for n,path in r['paths'].items():assert sha(entry['a']/path)==r['hashes'][n]
 run('sync','-f',str(M));run('sync','-f',str(D))
 run('systemctl','start','bmi30-core','bmi30-hotspot-info');paused=False
 for n in range(45):
  try:
   after=api();w=after['detector']['worker']
   if after['ok'] and not after['stale'] and after['connection']['connected'] and after['connection']['last_frame_age_s']<2 and w['alive'] and w['results']>0:break
  except Exception:pass
  time.sleep(.4)
 else:raise RuntimeError('Updated core failed health check')
 for key in ['selected','avg_n','rpi_avg_n','stream_mode','base_buf_len']:assert after['mode'][key]==before['mode'][key],key
 for f,raw in persistent.items():
  if f.name=='bmi30_sel.json':assert json.loads(f.read_bytes())['sel']==json.loads(raw)['sel']
  else:assert f.read_bytes()==raw,str(f)
 for entry in roots.values():
  for f,raw in entry['targets'].items():assert f.read_bytes()==raw
 committed=True
 print(json.dumps({'version':v,'firmware_version':r['firmware_version'],'active_root':'/dev/sda2','offline_root':'/dev/mmcblk0p2',
  'roots_verified':['usb','emmc'],'settings_preserved':True,'mode':after['mode'],'worker':after['detector']['worker'],
  'backup':'/var/backups/bmi30-averaging12/'+v,'archive_sha256':r['archive_sha256']}))
finally:
 if snapshots and not committed:
  run('systemctl','stop','bmi30-core','bmi30-hotspot-info')
  for f,raw in snapshots.items():atomic(f,raw)
  for f,raw in persistent.items():atomic(f,raw)
  run('systemctl','start','bmi30-core','bmi30-hotspot-info')
 elif paused:run('systemctl','start','bmi30-core','bmi30-hotspot-info')
 if subprocess.run(['mountpoint','-q',str(M)]).returncode==0:run('umount',str(M))
 M.rmdir()
