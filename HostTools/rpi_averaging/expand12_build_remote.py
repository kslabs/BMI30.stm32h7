import ast,base64,hashlib,json,os,pathlib,shutil,subprocess,sys,tarfile
p=json.load(sys.stdin);i=p['before'];P=pathlib.Path;d=P('/home/techaid/Documents');a=d/'host/bmi30_active_runtime/project'
w=d/'.tmp/rpi-averaging12-20261013';w.mkdir(exist_ok=True);v=p['version']
def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
assert run('hostname')==i['hostname'] and run('findmnt','-n','-o','SOURCE','/')==i['root']
assert all(sha(a/path)==i['hashes'][n] for n,path in i['paths'].items())
stage=w/('stage-'+v);assert not stage.exists()
shutil.copytree(a,stage,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','player_recordings','.lgd-*'))
for n,raw in p['files'].items():
 raw=base64.b64decode(raw);ast.parse(raw);(stage/i['paths'][n]).write_bytes(raw)
(stage/'host/bmi30_split_active_version.env').write_text('\n'.join([
 'BMI30_CORE_PATH='+i['paths']['core'],'BMI30_ENGINE_SOURCE='+i['paths']['engine'],
 'BMI30_GUI_PATH=host/BMI30.GUI.001.py.2026-09-30-1650','BMI30_PORTAL_PATH=hotspot_info_server.py','']))
bundle=d/'host/bmi30_split_bundles'/v;assert not bundle.exists()
r=subprocess.run(['bash',str(d/'utilities/create_bmi30_split_bundle.sh'),'--id',v,
 '--label','Raspberry averaging 1-12','--origin','Extend Raspberry rolling averaging to 12 packets; preserve centered readout',
 '--source-project',str(stage),'--portal-source','hotspot_info_server.py','--without-player','--for-server'],capture_output=True,text=True,timeout=140)
(w/'build.log').write_text(r.stdout+'\n'+r.stderr);assert r.returncode==0,r.stdout[-1500:]+r.stderr[-1500:]
for f in (stage/'host').glob('bmi30_*.py'):shutil.copy2(f,bundle/'project/host'/f.name)
for rel in ['system/usr/local/bin/bmi30-hotspot-info-server.py','system/home/techaid/Documents/hotspot_info_server.py']:
 f=bundle/rel
 if f.exists():f.write_bytes((stage/'hotspot_info_server.py').read_bytes())
files=sorted(f for f in bundle.rglob('*') if f.is_file() and f.name!='SHA256SUMS')
(bundle/'SHA256SUMS').write_text(''.join(sha(f)+'  '+f.relative_to(bundle).as_posix()+'\n' for f in files))
run('bash',str(d/'switch_bmi30_split_versions.sh'),'--validate',v)
archive=w/('BMI30-RPi-'+v+'.tar.gz')
with tarfile.open(archive,'w:gz') as t:t.add(bundle,arcname=v)
manifest=(bundle/'project/host/bmi30_firmware_release.env').read_text()
fv=next(x.split('=',1)[1] for x in manifest.splitlines() if x.startswith('BMI30_FIRMWARE_VERSION='))
print(json.dumps({'version':v,'firmware_version':fv,'archive_sha256':sha(archive),
 'archive':base64.b64encode(archive.read_bytes()).decode(),'paths':i['paths'],
 'hashes':{n:sha(bundle/'project'/path) for n,path in i['paths'].items()},'manifest_sha256':hashlib.sha256(manifest.encode()).hexdigest(),
 'bundle_checksums_sha256':sha(bundle/'SHA256SUMS')}))
