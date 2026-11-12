"""Board-pinned boot test controller. PREPARED LOCALLY; root invokes explicitly.

schedule: read-only preflight, persist prior boot ID LOCALLY, then revalidate and
set one-shot eMMC-first order0xf41 (USB fallback) and schedule reboot in4s. USB uses the
unchanged persistent0xf2614 order after a verified eMMC boot. No EEPROM writes.
verify: read-only new-boot/media/source/config/runtime/network checks; unavailable
or not-ready returns ok=false. Root controls retries and board sequencing.
"""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,pathlib,sys,time

W=pathlib.Path(__file__).resolve().parent;R=W.parents[1]
BOARDS={'s07':('BMI30-C9775FBAE','abdd2dbc9775fbae','slave',7),
        'm02':('BMI30-E29805C7C','490df72e29805c7c','master',2)}
DOC_URL='https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/config_txt/boot.adoc#set_reboot_order'

def digest(value):return hashlib.sha256(value).hexdigest()
def canon(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')
def save(path,value):
    data=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8')
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    tmp.replace(path)

REMOTE=r'''
import contextlib,fcntl,hashlib,json,math,os,pathlib,re,shlex,shutil,socket,subprocess,sys,time,uuid,urllib.request
P=pathlib.Path;p=json.load(sys.stdin);plan=p['plan'];D=P('/home/techaid/Documents')
class NotReady(RuntimeError):pass
def need(value,reason):
 if not value:raise NotReady(reason)
def run(*args,timeout=12):
 result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
 need(result.returncode==0,'Command failed: '+args[0]);return result.stdout.strip()
def sha(path):return hashlib.sha256(P(path).read_bytes()).hexdigest()

def semantic_config_sha(path):
 def unique(pairs):
  data={}
  for key,value in pairs:
   need(key not in data,'Duplicate JSON config key');data[key]=value
  return data
 def invalid(value):raise NotReady('Non-finite JSON config value')
 data=json.loads(P(path).read_bytes(),object_pairs_hook=unique,parse_constant=invalid)
 encoded=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')
 return hashlib.sha256(encoded).hexdigest()

def same_sources(first,last):
 return {k:v for k,v in first.items()if k!='config_sha256'}=={k:v for k,v in last.items()if k!='config_sha256'}
def pid(unit):
 value=int(run('systemctl','show',unit,'-p','MainPID','--value'));need(value>0,'Service not running: '+unit)
 return {'pid':value,'start_ticks':P('/proc',str(value),'stat').read_text().rsplit(')',1)[1].split()[19]}
def flat(nodes,parent=None):
 for node in nodes:
  disk=node if node.get('type')=='disk'else parent
  yield node,disk
  yield from flat(node.get('children',[]),disk)
def api(port,path):
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with opener.open('http://127.0.0.1:'+str(port)+path,timeout=5)as response:
  need(response.status==200,'HTTP status unavailable');return json.load(response)
def identity():
 need(socket.gethostname()==plan['host'],'Hostname mismatch')
 need(P('/proc/device-tree/serial-number').read_text().strip('\x00\n ')==plan['cm5'],'CM5 serial mismatch')
 need(b'raspberrypi,5-compute-module'in P('/proc/device-tree/compatible').read_bytes().split(b'\x00'),'CM5 model mismatch')
def get_result():
 result=p.get('copy_result')
 if result is None:
  need(run('findmnt','-n','-o','SOURCE','/')==plan['root'],'Read missing copy result only from original eMMC')
  job=P('/var/lib')/('bmi30-usb-owncopy-'+plan['job_id'])
  status=json.loads((job/'status.json').read_bytes());need(status.get('stage')=='complete','Copy job is not complete')
  result=json.loads((job/'result.json').read_bytes())
 for key in('ok','copied','verified_copy','safe_to_remove','own_identity_settings_verified'):
  need(result.get(key)is True,'Copy is not verified/safe: '+key)
 need(result['host']==plan['host']and result['job_id']==plan['job_id']and result['cm5']==plan['cm5'],'Foreign copy result')
 need(result['source']=='/dev/mmcblk0p2'and result['target']==plan['target']['path'],'Copy direction mismatch')
 need(result['usb_serial']==plan['target']['serial'],'Copy USB identity mismatch')
 need(result['source_root_partuuid']==plan['root_partuuid']and result['source_boot_partuuid']==plan['boot_partuuid'],'Copy eMMC identity mismatch')
 need(result['source_version']==plan['expected_source_version']=='2026-11-04-185916','Wrong active source patch')
 need(result['source_active_hashes']=={key:item['sha256']for key,item in plan['sources'].items()},'Copy source fingerprints mismatch')
 need(result.get('final_runtime')and result.get('source_env_sha256_at_final_sync')and result.get('network_source_hashes_at_final_sync'),'Final copy snapshot is missing')
 return result
def media(result):
 rows=list(flat(json.loads(run('lsblk','--json','--tree','-b','-o','NAME,PATH,TYPE,TRAN,SIZE,MODEL,SERIAL,WWN,RO,PARTUUID,UUID,MOUNTPOINTS'))['blockdevices']))
 usb=[node for node,parent in rows if node.get('type')=='disk'and str(node.get('serial')or'').strip()==plan['target']['serial']and int(node['size'])==plan['target']['size']and node.get('tran')=='usb']
 need(len(usb)==1,'Pinned USB missing/ambiguous');usb=usb[0]
 need(not usb.get('ro'),'USB is read-only')
 def partition(partuuid,parent_path):
  hits=[node for node,parent in rows if str(node.get('partuuid')or'').lower()==partuuid.lower()and parent and parent['path']==parent_path]
  need(len(hits)==1,'Partition missing/ambiguous: '+partuuid);return hits[0]
 ur=partition(result['root_partuuid'],usb['path']);ub=partition(result['boot_partuuid'],usb['path'])
 need(ur['uuid']==result['root_uuid']and str(ub['uuid']).lower()==result['boot_uuid'].lower(),'USB filesystem UUID mismatch')
 er=partition(plan['root_partuuid'],'/dev/mmcblk0');eb=partition(plan['boot_partuuid'],'/dev/mmcblk0')
 root=run('findmnt','-n','-o','SOURCE','/');boot=run('findmnt','-n','-o','SOURCE','/boot/firmware')
 if(root,boot)==(er['path'],eb['path']):current='emmc'
 elif(root,boot)==(ur['path'],ub['path']):current='usb'
 else:raise NotReady('Mixed/unexpected root and boot media')
 need(run('findmnt','-n','-o','PARTUUID','/').lower()==(plan['root_partuuid']if current=='emmc'else result['root_partuuid']).lower(),'Root PARTUUID mismatch')
 need(run('findmnt','-n','-o','PARTUUID','/boot/firmware').lower()==(plan['boot_partuuid']if current=='emmc'else result['boot_partuuid']).lower(),'Boot PARTUUID mismatch')
 inactive=(ur,ub)if current=='emmc'else(er,eb)
 need(not any(m for node in inactive for m in(node.get('mountpoints')or[])if m),'Inactive medium is mounted')
 prop=P('/proc/device-tree/chosen/bootloader/boot-mode')
 raw=prop.read_bytes()if prop.is_file()else b''
 boot_mode=int.from_bytes(raw,'big')if len(raw)==4 else None
 return {'current':current,'root':root,'boot':boot,'root_partuuid':er['partuuid']if current=='emmc'else ur['partuuid'],
  'boot_partuuid':eb['partuuid']if current=='emmc'else ub['partuuid'],'usb_serial':plan['target']['serial'],
  'usb_size':int(usb['size']),'usb_path':usb['path'],'boot_mode':boot_mode,'inactive_medium_unmounted':True}
def bootloader(read_tools=False):
 config=run('vcgencmd','bootloader_config')
 need(hashlib.sha256(config.encode()).hexdigest()==p['bootloader_config_sha256'],'Persistent bootloader configuration changed')
 order=[line.split('=',1)[1].strip().lower()for line in config.splitlines()if line.startswith('BOOT_ORDER=')]
 need(order==['0xf2614'],'Persistent USB-first boot order mismatch')
 for name in('vcmailbox','rpi-eeprom-update'):need(shutil.which(name)is not None,'Required read-only preflight tool absent: '+name)
 details={'config_sha256':p['bootloader_config_sha256'],'order':order[0],'eeprom_write_performed':False}
 if read_tools:
  check=subprocess.run(['rpi-eeprom-update'],capture_output=True,text=True,timeout=15)
  need(check.returncode in(0,1),'Read-only rpi-eeprom-update check failed')
  details['readonly_rpi_eeprom_check']={'returncode':check.returncode,'output_sha256':hashlib.sha256((check.stdout+check.stderr).encode()).hexdigest()}
 return details
def boot_file_safety():
 text=P('/boot/firmware/config.txt').read_text()
 need(not re.search(r'^\s*dtoverlay=bmi30-usb-no-emmc\s*(?:#.*)?$',text,re.M),'eMMC-hiding overlay is active')
 need(not any(P('/boot/firmware',name).exists()for name in('recovery.bin','pieeprom.upd','pieeprom.sig','vl805.bin','vl805.sig')),'Pending firmware/EEPROM boot update exists')
def sources(result):
 E=D/'host/bmi30_split_active_version.env';C=D/'host/bmi30_config.json'
 need(sha(E)==result['source_env_sha256_at_final_sync'],'Selection env differs from final copy snapshot')
 proof=p.get('source_runtime_proof')or{}
 need(proof.get('ok')and proof.get('source_reverified')and proof.get('host')==plan['host']and proof.get('cm5')==plan['cm5']and proof.get('copy_job_id')==plan['job_id'],'Source-runtime config anchor is missing/foreign')
 need(proof.get('copied_config_sha256')==result['source_config_sha256_at_final_sync'],'Config anchor does not bind copied bytes')
 need(proof.get('source_hashes')==result['source_active_hashes']and proof.get('env_sha256')==result['source_env_sha256_at_final_sync'],'Config anchor differs from source/env snapshot')
 semantic=semantic_config_sha(C)
 need(semantic==proof.get('config_semantic_sha256'),'Config values differ from final copy snapshot')
 env={key:shlex.split(value)[0]for line in E.read_text().splitlines()if '='in line and not line.startswith('#')for key,value in[line.split('=',1)]}
 need(env.get('BMI30_SPLIT_VERSION')==plan['expected_source_version'],'Active source version mismatch')
 code={}
 for key,item in plan['sources'].items():
  path=P(item['path']);need(path.is_relative_to(D),'Unexpected source path');need(not path.is_symlink()and sha(path)==item['sha256'],'Active source hash mismatch: '+key)
  if key in('BMI30_CORE_PATH','BMI30_ENGINE_SOURCE'):
   selected=P(env[key]);selected=selected if selected.is_absolute()else D/selected
   need(selected==path,'Selected source path mismatch: '+key)
  code[key]=item['sha256']
 net={}
 for name,expected in result['network_source_hashes_at_final_sync'].items():
  need(name.startswith('opt/bmi30-network/')and '..'not in P(name).parts,'Unexpected network source path')
  need(sha(P('/')/name)==expected,'Network code differs from final copy: '+name);net[name]=expected
 return {'source_version':env['BMI30_SPLIT_VERSION'],'base_bundle_id':env.get('BMI30_SPLIT_BUNDLE_ID'),
  'source_sha256':code,'network_source_sha256':net,'config_sha256':sha(C),
  'config_semantic_sha256':semantic,'copied_config_sha256':result['source_config_sha256_at_final_sync'],'selection_env_sha256':sha(E)}
def healthy(status,result,learned=False):
 need(status.get('ok')and not status.get('stale')and status['connection']['connected'],'Core not connected/current')
 need(0<=status['connection']['last_frame_age_s']<2,'No fresh acquisition frames')
 need(status['service']['engine_source']==plan['sources']['BMI30_ENGINE_SOURCE']['path'],'Running engine mismatch')
 need(status['detector']['active']and status['detector']['worker']['alive'],'Detector worker not active')
 frozen=result['final_runtime']
 need(status['mode']==frozen['mode']and status['detector']['settings']==frozen['settings'],'Runtime policy differs from final copy')
 need({ch:{key:status['detector']['channels'][ch][key]for key in frozen['channel_policy'][ch]}for ch in('upper','lower')}==frozen['channel_policy'],'Channel policy differs from final copy')
 need(not status['player_recorder']['recording']and not status['player_recorder']['playback_active'],'Player activity present')
 role=status['sync'];need(role.get('device_responded')and role.get('value')==p['expected_role']and role.get('device_id')==p['expected_id']and role.get('code')==p['expected_code'],'Own STM role/device ID mismatch')
 if learned:
  need(status['service']['uptime_s']>=30,'Core learning: uptime below30s')
  need(not status.get('dc_guard',{}).get('lightning',{}).get('active',False),'DC lightning active')
  need(status.get('optimize',{}).get('fast_adapt_active')is False and status.get('optimize',{}).get('dc_autotune_active')is False,'DC optimization active/unknown')
  need(all(status['detector'].get('noise_valid'+str(ch))and math.isfinite(status['detector']['noise_avg'+str(ch)])and status['detector']['noise_avg'+str(ch)]>0 for ch in(0,1)),'Noise reference not valid')
  depth=status['mode']['rpi_avg_n'];need(status['detector']['worker']['fresh_pairs']['window_counts']==[depth]*4,'Phase histories not full')
 return role
def no_work(locks):
 need(not P('/run/bmi30-access/busy').exists(),'Maintenance owner active')
 for path,kind in((P('/var/lib/bmi30-usb-owncopy.lock'),'flock'),(D/'.bmi30_cloud_sync/server_operation.lock','flock'),
  (P('/var/lib/dpkg/lock-frontend'),'lockf'),(P('/var/lib/dpkg/lock'),'lockf')):
  if not path.is_file():continue
  stream=locks.enter_context(path.open('r+b'))
  try:(fcntl.flock if kind=='flock'else fcntl.lockf)(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise NotReady('Copy/package/firmware operation owns a lock')
 for proc in P('/proc').iterdir():
  if not proc.name.isdigit()or int(proc.name)==os.getpid():continue
  try:
   comm=(proc/'comm').read_text().strip();argv=(proc/'cmdline').read_bytes().split(b'\x00')
  except OSError:continue
  need(comm not in('rsync','sfdisk','mkfs.vfat','mkfs.ext4','e2fsck','fsck.fat','apt','apt-get','dpkg'),'Media/package worker active: '+comm)
  if comm.startswith('python'):
   scripts=[a.decode(errors='replace')for a in argv[1:]if a and not a.startswith(b'-')]
   if len(argv)>1 and argv[1]in(b'-c',b'-m'):continue
   need(not any(P(a).name in('clone.py','clone_remote.py','media_copy_worker.py','usb_recovery_worker.py','passive_raw_capture.py','remote_pass_logger.py')for a in scripts),'Copy/capture Python worker active')
 units=run('systemctl','list-units','--type=service','--state=running,activating','--no-legend','--plain')
 need(not any(re.search(r'bmi30-(?:usb-owncopy-|usb-copy|media-sync|new-device-emmc-copy)',line.split()[0])for line in units.splitlines()if line.split()),'Copy service still active')
def schedule_check(result):
 identity();m=media(result);boot_file_safety();code=sources(result);status=api(8765,'/api/status');healthy(status,result)
 serial='333F35743032'if p['expected_id']==7 else'3342355E3032'
 need(len([path for path in P('/sys/bus/usb/devices').glob('*/serial')if path.read_text().strip()==serial])==1,'Expected STM USB serial missing/ambiguous')
 if p['medium']=='emmc':
  proof=p.get('source_runtime_proof')or{}
  need(proof.get('ok')and proof.get('source_reverified')and proof.get('host')==plan['host']and proof.get('cm5')==plan['cm5'],'Source runtime was not independently reverified')
  need(proof.get('boot_id')==P('/proc/sys/kernel/random/boot_id').read_text().strip()and proof.get('copy_job_id')==plan['job_id'],'Source-runtime proof is from another boot/copy')
  need(proof.get('config_semantic_sha256')==code['config_semantic_sha256']and proof.get('env_sha256')==code['selection_env_sha256']and proof.get('source_hashes')==code['source_sha256'],'Source-runtime proof differs from final snapshot')
 if p['medium']=='usb':
  need(m['current']=='emmc','Normal USB test must start from verified eMMC')
  proof=p.get('prior_emmc_proof')or{}
  need(proof.get('ok')and proof.get('boot_id')==P('/proc/sys/kernel/random/boot_id').read_text().strip(),'Current eMMC boot not verified first')
 return m,code,status
def network_health():
 net=api(8791,'/status');now=time.monotonic()
 need(net.get('version')=='0.2.10'and net.get('status')=='online'and not net.get('stale'),'Network0.2.10 not online/current')
 need(0<=now-float(net['checked_mono'])<45,'Routing observation stale')
 local=net['local'];need(local['id']==plan['host']and local.get('uplink')=='wifi'and bool(local.get('ssid')),'STA connection unavailable')
 need(local.get('ap',{}).get('available')and local['ap'].get('ssid')==plan['host'],'Own AP unavailable')
 group=json.loads(P('/run/bmi30-network/group.json').read_bytes())
 need(group.get('available')and group.get('local_id')==plan['host']and 0<=now-float(group['checked_mono'])<4,'Service group publication stale')
 expected={plan['host'],p['peer_host']};nodes={n['id']:n for n in group.get('nodes',[])if n.get('id')in expected}
 need(set(nodes)==expected,'Own/peer service group member missing')
 for node in nodes.values():need(node.get('available')and 0<=float(node['age_s'])<4 and float(node.get('cache_age_s',0))<4,'Service group member stale/unavailable')
 return {'version':net['version'],'status':net['status'],'STA_available':True,'own_AP_available':True,
  'routing_age_s':now-float(net['checked_mono']),'group_fresh':True,
  'group_nodes':[{'id':n['id'],'age_s':n['age_s'],'transport':n.get('transport')}for n in nodes.values()]}
def verify(result):
 identity();m=media(result);need(m['current']==p['medium'],'Still on the previous/unexpected medium')
 boot_id=P('/proc/sys/kernel/random/boot_id').read_text().strip();need(boot_id!=p['prior_boot_id'],'No new OS boot observed')
 need(m['boot_mode']==(4 if p['medium']=='usb'else 1),'Firmware DT boot-mode does not match requested medium')
 boot_file_safety();boot=bootloader();code=sources(result)
 serial='333F35743032'if p['expected_id']==7 else'3342355E3032'
 need(len([path for path in P('/sys/bus/usb/devices').glob('*/serial')if path.read_text().strip()==serial])==1,'Expected STM USB serial missing/ambiguous')
 need(not P('/run/bmi30-access/busy').exists(),'Maintenance still active')
 first=api(8765,'/api/status');role=healthy(first,result,True);core=pid('bmi30-core')
 time.sleep(2)
 last=api(8765,'/api/status');healthy(last,result,True)
 need(pid('bmi30-core')==core,'Core restarted during observation')
 need(last['detector']['worker']['results']>first['detector']['worker']['results'],'Worker is not advancing')
 need(last['detector']['worker']['overflows']==first['detector']['worker']['overflows'],'Worker overflow counter changed/reset')
 keys=('rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','seq_dup_ch0','seq_dup_ch1','crc_bad','magic_bad','host_rx_ack_fail')
 deltas={key:last['stream_stats'][key]-first['stream_stats'][key]for key in keys}
 need(all(deltas[k]>0 for k in('rx_cnt_ch0','rx_cnt_ch1'))and all(v==0 for k,v in deltas.items()if not k.startswith('rx_cnt_')),'Observed stream error/reset/non-advancement')
 portal=api(80,'/api/status');need(portal.get('hostname')==plan['host'],'Portal identity mismatch')
 need((portal.get('sync_mode')or{}).get('code')==p['expected_code'],'Portal own role code mismatch')
 net=network_health();end_code=sources(result);need(same_sources(end_code,code),'Sources/config values changed during verification')
 need(bootloader()==boot,'Persistent EEPROM configuration changed')
 return {'ok':True,'boot_tested':True,'host':plan['host'],'cm5':plan['cm5'],'medium':m,
  'boot_id':boot_id,'prior_boot_id':p['prior_boot_id'],'bootloader':boot,**end_code,
  'source_config_values_match_final_copy':True,'source_config_bytes_match_final_copy':end_code['config_sha256']==result['source_config_sha256_at_final_sync'],
  'config_sha256_at_observation_start':code['config_sha256'],'role':{k:role.get(k)for k in('value','device_id','code','role_persisted')},
  'mode':last['mode'],'core_process':core,'uptime_s':last['service']['uptime_s'],
  'worker_results_start_end':[first['detector']['worker']['results'],last['detector']['worker']['results']],
  'frame_age_s':last['connection']['last_frame_age_s'],'noise_reference_valid':True,'stm32_usb_serial':serial,
  'observed_stream_counter_deltas':deltas,'portal_http':200,'network':net,
  'scope':'Actual new boot/media and bounded runtime checks; no physical tag/range qualification or exact capture zero-loss claim'}
try:
 identity();result=get_result()
 if p['operation']=='verify':answer=verify(result)
 elif p['operation']=='prepare':
  with contextlib.ExitStack()as locks:
   no_work(locks);m,code,status=schedule_check(result);boot=bootloader(True)
   answer={'ok':True,'prepared':True,'host':plan['host'],'cm5':plan['cm5'],'boot_id':P('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'current_medium':m,'requested_medium':p['medium'],'bootloader':boot,**code,'copy_result':result}
 elif p['operation']=='commit':
  with contextlib.ExitStack()as locks:
   no_work(locks);m,code,status=schedule_check(result);boot=bootloader()
   need(P('/proc/sys/kernel/random/boot_id').read_text().strip()==p['prior_boot_id'],'Boot changed between prepare and commit')
   need(same_sources(code,p['prepared_sources']),'Sources/config values changed between prepare and commit')
   response=None
   if p['medium']=='emmc':
    response=run('vcmailbox','0x0003808b','4','4','0xf41')
    words=[int(v,16)for v in re.findall(r'0x[0-9a-fA-F]+',response)]
    need(len(words)>=6 and words[1]==0x80000000 and words[2]==0x3808b and words[4]==0x80000004 and words[5]==0,'One-shot mailbox response not acknowledged')
   need(bootloader()==boot,'EEPROM configuration changed after one-shot request')
   need(not P('/run/bmi30-access/busy').exists(),'Maintenance owner appeared before reboot')
   unit='bmi30-owncopy-boot-'+p['medium']+'-'+uuid.uuid4().hex[:10]
   run('systemd-run','--unit='+unit,'--on-active=4s','--timer-property=AccuracySec=1s','--collect','/usr/bin/systemctl','reboot')
   answer={'ok':True,'reboot_scheduled':True,'unit':unit,'medium':p['medium'],'prior_boot_id':p['prior_boot_id'],
    'one_shot_order':'0xf41'if p['medium']=='emmc'else None,'normal_persistent_USB_priority_test':p['medium']=='usb',
    'eeprom_config_unchanged':True,'mailbox_acknowledged':response is not None,'reboot_delay_s':4}
 else:raise NotReady('Unsupported operation')
except Exception as exc:
 answer={'ok':False,'boot_tested':False,'reason':str(exc)if isinstance(exc,NotReady)else type(exc).__name__,'error_type':type(exc).__name__}
print(json.dumps(answer))
'''

def load_route():
    spec=importlib.util.spec_from_file_location('boot_incident_route',R/'.tmp/m02-network-disappearance-20261104/incident_route.py')
    route=importlib.util.module_from_spec(spec);spec.loader.exec_module(route);return route

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board',choices=BOARDS)
    parser.add_argument('action',choices=('schedule','verify'))
    parser.add_argument('--medium',choices=('emmc','usb'),required=True)
    args=parser.parse_args(argv);board=args.board
    plan=json.loads((W/(board+'-plan.json')).read_bytes());inventory=json.loads((W/(board+'-inventory.json')).read_bytes())
    host,serial,role,ident=BOARDS[board]
    if(plan['host'],plan['cm5'])!=(host,serial):raise ValueError('Local plan target mismatch')
    boot_sha=digest(inventory['bootloader'].encode())
    result_path=W/(board+'-copy-result.json');copy_result=json.loads(result_path.read_bytes())if result_path.is_file()else None
    if isinstance(copy_result,dict)and 'ok'not in copy_result and isinstance(copy_result.get('result'),dict):copy_result=copy_result['result']
    state_path=W/(board+'-'+args.medium+'-boot-plan.json')
    peer=BOARDS['m02'if board=='s07'else's07'][0]
    payload={'plan':plan,'copy_result':copy_result,'bootloader_config_sha256':boot_sha,'medium':args.medium,
        'expected_role':role,'expected_id':ident,'expected_code':('S'if role=='slave'else'M')+f'{ident:02}', 'peer_host':peer}
    route=load_route()
    def call(data):
        try:return route.remote(board,REMOTE,data,address='192.168.0.154'if board=='m02'else None,timeout=40)
        except Exception as exc:return {'ok':False,'boot_tested':False,'reason':'Remote unavailable','error_type':type(exc).__name__}
    if args.action=='schedule':
        proof_path=W/(board+'-source-runtime-verified.json')
        if not proof_path.is_file():raise ValueError('Run the independent source-runtime check before scheduling')
        payload['source_runtime_proof']=json.loads(proof_path.read_bytes())
        if args.medium=='usb':
            proof_path=W/(board+'-emmc-boot-verified.json')
            if not proof_path.is_file():raise ValueError('Verify the fresh eMMC boot before normal USB scheduling')
            payload['prior_emmc_proof']=json.loads(proof_path.read_bytes())
        prepared=call({**payload,'operation':'prepare'})
        if not prepared.get('ok'):
            save(W/(board+'-'+args.medium+'-schedule-last-attempt.json'),prepared);print(json.dumps(prepared));return 1
        old=json.loads(state_path.read_bytes())if state_path.is_file()else{}
        if old.get('phase')in('prepared','scheduled','commit_outcome_unknown')and old.get('prior_boot_id')==prepared['boot_id']:
            print(json.dumps({'ok':False,'reason':'A boot test from this boot ID is already pending; use verify'}));return 1
        copy_result=prepared.pop('copy_result');save(result_path,copy_result)
        payload['copy_result']=copy_result
        state={'schema':1,'phase':'prepared','board':board,'medium':args.medium,'prepared_at':time.time(),
            'prior_boot_id':prepared['boot_id'],'prepared':prepared,'plan':plan,'copy_result':copy_result,'source_runtime_proof':payload['source_runtime_proof'],
            'bootloader_config_sha256':boot_sha,'controller_sha256':digest(pathlib.Path(__file__).read_bytes()),
            'one_shot_primary_documentation':DOC_URL}
        # This is deliberately persisted before ANY reboot/mailbox mutation.
        save(state_path,state)
        code={key:prepared[key]for key in('source_version','base_bundle_id','source_sha256','network_source_sha256','config_sha256','config_semantic_sha256','copied_config_sha256','selection_env_sha256')}
        committed=call({**payload,'operation':'commit','prior_boot_id':prepared['boot_id'],'prepared_sources':code})
        state.update(phase='scheduled'if committed.get('ok')else'commit_outcome_unknown',commit=committed)
        save(state_path,state);print(json.dumps({'board':board,'state':str(state_path),**committed}));return 0 if committed.get('ok')else 1
    if not state_path.is_file():
        print(json.dumps({'ok':False,'boot_tested':False,'reason':'No locally saved schedule proof'}));return 1
    state=json.loads(state_path.read_bytes())
    if state.get('board')!=board or state.get('medium')!=args.medium:raise ValueError('Foreign boot schedule state')
    checked=call({**payload,'operation':'verify','plan':state['plan'],'copy_result':state['copy_result'],
        'source_runtime_proof':state.get('source_runtime_proof'),
        'bootloader_config_sha256':state['bootloader_config_sha256'],'prior_boot_id':state['prior_boot_id']})
    attempt=W/(board+'-'+args.medium+'-boot-last-attempt.json');save(attempt,checked)
    if checked.get('ok'):
        verified_path=W/(board+'-'+args.medium+'-boot-verified.json');save(verified_path,checked)
        state.update(phase='verified',verified_at=time.time(),verified_report=str(verified_path));save(state_path,state)
    print(json.dumps({'board':board,'medium':args.medium,'report':str(attempt),**checked}));return 0 if checked.get('ok')else 1

if __name__=='__main__':sys.exit(main())
