"""Copy measured report to all freshly verified roots and read it back."""
import base64
import concurrent.futures
import json
from pathlib import Path
from audit_stability import HOSTS
from inspect_group_display import WORK
from deploy import remote
from write_stability_report import COPY
from build_display_release import ROOT, VERSION, NETWORK_RELEASE

REPORT=Path(__file__).resolve().parents[1]/'GROUP_DISPLAY_2026-10-05_RU.md'
SCRIPT=COPY.replace("=='0.1.2'","=='0.1.7'").replace(
    'BMI30_NETWORK_STABILITY_2026-10-05_RU.md','BMI30_GROUP_DISPLAY_2026-10-05_RU.md')

def main():
    data=REPORT.read_bytes()
    for directory in (ROOT/VERSION,ROOT/NETWORK_RELEASE):(directory/'README.md').write_bytes(data)
    def one(item):
        board,host=item
        inv=json.loads((WORK/(board+'-inventory-led-after.json')).read_text())
        results=[]
        for root in inv['roots']:
            result=json.loads(remote(host,SCRIPT,{'host':host,'live':inv['root'],
                'device':root['device'],'uuid':root.get('uuid'),'doc':base64.b64encode(data).decode()},timeout=35))
            results.append(result)
            print(json.dumps(result),flush=True)
        (WORK/(board+'-report-copied.json')).write_text(json.dumps(results,indent=2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(one,HOSTS.items()))

if __name__=='__main__':main()
