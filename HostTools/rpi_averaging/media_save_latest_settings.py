"""Snapshot the user's latest S07 USB settings to its inactive eMMC."""
import hashlib,json,os,pathlib,shutil,subprocess,sys,time
p=json.load(sys.stdin);P=pathlib.Path;M=P('/mnt/bmi30-media-last-settings')
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')==p['hostname']=='BMI30-C9775FBAE'
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2'
assert P('/sys/block/mmcblk0/device/cid').read_text()==p['emmc_cid']
assert not subprocess.run(['findmnt','-rn','-S','/dev/mmcblk0p2'],capture_output=True,text=True).stdout
files=['home/techaid/Documents/host/bmi30_config.json','home/techaid/Documents/host/bmi30_sel.json','usr/local/bin/host/bmi30_config.json']
snap={n:(P('/')/n).read_bytes() for n in files}
cfg=json.loads(snap[files[0]]);out={};M.mkdir(exist_ok=False)
try:
 run('mount','/dev/mmcblk0p2',str(M))
 backup=M/'var/backups/bmi30-media-sync'/('latest-settings-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
 for n,raw in snap.items():
  f=M/n;assert f.is_file() and not f.is_symlink()
  if f.read_bytes()!=raw:
   dst=backup/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dst)
   s=f.stat();tmp=f.with_name(f.name+'.media-settings-tmp');tmp.write_bytes(raw);os.chown(tmp,s.st_uid,s.st_gid);os.chmod(tmp,s.st_mode&0o777);tmp.replace(f)
  assert f.read_bytes()==raw;out[n]=hashlib.sha256(raw).hexdigest()
 run('sync','-f',str(M))
 same=all((P('/')/n).read_bytes()==raw for n,raw in snap.items())
finally:
 if subprocess.run(['mountpoint','-q',str(M)]).returncode==0:run('umount',str(M))
 M.rmdir()
print(json.dumps({'settings_sha256':out,'source_unchanged_during_copy':same,'avg_n':cfg['avg_n'],'rpi_avg_n':cfg['rpi_avg_n']}))
