"""Run candidate tests on M02 without changing its active runtime."""
import ast
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

p = json.load(sys.stdin)
assert subprocess.check_output(['hostname'], text=True).strip() == 'BMI30-E29805C7C'
W = Path('/home/techaid/Documents/.tmp/noise-area-20261014')
C = W / 'check'
C.mkdir(parents=True, exist_ok=True)
for name, raw in p['files'].items():
    assert Path(name).name == name
    data = base64.b64decode(raw)
    ast.parse(data)
    (C / name).write_bytes(data)
temp = W / 'tests'
temp.mkdir(exist_ok=True)
env = dict(os.environ, PYTHONWARNINGS='ignore::SyntaxWarning',
           NOISE_AREA_TEST_ROOT=str(W), NOISE_AREA_TEST_DIR=str(C),
           BASE_WIDTH_TEST_ENGINE=str(C / 'engine.py'), BASE_WIDTH_TEST_CORE=str(C / 'core.py'),
           BMI30_TEST_ENGINE=str(C / 'engine.py'), BMI30_TEST_PORTAL=str(C / 'portal.py'),
           BMI30_TEST_TEMP=str(temp))
results = {}
for suite in ['test_noise_area', 'test_base_width', 'test_averaging']:
    proc = subprocess.run([sys.executable, '-m', 'unittest', '-v', suite],
                          cwd=C, env=env, capture_output=True, text=True, timeout=65)
    results[suite] = {'rc': proc.returncode, 'output': proc.stdout + proc.stderr}
assert all(v['rc'] == 0 for v in results.values()), results
print(json.dumps({'all_passed': True, 'tests': results}))
