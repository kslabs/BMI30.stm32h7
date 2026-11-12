import json,pathlib,subprocess,sys
p=json.load(sys.stdin);P=pathlib.Path
assert subprocess.check_output(['hostname'],text=True).strip()==p['hostname']
f=P('/run/bmi30-media-sync.json')
r=json.loads(f.read_text()) if f.exists() else {}
def run(*a):return subprocess.run(a,capture_output=True,text=True).stdout.strip()
r['service']=run('systemctl','show','bmi30-media-sync','-p','ActiveState','-p','SubState','-p','Result')
r['work']=run('ps','-C','e2fsck,rsync,tar,gzip','-o','pid,etime,stat,comm')
r['journal']=run('journalctl','-u','bmi30-media-sync','-n','8','--no-pager')
print(json.dumps(r))
