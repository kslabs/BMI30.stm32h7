"""Place Privacy help beside field captions and follow the channel card order."""
import ast
from pathlib import Path
import re
import sys


OLD_HELP = "  if(text) {portalAddHelp(label,text); label.removeAttribute('title');}"
NEW_HELP = "  var caption=label.matches('#panel-privacy .privacy-fields .field') ? label.querySelector(':scope > span') : null;\n" \
           "  if(text) {portalAddHelp(caption || label,text); label.removeAttribute('title');}"
FORM_ORDER = ('portal-account-config', 'portal-hotspot-config',
              'portal-wifi-config', 'portal-remote-config')


def embed(js):
    return js.replace('{', '{{').replace('}', '}}')


def patch(source):
    old, new = embed(OLD_HELP), embed(NEW_HELP)
    operation_help = embed(NEW_HELP.replace('#panel-privacy .privacy-fields .field',
        '#panel-privacy .privacy-fields .field,#panel-operation label'))
    if new not in source and operation_help not in source:
        if source.count(old) != 1:
            raise ValueError('Unexpected help initializer; inspect the active portal')
        source = source.replace(old, new)
    begin = source.index('          <div class="privacy-forms">')
    end = source.index('          </div>\n        </section>', begin)
    region = source[begin:end]
    matches = list(re.finditer(
        r'          <form class="privacy-form"[^>]*\baction="([^"]+)"[^>]*>.*?          </form>\n',
        region, re.S))
    if len(matches) != 4 or {m[1] for m in matches} != set(FORM_ORDER):
        raise ValueError('Unexpected Privacy forms')
    for left, right in zip(matches, matches[1:]):
        if region[left.end():right.start()].strip():
            raise ValueError('Unexpected content between Privacy forms')
    forms = {m[1]: m[0] for m in matches}
    reordered = region[:matches[0].start()] + ''.join(forms[action] for action in FORM_ORDER) \
                + region[matches[-1].end():]
    source = source[:begin] + reordered + source[end:]
    ast.parse(source)
    return source


if __name__ == '__main__':
    source, target = map(Path, sys.argv[1:])
    target.write_text(patch(source.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
