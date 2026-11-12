"""Run candidate tests and benchmark without changing the installed runtime."""
import base64
import concurrent.futures
import json
from pathlib import Path
from read_runtime import ROOT, WORK, s07, m02

REMOTE = '''
import base64,json,os,pathlib,subprocess,sys
p=json.load(sys.stdin);d=pathlib.Path('/home/techaid/Documents/.tmp/rpi-averaging-20261013/check')
d.mkdir(parents=True,exist_ok=True)
for name,raw in p['files'].items():
 assert pathlib.Path(name).name==name
 (d/name).write_bytes(base64.b64decode(raw))
env=dict(os.environ,BMI30_TEST_ENGINE=str(d/'engine.py'),BMI30_TEST_PORTAL=str(d/'portal.py'),BMI30_TEST_TEMP=str(d/'tests'),PYTHONWARNINGS='ignore::SyntaxWarning')
test=subprocess.run(['python3','-m','unittest','-v','test_averaging'],cwd=d,env=env,capture_output=True,text=True,timeout=20)
assert test.returncode==0,test.stdout+test.stderr
bench=subprocess.run(['python3','benchmark.py'],cwd=d,env=env,capture_output=True,text=True,timeout=30)
assert bench.returncode==0,bench.stderr
print(json.dumps({'tests':test.stderr,'benchmark':json.loads(bench.stdout)}))
'''


def measure(board):
    files = {name:base64.b64encode((Path(__file__).parent/name).read_bytes()).decode()
             for name in ('bmi30_averaging.py','test_averaging.py','benchmark.py')}
    files.update({name+'.py':base64.b64encode((WORK/'candidate'/(name+'.py')).read_bytes()).decode()
                  for name in ('engine','portal')})
    result=(s07 if board=='S07' else m02)(REMOTE,payload={'files':files},timeout=60)
    (WORK/board/'measure.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({'board':board,**result}))


if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(measure,('S07','M02')))
