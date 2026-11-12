"""Keep Operating Mode field help next to each caption."""
import ast

OLD = "  var caption=label.matches('#panel-privacy .privacy-fields .field') ? label.querySelector(':scope > span') : null;"
NEW = "  var caption=label.matches('#panel-privacy .privacy-fields .field,#panel-operation label') ? label.querySelector(':scope > span') : null;"
OLD_TIMING = '.dc-timing-text{{display:block;min-width:0;flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}'
NEW_TIMING = OLD_TIMING.replace('flex:1 1 auto', 'flex:0 1 auto')


def patch(source):
    old = OLD.replace('{', '{{').replace('}', '}}')
    new = NEW.replace('{', '{{').replace('}', '}}')
    if source.count(new) != 1:
        if source.count(old) != 1:
            raise ValueError('Unexpected shared help initializer')
        source = source.replace(old, new, 1)
    if source.count(NEW_TIMING) != 1:
        if source.count(OLD_TIMING) != 1:
            raise ValueError('Unexpected adaptation caption style')
        source = source.replace(OLD_TIMING, NEW_TIMING, 1)
    ast.parse(source)
    return source
