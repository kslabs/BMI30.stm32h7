"""Run filesystem transaction tests on M02 in a temporary simulated root only."""
import base64,json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'.tmp/new-device-20261009'))
from device_remote import remote

SCRIPT=r'''
import pathlib,json,sys,base64,subprocess,shutil,uuid
P=pathlib.Path;assert P('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
p=json.load(sys.stdin);root=P('/var/tmp')/('bmi30-software-check-'+uuid.uuid4().hex);root.mkdir(mode=0o700)
for name,encoded in p['files'].items():
 relative=P(name);assert not relative.is_absolute() and '..' not in relative.parts
 path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(base64.b64decode(encoded))
results=[]
for suite in p['suites']:
 r=subprocess.run(['python3','-m','unittest','discover','-s',suite,'-p','test_*.py'],cwd=root,capture_output=True,text=True,timeout=100)
 results.append({'suite':suite,'returncode':r.returncode,'stdout':r.stdout[-1200:],'stderr':r.stderr[-4500:]})
report={'returncode':max(r['returncode'] for r in results),'suites':results,
 'scope':'temporary simulated filesystem; real M02 services, firmware, profiles and tokens unchanged'}
assert root.parent==P('/var/tmp') and root.name.startswith('bmi30-software-check-');shutil.rmtree(root)
print(json.dumps(report))
'''

if __name__=='__main__':
 files=[];delivery=ROOT/'HostTools/bmi30_network/recovery_legacy/server_delivery'
 for name in ('test_full_update.py','test_delivery.py','build_full_server.py','full_component.py','build_server.py','make_server_sources.py','full_bootstrap.py'):
  files.append(delivery/name)
 parent=delivery.parent
 files += [parent/name for name in ('legacy_recovery.py','active_recovery.py','patch_recovery.py','install_recovery.py')]
 files += list(parent.glob('test_*.py'))
 files += list(parent.parent.glob('*.py'))
 files += [ROOT/'HostTools/bmi30_network/portable_assets.py',ROOT/'FirmwareReleases/RaspberryPi/2026-10-23-1514/BMI30-RPi-2026-10-23-1514.tar.gz',
  ROOT/'.tmp/legacy-menu-20261027/M10-network-original.py',
  ROOT/'.tmp/relay-web-20260929/192.168.0.163/audit/home/techaid/Documents/utilities/bmi30_server_files.py',
  ROOT/'.tmp/wifi-offline-20261027/baseline/network/2026-10-05-network-13/service.py',
  ROOT/'FirmwareReleases/RaspberryPi/2026-10-05-network-13/BMI30-Network-2026-10-05-network-13.tar.gz']
 prior=ROOT/'FirmwareReleases/RaspberryPi/2026-10-27-133000/review/bundle/project/host/bmi30_legacy_recovery/network-0.1.13'
 files += [prior/name for name in ('service.py','legacy_recovery.py')]
 files += list((ROOT/'HostTools/usb_recovery').glob('*.py'))
 files += [ROOT/'HostTools/usb_recovery/recovery.html']
 files += [ROOT/'HostTools/usb_recovery/bmi30-usb-no-emmc.dtbo',ROOT/'HostTools/usb_recovery/bmi30-usb-no-emmc-overlay.dts']
 suites=['HostTools/bmi30_network/recovery_legacy','HostTools/bmi30_network/recovery_legacy/server_delivery']
 delivery_only='--delivery-only' in sys.argv
 if delivery_only:suites=suites[-1:]
 result=remote(SCRIPT,payload={'files':{f.relative_to(ROOT).as_posix():base64.b64encode(f.read_bytes()).decode() for f in files},'suites':suites},timeout=100)
 name='linux-recovery6-delivery-tests.json' if delivery_only else 'linux-recovery6-tests.json'
 (ROOT/'.tmp/current-client-migration-20261027'/name).write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result))
