"""Recover the published group UI and patch only portal version reconciliation."""
import argparse,ast,hashlib,json,tarfile
from pathlib import Path
from build_relay_display_release import ROOT,HERE
WORK=HERE.parents[1]/'.tmp/led-pattern-20261005'
BASE_VERSION='2026-10-05-1705'
def recover_group():
    folder=ROOT/'2026-10-05-network-12'
    manifest=json.loads((folder/'release.json').read_text())
    # Only undo the unpublished pattern-cache experiment from this turn.
    assert 'var _groupPatternSelections = {};' in (HERE/'group_ui.js').read_text(encoding='utf-8')
    with tarfile.open(folder/'BMI30-Network-2026-10-05-network-12.tar.gz') as tf:
        for name in ('group_ui.js','test_group_ui.js'):
            raw=tf.extractfile('2026-10-05-network-12/'+name).read()
            assert hashlib.sha256(raw).hexdigest()==manifest['hashes'][name]
            (HERE/name).write_bytes(raw)
def embed(js):return js.replace('\\','\\\\').replace('{','{{').replace('}','}}')
def candidate():
    with tarfile.open(ROOT/BASE_VERSION/('BMI30-RPi-'+BASE_VERSION+'.tar.gz')) as tf:
        source=tf.extractfile(BASE_VERSION+'/project/hotspot_info_server.py').read().decode()
    stamp='_PORTAL_ORIGINAL_URLOPEN = urlopen'
    assert source.count(stamp)==1
    source=source.replace(stamp,'_PORTAL_UI_SOURCE_SHA256 = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()\n\n'+stamp)
    start=source.index('def render_portal_page(');head=source.index('<head>',start)+len('<head>')
    source=source[:head]+'\n  <meta name="bmi30-portal-sha256" content="{html.escape(_PORTAL_UI_SOURCE_SHA256)}">'+source[head:]
    marker="    var connectionCountEls = Array.prototype.slice.call(document.querySelectorAll('[data-connection-count]'));"
    assert source.count(marker)==1
    source=source.replace(marker,embed((HERE/'portal_ui_version.js').read_text(encoding='utf-8'))+'\n'+marker)
    marker='          var firmware = data && data.firmware_release ? data.firmware_release : {{}};'
    assert source.count(marker)==1
    source=source.replace(marker,marker+'\n          if (portalUIVersionCheck(firmware.runtime_portal_sha256)) {{ return; }}')
    ast.parse(source);WORK.mkdir(exist_ok=True)
    (WORK/'portal-candidate.py').write_text(source,encoding='utf-8',newline='\n')
    print(json.dumps({'candidate':str(WORK/'portal-candidate.py'),'sha256':hashlib.sha256(source.encode()).hexdigest()}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['recover','candidate']);a=p.parse_args()
    if a.action=='recover':recover_group()
    else:candidate()
