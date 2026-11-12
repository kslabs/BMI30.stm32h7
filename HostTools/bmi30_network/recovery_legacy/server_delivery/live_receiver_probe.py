"""Run the unmodified 0.1.13 receiver over the actual S09/M10 Wi-Fi LAN."""
import base64
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_menu import remote, WORK

SCRIPT = r'''
import pathlib,sys,json,base64,types,threading,urllib.request,tempfile,subprocess
from unittest.mock import patch
P=pathlib.Path;p=json.load(sys.stdin)
key=json.loads(P('/etc/bmi30-network/config.json').read_text())['fleet_key']
sys.path.insert(0,'/opt/bmi30-network')
protocol=types.ModuleType('original_network_protocol')
exec(base64.b64decode(p['protocol']),protocol.__dict__)
protocol.time=types.SimpleNamespace(time=lambda:0)
scope={'__name__':'original_network_service','__file__':'original_network_service.py'}
with patch.dict(sys.modules,{'protocol':protocol}):exec(base64.b64decode(p['service']),scope)
import time
scope['time']=types.SimpleNamespace(time=lambda:0,monotonic=time.monotonic)
with tempfile.TemporaryDirectory(prefix='bmi30-old-receiver-') as directory:
 receiver=scope['Agent'].__new__(scope['Agent']);receiver.config={'id':'BMI30-27448D72B'}
 receiver.protocol=protocol.Protocol(key);receiver.records=protocol.Records(receiver.protocol)
 receiver.lock=threading.RLock();receiver.boot='recovery-test-receiver';receiver.seq=1
 receiver.local={'id':receiver.config['id'],'boot':receiver.boot,'seq':1,'issued':0,'cost':1000000,'path':[],'internet':{'ok':False}}
 receiver.records.records[receiver.config['id']]=receiver.protocol.sign(receiver.local)
 receiver.secrets={};receiver.secrets_path=P(directory)/'credentials.json';receiver.peers={}
 receiver.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 first=receiver.contact(p['peer_ip'],p['peer_id'],reserve=True)
 assert first['reserved'],'Parent reservation refused'
 assert first['self']['data']['internet']['ok'],'Parent Internet route missing'
 assert 'ap:'+p['peer_id'] in receiver.secrets,'Parent hotspot credentials missing'
 saved=json.loads(receiver.secrets_path.read_text())
 wifi=[v for k,v in saved.items() if k.startswith('wifi:')]
 assert wifi,'No Wi-Fi settings received'
 assert all(v['updated']<=15 for v in wifi),'Untranslated receiver timestamp'
 second=receiver.contact(p['peer_ip'],p['peer_id'])
 with receiver.opener.open('http://'+p['peer_ip']+':8791/status',timeout=4) as response:status=json.load(response)
 print(json.dumps({'peer_id':p['peer_id'],'old_receiver_protocol':'unmodified-0.1.13','emulated_id':'BMI30-27448D72B',
 'receiver_calendar':0,'received_wifi_count':len(wifi),'credentials_file_written':True,
 'receiver_timestamps_accepted':True,'peer_stage':status.get('legacy_recovery',{}).get('stage'),
 'parent_route_received':True,'parent_hotspot_credentials_received':True,'parent_reservation_accepted':True,
 'route':subprocess.check_output(['ip','route','get',p['peer_ip']],text=True).strip(),
 'system_time_changed':False,'secrets_exported':False}))
'''


def main():
    archive=ROOT/'FirmwareReleases/RaspberryPi/2026-10-05-network-13/BMI30-Network-2026-10-05-network-13.tar.gz'
    with tarfile.open(archive) as tar:
        code={name:base64.b64encode(tar.extractfile('2026-10-05-network-13/'+name+'.py').read()).decode()
              for name in ('protocol','service')}
    peers=[('S09','BMI30-D5EB8F26C','192.168.0.114'),('M10','BMI30-6922A5A0D','192.168.0.133')]
    for board,peer,ip in peers:
        result=remote(board,SCRIPT,{**code,'peer_id':peer,'peer_ip':ip},timeout=35)
        (WORK/(board+'-old-receiver-wifi-probe.json')).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'sender_board':board,**result}))


if __name__=='__main__':
    main()
