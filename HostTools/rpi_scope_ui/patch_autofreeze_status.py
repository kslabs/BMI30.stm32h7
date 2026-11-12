"""Remove only the redundant firing label and use available header width."""
import ast
from pathlib import Path
import re

from patch_autofreeze_annotations import once


def patch_core(source, version):
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, 'id', '') == 'SERVICE_HTML' for t in n.targets))
    page = ast.literal_eval(node.value)
    page = once(page, '  parts.push({text:kind,', '  if(!snap.fired)parts.push({text:kind,')
    page = once(page,
        '.scope-norm.decision{max-width:calc(100% - 300px);white-space:normal;',
        '.scope-head:has(.scope-norm.decision) h2,.scope-head:has(.scope-norm.decision) .legend{flex:0 0 auto}\n'
        '\t.scope-norm.decision{position:static;transform:none;flex:1 1 0;min-width:0;max-width:none;white-space:normal;')
    page = once(page, '.scope-norm.decision{max-width:54%;font-size:8px}',
                '.scope-norm.decision{max-width:none;font-size:8px}')
    lines = source.splitlines(keepends=True)
    result = ''.join(lines[:node.lineno-1])+'SERVICE_HTML = '+repr(page)+'\n'+''.join(lines[node.end_lineno:])
    result, count = re.subn(r'(DEFAULT_ENGINE_FILE\s*=\s*HOST_DIR / )[\'"][^\'\"]+[\'\"]',
        lambda m: m.group(1)+repr('BMI30.200.py.'+version), result)
    assert count == 1
    def backend(t):
        return [ast.dump(n) for n in t.body if not (isinstance(n, ast.Assign) and
                any(getattr(x, 'id', '') in ('SERVICE_HTML', 'DEFAULT_ENGINE_FILE') for x in n.targets))]
    assert backend(tree) == backend(ast.parse(result))
    return result, page


if __name__ == '__main__':
    import sys
    folder, version = Path(sys.argv[1]), sys.argv[2]
    candidate = folder/'candidate'
    candidate.mkdir(exist_ok=True)
    core, page = patch_core((folder/'core.py').read_text(encoding='utf8'), version)
    (candidate/'core.py').write_text(core, encoding='utf8', newline='\n')
    (candidate/'scope.html').write_text(page, encoding='utf8', newline='\n')
    for name in ['engine', 'portal']:
        (candidate/f'{name}.py').write_bytes((folder/f'{name}.py').read_bytes())
