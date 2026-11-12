"""Verify the actual USB boot and the installed runtime."""
import hashlib,json,pathlib,ssl,subprocess,sys,time,urllib.request
p=json.load(sys.stdin);P=pathlib.Path;D=P('/home/techaid/Documents');plan=p['plan'];i=p['inventory']
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
def api(path,port=8765):return json.load(op.open('http://127.0.0.1:'+str(port)+'/api/'+path,timeout=6))
assert run('hostname')==i['hostname']
assert P('/proc/device-tree/serial-number').read_text()==i['serial']
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2'
assert run('findmnt','-n','-o','SOURCE','/boot/firmware')=='/dev/sda1'
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==plan['serial']
boot_id=P('/proc/sys/kernel/random/boot_id').read_text().strip();assert boot_id!=plan['boot_id_before']
assert 'BMI30_SPLIT_VERSION='+plan['version']+'\n' in (D/'host/bmi30_split_active_version.env').read_text()
samples=[]
for n in range(11):
 s=api('status');assert s['ok'] and not s['stale'] and s['connection']['connected'] and s['connection']['last_frame_age_s']<2
 assert s['detector']['worker']['alive'];samples.append(s)
 if n<10:time.sleep(1)
keys=['rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','seq_dup_ch0','seq_dup_ch1','crc_bad','magic_bad','host_rx_ack_fail']
delta={k:samples[-1]['stream_stats'][k]-samples[0]['stream_stats'][k] for k in keys}
assert delta['rx_cnt_ch0']>0 and delta['rx_cnt_ch1']>0
assert all(delta[k]==0 for k in keys[2:]),delta
worker=samples[-1]['detector']['worker'];assert worker['overflows']==0 and worker['results']>samples[0]['detector']['worker']['results']
config=json.loads((D/'host/bmi30_config.json').read_text());assert config==plan['config_before']
for key in ['avg_n','rpi_avg_n','selected','stream_mode','base_buf_len']:
 assert s['mode'][key]==plan['mode_before'][key],key
services={n:run('systemctl','is-active',n) for n in ['bmi30-core','bmi30-hotspot-info','bmi30-network','bmi30-agent','bmi30-antenna-check']}
services['bmi30-tunnel']=subprocess.run(['systemctl','is-active','bmi30-tunnel'],capture_output=True,text=True).stdout.strip()
agent=json.loads(P('/var/lib/bmi30-agent/state.json').read_text())
assert agent['http_status']==200
assert agent['server_state']=='pending' or services['bmi30-tunnel']=='active'
portal=api('status',80)
assert portal['stm32_firmware_version']=='1.2.56'
assert portal['raspberry_firmware_version']==p['firmware_version'],portal['raspberry_firmware_version']
html=op.open('http://127.0.0.1:8765/',timeout=6).read().decode()
assert 'id="avgReadout"' in html and 'id="avgSel"' not in html and 'justify-content:center' in html
assert api('frame?max_points=200')['available']
assert op.open('https://127.0.0.1/',timeout=6).status==200
run('bash',str(D/'switch_bmi30_split_versions.sh'),'--validate',plan['version'])
hashes={}
for rel in p['hashes']:
 hashes[rel]=hashlib.sha256(P('/'+rel).read_bytes()).hexdigest();assert hashes[rel]==p['hashes'][rel],rel
phase=api('tx-phase')
result={'host':i['hostname'],'root':'/dev/sda2','boot':'/dev/sda1','boot_id':boot_id,'usb_serial':plan['serial'],
 'version':plan['version'],'firmware_version':portal['raspberry_firmware_version'],'stm32':portal['stm32_firmware_version'],'mode':s['mode'],'averaging':s['averaging'],
 'sync':s['sync'],'phase':phase,'services':services,'stream_delta':delta,'worker_overflows':worker['overflows'],
 'config_preserved':True,'bundle_validated':True,'http_https_frame_available':True,'hashes':hashes,
 'server_state':agent['server_state'],'server_message':agent['server_message']}
print(json.dumps(result))
