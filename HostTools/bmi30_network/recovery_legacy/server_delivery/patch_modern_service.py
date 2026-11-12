"""Keep each node's current boot authoritative when merging peer gossip."""
import ast,copy

def patch(source,authoritative):
    tree=ast.parse(source)
    agent=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Agent')
    if any(isinstance(n,ast.FunctionDef) and n.name=='merge_peer_records' for n in agent.body):return source
    expected=next(n for n in ast.parse(authoritative).body if isinstance(n,ast.ClassDef) and n.name=='Agent')
    method=next(n for n in expected.body if isinstance(n,ast.FunctionDef) and n.name=='merge_peer_records')
    changes=0
    for method_node in agent.body:
        if not isinstance(method_node,ast.FunctionDef) or method_node.name not in ('exchange','contact'):continue
        for node in ast.walk(method_node):
            if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='merge'
                and isinstance(node.func.value,ast.Attribute) and node.func.value.attr=='records'
                and isinstance(node.func.value.value,ast.Name) and node.func.value.value.id=='self'):
                node.func=ast.Attribute(value=ast.Name(id='self',ctx=ast.Load()),attr='merge_peer_records',ctx=ast.Load());changes+=1
    if changes!=2:raise ValueError('Unknown modern gossip merge sites')
    agent.body.append(copy.deepcopy(method));ast.fix_missing_locations(tree)
    return ast.unparse(tree)+'\n'
