"""Create static local test fixtures only; no device changes or publication."""
import ast,json,pathlib,sys
HERE=pathlib.Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE));import bundle_ui
work=ROOT/'.tmp/local-ui-20261028';work.mkdir(parents=True,exist_ok=True)
(work/'i18n.js').write_text(bundle_ui.javascript(),encoding='utf-8')
(work/'catalogs.json').write_text(json.dumps(bundle_ui.catalogs(),ensure_ascii=False),encoding='utf-8')
messages=set()
for path in (ROOT/'HostTools/usb_recovery').glob('usb_recovery_*.py'):
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node,ast.Constant) and isinstance(node.value,str) and '\n' not in node.value and any('\u0400'<=c<='\u04ff' for c in node.value):messages.add(node.value)
(work/'backend-messages.json').write_text(json.dumps(sorted(messages),ensure_ascii=False),encoding='utf-8')
for name,path in [('recovery',ROOT/'HostTools/usb_recovery/recovery.html'),('network',ROOT/'HostTools/bmi30_network/status.html')]:
 (work/(name+'.html')).write_text(bundle_ui.html(path.read_text(encoding='utf-8')),encoding='utf-8')
print(json.dumps({'fixtures':str(work),'languages':11,'published':False}))
