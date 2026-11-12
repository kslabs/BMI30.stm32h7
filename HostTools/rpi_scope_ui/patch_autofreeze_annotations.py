"""Restore full AutoFreeze rendering and replace only its heading badge with time."""
import ast
from pathlib import Path
import re


def once(source, old, new):
    assert source.count(old) == 1, (old[:100], source.count(old))
    return source.replace(old, new)


def patch_core(source, version):
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n,ast.Assign)
                and any(getattr(t,'id','')=='SERVICE_HTML' for t in n.targets))
    page = ast.literal_eval(node.value)
    # A missing helper aborted renderFrame before either canvas was updated.
    page = once(page,'tip:reviewReason(cm)+','tip:String(cm.reason||\'\')+')
    page = once(page,'display:none;content:"";margin-left:8px;',
                'content:attr(data-autofreeze-time);margin-left:8px;')
    page = once(page,'}].concat(detectorEventTimeParts(snap));','}];')
    page = once(page,'let parts=detectorEventTimeParts(snap),rawLevel=', 'let parts=[],rawLevel=')
    page = once(page,'if(!snap.fired)parts.push({text:kind,','parts.push({text:kind,')
    page = once(page,"let label=Math.round(minimum)+'..'+Math.round(maximum)",
                "let label='DETECT '+Math.round(minimum)+'..'+Math.round(maximum)")
    start = page.index('function paintAutoFreezeSources(){')
    end = page.index('\n',start)
    page = page[:start]+'''function paintAutoFreezeSources(){
  let active=freezeMode==='auto';
  for(let channel of ['upper','lower']){
    let card=byId(channel==='upper'?'upperCard':'lowerCard'),snap=autoFreezeDiagnostics[channel]||{},hit=active&&!!snap.fired;
    setClassToggle(card,'autofreeze-hit',hit);
    let heading=card&&card.querySelector('h2');if(!heading)continue;
    let part=hit?detectorEventTimeParts(snap)[0]:null,time=part?part.text.replace(/^⏱\\s*/,''):'';
    heading.setAttribute('data-autofreeze-time',time);setTooltip(heading,part?part.tip:'');
  }
}'''+page[end:]
    lines = source.splitlines(keepends=True)
    result = ''.join(lines[:node.lineno-1])+'SERVICE_HTML = '+repr(page)+'\n'+''.join(lines[node.end_lineno:])
    result, count = re.subn(r'(DEFAULT_ENGINE_FILE\s*=\s*HOST_DIR / )[\'"][^\'\"]+[\'\"]',
        lambda m:m.group(1)+repr('BMI30.200.py.'+version),result)
    assert count == 1
    def backend(t):
        return [ast.dump(n) for n in t.body if not (isinstance(n,ast.Assign) and
                any(getattr(x,'id','') in ('SERVICE_HTML','DEFAULT_ENGINE_FILE') for x in n.targets))]
    assert backend(tree) == backend(ast.parse(result))
    return result,page


if __name__=='__main__':
    import sys
    folder,version = Path(sys.argv[1]),sys.argv[2]
    candidate = folder/'candidate';candidate.mkdir(exist_ok=True)
    core,page = patch_core((folder/'core.py').read_text(encoding='utf8'),version)
    (candidate/'core.py').write_text(core,encoding='utf8',newline='\n')
    (candidate/'scope.html').write_text(page,encoding='utf8',newline='\n')
    for name in ['engine','portal']:(candidate/f'{name}.py').write_bytes((folder/f'{name}.py').read_bytes())
