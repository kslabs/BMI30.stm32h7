"""Consume relative network ages on the portal's own monotonic timer."""
import ast
from pathlib import Path
from patch_rpi_identity import replace_python_function

PATCH_MARKER = '"service_clock_mode": "monotonic-v2",'


def patch(source):
    if PATCH_MARKER in source:
        raise ValueError('Clock-independent portal patch already present')
    identity='"rpi_identity_version": RPI_IDENTITY_VERSION,'
    if source.count(identity)!=1:
        raise ValueError('Hardware identity patch required')
    source=source.replace(identity,identity+'\n        '+PATCH_MARKER,1)
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='read_bmi30_group_file')
    function=ast.get_source_segment(source,node)
    function=function.replace('now = time.time()', 'now = time.monotonic()')
    function=function.replace('age = now - float(state.get("checked_at", 0))',
        '''if state.get("clock_mode") != "monotonic-v2":
            raise ValueError("Clock-independent network runtime required")
        age = now - float(state.get("checked_mono", -1))''')
    function=function.replace('"signal", "rssi_dbm", "internet")','"signal", "rssi_dbm", "internet", "age_s")')
    function=function.replace('n["age_s"] = max(0, now - float(n.get("issued", 0)))',
        '''n["age_s"] = max(0, float(n.get("age_s", freshness)) + max(0, age))
            if isinstance(n.get("relay"), dict):
                n["relay"]["age_s"] = float(n["relay"].get("age_s", freshness) or 0) + max(0, age)''')
    if 'checked_mono' not in function or 'now - float(n.get("issued"' in function:
        raise ValueError('Unexpected portal freshness implementation')
    source=replace_python_function(source,'read_bmi30_group_file',function)
    # The relay's age is already accumulated by the service; remote timestamps
    # belong only in diagnostic logs, never in the browser's freshness logic.
    old='''        if(Number.isFinite(relay.observed_at) && Number.isFinite(groupLiveSnapshot.checked_at)) {{
          relay.age_s=Math.max(0,groupLiveSnapshot.checked_at-relay.observed_at);
        }}'''
    if old not in source:raise ValueError('Unexpected live relay age implementation')
    source=source.replace(old,'',1)
    start=source.index('var _groupNetwork = {{}};')
    end=source.index('    function _groupRebuildColumns(',start)
    ui=Path(__file__).with_name('group_ui.js').read_text(encoding='utf-8')
    ui=ui.removeprefix('// The RS485 builder remains the fallback. Fast rows join by hardware identity.\n')
    source=source[:start]+ui.replace('\\','\\\\').replace('{','{{').replace('}','}}')+'\n'+source[end:]
    start=source.index('    var _groupMatrix =')
    end=source.index('    function _updateDeviceSensors(',start)
    source=source[:start]+source[start:end].replace('Date.now()','_groupNow()')+source[end:]
    start=source.index('// Push every group change over one authenticated same-origin WebSocket.')
    end=source.index('    function _pollSensors()',start)
    live=Path(__file__).with_name('live_ui.js').read_text(encoding='utf-8')
    source=source[:start]+live.replace('\\','\\\\').replace('{','{{').replace('}','}}')+'\n'+source[end:]
    source=source.replace('Date.now()-groupLiveHTTPAt','performance.now()-groupLiveHTTPAt')
    source=source.replace('groupLiveHTTPAt=Date.now()','groupLiveHTTPAt=performance.now()')
    ast.parse(source)
    return source
