import json,pathlib,subprocess,sys
p=json.load(sys.stdin);P=pathlib.Path
def run(*a):
 r=subprocess.run(a,capture_output=True,text=True);return {'rc':r.returncode,'out':r.stdout.strip(),'err':r.stderr.strip()}
assert run('hostname')['out']==p['hostname']
print(json.dumps({'root':run('findmnt','-n','-o','SOURCE','/'),
 'units':run('systemctl','list-units','--all','--no-pager','--no-legend','bmi30*'),
 'tunnel_units':run('systemctl','list-unit-files','--no-pager','--no-legend','*tunnel*'),
 'failed':run('systemctl','--failed','--no-pager','--no-legend'),
 'tunnel_journal':run('journalctl','-b','-u','bmi30-tunnel','-n','10','--no-pager')}))
