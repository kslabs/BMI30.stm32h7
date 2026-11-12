"""Install reviewed event modules on every root, with per-root rollback."""
import argparse,ast,base64,hashlib,json,subprocess
from audit_stability import HOSTS
from deploy import remote,SSH,CONFIG
from inspect_realtime import WORK
from build_realtime_release import VERSION,NETWORK_RELEASE,ROOT,LABEL,HERE,BASE_VERSION,STREAM,OWNER,ENGINE
from deploy_display_release import installer as previous_installer

CHANGED=[STREAM,OWNER,ENGINE]
PREFIX='home/techaid/Documents/host/bmi30_active_runtime/project/'

def installer():
    s=previous_installer()
    s=s.replace('bmi30-group-display','bmi30-group-realtime')
    s=s.replace('2026-10-05-1406',BASE_VERSION)
    s=s.replace('Stable group settings, retained LED selection and portal parameter help',LABEL)
    s=s.replace('group-display-fleet-verified','group-realtime-fleet-verified')
    s=s.replace('group display correction; acquisition bytes unchanged','direct detector push; detection rules and STM32 unchanged')
    s=s.replace(" for name in components:assert sha(active/name)==p['hashes'][name],'Acquisition differs: '+name", """ for name in components:
  if name not in p['changed_modules']:assert sha(active/name)==p['hashes'][name],'Unexpected acquisition change: '+name
  elif (active/name).exists():assert sha(active/name)==p['expected']['home/techaid/Documents/host/bmi30_active_runtime/project/'+name],name""")
    s=s.replace(" changes={f:portal for f in portal_paths}",""" changes={f:portal for f in portal_paths}
 for name in p['changed_modules']:
  raw=(project/name).read_bytes();ast.parse(raw);changes[active/name]=raw""")
    s=s.replace(" before_settings={str(f):sha(f) for f in settings if f.exists()}",""" before_settings={str(f):sha(f) for f in settings if f.exists()}
 settings_snapshot={f:(f.read_bytes(),f.stat()) for f in settings if f.exists()}""")
    s=s.replace(" saved={}",""" saved={}
 for f,(data,st) in settings_snapshot.items():shutil.copy2(f,backup/('settings-'+f.name))""")
    s=s.replace("  assert before_settings==", """  # Startup refreshes bmi30_sel.json's ts, while sel stays the same.
  # Restore its exact saved bytes only after proving every actual setting matches.
  for f,(data,st) in settings_snapshot.items():
   if sha(f)==before_settings[str(f)]:continue
   assert f.name=='bmi30_sel.json','Unexpected configuration rewrite: '+str(f)
   old=json.loads(data);new=json.loads(f.read_bytes())
   assert {k:v for k,v in old.items() if k!='ts'}=={k:v for k,v in new.items() if k!='ts'},'Selected mode changed'
   atomic(f,data,st)
  assert before_settings==""")
    s=s.replace("'test_network','test_group','test_stability'", "'test_network','test_group','test_stability','test_realtime'")
    s=s.replace("cwd=network,capture_output=True,text=True,timeout=40)","cwd=network,env=dict(os.environ,BMI30_TEST_ENGINE=str(active/'host/BMI30.200.py.2026-09-30-1650')),capture_output=True,text=True,timeout=40)")
    s=s.replace("   subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)\n   for attempt", """   subprocess.run(['systemctl','restart','bmi30-core'],check=True)
   subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)
   for attempt""")
    s=s.replace("     assert core['ok']", """     cache=json.loads(pathlib.Path('/tmp/bmi30_device_state.json').read_text())
     assert len(cache.get('owner_event_boot',''))==32 and 'host_detection' in cache
     assert core.get('detector',{}).get('worker',{}).get('alive')
     assert core['ok']""")
    s=s.replace("  assert before_pid==run('systemctl','show','bmi30-core','-p','MainPID','--value')", """  after_pid=run('systemctl','show','bmi30-core','-p','MainPID','--value')
  assert (before_pid==after_pid) if offline else (before_pid!=after_pid and after_pid!='0')""")
    s=s.replace("  if not offline:subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)","""  if not offline:
   subprocess.run(['systemctl','restart','bmi30-core'],check=True)
   subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)""")
    s=s.replace('core_pid=before_pid','core_pid_before=before_pid,core_pid=after_pid')
    s=s.replace("assert 'BMI30_SPLIT_VERSION="+BASE_VERSION+"' in env_before.decode()",
      "assert any('BMI30_SPLIT_VERSION='+version in env_before.decode() for version in ("+repr(BASE_VERSION)+","+repr(VERSION)+"))")
    s=s.replace(" changes.update({active/'host/bmi30_firmware_release.env'",
      " module_restart=any(not f.exists() or f.read_bytes()!=data for f,data in changes.items() if f in [active/n for n in p['changed_modules']])\n changes.update({active/'host/bmi30_firmware_release.env'")
    s=s.replace("   subprocess.run(['systemctl','restart','bmi30-core'],check=True)",
      "   if module_restart:subprocess.run(['systemctl','restart','bmi30-core'],check=True)")
    s=s.replace('if offline else (before_pid!=after_pid', 'if offline or not module_restart else (before_pid!=after_pid')
    ast.parse(s);(WORK/'installer-reviewed.py').write_text(s)
    assert s.count("['systemctl','restart','bmi30-core']")==2
    return s

def install(board):
    inventory=WORK/(board+'-inventory-final-before.json')
    if not inventory.exists():inventory=WORK/(board+'-inventory-before.json')
    inv=json.loads(inventory.read_text())
    info=json.loads((WORK/'release.json').read_text())
    manifest=json.loads((ROOT/NETWORK_RELEASE/'release.json').read_text())
    archive=ROOT/VERSION/('BMI30-RPi-'+VERSION+'.tar.gz')
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==info['sha256']
    for name,digest in manifest['hashes'].items():assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==digest,name
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',str(archive),HOSTS[board]+':/tmp/'+archive.name],check=True)
    results=[]
    for root in sorted(inv['roots'],key=lambda r:r['active']):
        assert root['hostname']==HOSTS[board] and 'hashes' in root
        if (root.get('network_release') or {}).get('version')==manifest['version']:
            assert all(root['hashes'].get(PREFIX+n)==info['hashes'][n] for n in CHANGED)
            print(json.dumps({'board':board,'root':root['device'],'already_verified':True}),flush=True)
            continue
        payload={'host':HOSTS[board],'version':VERSION,'live_root':inv['root'],'device':None if root['active'] else root['device'],
          'uuid':root.get('uuid'),'archive_sha256':info['sha256'],'hashes':info['hashes'],
          'expected_portal':[root['hashes']['usr/local/bin/bmi30-hotspot-info-server.py']],
          'expected':root['hashes'],'changed_modules':CHANGED,'network_manifest':manifest,
          'network_files':{n:base64.b64encode((HERE/n).read_bytes()).decode() for n in manifest['hashes']}}
        result=json.loads(remote(HOSTS[board],installer(),payload,timeout=150));results.append(result)
        (WORK/(board+'-install.json')).write_text(json.dumps(results,indent=2))
        print(json.dumps(result),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--board',choices=HOSTS);args=parser.parse_args()
    for board in ([args.board] if args.board else ('S13','S12','S10','S11','M01')):install(board)
