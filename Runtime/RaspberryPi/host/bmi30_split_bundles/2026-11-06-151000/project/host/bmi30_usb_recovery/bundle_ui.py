"""Build one offline language bootstrap for every device web page."""
import ast,json,re
from pathlib import Path
HERE=Path(__file__).resolve().parent
CODES=('uk','es','de','cs','ja','fr','pt','zh-Hans','it','ru')

def catalogs():
    result={code:json.loads((HERE/('translations.'+code+'.json')).read_text(encoding='utf-8')) for code in CODES}
    aliases=json.loads((HERE/'recovery_aliases.json').read_text(encoding='utf-8'))
    for line in (HERE/'recovery_catalogs.tsv').read_text(encoding='utf-8').splitlines():
        if not line.strip():continue
        row=line.split('|')
        if len(row)!=11 or not all(row):raise ValueError('Incomplete recovery translation: '+row[0])
        for code,value in zip(CODES,row[1:]):result[code][row[0]]=value
        aliases[row[-1]]=row[0]
    result['_aliases']=aliases
    return result

def javascript():
    return (HERE/'ui_language.js').read_text(encoding='utf-8').replace('__BMI30_CATALOGS__',json.dumps(catalogs(),ensure_ascii=False,separators=(',',':')).replace('</','<\\/'))

def bootstrap():return '<script id="bmi30-i18n">'+javascript()+'</script>'

def html(page):
    script=bootstrap()
    if re.search(r'<script\b[^>]*id=["\']bmi30-i18n["\']',page):
        return re.sub(r'<script\b[^>]*id=["\']bmi30-i18n["\'][^>]*>[\s\S]*?</script>',lambda m:script,page,count=1)
    if '</head>' in page:return page.replace('</head>',script+'</head>',1)
    return page.replace('<style>',script+'<style>',1)

def replace_assignment(source,name,value):
    tree=ast.parse(source)
    node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))
    lines=source.splitlines(keepends=True)
    return ''.join(lines[:node.lineno-1])+name+' = '+repr(value)+'\n'+''.join(lines[node.end_lineno:])

def portal(source):
    candidate=replace_assignment(source,'BMI30_I18N_BOOTSTRAP',bootstrap())
    ast.parse(candidate);return candidate

def core(source):
    before=ast.parse(source)
    node=next(n for n in before.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SERVICE_HTML' for t in n.targets))
    candidate=replace_assignment(source,'SERVICE_HTML',html(ast.literal_eval(node.value)))
    def backend(tree):return [ast.dump(n,include_attributes=False) for n in tree.body if not (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SERVICE_HTML' for t in n.targets))]
    assert backend(before)==backend(ast.parse(candidate)),'Core backend changed while localising HTML'
    return candidate
