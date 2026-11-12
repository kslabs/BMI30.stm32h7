"""Prepare a complete portal-only bundle and install it on the approved M02."""
import base64,hashlib,json,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).parent
WORK=ROOT/'.tmp/flag-20261016'
WORK.mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT/'.tmp/new-device-20261009'))
from device_remote import remote

def script(name):
    if name=='inspect':return (ROOT/'HostTools/rpi_detection/noise_area_inspect_remote.py').read_text(encoding='utf8')
    if name=='registry':
        sys.path.insert(0,str(ROOT/'HostTools/rpi_detection'))
        import deploy_noise_readout_help
        return deploy_noise_readout_help.script('registry').replace('.tmp/noise-readout-help-20261016','.tmp/flag-20261016')
    if name=='build':
        s=(ROOT/'HostTools/rpi_detection/noise_area_build_remote.py').read_text(encoding='utf8')
        s=s.replace('.tmp/noise-area-20261014','.tmp/flag-20261016')
        s=s.replace("paths = dict(i['paths'], core='host/BMI30.001.py.' + v, engine='host/BMI30.200.py.' + v)","paths = dict(i['paths'])")
        s=s.replace('Adjustable noise measurement area','Decorative Ukrainian flag in portal header')
        s=s.replace('Peak-relative noise boundaries, 5-95 percent; preserve signal/noise coefficients','Portal brand flag only, without a tooltip; acquisition modules unchanged')
        return s
    return (HERE/('flag_'+name+'_remote.py')).read_text(encoding='utf8')

def call(name,payload=None):
    value=remote(script(name),payload=payload,timeout=180 if name=='build' else 100)
    if name=='inspect':
        source=WORK/'source';source.mkdir(exist_ok=True)
        for key,data in value.pop('sources').items():(source/f'{key}.py').write_bytes(base64.b64decode(data))
    if name=='build':
        archive=base64.b64decode(value.pop('archive'))
        assert hashlib.sha256(archive).hexdigest()==value['archive_sha256']
        folder=ROOT/'FirmwareReleases/RaspberryPi'/value['version'];folder.mkdir(exist_ok=False)
        file=folder/('BMI30-RPi-'+value['version']+'.tar.gz');file.write_bytes(archive)
        file.with_name(file.name+'.sha256').write_text(value['archive_sha256']+'  '+file.name+'\n')
        value['archive_path']=str(file)
    (WORK/f'{name}.json').write_text(json.dumps(value,indent=2),encoding='utf8')
    return value

if __name__=='__main__':
    payload=json.loads(Path(sys.argv[2]).read_text(encoding='utf8')) if len(sys.argv)>2 else None
    value=call(sys.argv[1],payload)
    print(json.dumps({k:v for k,v in value.items() if k not in ['agents','status','config','packager','switcher','registry_head','env']},indent=2))
