"""Embed offline language catalogs in a dated paired websplit release."""
import ast,json
from pathlib import Path
import re,sys
HERE=Path(__file__).parent
WORK=HERE.parents[1]/'.tmp/language-20261016'
def once(s,old,new):
    assert s.count(old)==1,(old,s.count(old));return s.replace(old,new)
def build(version):
    catalogs={code:json.loads((HERE/f'translations.{code}.json').read_text(encoding='utf8')) for code in ['uk','es','de','cs','ja','fr','pt','zh-Hans','it','ru']}
    keys=json.loads((HERE/'source_strings.json').read_text(encoding='utf8'))
    for code,values in catalogs.items():assert all(k in values and values[k] for k in keys),code
    javascript=(HERE/'ui_language.js').read_text(encoding='utf8').replace('__BMI30_CATALOGS__',json.dumps(catalogs,ensure_ascii=False,separators=(',',':')).replace('</','<\\/'))
    bootstrap='<script id="bmi30-i18n">'+javascript+'</script>'
    baseline=WORK/'baseline'
    core=(baseline/'core.py').read_text(encoding='utf8')
    tree=ast.parse(core)
    node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(getattr(t,'id','')=='SERVICE_HTML' for t in n.targets))
    page=node.value.value
    start=page.index('function detectorNoiseDescription(');end=page.index('\nfunction detectorNoiseHtml(',start)
    page=page[:start]+'''function detectorNoiseDescription(channel,seconds){return[(channel==='lower'?'Lower':'Upper')+' channel: p / n / t / SN',
'p (peak) — signal peak in the current frame.',
'n (noise) — noise estimate: max(mean, P95) over '+normNumber(seconds)+' s. P95 is the 95th percentile of measurement history.',
't (trigger) — calculated detection threshold.',
'SN — current p / n ratio.',
'p, n, t and SN are rounded to integers for display. Calculations retain full mean and P95 precision.',
'p, n and t use native product units. Only the waveform is scaled to 0–65535.',
'Zero or invalid reference noise cannot confirm a tag.'].join(String.fromCharCode(10))}'''+page[end:]
    page=once(page,'function setElText(el,text){if(el){text=String(text);if(el.textContent!==text)el.textContent=text}}',
         'function setElText(el,text){if(window.BMI30I18n){window.BMI30I18n.text(el,text)}else if(el){text=String(text);if(el.textContent!==text)el.textContent=text}}')
    page=once(page,"if(el.getAttribute('data-tooltip')!==text)el.setAttribute('data-tooltip',text)",
        "if(window.BMI30I18n){window.BMI30I18n.attribute(el,'data-tooltip',text)}else if(el.getAttribute('data-tooltip')!==text)el.setAttribute('data-tooltip',text)")
    page=page.replace('ctx.fillText(', 'scopeFillText(ctx,').replace('ctx.measureText(', 'scopeMeasureText(ctx,')
    wrappers='''function scopeUiText(text){return window.BMI30I18n?window.BMI30I18n.t(text):String(text)}
function scopeFillText(ctx,text,...args){return ctx.fillText(scopeUiText(text),...args)}
function scopeMeasureText(ctx,text){return ctx.measureText(scopeUiText(text))}
window.addEventListener('bmi30-language-change',function(){if(lastStatus)renderDetector(lastStatus.detector);redrawScope()});
'''
    page=once(page,'function drawGrid(',wrappers+'function drawGrid(')
    page=once(page,'</head>',bootstrap+'\n</head>')
    lines=core.splitlines(keepends=True)
    # A Python literal retains exactly the decoded HTML/JS bytes, including regex escapes.
    replacement='SERVICE_HTML = '+repr(page)+'\n'
    core=''.join(lines[:node.lineno-1])+replacement+''.join(lines[node.end_lineno:])
    current=re.search(r'DEFAULT_ENGINE_FILE\s*=\s*HOST_DIR / [\'"]([^\'"]+)',core).group(1)
    core=core.replace(current,'BMI30.200.py.'+version)
    portal=(baseline/'portal.py').read_text(encoding='utf8')
    portal=once(portal,'Signed in as {signed_in_as or "authorized user"} · {access_label}',
                'Signed in as <span translate="no">{signed_in_as or "authorized user"}</span> · {access_label}')
    anchor='def render_html_page('
    helper='BMI30_I18N_BOOTSTRAP = '+repr(bootstrap)+'\n\ndef localize_ui_html(body):\n    return body.replace("</head>", BMI30_I18N_BOOTSTRAP + "</head>", 1)\n\n\n'
    portal=once(portal,anchor,helper+anchor)
    assert portal.count('return body.encode("utf-8")')==2
    portal=portal.replace('return body.encode("utf-8")','return localize_ui_html(body).encode("utf-8")')
    out=WORK/'candidate';out.mkdir(exist_ok=True)
    for name,value in [('core',core),('portal',portal)]:
        ast.parse(value);(out/f'{name}.py').write_text(value,encoding='utf8')
    (out/'engine.py').write_bytes((baseline/'engine.py').read_bytes())
    old_core=ast.parse((baseline/'core.py').read_text(encoding='utf8'))
    new_core=ast.parse(core)
    def backend(tree):
        return [ast.dump(n,include_attributes=False) for n in tree.body if not (isinstance(n,ast.Assign) and any(getattr(t,'id','') in ['SERVICE_HTML','DEFAULT_ENGINE_FILE'] for t in n.targets))]
    assert backend(old_core)==backend(new_core),'Core backend changed'
    (out/'candidate.json').write_text(json.dumps({'version':version}),encoding='utf8')
    (WORK/'scope-localized.html').write_text(page,encoding='utf8')
    (WORK/'i18n-bundled.js').write_text(javascript,encoding='utf8')
    print(json.dumps({'version':version,'catalogs':{k:len(v) for k,v in catalogs.items()},'script_bytes':len(javascript.encode())}))
if __name__=='__main__':build(sys.argv[1])
