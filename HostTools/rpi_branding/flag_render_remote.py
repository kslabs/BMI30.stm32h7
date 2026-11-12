"""Render the candidate with the installed portal code before activation."""
import ast,base64,json,os,runpy,sys
from html.parser import HTMLParser
from pathlib import Path
p=json.load(sys.stdin)
data=base64.b64decode(p['files']['portal']);ast.parse(data)
D=Path('/home/techaid/Documents');W=D/'.tmp/flag-20261016';W.mkdir(exist_ok=True)
candidate=W/'portal-candidate.py';candidate.write_bytes(data)
os.environ['BMI30_CONFIG_JSON']=str(D/'host/bmi30_config.json')
ns=runpy.run_path(str(candidate))
html=ns['render_portal_page']('BMI30-E29805C7C',session_username='verification',request_host='192.168.0.154').decode()
class Flags(HTMLParser):
    def __init__(self):super().__init__();self.found=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'brand-origin-flag' in attrs.get('class','').split():self.found.append((tag,attrs))
parser=Flags();parser.feed(html)
assert len(parser.found)==1
tag,attrs=parser.found[0]
assert tag=='svg' and attrs['width']=='18' and attrs['height']=='12'
assert attrs['aria-hidden']=='true' and attrs['focusable']=='false'
assert not any(k in attrs for k in ['title','data-tip','data-tooltip','aria-label','tabindex'])
assert html.count('fill="#0057B7"')==1 and html.count('fill="#FFD700"')==1
assert 'BMI30 Management Portal<svg class="brand-origin-flag"' in html
print(json.dumps({'ok':True,'flag_count':1,'tooltip':False,'html':base64.b64encode(html.encode()).decode()}))
