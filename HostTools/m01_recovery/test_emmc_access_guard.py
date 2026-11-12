"""Regression tests for network access preservation during maintenance."""
import ast,unittest
from pathlib import Path

def load_guard():
    tree=ast.parse(Path(__file__).with_name('clone_emmc_system_remote.py').read_text())
    selected=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='guard_stop_dependencies'
              or isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='PROTECTED_UNITS' for target in node.targets)]
    scope={};exec(compile(ast.Module(body=selected,type_ignores=[]),'<access guard>','exec'),scope)
    return scope['guard_stop_dependencies']

class AccessGuardTests(unittest.TestCase):
    def test_original_channel_stop_is_rejected_before_any_action(self):
        guard=load_guard()
        with self.assertRaisesRegex(AssertionError,'channel-policy'):
            guard(['bmi30-channel-policy.service'],lambda unit:{'NetworkManager.service'})

    def test_transitive_stop_propagation_is_rejected(self):
        graph={'maintenance.service':{'pre-network.service'},
               'pre-network.service':{'NetworkManager.service'}}
        with self.assertRaisesRegex(AssertionError,'NetworkManager'):
            load_guard()(['maintenance.service'],lambda unit:graph.get(unit,set()))

    def test_desktop_dependency_is_also_protected(self):
        with self.assertRaisesRegex(AssertionError,'xrdp'):
            load_guard()(['helper.service'],lambda unit:{'xrdp-sesman.service'})

    def test_safe_leaf_and_dependency_cycles_terminate(self):
        graph={'a.service':{'b.service'},'b.service':{'a.service'}}
        self.assertEqual(load_guard()(['a.service'],lambda unit:graph[unit]),['a.service','b.service'])

if __name__=='__main__':unittest.main()
