"""Let the group legend use the panel width and wrap complete status labels."""
import ast
from pathlib import Path
import sys


OLD = '    #panel-group .group-legend{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:10px 0 2px;font-size:12px;color:var(--text)}}'
NEW = '    #panel-group .group-legend{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;max-width:none;margin:10px 0 2px;font-size:12px;color:var(--text)}}\n' \
      '    #panel-group .group-legend > span{{display:inline-flex;align-items:center;flex:0 0 auto;white-space:nowrap}}'


def patch(source):
    if NEW in source:
        ast.parse(source)
        return source
    if source.count(OLD) != 1:
        raise ValueError('Unexpected group legend style; inspect the active portal first')
    source = source.replace(OLD, NEW)
    ast.parse(source)
    return source


if __name__ == '__main__':
    source, target = map(Path, sys.argv[1:])
    target.write_text(patch(source.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
