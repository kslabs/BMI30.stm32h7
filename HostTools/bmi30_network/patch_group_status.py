"""Use direct local Internet telemetry without requiring detector peer data."""
import ast
from pathlib import Path

READ='''def read_bmi30_group_file(name: str, freshness: float) -> dict:
    try:
        path = pathlib.Path("/run/bmi30-network") / name
        if path.stat().st_size > 1048576:
            raise ValueError("Status file exceeds limit")
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("clock_mode") != "monotonic-v2":
            raise ValueError("Clock-independent network runtime required")
        age = time.monotonic() - float(state.get("checked_mono", -1))
        fields = ("id", "code", "role", "rs485_id", "stm32_uid96", "issued", "parent", "path",
                  "uplink", "iface", "ssid", "freq", "signal", "rssi_dbm", "internet", "age_s")
        local = state.get("local") or {}
        local_id = state.get("local_id") or local.get("id")
        nodes = state.get("nodes", [])[:128]
        if name == "status.json":
            # The direct local probe is authoritative even if its reflected
            # network record is absent, expired, or belongs to an older boot.
            if local.get("id"):
                nodes = [n for n in nodes if n.get("id") != local["id"]]
                nodes.append({**local, "age_s": 0})
            nodes = [{k: n.get(k) for k in fields if k in n} for n in nodes]
        for n in nodes:
            n["age_s"] = max(0, float(n.get("age_s", freshness)) + max(0, age))
            if isinstance(n.get("relay"), dict):
                n["relay"]["age_s"] = float(n["relay"].get("age_s", freshness) or 0) + max(0, age)
        local_node = next((n for n in nodes if n.get("id") == local_id), None) if name == "status.json" else None
        return {"available": 0 <= age < freshness, "age_s": round(age, 2),
                "coordinator": state.get("coordinator"), "local_id": local_id,
                "local": local_node, "nodes": nodes}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"available": False, "nodes": [], "local": None}
'''

def patch(source):
    tree=ast.parse(source)
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='read_bmi30_group_file')
    lines=source.splitlines(keepends=True)
    source=''.join(lines[:node.lineno-1])+READ.rstrip()+'\n'+''.join(lines[node.end_lineno:])
    start=source.index('var _groupNetwork = {{}};')
    end=source.index('    function _groupRebuildColumns(',start)
    ui=Path(__file__).with_name('group_ui.js').read_text(encoding='utf-8')
    ui=ui.removeprefix('// The RS485 builder remains the fallback. Fast rows join by hardware identity.\n')
    source=source[:start]+ui.replace('\\','\\\\').replace('{','{{').replace('}','}}')+'\n'+source[end:]
    ast.parse(source);return source
