"""Render the candidate with installed read-only providers and extract scripts."""
import base64
import json
from deploy import remote
from inspect_group_display import WORK
from build_display_release import patch_portal
SCRIPT = '''
import ast,base64,json,runpy,re,sys
p=json.load(sys.stdin)
namespace=runpy.run_path('/usr/local/bin/bmi30-hotspot-info-server.py',run_name='preview_validation')
source=base64.b64decode(p['source']).decode()
tree=ast.parse(source)
for name in ('_read_core_optic_settings','render_portal_page'):
 node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
 exec(compile(ast.Module(body=[node],type_ignores=[]),'candidate','exec'),namespace)
rendered=namespace['render_portal_page']('BMI30-7AA5CED27',session_username='admin').decode()
scripts=re.findall(r'<script(?:\\s[^>]*)?>(.*?)</script>',rendered,re.S)
assert 'data-group-row="led-output"' in rendered
print(json.dumps({'scripts':scripts,'bytes':len(rendered),
 'optic':{k:v for k,v in namespace['_read_core_optic_settings']().items() if k.startswith('led_pattern_')}}))
'''
if __name__=='__main__':
    source=patch_portal((WORK/'portal-after.py').read_text(encoding='utf-8')).encode()
    (WORK/'portal-preview.py').write_bytes(source)
    data=json.loads(remote('BMI30-7AA5CED27',SCRIPT,{'source':base64.b64encode(source).decode()},timeout=35))
    (WORK/'candidate-render-scripts.json').write_text(json.dumps(data,indent=2))
    print(json.dumps({'scripts':len(data['scripts']),'bytes':data['bytes'],'optic':data['optic']}))
