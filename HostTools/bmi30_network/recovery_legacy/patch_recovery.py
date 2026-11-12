"""Patch only the credential exchange endpoint of the shipped 0.1.13 service."""
import ast

VERSION = '0.1.13-recovery1'
RELEASE = '2026-10-27-legacy-recovery-r1'
MARKER = '# BMI30 credential recovery for BMI30-27448D72B: v1 peer clock only'


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Unsupported network source; nothing changed')
    return source.replace(old, new, 1)


def patch(source):
    if MARKER in source:
        if source.count(MARKER) != 1 or f'VERSION = "{VERSION}"' not in source:
            raise ValueError('Incomplete recovery patch')
        compile(source, 'recovery-service', 'exec')
        return source
    source = replace_once(source, 'VERSION = "0.1.13"',
                          'from legacy_recovery import LegacyRecovery\n' + MARKER + '\nVERSION = "' + VERSION + '"')
    source = replace_once(source, '        self.group = group.Group(self)\n',
                          '        self.group = group.Group(self)\n        self.legacy_recovery = LegacyRecovery(self)\n')
    source = replace_once(source, '                    raw = encode(agent.state)\n',
                          '                    raw = encode({**agent.state, "legacy_recovery": agent.legacy_recovery.snapshot()})\n')
    original = '''                with agent.lock:
                    data = agent.protocol.open(raw)
                reply = agent.exchange(data, self.client_address[0])
'''
    candidate = '''                try:
                    with agent.lock:
                        agent.legacy_recovery.check_replay(raw)
                        data = agent.protocol.open(raw)
                except Exception:
                    # Only the named target may receive credential-only replies.
                    raw = agent.legacy_recovery.respond(raw)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                reply = agent.exchange(data, self.client_address[0])
'''
    source = replace_once(source, original, candidate)
    compile(source, 'recovery-service', 'exec')
    return source


def unchanged_methods(original, candidate):
    def methods(source):
        tree = ast.parse(source)
        agent = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Agent')
        return {n.name: ast.dump(n, include_attributes=False) for n in agent.body if isinstance(n, ast.FunctionDef)}
    before, after = methods(original), methods(candidate)
    return sorted(name for name in before if before[name] == after.get(name))
