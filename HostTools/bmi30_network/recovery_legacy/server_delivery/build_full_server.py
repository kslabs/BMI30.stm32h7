"""Current application + complete portable network assets + old-peer recovery."""
import argparse,ast,hashlib,io,json,pathlib,sys,tarfile
import build_server as base
import make_server_sources as sources
import patch_modern_service
import patch_router_credentials
HERE=pathlib.Path(__file__).resolve().parent
ROOT=base.ROOT
sys.path.insert(0,str(ROOT/'HostTools/usb_recovery'))
from integrate_bundle import add_to_bundle as add_emergency_usb,portal as emergency_usb_portal,validate_included,shared_module
from patch_hotspot_identity import boot_script as safe_hotspot_profiles,identity_script as safe_identity_profiles
COHORT=(*base.CLIENT_IDS,'BMI30-27448D72B','BMI30-E29805C7C')
NETWORK_VERSION='0.2.9'
HARDWARE_COMPATIBILITY={'schema':1,'models':['raspberrypi,5-compute-module'],'software_contract':'bmi30-split-v1'}

ADDRESS_PY='''def retained_ap_cidr():
    import ipaddress
    for path,key in ((Path('/var/lib/bmi30-access/retained-ap.json'),'cidr'),
                     (Path('/etc/bmi30-network/config.json'),'ap_address')):
        try:
            value=ipaddress.IPv4Interface(json.loads(path.read_text())[key])
            if value.ip.is_private:return str(value)
        except (OSError,ValueError,KeyError,TypeError):pass
    return '10.42.0.1/24'

AP_CIDR = retained_ap_cidr()
AP_IP = AP_CIDR.split('/')[0]
'''

def patch_access(source):
 source=source.replace("AP_IP = '10.42.0.1'",ADDRESS_PY)
 source=source.replace("'ipv4.addresses', AP_IP+'/24'", "'ipv4.addresses', AP_CIDR")
 source=sources.patch_recovery.replace_once(source,"\n    (RUNTIME/'busy').unlink(missing_ok=True)\n",
     "\n    if not access_policy.maintenance():\n        (RUNTIME/'busy').unlink(missing_ok=True)\n")
 ast.parse(source)
 return source

def patch_saved_profiles(source):
 # Keep the shipped adapter intact except for choosing among duplicate SSIDs.
 current=(ROOT/'HostTools/bmi30_network/network.py').read_text(encoding='utf-8')
 def method(text):
  cls=next(n for n in ast.parse(text).body if isinstance(n,ast.ClassDef) and n.name=='Network')
  return next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='profiles')
 old=method(source);new=method(current)
 lines=source.splitlines(keepends=True);replacement=''.join(current.splitlines(keepends=True)[new.lineno-1:new.end_lineno])
 patched=''.join(lines[:old.lineno-1])+replacement+''.join(lines[old.end_lineno:])
 ast.parse(patched)
 return patched

def patch_hotspot(source):
 # The new supervisor and the boot script read the same preserved AP subnet.
 setup="""\nBMI30_RETAINED_AP_CIDR=\"$(python3 - <<'PY'
import json,pathlib,ipaddress
value='10.42.0.1/24'
for path,key in [('/var/lib/bmi30-access/retained-ap.json','cidr'),('/etc/bmi30-network/config.json','ap_address')]:
    try:
        candidate=ipaddress.IPv4Interface(json.loads(pathlib.Path(path).read_text())[key])
        if candidate.ip.is_private:value=str(candidate);break
    except (OSError,ValueError,KeyError,TypeError):pass
print(value)
PY
)\"\n"""
 source=source.replace('set -euo pipefail\n','set -euo pipefail\n'+setup,1)
 return safe_hotspot_profiles(source.replace('ipv4.addresses 10.42.0.1/24','ipv4.addresses "$BMI30_RETAINED_AP_CIDR"'))

def full_portal(source,bundle_id):
 source=sources.portal(source)
 begin=source.index('def install_legacy_service_recovery_menu_component():')
 end=source.index('\ndef main() -> None:',begin)
 source=source[:begin]+'''def install_legacy_service_recovery_menu_component():
    split = detect_bmi30_split_system_version()
    core = pathlib.Path(_resolve_project_path(split['core_path']))
    path = core.parent / 'bmi30_full_bootstrap.py'
    spec = importlib.util.spec_from_file_location('bmi30_full_bootstrap', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.start()

''' +source[end:]
 status='''def last_full_update_rollback():
    try:
        path=pathlib.Path('/var/lib/bmi30-update-diagnostics/last-rollback.json')
        if path.stat().st_size>32768:return None
        value=json.loads(path.read_text())
        return {key:value.get(key) for key in ('schema','bundle_id','expected_id','trigger','phase',
                'last_observation','last_failed_observation','server_probe','history')}
    except Exception:
        return None

def full_update_status():
    try:
        value=json.loads(pathlib.Path('/var/lib/bmi30-legacy-menu/BOUND_FULL_BUNDLE/state.json').read_text())
        watch=value.get('connectivity_watch') or {}
        return {'bundle_id':'BOUND_FULL_BUNDLE','stage':value.get('stage'),'network_version':value.get('network_version'),'system_files':len(value.get('files',{})),
                'connection_guard':{key:watch.get(key) for key in ('armed','status','phase','last_check_ok','first_boot_pending',
                                    'last_observation','last_failed_observation','server_probe')},
                'persistent_guard':bool(value.get('persistent_guard')),'last_rollback':last_full_update_rollback()}
    except Exception:
        return {'bundle_id':'BOUND_FULL_BUNDLE','stage':'waiting-for-system-component','last_rollback':last_full_update_rollback()}

'''.replace('BOUND_FULL_BUNDLE',bundle_id)
 source=source.replace('def read_legacy_service_recovery_diagnostics():',status+'def read_legacy_service_recovery_diagnostics():',1)
 source=source.replace("return {'available': True, 'network_version':", "return {'available': True, 'full_update':full_update_status(), 'network_version':",1)
 source=source.replace("return {'available': False}\n", "return {'available': False, 'full_update':full_update_status()}\n",1)
 source=source.replace("'local_id': str(local.get('id', ''))[:64]", "'network_status':str(state.get('status',''))[:64], "
      "'network_observation_age_s': round(max(0,time.monotonic()-state['checked_mono']),1) if isinstance(state.get('checked_mono'),(int,float)) else None, "
      "'local_id': str(local.get('id', ''))[:64]",1)
 return source

def assemble(bundle_id,created):
 stock=ROOT/'FirmwareReleases/RaspberryPi/2026-10-23-1514/BMI30-RPi-2026-10-23-1514.tar.gz'
 modes={};files=base.read_bundle(stock,modes);original=dict(files)
 manifest=base.contract.parse_env(files['manifest.env']);component=(HERE/'full_component.py').read_bytes()
 guard_sha=base.sha(component)
 helper=sources.helper(COHORT,bundle_id,guard_sha)
 original_service=files['system/opt/bmi30-network/service.py'].decode()
 service=sources.service(original_service,NETWORK_VERSION)
 service=patch_router_credentials.patch(service,(ROOT/'HostTools/bmi30_network/service.py').read_text(encoding='utf-8'))
 service=patch_modern_service.patch(service,(ROOT/'HostTools/bmi30_network/service.py').read_text(encoding='utf-8'))
 service=service.replace('from legacy_recovery import LegacyRecovery\n',helper+'\n',1).encode()
 ast.parse(service)
 system={name[7:]:data for name,data in files.items() if name.startswith('system/')}
 system['opt/bmi30-network/service.py']=service
 system['opt/bmi30-network/network.py']=patch_saved_profiles(system['opt/bmi30-network/network.py'].decode()).encode()
 system['opt/bmi30-network/access.py']=patch_access(system['opt/bmi30-network/access.py'].decode()).encode()
 system['opt/bmi30-network/status.html']=shared_module('bundle_ui').html((ROOT/'HostTools/bmi30_network/status.html').read_text(encoding='utf-8')).encode()
 system['opt/bmi30-network/group_ui.js']=(ROOT/'HostTools/bmi30_network/group_ui.js').read_bytes()
 system['usr/local/bin/bmi30-hotspot.sh']=patch_hotspot(system['usr/local/bin/bmi30-hotspot.sh'].decode()).encode()
 system['usr/local/sbin/bmi30-refresh-network-identity.sh']=safe_identity_profiles(system['usr/local/sbin/bmi30-refresh-network-identity.sh'].decode()).encode()
 portal_rel=manifest['BMI30_BUNDLE_PORTAL_REL'];portal=emergency_usb_portal(full_portal(files[portal_rel].decode(),bundle_id)).encode()
 system['home/techaid/Documents/hotspot_info_server.py']=portal
 info={'version':NETWORK_VERSION,'release':bundle_id,'created_at':created,
       'description':'Current network stack with authenticated active legacy recovery',
       'hashes':{name[len('opt/bmi30-network/'):]:base.sha(data) for name,data in system.items()
                 if name.startswith('opt/bmi30-network/') and not name.endswith('/release.json')}}
 system['opt/bmi30-network/release.json']=(json.dumps(info,indent=2)+'\n').encode()
 assets={'schema':4,'assets':{name:base.sha(data) for name,data in system.items()
         if name in json.loads(files['metadata/portable-assets.json'])['assets']}}
 portable=(ROOT/'HostTools/bmi30_network/portable_assets.py').read_bytes()
 payload={'component.py':component,'portable.py':portable,
          'payload/metadata/portable-assets.json':(json.dumps(assets,indent=2)+'\n').encode(),
          'payload/project/hotspot_info_server.py':portal}
 payload.update({'payload/system/'+name:data for name,data in system.items() if name in assets['assets']})
 previous=ROOT/'FirmwareReleases/RaspberryPi/2026-10-27-133000/review/bundle/project/host/bmi30_legacy_recovery/network-0.1.13'
 recovery3={name:base.sha((previous/name).read_bytes()) for name in ('service.py','legacy_recovery.py')}
 for name in recovery3:payload['recovery3/'+name]=(previous/name).read_bytes()
 old=ROOT/'.tmp/wifi-offline-20261027/baseline/network/2026-10-05-network-13/service.py'
 shipped_full=ROOT/'FirmwareReleases/RaspberryPi/2026-10-27-152521/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 previous_full=ROOT/'FirmwareReleases/RaspberryPi/2026-10-28-153913/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 previous_local=ROOT/'FirmwareReleases/RaspberryPi/2026-10-28-181307/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 current_local=ROOT/'FirmwareReleases/RaspberryPi/2026-10-28-183427/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 published_current=ROOT/'FirmwareReleases/RaspberryPi/2026-10-29-084854/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 published_usb=ROOT/'FirmwareReleases/RaspberryPi/2026-10-29-112719/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 local_guard_trial=ROOT/'FirmwareReleases/RaspberryPi/2026-10-29-123717/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 local_seed_trial=ROOT/'FirmwareReleases/RaspberryPi/2026-10-29-125008/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 local_peer_trial=ROOT/'FirmwareReleases/RaspberryPi/2026-10-29-140802/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 current_verified=ROOT/'FirmwareReleases/RaspberryPi/2026-10-29-143408/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 saved_wifi_local=ROOT/'FirmwareReleases/RaspberryPi/2026-10-29-164828/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 saved_wifi_published=ROOT/'FirmwareReleases/RaspberryPi/2026-10-29-171220/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 maintenance_local=ROOT/'FirmwareReleases/RaspberryPi/2026-10-30-084653/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 continuous_local=ROOT/'FirmwareReleases/RaspberryPi/2026-10-30-085518/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 published_destination=ROOT/'FirmwareReleases/RaspberryPi/2026-10-30-095430/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
 # Older already-running guardians understand only allowed_ids. This field
 # keeps their handover compatible; native installation uses the model check.
 component_manifest={'bundle_id':bundle_id,'allowed_ids':list(COHORT)+['BMI30-C9775FBAE'],
   'hardware_compatibility':HARDWARE_COMPATIBILITY,'network_version':NETWORK_VERSION,
   'requires_saved_rollback_from':['2026-10-27-152521'],
   'allowed_service_sha256':[base.sha(old.read_bytes()),recovery3['service.py'],base.sha(original_service.encode()),base.sha(service),base.sha((ROOT/'HostTools/bmi30_network/service.py').read_bytes()),base.sha(shipped_full.read_bytes()),base.sha(previous_full.read_bytes()),base.sha(previous_local.read_bytes()),base.sha(current_local.read_bytes()),base.sha(published_current.read_bytes()),base.sha(published_usb.read_bytes()),base.sha(local_guard_trial.read_bytes()),base.sha(local_seed_trial.read_bytes()),base.sha(local_peer_trial.read_bytes()),base.sha(current_verified.read_bytes()),base.sha(saved_wifi_local.read_bytes()),base.sha(saved_wifi_published.read_bytes())],
   'recovery3':recovery3,'payload_sha256':{name:base.sha(data) for name,data in payload.items()}}
 component_manifest['allowed_service_sha256'].append(base.sha(maintenance_local.read_bytes()))
 component_manifest['allowed_service_sha256'].append(base.sha(continuous_local.read_bytes()))
 component_manifest['allowed_service_sha256'].append(base.sha(published_destination.read_bytes()))
 payload['manifest.json']=(json.dumps(component_manifest,indent=2)+'\n').encode()
 for name in list(files):
  if name.startswith(('metadata/','system/')):del files[name]
 manifest['BMI30_BUNDLE_PORTABLE_SYSTEM']='0'
 files[portal_rel]=portal
 add_emergency_usb(files,manifest,bundle_id);validate_included(files,manifest)
 files['project/host/bmi30_full_bootstrap.py']=(HERE/'full_bootstrap.py').read_bytes()
 files.update({'project/host/bmi30_full_update/'+name:data for name,data in payload.items()})
 label='Connection guard: verified TLS address fallback and persistent rollback diagnosis; USB recovery retained'
 manifest.update(BMI30_BUNDLE_ID=bundle_id,BMI30_BUNDLE_CREATED_AT=created,BMI30_BUNDLE_LABEL=label,
                 BMI30_BUNDLE_ORIGIN='Full base 2026-10-23-1514; complete network '+NETWORK_VERSION)
 files['manifest.env']=base.contract.env_bytes(manifest)
 release_rel=manifest['BMI30_BUNDLE_RELEASE_REL'];release=base.contract.parse_env(files[release_rel])
 release.update(BMI30_FIRMWARE_VERSION=bundle_id,BMI30_FIRMWARE_BUNDLE_ID=bundle_id,
                BMI30_FIRMWARE_CREATED_AT=created,BMI30_FIRMWARE_LABEL=label)
 for part in ('CORE','ENGINE','GUI','PORTAL'):
  path=manifest['BMI30_BUNDLE_'+part+'_REL'];release['BMI30_FIRMWARE_'+part+'_SHA256']=base.sha(files[path])
  if part in ('ENGINE','GUI'):assert files[path]==original[path]
  if part=='CORE':
   def backend(data):return [ast.dump(n,include_attributes=False) for n in ast.parse(data).body if not (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SERVICE_HTML' for t in n.targets))]
   assert backend(files[path])==backend(original[path]),'Core backend changed'
 files[release_rel]=base.contract.env_bytes(release)
 files['SHA256SUMS']=''.join(base.sha(data)+'  '+name+'\n' for name,data in sorted(files.items()) if name!='SHA256SUMS').encode()
 return files,modes,manifest,release,component_manifest

def build(bundle_id,created):
 files,modes,manifest,release,component_manifest=assemble(bundle_id,created)
 output=ROOT/'FirmwareReleases/RaspberryPi'/bundle_id;output.mkdir(exist_ok=False)
 review=output/'review/bundle'
 for name,data in files.items():
  path=review/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
  if name.endswith('.py'):ast.parse(data,filename=name)
 base.contract.verify_bundle(review,bundle_id)
 # Check the exact portable helper against its actual nested payload.
 portable=base.contract
 import importlib.util
 helper=review/'project/host/bmi30_full_update/portable.py'
 spec=importlib.util.spec_from_file_location('full_candidate_portable',helper);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 assets=module.validate(helper.parent/'payload')
 name='bmi30_backup_'+bundle_id[:10].replace('-','')+'_'+bundle_id[-6:]+'.tar.gz'
 outer={'host/bmi30_firmware_release.env':files[manifest['BMI30_BUNDLE_RELEASE_REL']],
        'host/bmi30_split_active_version.env':base.contract.env_bytes({'BMI30_SPLIT_BUNDLE_ID':bundle_id})}
 outer.update({'host/bmi30_split_bundles/'+bundle_id+'/'+path:data for path,data in files.items()})
 with tarfile.open(output/name,'w:gz') as tar:
  prefix='host/bmi30_split_bundles/'+bundle_id+'/'
  for path,data in sorted(outer.items()):
   item=tarfile.TarInfo('Documents/'+path);item.size=len(data);item.mtime=0
   item.mode=modes.get(path[len(prefix):],0o644) if path.startswith(prefix) else 0o644
   tar.addfile(item,io.BytesIO(data))
 info=base.contract.marker(base.contract.env_bytes({'FIRMWARE_VERSION':bundle_id,'FIRMWARE_BUNDLE_ID':bundle_id,
       'ARCHIVE_NAME':name,'ARCHIVE_SIZE':str((output/name).stat().st_size),'ARCHIVE_SHA256':base.sha((output/name).read_bytes())}))
 (output/'bmi30_latest.env').write_bytes(base.contract.env_bytes(info))
 report={'kind':'current-client-full','base_bundle':'2026-10-23-1514','firmware_version':bundle_id,'bundle_id':bundle_id,
  'marker':info,'archive':str(output/name),'network_version':NETWORK_VERSION,
  'hardware_compatibility':HARDWARE_COMPATIBILITY,'device_id_restriction':False,
  'legacy_guard_ids':component_manifest['allowed_ids'],
  'complete_portable_assets':len(assets),'stm32_updated':False,'published':False,'tested_on_hardware':False,
  'component_hashes':{part:release['BMI30_FIRMWARE_'+part+'_SHA256'] for part in ('CORE','ENGINE','GUI','PORTAL')}}
 (output/'release.json').write_text(json.dumps(report,indent=2)+'\n');return report

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--bundle-id',required=True);parser.add_argument('--created-at',required=True)
 args=parser.parse_args();print(json.dumps(build(args.bundle_id,args.created_at),indent=2))
