"""Read back all four boot roots and the canonical release identity."""
import concurrent.futures,json,hashlib,tarfile
from media_sync import WORK,ROOT,call
REMOTE=r'''
import hashlib,json,pathlib,shlex,subprocess,sys
p=json.load(sys.stdin);P=pathlib.Path;i=p['inventory'];M=P('/mnt/bmi30-media-final-audit')
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def env(f):return {k:shlex.split(v)[0] if shlex.split(v) else '' for line in f.read_text().splitlines() if line and not line.startswith('#') and '=' in line for k,v in [line.split('=',1)]}
assert run('hostname')==i['hostname']
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2'
assert P('/sys/block/mmcblk0/device/cid').read_text()==i['emmc_cid']
assert not subprocess.run(['findmnt','-rn','-S','/dev/mmcblk0p2'],capture_output=True,text=True).stdout
out={};M.mkdir(exist_ok=False)
try:
 run('mount','-o','ro,noload','/dev/mmcblk0p2',str(M))
 for label,root in [('usb',P('/')),('emmc',M)]:
  d=root/'home/techaid/Documents';a=d/'host/bmi30_active_runtime/project';b=d/'host/bmi30_split_bundles'/p['version']
  release=env(d/'host/bmi30_firmware_release.env');active=env(d/'host/bmi30_split_active_version.env')
  assert active['BMI30_SPLIT_VERSION']==p['version']
  assert release['BMI30_FIRMWARE_VERSION']==p['firmware_version']
  assert sha(b/'SHA256SUMS')==p['bundle_checksums_sha256']
  assert sha(d/'host/bmi30_firmware_release.env')==p['manifest_sha256']
  hashes={}
  for component in ['CORE','ENGINE','GUI','PORTAL','USB_STREAM']:
   f=d/release['BMI30_FIRMWARE_'+component+'_PATH'];hashes[component]=sha(f)
   assert hashes[component]==release['BMI30_FIRMWARE_'+component+'_SHA256']
  hashes['AVERAGING']=sha(a/'host/bmi30_averaging.py');assert hashes['AVERAGING']==p['helper_sha256']
  assert sha(root/'usr/local/bin/bmi30-hotspot-info-server.py')==hashes['PORTAL']
  out[label]={'version':active['BMI30_SPLIT_VERSION'],'firmware_version':release['BMI30_FIRMWARE_VERSION'],
   'manifest_sha256':sha(d/'host/bmi30_firmware_release.env'),'hashes':hashes,
   'config_sha256':sha(d/'host/bmi30_config.json'),'selected':json.loads((d/'host/bmi30_sel.json').read_text())['sel']}
 assert all(out['usb'][k]==out['emmc'][k] for k in ['version','firmware_version','manifest_sha256','hashes'])
 out['settings_equal']=out['usb']['config_sha256']==out['emmc']['config_sha256'] and out['usb']['selected']==out['emmc']['selected']
 if not out['settings_equal']:
  live=json.loads(P('/home/techaid/Documents/host/bmi30_config.json').read_text())
  offline=json.loads((M/'home/techaid/Documents/host/bmi30_config.json').read_text())
  out['changed_config_keys']=[k for k in live.keys()|offline.keys() if live.get(k)!=offline.get(k)]
finally:
 if subprocess.run(['mountpoint','-q',str(M)]).returncode==0:run('umount',str(M))
 M.rmdir()
out['code_and_version_match']=True;print(json.dumps(out))
'''
def audit(board):
    archive=ROOT/'FirmwareReleases/RaspberryPi/2026-10-13-1534/BMI30-RPi-2026-10-13-1534.tar.gz';v='2026-10-13-1534'
    with tarfile.open(archive) as t:
        hashes={n:hashlib.sha256(t.extractfile(v+'/'+n).read()).hexdigest() for n in ['SHA256SUMS','project/host/bmi30_firmware_release.env']}
    payload={'inventory':json.loads((WORK/(board+'-inventory.json')).read_text()),'version':v,'firmware_version':'2026-10-13-153432',
             'bundle_checksums_sha256':hashes['SHA256SUMS'],'manifest_sha256':hashes['project/host/bmi30_firmware_release.env'],
             'helper_sha256':hashlib.sha256((ROOT/'HostTools/rpi_averaging/bmi30_averaging.py').read_bytes()).hexdigest()}
    r=call(board,REMOTE,'all-media-verified',payload,timeout=30)
    return {'board':board,**r}
if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for r in pool.map(audit,['S07','M02']):print(json.dumps(r))
