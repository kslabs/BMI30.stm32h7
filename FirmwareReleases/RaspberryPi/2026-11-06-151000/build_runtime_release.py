"""Deterministic local standard server runtime package; never installs/publishes."""
import ast,datetime as dt,gzip,hashlib,io,json,re,shlex,sys,tarfile,types
from pathlib import Path,PurePosixPath
W=Path(__file__).resolve().parent;ROOT=W.parents[1]
OLD='2026-11-06-123518';NEW='2026-11-06-151000'
SOURCE=ROOT/'.tmp/s07-m02-return-20261106/release-builder-v2/native'/OLD/'bmi30_backup_20261106_123518.tar.gz'
SOURCE_SHA='7b095f98316962d85690776cddac2594230ab9e9a1cfb475beb50e9c7eb7574f'
PREFIX='Documents/host/bmi30_split_bundles/'+OLD+'/'
TARGET='Documents/host/bmi30_split_bundles/'+NEW+'/'
CREATED='2026-11-06T15:10:00+01:00'
PINS={'project/host/BMI30.001.py.2026-11-06-111748':'bea3ead6c995e7336f47d81296ea15ca865ce9c5a50a4bbe2bd983ce08f0d55c','project/host/BMI30.200.py.2026-11-04-185916':'d5905f22c629b6b915f656958e236b48de28288c720a537f1781df954a0d74a6','project/host/bmi30_common_mode.py':'9d0b58edd1c81d217777df6fc6954e2592c36f1f93c51717c5f7a45bd1a1270e','project/host/bmi30_dual_detector.py':'166f1f6ed1e55267462ab18232ed4d4746c3feeb6638b3c65e9ab3f16143c08d','project/host/usb_vendor/usb_stream.py':'aafe8faa0d04824e0374a784d7ed58b819a63b8d5fa117b8eb967700cbcc8945','project/host/BMI30.GUI.001.py.2026-09-30-1650':'36223981ca37f49ed53aaa83b4e2d92dde33d23274329b66ff63c3cf378163c1'}
ADAPTER=b'''"""Standard runtime-only delivery: no full-system migration is included."""\n\ndef start():\n    """Portal startup compatibility entry point; no background installer."""\n    return None\n'''
def need(v,c):
 if not v:raise RuntimeError(c)
def sha(b):return hashlib.sha256(b).hexdigest()
def env(raw):
 out={}
 for line in raw.decode().splitlines():
  if not line or line.startswith('#'):continue
  k,v=line.split('=',1);out[k]=shlex.split(v)[0]if shlex.split(v)else''
 return out
def eb(d):return ''.join(k+'='+shlex.quote(str(v))+'\n'for k,v in d.items()).encode()
def load_module(name,raw):
 m=types.ModuleType(name);m.__file__=str(W/(name+'.py'));sys.modules[name]=m;exec(compile(raw,m.__file__,'exec'),m.__dict__);return m
def read_source():
 raw=SOURCE.read_bytes();need(sha(raw)==SOURCE_SHA,'frozen_native_source_changed');files={};modes={}
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz')as t:
  for member in t:
   p=PurePosixPath(member.name);need(member.isfile()and not p.is_absolute()and'..'not in p.parts and str(p)==member.name and member.name not in files,'unsafe_native_archive_member')
   files[member.name]=t.extractfile(member).read();modes[member.name]=member.mode&0o777
 need(len(files)==326,'native_file_count_changed');return files,modes
def render():
 source,modes=read_source();bundle={n[len(PREFIX):]:b for n,b in source.items()if n.startswith(PREFIX)}
 old_sums={line.split('  ',1)[1]:line.split('  ',1)[0]for line in bundle['SHA256SUMS'].decode().splitlines()}
 need(old_sums=={n:sha(b)for n,b in bundle.items()if n!='SHA256SUMS'},'source_bundle_closure_changed')
 removed=[n for n in bundle if n.startswith('project/host/bmi30_full_update/')or n.endswith('/bmi30_native_service_variants.py')]
 need(sum(n.startswith('project/host/bmi30_full_update/')for n in removed)==105,'native_payload_count_changed')
 bundle={n:b for n,b in bundle.items()if n not in removed}
 old_adapter=sha(bundle['project/host/bmi30_full_bootstrap.py']);bundle['project/host/bmi30_full_bootstrap.py']=ADAPTER
 bt='project/host/bmi30_bluetooth_remote_test.py';old_bt=sha(bundle[bt]);text=bundle[bt].decode();needle="DEFAULT_HOSTNAME = 'BMI30-C9775FBAE'";need(text.count(needle)==1 and 'import socket'in text,'exact_BT_own_hostname_source_changed');bundle[bt]=text.replace(needle,'DEFAULT_HOSTNAME = socket.gethostname()').encode();ast.parse(bundle[bt])
 for name in ('project/host/bmi30_config.json','project/host/bmi30_sel.json'):bundle[name]=b'{}\n'
 for n in list(bundle):
  if n.endswith('.env'):
   d=env(bundle[n]);d={k:(v.replace(OLD,NEW))for k,v in d.items()}
   for k in list(d):
    if k.endswith('_CREATED_AT'):d[k]=CREATED
    if k.endswith('_LABEL'):d[k]='Standard runtime-only Core111748 Engine185916'
    if k=='BMI30_BUNDLE_ORIGIN':d[k]='Public runtime from123518; no full-system migration or device state'
   if 'BMI30_FIRMWARE_BT_REMOTE_SHA256'in d:d['BMI30_FIRMWARE_BT_REMOTE_SHA256']=sha(bundle[bt])
   if 'BMI30_FIRMWARE_CONFIG_SHA256'in d:d['BMI30_FIRMWARE_CONFIG_SHA256']=sha(bundle['project/host/bmi30_config.json'])
   bundle[n]=eb(d)
 recovery='project/host/bmi30_usb_recovery/config.json';j=json.loads(bundle[recovery]);need(j.get('version')==OLD,'recovery_metadata_version_changed');j['version']=NEW;bundle[recovery]=(json.dumps(j,ensure_ascii=False,indent=2)+'\n').encode()
 for n,pin in PINS.items():need(sha(bundle[n])==pin,'numeric_GUI_USB_source_changed_'+n)
 need(sha(bundle['project/hotspot_info_server.py'])==sha(source[PREFIX+'project/hotspot_info_server.py']),'Portal_source_changed')
 manifest=env(bundle['manifest.env']);need(manifest['BMI30_BUNDLE_ID']==NEW and manifest['BMI30_BUNDLE_PORTABLE_SYSTEM']=='0','runtime_only_manifest')
 bundle['SHA256SUMS']=''.join(sha(b)+'  '+n+'\n'for n,b in sorted(bundle.items())if n!='SHA256SUMS').encode()
 server=ast.parse(source['Documents/utilities/bmi30_server_update.py']);tools=ast.literal_eval(next(n.value for n in server.body if isinstance(n,ast.Assign)and any(isinstance(k,ast.Name)and k.id=='ARCHIVE_TOOLS'for k in n.targets)))
 result={TARGET+n:b for n,b in bundle.items()};out_modes={TARGET+n:modes.get(PREFIX+n,0o644)for n in bundle}
 for n in tools:
  key='Documents/'+n;need(key in source,'archive_tool_missing');result[key]=source[key];out_modes[key]=modes[key]
 release=bundle[manifest['BMI30_BUNDLE_RELEASE_REL']]
 result['Documents/host/bmi30_firmware_release.env']=release
 selected=env(source['Documents/host/bmi30_split_active_version.env']);selected={k:v.replace(OLD,NEW)for k,v in selected.items()};selected['BMI30_SPLIT_LABEL']='Standard runtime-only Core111748 Engine185916';result['Documents/host/bmi30_split_active_version.env']=eb(selected)
 for n in result:
  path=PurePosixPath(n);need('bmi30_full_update'not in path.parts and path.name!='bmi30_native_service_variants.py','native_installer_or_cohort_remains')
  need(not any(p in path.parts for p in ('player_recordings','secrets','.ssh','.tmp','backups'))and path.suffix not in('.key','.pem','.wav','.npz','.log','.jsonl')and path.name not in('device_api_token','id_ed25519','USB_config.json','plot_config.json'),'private_device_file_in_delivery')
  out_modes.setdefault(n,0o644)
 return result,out_modes,{'source_archive_sha256':SOURCE_SHA,'source_bundle_files':len(old_sums)+1,'removed_native_full_update_files':105,'removed_nested_cohort_helpers':len(removed)-105,'not_copied_outer_cohort_helper':True,'final_archive_files':len(result),'code_pins_unchanged':PINS,'Portal_sha256_unchanged':sha(bundle['project/hotspot_info_server.py']),'BT_remote_before_sha256':old_bt,'BT_remote_after_sha256':sha(bundle[bt]),'bootstrap_before_sha256':old_adapter,'bootstrap_after_sha256':sha(ADAPTER),'runtime_only':True,'system_network_OS_STM_update_included':False,'factory_config_empty':True,'saved_mode_empty':True,'local_switcher_installs_entire_project_sidecar':True}
def archive_bytes(files,modes):
 data=io.BytesIO();epoch=int(dt.datetime.fromisoformat(CREATED).timestamp())
 with gzip.GzipFile(filename='',fileobj=data,mode='wb',mtime=0)as zipped,tarfile.open(fileobj=zipped,mode='w',format=tarfile.USTAR_FORMAT)as tar:
  for n,b in sorted(files.items()):
   item=tarfile.TarInfo(n);item.size=len(b);item.mode=modes[n];item.uid=item.gid=0;item.uname=item.gname='root';item.mtime=epoch;tar.addfile(item,io.BytesIO(b))
 return data.getvalue()
def validate(output,archive,marker_raw,source):
 # Execute the EXISTING standard validators, never incoming maintenance.
 sf=load_module('bmi30_server_files',source['Documents/utilities/bmi30_server_files.py'])
 load_module('bmi30_portable_assets',source['Documents/utilities/bmi30_portable_assets.py'])
 had=sys.modules.get('fcntl');fake=types.ModuleType('fcntl');fake.flock=lambda*a,**k:(_ for _ in()).throw(AssertionError('No locks/hardware in source validation'));fake.LOCK_EX=2;fake.LOCK_NB=4;fake.LOCK_UN=8;sys.modules['fcntl']=fake
 try:su=load_module('runtime_delivery_updater',source['Documents/utilities/bmi30_server_update.py'])
 finally:
  if had is None:sys.modules.pop('fcntl',None)
  else:sys.modules['fcntl']=had
 extract=output/'validation-extract';extract.mkdir();info=sf.marker(marker_raw);project=sf.extract_verified(archive,info,extract);bundle=project/'host/bmi30_split_bundles'/NEW;manifest=sf.verify_bundle(bundle,NEW);su.Updater.ensure_distributable(bundle,manifest)
 # Exact Portal startup hook locates Core.parent sidecar after standard rsync.
 installed=output/'activation-model/host/bmi30_active_runtime/project';installed.mkdir(parents=True)
 import shutil
 shutil.copytree(bundle/'project',installed,dirs_exist_ok=True)
 hook=load_module('runtime_only_bootstrap_model',(installed/'host/bmi30_full_bootstrap.py').read_bytes());need(hook.start()is None,'runtime_only_adapter_not_noop')
 switch=source['Documents/switch_bmi30_split_versions.sh'].decode();need('rsync -a --delete "$(bundle_dir "$id")/project/" "$stage/project/"'in switch,'local_switcher_project_copy_contract_changed')
 return {'existing_marker_parser':True,'existing_extract_verified':True,'existing_verify_bundle':True,'existing_ensure_distributable':True,'Portal_sidecar_activation_model':True,'fcntl_only_mocked_at_import_no_IO_calls':True,'hardware_or_network_calls':False}
def main():
 files,modes,report=render();raw=archive_bytes(files,modes);need(raw==archive_bytes(files,modes),'archive_not_deterministic');out=W/NEW;need(not out.exists(),'new_release_output_required');out.mkdir();name='bmi30_backup_20261106_151000.tar.gz';archive=out/name;archive.write_bytes(raw)
 info={'FIRMWARE_VERSION':NEW,'FIRMWARE_BUNDLE_ID':NEW,'ARCHIVE_NAME':name,'ARCHIVE_SIZE':str(len(raw)),'ARCHIVE_SHA256':sha(raw)};marker=eb(info);(out/'bmi30_latest.env').write_bytes(marker)
 source,_=read_source();report.update(ok=True,version=NEW,archive=str(archive),archive_bytes=len(raw),archive_sha256=sha(raw),marker_sha256=sha(marker),deterministic_archive_repeated_bytes_equal=True,validation=validate(out,archive,marker,source),source_hash=sha(Path(__file__).read_bytes()),published=False,installed=False)
 (out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8');print(json.dumps({k:report[k]for k in('ok','version','archive_bytes','archive_sha256','marker_sha256','published','installed')},indent=2))
if __name__=='__main__':main()
