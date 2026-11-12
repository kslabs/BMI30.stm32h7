"""Audit the current full base and stage recovery sources for the client fleet.

This is a review tree, not an installable/published firmware. The full portable
asset transaction and rollback to the old network stack still need integration.
"""
import hashlib,json,pathlib,sys
HERE=pathlib.Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(HERE/'server_delivery'))
import build_server,make_server_sources

COHORT=('BMI30-05AB38E6A','BMI30-D5507EDFC','BMI30-7AA5CED27','BMI30-27448D72B')
BASE='2026-10-23-1514'
OUTPUT=ROOT/'.tmp/current-client-migration-20261027'

def sha(data):return hashlib.sha256(data).hexdigest()

def main():
 files=build_server.read_bundle(ROOT/'FirmwareReleases/RaspberryPi'/BASE/('BMI30-RPi-'+BASE+'.tar.gz'))
 sums={name:checksum.lower() for line in files['SHA256SUMS'].decode().splitlines()
       for checksum,name in [line.split('  ',1)]}
 actual={name:sha(data) for name,data in files.items() if name!='SHA256SUMS'}
 assert actual==sums,'Full base archive checksum mismatch'
 manifest=build_server.contract.parse_env(files['manifest.env'])
 assert manifest['BMI30_BUNDLE_ID']==BASE
 network=files['system/opt/bmi30-network/service.py'].decode()
 assert 'VERSION = "0.2.4"' in network
 candidate=make_server_sources.service(network,'0.2.4-recovery4').encode()
 OUTPUT.mkdir(exist_ok=True)
 (OUTPUT/'service.py').write_bytes(candidate)
 report={'status':'source-review-only; not installable or published',
  'target_base':BASE,'target_firmware_base':'2026-10-23-151427',
  'client_cohort':list(COHORT),'active_recovery_donor':COHORT[0],
  'offline_target':COHORT[-1],'base_network':'0.2.4','recovery_network':'0.2.4-recovery4',
  'base_checksum_files_verified':len(sums),'rendered_service_sha256':sha(candidate),
  'components':{name:sha(files[manifest['BMI30_BUNDLE_'+name+'_REL']]) for name in ('CORE','ENGINE','GUI','PORTAL')},
  'system_asset_count':sum(name.startswith('system/') for name in files),
  'network_assets':[name for name in files if name.startswith('system/opt/bmi30-network/')],
  'stm32_payload_included':False,'hardware_checked':False,
  'required_before_publication':[
   'Migrate the complete network dependency set, not service.py alone.',
   'Preserve device identity, fleet key, router/AP passwords, detector settings and calibration.',
   'Preserve distinct AP subnets consistently in NetworkManager, DNS and access supervisor.',
   'Snapshot and roll back all portable files/services as well as the core bundle.',
   'Accept upgrade from original 0.1.13 and exact deployed 0.1.13-recovery3.',
   'Verify unchanged legacy 0.1.13 receiver recovery and normal modern v2 exchange.',
   'Boot/runtime and radio/rollback tests on positively identified connected test boards.',
   'Publish one reviewed client release for all four boards through the BMI30 server.']}
 (OUTPUT/'migration.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ('status','target_base','recovery_network','base_checksum_files_verified','system_asset_count','client_cohort')}))

if __name__=='__main__':main()
