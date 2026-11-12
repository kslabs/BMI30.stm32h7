"""Give pulse-base geometry a palette distinct from green peak-position gates."""
from patch_base_width import replace


def patch_core(source):
    source = replace(source,
                     "let color=info.enabled?(info.ok?'#70ffa4':'#ff7785'):'#a8aeba';",
                     "let color=info.enabled?(info.ok?'#b697ff':'#ffa657'):'#a8aeba';")
    source = replace(source,
                     '.scope-readout-part.fail{color:#ff6b6b}',
                     '.scope-readout-part.fail{color:#ff6b6b}\n'
                     '\t.scope-readout-part.base-width-pass{color:#b697ff}\n'
                     '\t.scope-readout-part.base-width-fail{color:#ffa657}')
    source = replace(source,
                     "className:bw.ok?'pass':'fail'",
                     "className:bw.ok?'base-width-pass':'base-width-fail'")
    return source


if __name__ == '__main__':
    import argparse
    import ast
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    text = patch_core(args.source.read_text(encoding='utf-8'))
    ast.parse(text)
    args.target.write_text(text, encoding='utf-8', newline='\n')
