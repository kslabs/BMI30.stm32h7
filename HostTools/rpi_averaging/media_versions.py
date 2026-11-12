import json,pathlib,subprocess,sys,urllib.request
p=json.load(sys.stdin);P=pathlib.Path
assert subprocess.check_output(['hostname'],text=True).strip()==p['hostname']
j=json.load(urllib.request.urlopen('http://127.0.0.1/api/status',timeout=5))
print(json.dumps({'api':{k:v for k,v in j.items() if any(t in k for t in ['version','firmware'])},
 'manifest':(P('/home/techaid/Documents/host/bmi30_firmware_release.env')).read_text(),
 'bundle_manifest':(P('/home/techaid/Documents/host/bmi30_active_runtime/project/host/bmi30_firmware_release.env')).read_text()}))
