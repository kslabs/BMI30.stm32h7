"""Extract only static UI source, without device data, for offline localization."""
import ast
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re

HERE=Path(__file__).parent
WORK=HERE.parents[1]/'.tmp/language-20261016'
ATTRS={'title','aria-label','placeholder','data-tip','data-tooltip','data-channel-label','alt'}
def norm(s):return ' '.join(html.unescape(s).split())
keys=set()
class Text(HTMLParser):
    def __init__(self):super().__init__();self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ['script','style','svg']:self.skip+=1
        if not self.skip:
            keys.update(norm(v) for k,v in attrs if k in ATTRS and v)
    def handle_endtag(self,tag):
        if tag in ['script','style','svg']:self.skip=max(0,self.skip-1)
    def handle_data(self,s):
        if not self.skip:keys.add(norm(s))

for name in ['portal','core']:
    source=(WORK/'source'/f'{name}.py').read_text(encoding='utf8')
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,ast.Constant) and isinstance(node.value,str):s=node.value
        elif isinstance(node,ast.JoinedStr):s=''.join(v.value if isinstance(v,ast.Constant) and isinstance(v.value,str) else ' ' for v in node.values)
        else:continue
        if '<' in s and '>' in s:Text().feed(s)
        # JavaScript quoted UI phrases in templates, excluding identifiers, URLs and code.
        if '<script' in s or 'portalParameterHelp' in s:
            for m in re.finditer(r"(['\"])((?:\\.|(?!\1).)*?)\1",s):
                value=m.group(2)
                if '\\' in value or '<' in value or '>' in value:continue
                if re.fullmatch(r'[A-Za-z][A-Za-z0-9 /:;,.!?()\[\]%+−–—…=\-]+',value) and ' ' in value:
                    keys.add(norm(value))
keys.update(line.split('\t',1)[0] for line in (HERE/'ru.tsv').read_text(encoding='utf8').splitlines() if '\t' in line)
keys.update([
    'Choose language','Hover for 2 seconds or click to choose the interface language.',
    'Upper channel: p / n / t / SN','Lower channel: p / n / t / SN',
    'p (peak) — signal peak in the current frame.',
    'n (noise) — noise estimate: max(mean, P95) over {seconds} s. P95 is the 95th percentile of measurement history.',
    't (trigger) — calculated detection threshold.',
    'SN — current p / n ratio.',
    'p, n, t and SN are rounded to integers for display. Calculations retain full mean and P95 precision.',
    'p, n and t use native product units. Only the waveform is scaled to 0–65535.',
    'Zero or invalid reference noise cannot confirm a tag.',
])
keys={s for s in keys if s and re.search('[A-Za-z]',s) and len(s)<1600 and not re.search(r'https?://|function\b|document\.|\bvar\b|\bconst\b|=>|[{}]|\.py\b|#[a-fA-F0-9]{6}',s)}|{s for s in keys if '{seconds}' in s}
# Exclude source code fragments, protocol identifiers and vendor names.
keys={s for s in keys if not re.search(r'[;=]|\breturn\b|\bif\b.*[()]|^\.?[\w/-]+\.(?:js|css|json|wav|pdf)$',s) or len(s)>120 or '{seconds}' in s}
keys.update([
    '0 uses the same sample. A larger tolerance uses the strongest response from the other channel within this range, including secondary peaks.',
    '100% subtracts the full reference level; 0% disables subtraction.',
    'A reference peak below this noise ratio is not subtracted. Set to 0 to skip this check.',
    'After the pairing wait expires, a missing or invalid reference resets detection confirmations.',
    'Consecutive detector/phase calculation cycles required before sound starts; short detections remain silent.',
    'Current system password','Scroll to top',
    'Use the candidate phase shift for the other channel as an additional check. Both checks must pass; existing rejections are preserved.',
])
keys=sorted(keys)
# Include complete rendered phrases only when they are present verbatim in static
# source constants. Device names, scan results, credentials and live telemetry fail
# this check and never enter translation input.
static=[]
for name in ['portal','core']:
    for node in ast.walk(ast.parse((WORK/'source'/f'{name}.py').read_text(encoding='utf8'))):
        if isinstance(node,ast.Constant) and isinstance(node.value,str):static.append(norm(node.value))
rendered=json.loads((WORK/'strings.json').read_text(encoding='utf8'))
for value in {s for page in rendered.values() for group in page.values() for s in group}:
    if len(value)>4 and any(value in literal for literal in static):keys.append(value)
keys=sorted(set(keys))
(HERE/'source_strings.json').write_text(json.dumps(keys,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(len(keys),sum(map(len,keys)))
