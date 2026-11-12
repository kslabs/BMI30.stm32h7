import json
import sys
from read_runtime import WORK,s07,m02

REMOTE='''
import json,pathlib,urllib.request
s=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=5))
c=json.loads(pathlib.Path('/home/techaid/Documents/host/bmi30_config.json').read_text())
print(json.dumps({'mode':s['mode'],'averaging':s['averaging'],'saved':c.get('rpi_avg_n'), 'saved_operation':c.get('operation'),
 'worker':s['detector']['worker'],'sync':s['sync']}))
'''
for board in sys.argv[1:] or ('S07','M02'):
    result=(s07 if board=='S07' else m02)(REMOTE)
    (WORK/board/'saved-check.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({'board':board,**result}))
