import hashlib,json,sys
from pathlib import Path
from media_sync import WORK,call,ROOT
for board in sys.argv[1:]:
    inventory=json.loads((WORK/(board+'-inventory.json')).read_text())
    plan=json.loads((WORK/(board+'-media_boot_usb.json')).read_text())
    audit=json.loads((ROOT/'.tmp/rpi-averaging-20261013'/board/'final-audit.json').read_text())
    h=audit['hashes'];base='home/techaid/Documents/host/bmi30_active_runtime/project/'
    h['portal']=hashlib.sha256((ROOT/'.tmp/rpi-averaging-20261013/candidate/portal.py').read_bytes()).hexdigest()
    h['helper']=hashlib.sha256(Path(__file__).with_name('bmi30_averaging.py').read_bytes()).hexdigest()
    hashes={base+'host/BMI30.001.py.2026-10-07-0959':h['core'],base+'host/BMI30.200.py.2026-10-06-1257':h['engine'],
      base+'hotspot_info_server.py':h['portal'],base+'host/bmi30_averaging.py':h['helper'],
      'usr/local/bin/bmi30-hotspot-info-server.py':h['portal']}
    r=call(board,Path(__file__).with_name('media_verify_usb.py').read_text(),'usb-verified',
           {'inventory':inventory,'plan':plan,'hashes':hashes,'firmware_version':'2026-10-13-153432'},timeout=50)
    print(json.dumps({'board':board,**r}),flush=True)
