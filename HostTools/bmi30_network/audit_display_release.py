"""Fresh per-root inventory for each immutable group-display installation."""
import argparse
import concurrent.futures
import json
from audit_stability import HOSTS, INVENTORY
from deploy import remote
from inspect_group_display import WORK

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--phase',default='led-before'); args=parser.parse_args()
    def one(item):
        board,host=item
        data=json.loads(remote(host,INVENTORY,{'board':board,'host':host},timeout=140))
        (WORK/(board+'-inventory-'+args.phase+'.json')).write_text(json.dumps(data,indent=2))
        print(json.dumps({'board':board,'root':data['root'], 'roots':[
            {'device':r['device'],'active':r['active'],'network':(r.get('network_release') or {}).get('version'),
             'files':len(r.get('hashes',{})), 'hostname':r.get('hostname')} for r in data['roots']]}),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(one,HOSTS.items()))

if __name__=='__main__':main()
