"""Guarded portal/network update; keep acquisition and each root's settings."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from audit_stability import HOSTS
from build_display_release import VERSION, NETWORK_RELEASE, ROOT, LABEL, HERE, BASE_VERSION
from deploy import remote, SSH, CONFIG
from inspect_group_display import WORK

sys.path.insert(0,str(HERE.parent/'m01_recovery'))
from deploy_fleet_release import SCRIPT as PORTAL_INSTALL


def installer():
    s=PORTAL_INSTALL.replace('bmi30-portal-release-20261002','bmi30-group-display-20261005')
    s=s.replace('bmi30-portal-fleet','bmi30-group-display')
    s=s.replace("assert 'BMI30_SPLIT_VERSION=2026-09-30-1650'", "assert 'BMI30_SPLIT_VERSION="+BASE_VERSION+"'")
    s=s.replace('Portal TLS client isolation and timeouts',LABEL)
    s=s.replace('portal-only-fleet-verified','group-display-fleet-verified')
    s=s.replace('Based on 2026-09-30-1650; portal-only fix; acquisition bytes unchanged',
                'Based on '+BASE_VERSION+'; group display correction; acquisition bytes unchanged')
    # The first network probe can overlap the portal restart; allow the next
    # scheduled probe to finish before deciding whether to roll back.
    s=s.replace('for attempt in range(20):','for attempt in range(80):')
    s=s.replace('if attempt==19:raise','if attempt==79:raise')
    s=s.replace('time.sleep(.3)','time.sleep(.5)')
    s=s.replace("before_settings={str(f):sha(f) for f in settings if f.exists()}",
      """settings.append(root/'etc/bmi30-network/config.json')
 assert json.loads(settings[-1].read_text())['id']==host
 for name,digest in p['expected'].items():assert sha(root/name)==digest,'Unreviewed file: '+name
 before_settings={str(f):sha(f) for f in settings if f.exists()}""")
    s=s.replace("backup=root/'var/backups/bmi30-group-display'", """network=root/'opt/bmi30-network'
 for name,data in p['network_files'].items():
  assert pathlib.Path(name).name==name
  raw=__import__('base64').b64decode(data)
  if name.endswith('.py'):ast.parse(raw)
  changes[network/name]=raw
 changes[network/'release.json']=json.dumps(p['network_manifest'],indent=2).encode()+b'\\n'
 backup=root/'var/backups/bmi30-group-display'""")
    s=s.replace("saved[f]=(f.read_bytes(),f.stat());shutil.copy2(f,backup/str(i))",
                "saved[f]=(f.read_bytes(),f.stat()) if f.exists() else None\n  if f.exists():shutil.copy2(f,backup/str(i))")
    s=s.replace("st=st or f.stat();tmp=", "st=st or (f.stat() if f.exists() else f.parent.stat());tmp=")
    s=s.replace("os.chmod(tmp,stat.S_IMODE(st.st_mode));", "os.chmod(tmp,stat.S_IMODE(st.st_mode) if stat.S_ISREG(st.st_mode) else 0o644);")
    s=s.replace("  if not offline:\n   subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)",
     """  test=subprocess.run(['python3','-m','unittest','-v','test_network','test_group','test_stability'],cwd=network,capture_output=True,text=True,timeout=40)
  assert test.returncode==0,test.stderr[-4000:]
  for name,digest in p['network_manifest']['hashes'].items():assert sha(network/name)==digest,name
  if not offline:
   subprocess.run(['systemctl','restart','bmi30-network'],check=True)
   subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)""")
    s=s.replace("assert core['ok'] and not core['stale']", """net=json.loads((root/'run/bmi30-network/status.json').read_text())
     group=json.loads((root/'run/bmi30-network/group.json').read_text())
     assert net['version']==p['network_manifest']['version']
     assert any(n['id']==host and n['available'] for n in group['nodes'])
     assert core['ok'] and not core['stale']""")
    s=s.replace("  for f,(data,st) in saved.items():atomic(f,data,st)",
      """  for f,old in saved.items():
   if old:atomic(f,old[0],old[1])
   elif f.exists():f.unlink()
  if not offline:subprocess.run(['systemctl','restart','bmi30-network'],check=True)""")
    s=s.replace("portal_sha256=p['hashes']['hotspot_info_server.py'],backup=", 
                "network_version=p['network_manifest']['version'],tests=test.stderr[-180:],portal_sha256=p['hashes']['hotspot_info_server.py'],backup=")
    return s


def install(board):
    inv=json.loads((WORK/(board+'-inventory-led-before.json')).read_text())
    info=json.loads((WORK/'release.json').read_text())
    manifest=json.loads((ROOT/NETWORK_RELEASE/'release.json').read_text())
    archive=ROOT/VERSION/('BMI30-RPi-'+VERSION+'.tar.gz')
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==info['sha256']
    for name,digest in manifest['hashes'].items():assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==digest,name
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',str(archive),
                    HOSTS[board]+':/tmp/'+archive.name],check=True)
    results=[]
    for root in sorted(inv['roots'],key=lambda r:r['active']):
        assert root['hostname']==HOSTS[board] and 'hashes' in root
        expected=root['hashes']
        portal=expected['usr/local/bin/bmi30-hotspot-info-server.py']
        payload={'host':HOSTS[board],'version':VERSION,'live_root':inv['root'],
          'device':None if root['active'] else root['device'],'uuid':root.get('uuid'),
          'archive_sha256':info['sha256'],'hashes':info['hashes'],'expected_portal':[portal],
          'expected':expected,'network_manifest':manifest,
          'network_files':{n:base64.b64encode((HERE/n).read_bytes()).decode() for n in manifest['hashes']}}
        result=json.loads(remote(HOSTS[board],installer(),payload,timeout=130))
        results.append(result)
        (WORK/(board+'-install-led.json')).write_text(json.dumps(results,indent=2))
        print(json.dumps(result),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--board',choices=HOSTS);args=parser.parse_args()
    for board in ([args.board] if args.board else ('S12','S13','S10','S11','M01')):install(board)
