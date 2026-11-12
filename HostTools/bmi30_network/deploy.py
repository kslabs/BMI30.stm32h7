"""Windows deployment through existing trusted SSH aliases; no passwords in output."""
import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import time

import policy

HERE = Path(__file__).resolve().parent
WORK = HERE.parents[1] / ".tmp" / "network-20260930"
HOSTS = ["BMI30-05AB38E6A", "BMI30-D5EB8F26C", "BMI30-D5507EDFC", "BMI30-27448D72B", "BMI30-7AA5CED27"]
SSH = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH/ssh.exe"
CONFIG = Path.home() / ".ssh/bmi30_config"
FILES = ["policy.py", "network.py", "protocol.py", "service.py", "group.py", "status.html", "test_network.py", "test_group.py", "bmi30-network.service"]


def remote(host, script, payload=None, timeout=120):
    args = [str(SSH), "-F", str(CONFIG), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=5", host]
    if payload is None:
        command, data = "sudo -n python3 -", script.encode()
    else:
        command, data = "sudo -n python3 -c " + shlex.quote(script), json.dumps(payload).encode()
    result = subprocess.run(args + [command], input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{host}: remote command failed: " + result.stderr.decode(errors="replace")[-1200:])
    return result.stdout


AUDIT = '''
import json, subprocess, urllib.request
def run(*a):
 return subprocess.run(a,capture_output=True,text=True,check=True).stdout.strip()
with urllib.request.urlopen('http://127.0.0.1/api/status', timeout=5) as r: s=json.load(r)
ap=s['hotspot']['ssid']
psk=run('nmcli','--show-secrets','-g','802-11-wireless-security.psk','con','show',ap)
code=s['sync_mode']['code']
print(json.dumps({'id':s['hostname'],'role':s['sync_mode']['value'],'rs485_id':int(code[1:]),
 'ap_ssid':ap,'psk':psk,'addresses':[a['ip'] for a in s['interfaces'] if a['role']=='wifi'],
 'routes':json.loads(run('ip','-j','route')),'code':code,
 'clients':run('/usr/sbin/iw','dev','wlan0ap','station','dump').count('Station ')}))
'''

INSTALL = '''
import base64, json, os, pathlib, shutil, subprocess, sys, time
p=json.load(sys.stdin); root=pathlib.Path('/opt/bmi30-network'); root.mkdir(exist_ok=True)
backup=pathlib.Path('/var/backups/bmi30-network')/time.strftime('%Y%m%d-%H%M%S'); backup.mkdir(parents=True,mode=0o700)
for name,value in p['files'].items():
 if pathlib.Path(name).name != name: raise ValueError('Invalid file')
 target=root/name
 if target.exists(): shutil.copy2(target,backup/name)
 temp=target.with_suffix(target.suffix+'.new'); temp.write_bytes(base64.b64decode(value)); temp.chmod(0o644); temp.replace(target)
config=pathlib.Path('/etc/bmi30-network/config.json'); config.parent.mkdir(exist_ok=True,mode=0o700)
if config.exists():
 shutil.copy2(config,backup/'config.json')
 old=json.loads(config.read_text())
 if old['fleet_key']!=p['config']['fleet_key']: raise RuntimeError('Fleet key mismatch')
 for key in ('automatic','ap_address'): p['config'][key]=old[key]
fd=os.open(config,os.O_WRONLY|os.O_TRUNC|os.O_CREAT,0o600)
with os.fdopen(fd,'w') as f: json.dump(p['config'],f)
config.chmod(0o600)
nm=backup/'NetworkManager'; shutil.copytree('/etc/NetworkManager/system-connections',nm); nm.chmod(0o700)
subprocess.run(['python3','-m','unittest','-v','test_network','test_group'],cwd=root,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
shutil.copy2(root/'bmi30-network.service','/etc/systemd/system/bmi30-network.service')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','bmi30-network.service'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
subprocess.run(['systemctl','restart','bmi30-network.service'],check=True)
print(json.dumps({'id':p['config']['id'],'installed':True,'automatic':p['config']['automatic'],'backup':str(backup)}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["audit", "install", "status", "enable", "observe"])
    parser.add_argument("--host", choices=HOSTS)
    args = parser.parse_args()
    hosts = [args.host] if args.host else HOSTS
    WORK.mkdir(parents=True, exist_ok=True)
    inventory = WORK / "private-inventory.json"
    if args.action == "audit":
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda h: json.loads(remote(h, AUDIT)), hosts))
        inventory.write_text(json.dumps(results, indent=2))
        print(json.dumps([{k: v for k, v in n.items() if k not in ("psk", "routes")} for n in results], indent=2))
        return
    if args.action == "status":
        for host in hosts:
            raw = remote(host, "from pathlib import Path; print(Path('/run/bmi30-network/status.json').read_text())")
            state = json.loads(raw)
            (WORK / (host + "-status.json")).write_bytes(raw)
            print(json.dumps({"host":host,"status":state.get("status"),"coordinator":state.get("coordinator"),
                              "nodes":len(state.get("nodes",[])),"automatic":state.get("automatic"),
                              "local":{k:state.get("local",{}).get(k) for k in ('code','ssid','signal','freq','internet','path')},
                              "history":state.get("history")}))
        return
    if args.action in ("enable", "observe"):
        for host in hosts:
            script = """import sys,json,pathlib,subprocess
sys.path.insert(0,'/opt/bmi30-network')
from network import Network,atomic
p=pathlib.Path('/etc/bmi30-network/config.json'); c=json.loads(p.read_text())
"""
            if args.action == "enable":
                script += "Network(c).prepare_ap()\nc['automatic']=True\n"
            else:
                script += "c['automatic']=False\n"
            script += "atomic(p,json.dumps(c))\nsubprocess.run(['systemctl','restart','bmi30-network'],check=True)\nprint('Configured '+c['id'])\n"
            print(remote(host, script).decode().strip())
        return
    nodes = json.loads(inventory.read_text())
    keyfile = WORK / "fleet-key"
    if not keyfile.exists():
        keyfile.write_text(secrets.token_hex(32))
    occupied = [r['dst'] for n in nodes for r in n['routes'] if '/' in r.get('dst','')]
    files = {name:base64.b64encode((HERE/name).read_bytes()).decode() for name in FILES}
    for node in sorted(nodes, key=lambda n:n['id']):
        address = policy.subnet(node['id'], occupied)
        occupied.append(address)
        if node['id'] not in hosts:
            continue
        # Updating binaries preserves an existing installation's operating mode.
        config = {"id":node['id'],"fleet_key":keyfile.read_text().strip(),"ap_address":address,
                  "portal_config":"/home/techaid/Documents/host/bmi30_config.json", "automatic":False,
                  "peers":[{k:v for k,v in n.items() if k not in ('routes','clients','code')} for n in nodes]}
        result = remote(node['id'],INSTALL,{"config":config,"files":files})
        print(result.decode().strip())


if __name__ == '__main__':
    main()
