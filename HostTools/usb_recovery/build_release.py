"""Lab release: existing detector/network with the isolated USB recovery page."""
import ast,datetime,hashlib,io,json,pathlib,sys,tarfile
HERE=pathlib.Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'HostTools/bmi30_network/recovery_legacy/server_delivery'))
import build_server as base
from integrate_bundle import add_to_bundle,validate_included

def build():
    now=datetime.datetime.now(datetime.timezone.utc)
    version=now.strftime('%Y-%m-%d-%H%M%S');created=now.isoformat(timespec='seconds')
    modes={};files=base.read_bundle(ROOT/'FirmwareReleases/RaspberryPi/2026-10-23-1514/BMI30-RPi-2026-10-23-1514.tar.gz',modes)
    original=dict(files);manifest=base.contract.parse_env(files['manifest.env'])
    for name in list(files):
        if name.startswith(('metadata/','system/')):del files[name]
    manifest['BMI30_BUNDLE_PORTABLE_SYSTEM']='0'
    label='Pin portal to selected release; emergency USB, manual update check and multilingual group status'
    manifest.update(BMI30_BUNDLE_ID=version,BMI30_BUNDLE_CREATED_AT=created,BMI30_BUNDLE_LABEL=label,
       BMI30_BUNDLE_ORIGIN='Working base 2026-10-23-1514; portable emergency USB 0.2.0')
    add_to_bundle(files,manifest,version);validate_included(files,manifest)
    release_path=manifest['BMI30_BUNDLE_RELEASE_REL'];release=base.contract.parse_env(files[release_path])
    release.update(BMI30_FIRMWARE_VERSION=version,BMI30_FIRMWARE_BUNDLE_ID=version,
        BMI30_FIRMWARE_CREATED_AT=created,BMI30_FIRMWARE_LABEL=label)
    for part in ('CORE','ENGINE','GUI','PORTAL'):
        name=manifest['BMI30_BUNDLE_'+part+'_REL'];release['BMI30_FIRMWARE_'+part+'_SHA256']=base.sha(files[name])
        if part in ('ENGINE','GUI'):assert files[name]==original[name]
        if part=='CORE':
            def backend(data):return [ast.dump(n,include_attributes=False) for n in ast.parse(data).body if not (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SERVICE_HTML' for t in n.targets))]
            assert backend(files[name])==backend(original[name]),'Core backend changed'
    files[release_path]=base.contract.env_bytes(release);files['manifest.env']=base.contract.env_bytes(manifest)
    files['SHA256SUMS']=''.join(base.sha(data)+'  '+name+'\n' for name,data in sorted(files.items()) if name!='SHA256SUMS').encode()
    output=ROOT/'FirmwareReleases/RaspberryPi'/version;output.mkdir(exist_ok=False)
    review=output/'review/bundle'
    for name,data in files.items():
        path=review/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        if name.endswith('.py'):ast.parse(data,filename=name)
    base.contract.verify_bundle(review,version)
    name='bmi30_backup_'+now.strftime('%Y%m%d_%H%M%S')+'.tar.gz'
    outer={'host/bmi30_firmware_release.env':files[release_path],
           'host/bmi30_split_active_version.env':base.contract.env_bytes({'BMI30_SPLIT_BUNDLE_ID':version})}
    outer.update({'host/bmi30_split_bundles/'+version+'/'+path:data for path,data in files.items()})
    archive=output/name
    with tarfile.open(archive,'w:gz') as tar:
        prefix='host/bmi30_split_bundles/'+version+'/'
        for path,data in sorted(outer.items()):
            item=tarfile.TarInfo('Documents/'+path);item.size=len(data);item.mtime=0
            item.mode=modes.get(path[len(prefix):],0o644) if path.startswith(prefix) else 0o644
            tar.addfile(item,io.BytesIO(data))
    marker=base.contract.marker(base.contract.env_bytes({'FIRMWARE_VERSION':version,'FIRMWARE_BUNDLE_ID':version,
        'ARCHIVE_NAME':name,'ARCHIVE_SIZE':str(archive.stat().st_size),'ARCHIVE_SHA256':base.sha(archive.read_bytes())}))
    (output/'bmi30_latest.env').write_bytes(base.contract.env_bytes(marker))
    report={'kind':'portable-emergency-usb','version':version,'bundle_id':version,'marker':marker,
       'archive':str(archive),'base_bundle':'2026-10-23-1514','deployment_scope':['M02'],
       'capability':'all compatible BMI30 devices','installed_boards':[],'published':False,
       'usb_write_tested':False,'usb_boot_tested':False,'stm32_updated':False,
       'checks':'See isolated Linux validation report; actual USB write and boot remain pending'}
    (output/'release.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    (ROOT/'.tmp/usb-recovery-20261028/build.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report

if __name__=='__main__':print(json.dumps(build()))
