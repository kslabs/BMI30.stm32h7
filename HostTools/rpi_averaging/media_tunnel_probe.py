import json,pathlib,subprocess,sys
p=json.load(sys.stdin);P=pathlib.Path
def run(*a):return subprocess.run(a,capture_output=True,text=True).stdout.strip()
assert run('hostname')==p['hostname']
def meta(f):
 r={'exists':f.exists(),'symlink':f.is_symlink()}
 if f.is_symlink():r['target']=str(f.readlink())
 if f.exists():s=f.stat();r.update(size=s.st_size,mode=oct(s.st_mode&0o777),uid=s.st_uid,gid=s.st_gid)
 return r
names=['etc/bmi30-agent/tunnel.env','var/lib/bmi30-agent/device_api_token','var/lib/bmi30-agent/bound_raspberry_serial']
out={'files':{n:meta(P('/')/n) for n in names},'agent_keys':[]}
state=P('/var/lib/bmi30-agent/state.json')
if state.exists():
 j=json.loads(state.read_text());out['agent_keys']=list(j);out['agent']={k:j.get(k) for k in ['status','http_status','approved','last_checkin_unix','server_state','server_message','tunnel_service']}
out['bound_serial']=(P('/')/names[-1]).read_text() if (P('/')/names[-1]).is_file() else None
out['tunnel_service']=run('systemctl','cat','bmi30-tunnel')
out['agent_service']=run('systemctl','cat','bmi30-agent')
out['tunnel_scripts']=run('find','/home/techaid/Documents','/opt','/usr/local','-name','run_bmi30_tunnel.sh','-type','f')
out['credential_writers']=run('grep','-rl','--include=*.py','--include=*.sh','tunnel.env','/opt/bmi30-agent','/usr/local/lib/bmi30','/home/techaid/Documents/utilities')
if run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2':
 M=P('/mnt/bmi30-media-source-audit');M.mkdir(exist_ok=False)
 try:
  assert P('/sys/block/mmcblk0/device/cid').read_text()==p['emmc_cid']
  assert not run('findmnt','-rn','-S','/dev/mmcblk0p2')
  subprocess.run(['mount','-o','ro,noload','/dev/mmcblk0p2',str(M)],check=True)
  out['emmc_files']={n:meta(M/n) for n in names}
  out['emmc_bound_serial']=(M/names[-1]).read_text() if (M/names[-1]).is_file() else None
  j=json.loads((M/'var/lib/bmi30-agent/state.json').read_text())
  out['emmc_agent']={k:j.get(k) for k in ['http_status','last_checkin_unix','server_state','server_message','tunnel_service']}
  out['identity_files_equal']={n:(P('/')/n).read_bytes()==(M/n).read_bytes() for n in names[1:]+['etc/bmi30-agent/id_ed25519','etc/bmi30-agent/id_ed25519.pub']}
 finally:
  subprocess.run(['umount',str(M)],check=True);M.rmdir()
print(json.dumps(out))
