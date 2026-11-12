"""Build immutable portal and network releases from the audited installed code."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile

from deploy import remote, SSH, CONFIG, FILES
from inspect_group_display import WORK

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'m01_recovery'))
from build_fleet_release import SCRIPT as BUILD_PORTAL

VERSION = '2026-10-05-1430'
NETWORK_RELEASE = '2026-10-05-network-07'
BASE_VERSION = '2026-10-05-1406'
BUILDER = 'BMI30-7AA5CED27'
ROOT = HERE.parents[1]/'FirmwareReleases/RaspberryPi'
LABEL = 'Stable group settings, retained LED selection and portal parameter help'


def patch_portal(source):
    start=source.index('// The RS485 builder remains the fallback.')
    end=source.index('    function _groupRebuildColumns(devices)',start)
    js=(HERE/'group_ui.js').read_text(encoding='utf-8')
    source=source[:start]+js.replace('\\','\\\\').replace('{','{{').replace('}','}}')+'\n'+source[end:]
    marker='    _CORE_OPTIC_CACHE["t"] = now\n    _CORE_OPTIC_CACHE["data"] = dict(out)'
    guard='''    # Use the same validated observation as the group matrix for local LED
    # feedback. A legacy STAT byte with an unconfirmed layout is not a pattern.
    out["led_pattern_actual"] = None
    out["led_pattern_actual_age_s"] = None
    out["led_pattern_actual_source"] = "none"
    try:
        group = read_bmi30_group_file("group.json", 2)
        for node in group.get("nodes", []) if group.get("available") else []:
            if node.get("id") != group.get("local_id") or not node.get("available") or node.get("age_s", 4) >= 4:
                continue
            confirmed = node.get("optic") or {}
            pattern = confirmed.get("led_pattern_actual")
            age = confirmed.get("led_pattern_actual_age_s")
            if type(pattern) is int and 0 <= pattern <= 15 and isinstance(age, (int, float)) and 0 <= age <= 5:
                out["led_pattern_actual"] = pattern
                out["led_pattern_actual_age_s"] = age
                out["led_pattern_actual_source"] = "validated-group"
            selected = confirmed.get("led_pattern_selected")
            if type(selected) is int and 0 <= selected <= 15:
                out["led_pattern_selected"] = selected
                out["led_pattern_selected_source"] = confirmed.get("led_pattern_selected_source")
            out["led_output_active"] = confirmed.get("led_output_active")
            break
    except Exception:
        pass
'''
    assert source.count(marker)==1
    source=source.replace(marker,guard+marker)
    # The owner's retained selection does not expire because an unchanged
    # OPTIC_STATE event gets old. Keep hardware readback separate.
    marker='    # Use the same validated observation as the group matrix'
    init='''    commanded = out.get("led_pattern_commanded")
    out["led_pattern_selected"] = commanded if type(commanded) is int and 0 <= commanded <= 15 else None
    out["led_pattern_selected_source"] = "commanded" if out["led_pattern_selected"] is not None else "none"
'''
    source=source.replace(marker,init+marker)
    source=source.replace('group_led_actual_raw = group_optic_cfg.get("led_pattern_actual")',
                          'group_led_actual_raw = group_optic_cfg.get("led_pattern_selected")')
    source=source.replace("'<span>Current <b data-group-led-feedback=\"actual\">'",
                          "'<span>Selected <b data-group-led-feedback=\"actual\">'")
    source=source.replace('groupLedPatternLabel(settings.led_pattern_actual)',
                          'groupLedPatternLabel(settings.led_pattern_selected)')
    source=source.replace("var n = parseInt(value, 10);\n      return isFinite(n) ? ('Pattern ' + String(Math.max(0, Math.min(255, n)))) : '---';",
        "return Number.isInteger(value) && value >= 0 && value <= 15 ? ('Pattern ' + value) : '—';")
    source=source.replace("['network','link','internet','relay','led','sound','fault'].forEach(function(key)",
                          "['network','link','internet','relay','led','led-output','sound','fault'].forEach(function(key)")
    source=source.replace('var cell = _groupMakeTextCell(d); _groupSetText(cell, _groupNetworkText(d,key));',
                          'var cell = _groupMakeTextCell(d); cell._key=_groupKey(d); _groupSetText(cell, _groupNetworkText(d,key));')
    marker='<tr data-group-row="led"><th class="group-param">LED pattern</th></tr>'
    assert source.count(marker)==1
    source=source.replace(marker,marker+'\n                  <tr data-group-row="led-output"><th class="group-param">LED output</th></tr>')
    help_js=(HERE/'portal_help.js').read_text(encoding='utf-8')
    marker="    var groupLedForm = document.querySelector('.group-led-form');"
    assert source.count(marker)==1
    source=source.replace(marker,help_js.replace('\\','\\\\').replace('{','{{').replace('}','}}')+'\n'+marker)
    css='''    .tag-help[data-tip]::after{{display:none}}
    .portal-parameter-help{{position:fixed;z-index:10000;max-width:min(380px,calc(100vw - 16px));padding:12px 14px;
      box-sizing:border-box;background:var(--panel-fallback,#fff);color:var(--text);border:1px solid var(--note-border);border-radius:8px;
      font-size:13px;font-weight:400;line-height:1.5;text-align:left;white-space:normal;box-shadow:0 4px 18px #0004;pointer-events:none}}
    .portal-parameter-help[hidden]{{display:none}}
    [data-tip]{{cursor:help}}
'''
    index=source.index('    #panel-group .group-matrix .group-param')
    end=source.index('  </style>',index)
    source=source[:end]+css+source[end:]
    ast.parse(source)
    return source


def main():
    source=(WORK/'portal-after.py').read_text(encoding='utf-8')
    portal=patch_portal(source).encode()
    (WORK/'portal-fixed.py').write_bytes(portal)
    script=BUILD_PORTAL.replace('fleet-portal-20261002','group-display-20261005')
    script=script.replace("src=d/'host/bmi30_split_bundles/2026-09-30-1650/project'", "src=d/'host/bmi30_split_bundles/"+BASE_VERSION+"/project'")
    script=script.replace('Portal TLS client isolation and timeouts',LABEL)
    script=script.replace('Based on 2026-09-30-1650; portal-only fix; acquisition bytes unchanged',
                          'Based on '+BASE_VERSION+'; group display correction; acquisition bytes unchanged')
    info=json.loads(remote(BUILDER,script,{'host':BUILDER,'version':VERSION,'portal':base64.b64encode(portal).decode()},timeout=110))
    dest=ROOT/VERSION;dest.mkdir(exist_ok=False)
    name='BMI30-RPi-'+VERSION+'.tar.gz'
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',
                    BUILDER+':/home/techaid/Documents/.tmp/group-display-20261005/'+name,str(dest/name)],check=True)
    assert hashlib.sha256((dest/name).read_bytes()).hexdigest()==info['sha256']
    (dest/'release.json').write_text(json.dumps(info,indent=2)+'\n')
    (dest/'SHA256SUMS').write_text(info['sha256']+'  '+name+'\n')
    (WORK/'release.json').write_text(json.dumps(info,indent=2))
    net=ROOT/NETWORK_RELEASE;net.mkdir(exist_ok=False)
    names=[*FILES,'test_stability.py','group_ui.js','test_group_ui.js','portal_help.js']
    manifest={'version':'0.1.7','release_id':NETWORK_RELEASE,'app_bundle':VERSION,
              'scope':LABEL,'hashes':{n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names}}
    (net/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive=net/('BMI30-Network-'+NETWORK_RELEASE+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tf:
        for n in names:tf.add(HERE/n,arcname=NETWORK_RELEASE+'/'+n)
        tf.add(net/'release.json',arcname=NETWORK_RELEASE+'/release.json')
    (net/'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(json.dumps({'portal_bundle':VERSION,'portal_sha256':info['hashes']['hotspot_info_server.py'],
                      'network':manifest['version'],'release':info['release']}))


if __name__=='__main__':main()
