"""Run on one idle node: verify a real relay then restore its original route."""
import json
from pathlib import Path
import sys
import time
import urllib.request
import uuid

import network
from protocol import Protocol
from policy import valid_path

config = json.loads(Path('/etc/bmi30-network/config.json').read_text())
adapter = network.Network(config)
assert not adapter.remote_desktop_active(), 'Interactive RDP session is active'
state = json.loads(Path('/run/bmi30-network/status.json').read_text())
assert not state['local']['clients'], 'Hotspot clients are active'
parent = next(n for n in state['nodes'] if n['id'] == sys.argv[1])
assert valid_path(parent, config['id'])
adapter.visible(rescan=True)
time.sleep(3)
ap = next(a for a in adapter.visible() if a['bssid'] == parent['ap']['bssid'])
secret = json.loads(Path('/var/lib/bmi30-network/credentials.json').read_text())['ap:' + parent['id']]
protocol = Protocol(config['fleet_key'])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
checks = []
def verify(address):
    local = json.loads(Path('/run/bmi30-network/status.json').read_text())['local']
    request_id = uuid.uuid4().hex
    payload = {'self':protocol.sign(local),'records':[], 'credentials':{},'reserve':True,'request_id':request_id}
    req=urllib.request.Request(f'http://{address}:8791/exchange',protocol.seal(payload))
    with opener.open(req,timeout=5) as response: reply=protocol.open(response.read())
    assert reply['request_id']==request_id and reply['id']==parent['id'] and reply['reserved']
    assert valid_path(reply['self']['data'], config['id'])
    checks.append({'gateway':address,'authenticated_parent':reply['id']})
before=adapter.snapshot()
verify(next(p['addresses'][0] for p in config['peers'] if p['id'] == parent['id']))
choice={**ap,'kind':'relay','parent':parent['id']}
health=adapter.activate(choice,secret,verify,commit=False)
after=adapter.snapshot()
assert after['station_uuid']==before['station_uuid'] and after['bssid']==before['bssid']
result={'node':config['id'],'parent':parent['id'],'internet_via_relay':health,'peer_checks':checks,
        'previous_route_restored':True,'before_bssid':before['bssid'],'after_bssid':after['bssid']}
Path('/var/lib/bmi30-network/relay-test.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
