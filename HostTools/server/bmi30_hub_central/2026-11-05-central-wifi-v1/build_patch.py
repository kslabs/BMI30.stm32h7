"""Deterministic LOCAL source patch. No server connection or execution."""
import difflib
import hashlib
import json
from pathlib import Path

W=Path(__file__).resolve().parent
BASE=W/'baseline/main.py'
if not BASE.is_file():BASE=W.parent/'m04-update-as-s07-20261105/hub-server-source/main.py'
EXPECTED='d3eb53e4012c259772ba9b62a6a937150734a188a8c339a7c647dd4dc056bd9c'
raw=BASE.read_bytes()
if hashlib.sha256(raw).hexdigest()!=EXPECTED:raise RuntimeError('Actual reviewed r36 main baseline changed')
eol=b'\r\n'if b'\r\n'in raw else b'\n'
replacements=[
 (b'from .firmware import FirmwareStorage, build_firmware_router',
  b'from .firmware import FirmwareStorage, build_firmware_router'+eol+b'from .central_store import CentralStore, build_central_router, system_scope_resolver'),
 (b'FIRMWARE_STORAGE = FirmwareStorage(SETTINGS, DB)',
  b'FIRMWARE_STORAGE = FirmwareStorage(SETTINGS, DB)'+eol+b'CENTRAL_STORE = CentralStore(SETTINGS.root)'),
 (b'    FIRMWARE_STORAGE.ensure_layout()',
  b'    FIRMWARE_STORAGE.ensure_layout()'+eol+b'    CENTRAL_STORE.initialize_optional()'),
 (b'app.include_router(build_firmware_router(FIRMWARE_STORAGE))',
  b'app.include_router(build_firmware_router(FIRMWARE_STORAGE))'+eol+
  b'app.include_router(build_central_router(CENTRAL_STORE, FIRMWARE_STORAGE.authenticate_device, system_scope_resolver(DB), require_https=True, enable_captures=False))')]
patched=raw
for old,new in replacements:
 if patched.count(old)!=1:raise RuntimeError('Non-unique source anchor')
 patched=patched.replace(old,new)
target=W/'candidate/bmi30hub/main.py';target.write_bytes(patched)
compile(patched,str(target),'exec')
diff=''.join(difflib.unified_diff(raw.decode().splitlines(True),patched.decode().splitlines(True),fromfile='a/app/bmi30hub/main.py',tofile='b/app/bmi30hub/main.py'))
module=(W/'candidate/bmi30hub/central_store.py').read_text(encoding='utf-8')
diff+=''.join(difflib.unified_diff([],module.splitlines(True),fromfile='/dev/null',tofile='b/app/bmi30hub/central_store.py'))
(W/'SOURCE.diff').write_text(diff,encoding='utf-8',newline='\n')
result={'baseline_main_sha256':EXPECTED,'candidate_main_sha256':hashlib.sha256(patched).hexdigest(),
 'central_store_sha256':hashlib.sha256((W/'candidate/bmi30hub/central_store.py').read_bytes()).hexdigest(),
 'exact_main_additions':4,'old_firmware_router_preserved':True,'captures_enabled':False,
 'new_external_server_dependencies':False,'server_deployed':False}
(W/'patch-provenance.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result))
