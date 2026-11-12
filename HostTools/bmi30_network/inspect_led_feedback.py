"""Read the existing acquisition owner's LED status and its export implementation."""
from deploy import remote
SCRIPT = '''
import json,pathlib,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8765/api/status') as r:c=json.load(r)
print(json.dumps(c.get('optic',{})))
p=pathlib.Path('/home/techaid/Documents/host/bmi30_active_runtime/project/host')
for f in p.glob('*.py*'):
 if f.is_file():
  t=f.read_text(errors='replace')
  if 'led_pattern_commanded' in t:
   print(f.name)
   a=t.find('led_pattern_commanded');print(t[max(0,a-650):a+850])
'''
if __name__ == '__main__':print(remote('BMI30-27448D72B', SCRIPT).decode())
