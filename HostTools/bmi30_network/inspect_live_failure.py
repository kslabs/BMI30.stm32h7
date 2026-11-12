from deploy import remote
print(remote('BMI30-7AA5CED27',"""
import json,pathlib,subprocess,urllib.request
with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8791/live',headers={'X-BMI30-Local':'1'}),timeout=3) as r:
 print(json.dumps({'stream_status':r.status,'first_bytes':len(r.readline())}))
print(subprocess.check_output(['journalctl','-u','bmi30-hotspot-info','-u','bmi30-network','--since','-5 min','--no-pager'],text=True)[-9000:])
""",timeout=20).decode())
