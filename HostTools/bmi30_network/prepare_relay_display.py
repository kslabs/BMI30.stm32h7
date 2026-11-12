"""Read-only fleet audit and immutable portal candidate for the relay display fix."""
import argparse,ast,concurrent.futures,json,tarfile
from pathlib import Path
from audit_stability import HOSTS,INVENTORY
from deploy import remote
HERE=Path(__file__).resolve().parent
WORK=HERE.parents[1]/'.tmp/relay-display-20261005'
BASE_VERSION='2026-10-05-1540'
PROBE=r'''
import json,pathlib,subprocess
assert subprocess.check_output(['hostname'],text=True).strip()==HOST
g=json.loads(pathlib.Path('/run/bmi30-network/group.json').read_text())
n=json.loads(pathlib.Path('/run/bmi30-network/status.json').read_text())
print(json.dumps({'nodes':[{'id':x['id'],'available':x['available'],'via':x.get('via'),'age_s':x.get('age_s')} for x in g['nodes']],
 'peers':n.get('nodes'),'neighbors':json.loads(subprocess.check_output(['ip','-j','neigh'],text=True))}))
'''
def audit(board,phase='before'):
    try:
        data=json.loads(remote(HOSTS[board],INVENTORY,{'board':board,'host':HOSTS[board]},timeout=100))
        WORK.mkdir(exist_ok=True)
        (WORK/(board+'-inventory-'+phase+'.json')).write_text(json.dumps(data,indent=2))
        print(json.dumps({'board':board,'roots':len(data['roots']),'services':data['services'],
                          'available':[n['id'] for n in data['group']['nodes'] if n['available']]}),flush=True)
        return data
    except Exception as exc:
        print(json.dumps({'board':board,'error':str(exc)}),flush=True)
        return None
def embed(js):return js.replace('\\','\\\\').replace('{','{{').replace('}','}}')
def candidate():
    archive=HERE.parents[1]/'FirmwareReleases/RaspberryPi'/BASE_VERSION/('BMI30-RPi-'+BASE_VERSION+'.tar.gz')
    with tarfile.open(archive) as tf:source=tf.extractfile(BASE_VERSION+'/project/hotspot_info_server.py').read().decode()
    start=source.index("    var relayRoot = document.querySelector('[data-relay-root]');")
    end=source.index('// Shared help for all portal parameter panels.',start)
    source=source[:start]+embed((HERE.parent/'rpi_relay_web/portal_relay.js').read_text(encoding='utf-8'))+'\n'+source[end:]
    start=source.index('// Push every group change over one authenticated same-origin WebSocket.')
    end=source.index('    function _pollSensors() {{',start)
    source=source[:start]+embed((HERE/'live_ui.js').read_text(encoding='utf-8'))+'\n'+source[end:]
    ast.parse(source)
    WORK.mkdir(exist_ok=True);(WORK/'portal-candidate.py').write_text(source,encoding='utf-8',newline='\n')
    print(json.dumps({'candidate':str(WORK/'portal-candidate.py')}))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['audit','candidate','probe']);parser.add_argument('--phase',default='before');args=parser.parse_args()
    if args.action=='candidate':candidate()
    elif args.action=='probe':print(remote(HOSTS['S10'],PROBE.replace('HOST',repr(HOSTS['S10']))).decode())
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(lambda b:audit(b,args.phase),HOSTS))
