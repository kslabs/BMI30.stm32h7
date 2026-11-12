"""Prepare and deploy one canonical 1-12 release to the two approved devices."""
import ast,base64,concurrent.futures,datetime,hashlib,json,os,subprocess,sys
from pathlib import Path
from read_runtime import ROOT,s07,m02
W=ROOT/'.tmp/rpi-averaging12-20261013';W.mkdir(exist_ok=True)
HERE=Path(__file__).parent

INSPECT=r'''
import base64,hashlib,json,pathlib,shlex,subprocess,urllib.request
P=pathlib.Path;d=P('/home/techaid/Documents');a=d/'host/bmi30_active_runtime/project'
def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()
def api():return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=5))
raw=(d/'host/bmi30_split_active_version.env').read_text()
env={k:shlex.split(v)[0] if shlex.split(v) else '' for line in raw.splitlines() if line and not line.startswith('#') and '=' in line for k,v in [line.split('=',1)]}
assert env['BMI30_SPLIT_VERSION']=='2026-10-13-1534'
paths={'core':str((d/env['BMI30_CORE_PATH']).relative_to(a)),
 'engine':str(P(env['BMI30_ENGINE_SOURCE']).relative_to(a)),'portal':'hotspot_info_server.py','helper':'host/bmi30_averaging.py'}
s=api();assert s['ok'] and not s['stale'] and s['connection']['connected']
assert not s['player_recorder']['recording'] and not s['player_recorder']['playback_active']
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2'
print(json.dumps({'hostname':run('hostname'),'root':'/dev/sda2','serial':P('/proc/device-tree/serial-number').read_text(),
 'usb_serial':run('lsblk','-dn','-o','SERIAL','/dev/sda'),'emmc_cid':P('/sys/block/mmcblk0/device/cid').read_text(),
 'emmc_uuid':run('blkid','-s','UUID','-o','value','/dev/mmcblk0p2'),
 'env':raw,'paths':paths,'mode':s['mode'],'config':json.loads((d/'host/bmi30_config.json').read_text()),
 'sources':{k:base64.b64encode((a/v).read_bytes()).decode() for k,v in paths.items()},
 'hashes':{k:hashlib.sha256((a/v).read_bytes()).hexdigest() for k,v in paths.items()},
 'agents':(d/'AGENTS.md').read_text(),
 'registry_head':(d/'docs/BMI30_version_registry_google_sheet.csv').read_text().splitlines()[:3],
 'registry_tool':(d/'utilities/sync_bmi30_version_registry.py').read_text(),
 'packager':(d/'utilities/create_bmi30_split_bundle.sh').read_text()}))
'''

def call(board,script,label,payload=None,timeout=90):
    r=(s07 if board=='S07' else m02)(script,payload=payload,timeout=timeout)
    (W/(board+'-'+label+'.json')).write_text(json.dumps(r,indent=2),encoding='utf8')
    return r

def inspect(board):
    r=call(board,INSPECT,'before')
    source=W/board/'source';source.mkdir(parents=True,exist_ok=True)
    for name,data in r['sources'].items():(source/(name+'.py')).write_bytes(base64.b64decode(data))
    print(json.dumps({'board':board,**{k:r[k] for k in ['root','mode','hashes','registry_head']}}),flush=True)

def prepare():
    source=W/'S07/source';target=W/'candidate';target.mkdir(exist_ok=True)
    from patch_runtime import once
    for name in ['core','engine','portal']:
        s=(source/(name+'.py')).read_text(encoding='utf8')
        if name=='engine':s=once(s,"rpi_avg_n = max(1, min(8, int(scalar('rpi_avg_n', 1))))","rpi_avg_n = max(1, min(12, int(scalar('rpi_avg_n', 1))))")
        if name=='portal':
            for old,new in [('not 1 <= depth <= 8','not 1 <= depth <= 12'),
                            ('Raspberry averaging must be an integer from 1 to 8','Raspberry averaging must be an integer from 1 to 12'),
                            ('for value in range(1, 9))','for value in range(1, 13))'),
                            ('"rpi_avg_n", load_default_rpi_avg_n(), 1, 8)','"rpi_avg_n", load_default_rpi_avg_n(), 1, 12)'),
                            ('Raspberry: 1-8 received packets','Raspberry: 1-12 received packets')]:s=once(s,old,new)
        ast.parse(s);(target/(name+'.py')).write_text(s,encoding='utf8',newline='\n')
    env=dict(os.environ,BMI30_TEST_ENGINE=str(target/'engine.py'),BMI30_TEST_PORTAL=str(target/'portal.py'),PYTHONWARNINGS='ignore::SyntaxWarning')
    subprocess.run([sys.executable,'-m','unittest','-v','test_averaging'],cwd=HERE,env=env,check=True)

def measure(board):
    from measure_boards import REMOTE
    files={name:base64.b64encode((HERE/name).read_bytes()).decode() for name in ['bmi30_averaging.py','test_averaging.py','benchmark.py']}
    files.update({n+'.py':base64.b64encode((W/'candidate'/(n+'.py')).read_bytes()).decode() for n in ['engine','portal']})
    script=REMOTE.replace('rpi-averaging-20261013/check','rpi-averaging12-20261013/check')
    r=call(board,script,'tests',{'files':files},timeout=65)
    print(json.dumps({'board':board,'tests_passed':'OK' in r['tests'],'depth12':[v for v in r['benchmark'] if v['depth']==12]}),flush=True)

def build():
    info=json.loads((W/'S07-before.json').read_text())
    version=datetime.datetime.now().strftime('%Y-%m-%d-%H%M')
    files={name:base64.b64encode((W/'candidate'/(name+'.py')).read_bytes()).decode() for name in ['core','engine','portal']}
    files['helper']=base64.b64encode((HERE/'bmi30_averaging.py').read_bytes()).decode()
    r=call('S07',(HERE/'expand12_build_remote.py').read_text(),'build',{'before':info,'version':version,'files':files},timeout=210)
    archive=base64.b64decode(r.pop('archive'));assert hashlib.sha256(archive).hexdigest()==r['archive_sha256']
    release=ROOT/'FirmwareReleases/RaspberryPi'/version;release.mkdir(exist_ok=False)
    filename='BMI30-RPi-'+version+'.tar.gz';(release/filename).write_bytes(archive)
    (release/(filename+'.sha256')).write_text(r['archive_sha256']+'  '+filename+'\n')
    r['archive_path']=str(release/filename);(W/'release.json').write_text(json.dumps(r,indent=2))
    print(json.dumps(r))

def install(board):
    release=json.loads((W/'release.json').read_text())
    payload={'before':json.loads((W/(board+'-before.json')).read_text()),'release':release,
             'archive':base64.b64encode(Path(release['archive_path']).read_bytes()).decode()}
    r=call(board,(HERE/'expand12_install_remote.py').read_text(),'installed',payload,timeout=130)
    print(json.dumps({'board':board,**r}),flush=True)

def verify(board):
    r=call(board,(HERE/'expand12_verify_remote.py').read_text(),'live',timeout=60)
    print(json.dumps({'board':board,**r}),flush=True)

def audit(board):
    from media_final_audit import REMOTE
    release=json.loads((W/'release.json').read_text())
    payload={'inventory':json.loads((W/(board+'-before.json')).read_text()),'version':release['version'],
             'firmware_version':release['firmware_version'],'bundle_checksums_sha256':release['bundle_checksums_sha256'],
             'manifest_sha256':release['manifest_sha256'],'helper_sha256':release['hashes']['helper']}
    r=call(board,REMOTE,'all-media',payload,timeout=35)
    print(json.dumps({'board':board,'usb':r['usb']['firmware_version'],'emmc':r['emmc']['firmware_version'],
                      'code_and_version_match':r['code_and_version_match']}),flush=True)

if __name__=='__main__':
    action=sys.argv[1]
    if action=='inspect':
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(inspect,['S07','M02']))
    elif action=='prepare':prepare()
    elif action=='measure':
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(measure,['S07','M02']))
    elif action=='build':build()
    elif action=='install':
        for board in sys.argv[2:]:install(board)
    elif action=='verify':
        for board in sys.argv[2:]:verify(board)
    elif action=='audit':
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(audit,['S07','M02']))
