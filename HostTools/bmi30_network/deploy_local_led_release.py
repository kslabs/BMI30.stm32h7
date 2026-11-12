"""Update six audited roots; restart the active core for the LED policy only."""
import argparse,ast,base64,concurrent.futures,hashlib,json,subprocess
from pathlib import Path
from audit_stability import HOSTS,INVENTORY
from prepare_led_page import WORK,BASE_VERSION
from prepare_local_led import ENGINE
from build_local_led_release import VERSION,NETWORK_RELEASE,ROOT,LABEL,sources
from deploy import remote,SSH,CONFIG
import deploy_display_release as previous
PREFIX='home/techaid/Documents/host/bmi30_active_runtime/project/'

def audit(board,phase):
    data=json.loads(remote(HOSTS[board],INVENTORY,{'board':board,'host':HOSTS[board]},timeout=100))
    assert len(data['roots'])==(2 if board=='S10' else 1)
    (WORK/(board+'-inventory-'+phase+'.json')).write_text(json.dumps(data,indent=2))
    print(json.dumps({'board':board,'roots':len(data['roots']),'services':data['services']}),flush=True)
    return data
def installer():
    previous.BASE_VERSION=BASE_VERSION;previous.LABEL=LABEL
    s=previous.installer().replace('bmi30-group-display','bmi30-local-led')
    def replace(old,new):
        nonlocal s
        assert s.count(old)==1,(old,s.count(old));s=s.replace(old,new)
    replace(" for name in components:assert sha(active/name)==p['hashes'][name],'Acquisition differs: '+name", """ for name in components:
  assert sha(active/name)==p['expected']['home/techaid/Documents/host/bmi30_active_runtime/project/'+name],name
  if name!=p['engine']:assert sha(active/name)==p['hashes'][name],'Unexpected acquisition change: '+name""")
    replace(" changes={f:portal for f in portal_paths}",""" changes={f:portal for f in portal_paths}
 engine=(project/p['engine']).read_bytes();ast.parse(engine);changes[active/p['engine']]=engine""")
    replace(" before_settings={str(f):sha(f) for f in settings if f.exists()}",""" before_settings={str(f):sha(f) for f in settings if f.exists()}
 settings_snapshot={f:(f.read_bytes(),f.stat()) for f in settings if f.exists()}""")
    replace(" saved={}",""" saved={}
 for f,(data,st) in settings_snapshot.items():shutil.copy2(f,backup/('settings-'+f.name))""")
    replace("['python3','-m','unittest','-v','test_network','test_group','test_stability'],cwd=network",
            "['python3','-m','unittest','-v','test_network','test_group','test_stability','test_realtime','test_local_led'],env={**os.environ,'BMI30_TEST_ENGINE':str(active/p['engine'])},cwd=network")
    replace("   subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)\n   for attempt",
            "   subprocess.run(['systemctl','restart','bmi30-core'],check=True)\n   subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)\n   for attempt")
    replace("     assert core['ok']", """     assert core.get('detector',{}).get('worker',{}).get('alive')
     assert pathlib.Path(core['service']['engine_source']).resolve()==(active/p['engine']).resolve()
     assert core['ok']""")
    replace("  assert before_pid==run('systemctl','show','bmi30-core','-p','MainPID','--value')", """  after_pid=run('systemctl','show','bmi30-core','-p','MainPID','--value')
  assert (before_pid==after_pid) if offline else (before_pid!=after_pid and after_pid!='0')""")
    replace("  assert before_settings==", """  # Core startup updates selection timestamp; actual settings must be identical.
  for f,(data,st) in settings_snapshot.items():
   if sha(f)==before_settings[str(f)]:continue
   assert f.name=='bmi30_sel.json','Unexpected setting change: '+str(f)
   old=json.loads(data);new=json.loads(f.read_bytes())
   assert {k:v for k,v in old.items() if k!='ts'}=={k:v for k,v in new.items() if k!='ts'},'Selected mode changed'
   atomic(f,data,st)
  assert before_settings==""")
    replace("  if not offline:subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)", """  if not offline:
   subprocess.run(['systemctl','restart','bmi30-core'],check=True)
   subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)""")
    s=s.replace('core_pid=before_pid','core_pid_before=before_pid,core_pid=after_pid')
    s=s.replace('group display correction; acquisition bytes unchanged','local LED detection guard; detector filters and USB unchanged')
    ast.parse(s);(WORK/'installer-local-led-reviewed.py').write_text(s)
    return s
def release():
    info=json.loads((ROOT/VERSION/'release.json').read_text());manifest=json.loads((ROOT/NETWORK_RELEASE/'release.json').read_text())
    archive=ROOT/VERSION/('BMI30-RPi-'+VERSION+'.tar.gz');assert hashlib.sha256(archive.read_bytes()).hexdigest()==info['sha256']
    files=sources()
    for name,digest in manifest['hashes'].items():assert hashlib.sha256(files[name].read_bytes()).hexdigest()==digest,name
    return info,manifest,archive,files
def install(board):
    inv=json.loads((WORK/(board+'-inventory-local-before.json')).read_text());info,manifest,archive,files=release()
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',str(archive),HOSTS[board]+':/tmp/'+archive.name],check=True)
    results=[]
    for root in sorted(inv['roots'],key=lambda r:r['active']):
        assert root['hostname']==HOSTS[board] and root['network_release']['version']=='0.1.12'
        payload={'host':HOSTS[board],'version':VERSION,'live_root':inv['root'],'device':None if root['active'] else root['device'],
            'uuid':root.get('uuid'),'archive_sha256':info['sha256'],'hashes':info['hashes'],'engine':ENGINE,
            'expected_portal':[root['hashes']['usr/local/bin/bmi30-hotspot-info-server.py']],
            'expected':root['hashes'],'network_manifest':manifest,
            'network_files':{name:base64.b64encode(f.read_bytes()).decode() for name,f in files.items()}}
        result=json.loads(remote(HOSTS[board],installer(),payload,timeout=160));results.append(result)
        (WORK/(board+'-local-install.json')).write_text(json.dumps(results,indent=2));print(json.dumps(result),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['audit','review','install']);p.add_argument('--phase',default='local-before');p.add_argument('--board',choices=HOSTS);a=p.parse_args()
    if a.action=='review':installer();print('Installer AST and guarded replacement checks passed')
    elif a.action=='audit':
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(lambda b:audit(b,a.phase),HOSTS))
    else:
        for board in ([a.board] if a.board else ['S10','S12','S13','S11','M01']):install(board)
