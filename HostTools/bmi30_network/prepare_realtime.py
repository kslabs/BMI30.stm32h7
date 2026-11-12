import base64,concurrent.futures,json
from inspect_realtime import WORK
from audit_stability import HOSTS,INVENTORY
from deploy import remote
from build_realtime_release import candidates,ENGINE
from validate_display_portal import SCRIPT

def audit(board,phase='before'):
    data=json.loads(remote(HOSTS[board],INVENTORY,{'board':board,'host':HOSTS[board]},timeout=150))
    (WORK/(board+'-inventory-'+phase+'.json')).write_text(json.dumps(data,indent=2))
    print(json.dumps({'board':board,'phase':phase,'roots':len(data['roots'])}),flush=True)
    return data

if __name__=='__main__':
    WORK.mkdir(exist_ok=True)
    data=json.loads(remote(HOSTS['S13'],"import json,pathlib;print(json.dumps(pathlib.Path('/home/techaid/Documents/host/bmi30_active_runtime/project/"+ENGINE+"').read_text()))"))
    (WORK/'engine-before.py').write_text(data,encoding='utf-8')
    portal,stream,engine=candidates()
    rendered=json.loads(remote(HOSTS['S13'],SCRIPT,{'source':base64.b64encode(portal).decode()},timeout=35))
    (WORK/'candidate-render-scripts.json').write_text(json.dumps(rendered,indent=2))
    print(json.dumps({'scripts':len(rendered['scripts']),'bytes':rendered['bytes']}),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(audit,HOSTS))
