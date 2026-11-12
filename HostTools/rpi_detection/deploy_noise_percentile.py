"""Guarded M02-only deployment of the temporal mean/P95 noise estimator."""
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).parent
WORK=ROOT/'.tmp/noise-percentile-20261015'
WORK.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'.tmp/new-device-20261009'))
from device_remote import remote


def once(source,old,new):
    assert source.count(old)==1,(old,source.count(old))
    return source.replace(old,new)


def script(name):
    direct=HERE/('noise_percentile_'+name+'_remote.py')
    if direct.exists():return direct.read_text(encoding='utf8')
    source=(HERE/('noise_area_'+name+'_remote.py')).read_text(encoding='utf8')
    source=source.replace('.tmp/noise-area-20261014','.tmp/noise-percentile-20261015')
    if name=='test':
        source=once(source,"for suite in ['test_noise_area', 'test_base_width', 'test_averaging']:",
                    "env['NOISE_PERCENTILE_BASE_ENGINE']=str(C/'base_engine.py')\nfor suite in ['test_noise_percentile', 'test_noise_area', 'test_base_width', 'test_averaging']:")
    if name in ('build','install'):
        source=source.replace('Adjustable noise measurement area','Temporal noise estimate: mean and P95')
        source=source.replace('Peak-relative noise boundaries, 5-95 percent; preserve signal/noise coefficients',
                              'Noise estimate max(mean, linear P95); preserve area, window, and configured S/N coefficients')
        source=source.replace('Peak-relative noise boundaries; preserve signal/noise coefficients',
                              'max(mean, P95) temporal noise estimate; retain instantaneous noise check')
        source=source.replace('validated-M02-noise-area-update','validated-M02-noise-percentile-update')
    if name=='install':
        source=once(source,"assert after['detector']['settings']['noise_area_percent'] == 50",
                    "assert all(after['detector']['settings'][key]==value for key,value in before['detector']['settings'].items()), 'detector settings changed'\n"
                    "    assert after['detector']['settings']['noise_estimator']=='max_mean_p95'")
        source=once(source,"'worker': after['detector']['worker'], 'noise_area_percent': 50, 'validation': validation,",
                    "'worker': after['detector']['worker'], 'noise_area_percent': after['detector']['settings']['noise_area_percent'],\n"
                    "              'noise_window_s': after['detector']['settings']['noise_window_s'], 'noise_estimator': 'max_mean_p95', 'validation': validation,")
    return source


def call(name,payload=None,timeout=90):
    value=remote(script(name),payload=payload,timeout=timeout)
    if name=='inspect':
        folder=WORK/'source';folder.mkdir(exist_ok=True)
        for key,raw in value.pop('sources').items():(folder/(key+'.py')).write_bytes(base64.b64decode(raw))
    if name=='build':
        archive=base64.b64decode(value.pop('archive'));assert hashlib.sha256(archive).hexdigest()==value['archive_sha256']
        folder=ROOT/'FirmwareReleases/RaspberryPi'/value['version'];folder.mkdir(exist_ok=False)
        file=folder/('BMI30-RPi-'+value['version']+'.tar.gz');file.write_bytes(archive)
        file.with_name(file.name+'.sha256').write_text(value['archive_sha256']+'  '+file.name+'\n')
        value['archive_path']=str(file)
    (WORK/(name+'.json')).write_text(json.dumps(value,indent=2),encoding='utf8')
    return value


if __name__=='__main__':
    name=sys.argv[1];payload=json.loads(Path(sys.argv[2]).read_text(encoding='utf8')) if len(sys.argv)>2 else None
    result=call(name,payload,timeout=210 if name=='build' else 120)
    result={key:value for key,value in result.items() if key not in ('inline_scripts','sources','agents','packager','switcher','config','status')}
    print(json.dumps(result,indent=2))
