"""Local source-pinned author validation. No server/network/device operation."""
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import sys
sys.dont_write_bytecode=True
import time
import unittest

W=Path(__file__).resolve().parent;sys.path.insert(0,str(W))
import test_central_store
start=time.monotonic();output=io.StringIO()
result=unittest.TextTestRunner(stream=output,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_central_store))
files=[p for p in(W/'candidate').rglob('*')if p.is_file()and '__pycache__'not in p.parts]
files+=[W/name for name in('test_central_store.py','build_patch.py','validate_candidate.py','API_CONTRACT.json','README_RU.md','DEPLOYMENT_RU.md','SOURCE.diff','patch-provenance.json')]
files+=[p for folder in('baseline','deployment-evidence')for p in(W/folder).rglob('*')if p.is_file()]
hashes={p.relative_to(W).as_posix():{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}for p in sorted(files)}
for p in files:
 if p.suffix=='.py':compile(p.read_bytes(),str(p),'exec')
report={'schema':1,'ok':result.wasSuccessful(),'author_tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
 'skipped':len(result.skipped),'independent_review_complete':False,'tested_at':datetime.now(timezone.utc).isoformat(),
 'duration_s':time.monotonic()-start,'environment':{'python':sys.version,'platform':platform.platform(),
  'packages':{name:importlib.metadata.version(name)for name in('fastapi','starlette','httpx','cryptography','pydantic')}},
 'real_handlers':True,'real_AESGCM':True,'fake_component':'approved-device authentication only; existing system query runs against SQLite fixture',
 'server_calls':0,'server_writes':False,'deployed':False,'captures_enabled_in_main':False,'source_hashes':hashes,'output':output.getvalue()}
p=W/'author-validation.json';p.write_text(json.dumps(report,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
print(json.dumps({'ok':report['ok'],'tests':report['author_tests'],'failures':report['failures'],'errors':report['errors'],'skipped':report['skipped'],'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))
raise SystemExit(0 if result.wasSuccessful()else 1)
