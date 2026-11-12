"""Add the decorative Ukrainian flag to the portal's existing brand line."""
import ast

FLAG = ('<svg class="brand-origin-flag" xmlns="http://www.w3.org/2000/svg" '
        'width="18" height="12" viewBox="0 0 18 12" aria-hidden="true" focusable="false">'
        '<rect width="18" height="6" fill="#0057B7"/>'
        '<rect y="6" width="18" height="6" fill="#FFD700"/></svg>')
CSS = '''    .brand-origin-flag{{display:inline-block;width:18px;height:12px;margin-left:8px;
      vertical-align:-1px;border-radius:2px;overflow:hidden;box-shadow:0 0 0 1px #80808030}}
'''

def patch(source):
    anchor='<p class="eyebrow">BMI30 Management Portal</p>'
    assert source.count(anchor)==1
    result=source.replace(anchor,'<p class="eyebrow">BMI30 Management Portal'+FLAG+'</p>')
    # Only the logged-in portal's styles and brand line change.
    start=result.index('def render_portal_page(')
    index=result.index('  </style>',start)
    result=result[:index]+CSS+result[index:]
    old,new=ast.parse(source),ast.parse(result)
    def without_html(tree):
        for node in ast.walk(tree):
            if isinstance(node,ast.JoinedStr):
                node.values=[v for v in node.values if not isinstance(v,ast.Constant)]
        return ast.dump(tree,include_attributes=False)
    assert without_html(old)==without_html(new),'Non-HTML portal code changed'
    return result
