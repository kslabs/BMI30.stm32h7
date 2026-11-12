"""Deploy only the M02 readout help, preserving the audited detector code."""
import ast
import json
from pathlib import Path
import sys

import deploy_noise_scale as previous

ROOT, HERE = previous.ROOT, previous.HERE
WORK = ROOT/'.tmp/noise-readout-help-20261016'
WORK.mkdir(exist_ok=True)
_previous_script = previous.script


def script(name):
    direct = HERE/('noise_readout_help_'+name+'_remote.py')
    if direct.exists(): return direct.read_text(encoding='utf8')
    source = _previous_script(name).replace('.tmp/noise-scale-20261016', '.tmp/noise-readout-help-20261016')
    source = source.replace('Native 64-bit detector levels and zero-noise rejection', 'Integer readouts and two-second p/n/t/SN help')
    source = source.replace('Native int64/float64 levels, unscaled numeric readouts, reject zero reference noise',
                            'Integer scope readouts; explain p/n/t/SN after two-second hover while live values refresh')
    source = source.replace('Native int64/float64 arithmetic and readouts; reject zero reference noise',
                            'Integer p/n/t/SN readouts and two-second help; retain precise native arithmetic')
    source = source.replace('validated-M02-noise-scale-update', 'validated-M02-noise-readout-help-update')
    if name == 'install':
        source = previous.base.once(source, '                break\n        except Exception:',
            '                break\n'
            '            if attempt == 25 and after["connection"]["connected"]:\n'
            '                # Restore the already-selected operating mode after USB reconnect.\n'
            '                # A BUF=200 startup can otherwise skip its full detector setup.\n'
            '                request=urllib.request.Request("http://127.0.0.1:8765/api/command",\n'
            '                    data=json.dumps({"action":"set_mode","params":{"idx":before["mode"]["selected"]}}).encode(),\n'
            '                    headers={"Content-Type":"application/json","X-BMI30-Source":"deployment_restore_saved_mode"},method="POST")\n'
            '                reply=json.load(urllib.request.urlopen(request,timeout=5))\n'
            '                (BACKUP/"startup-mode-restore.json").write_text(json.dumps({"before_restore":after,"reply":reply},indent=2))\n'
            '                assert reply.get("ok"),reply\n'
            '        except Exception:')
        source = previous.base.once(source, "        raise RuntimeError('Updated core failed its startup health check')",
            '        (BACKUP/"startup-failure-status.json").write_text(json.dumps(api(),indent=2))\n'
            "        raise RuntimeError('Updated core failed its startup health check')")
    if name == 'verify':
        source = previous.base.once(source, "assert 'p/n/t/SN' in core_html and 'function detectorRawValue(' in core_html",
             "assert 'p/n/t/SN' in core_html and 'function detectorRawValue(' in core_html\n"
             "assert 'function detectorNoiseDescription(' in core_html\n"
             "assert 'TOOLTIP_HOVER_DELAY_MS=2000' in core_html\n"
             "assert '#detUpperNoise>*,#detLowerNoise>*{pointer-events:none}' in core_html\n"
             "assert 'В расчётах сохраняется полная точность' in core_html")
    if name == 'registry':
        node = next(node for node in ast.parse(source).body if isinstance(node,ast.Assign)
                    and any(getattr(target,'id','')=='values' for target in node.targets))
        lines = source.splitlines(keepends=True)
        source = ''.join(lines[:node.lineno-1])+"values = p['journal']\n"+''.join(lines[node.end_lineno:])
    return source


previous.base.WORK, previous.base.script = WORK, script
call = previous.call

if __name__ == '__main__':
    name = sys.argv[1]
    payload = json.loads(Path(sys.argv[2]).read_text(encoding='utf8')) if len(sys.argv)>2 else None
    result = call(name,payload,timeout=210 if name=='build' else 120)
    print(json.dumps({key:value for key,value in result.items()
        if key not in ('inline_scripts','diagnostics','sources','agents','packager','switcher','config','status')}, indent=2))
