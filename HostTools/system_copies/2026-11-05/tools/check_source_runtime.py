"""Read-only post-copy verification of the original eMMC. CLI: s07|m02|both.

Root invokes this after the clone result exists. No reboot, service or media writes.
The local JSON is source-runtime evidence, not evidence that USB has booted.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import time

W = Path(__file__).resolve().parent
R = W.parents[1]

SOURCE = r'''
import hashlib,json,math,pathlib,re,shlex,socket,subprocess,sys,time,traceback,urllib.request
P=pathlib.Path
class Refused(RuntimeError): pass
def require(value,reason):
 if not value: raise Refused(reason)
def sha(path):
 h=hashlib.sha256()
 with P(path).open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def canonical_config_hash(raw):
 def pairs(rows):
  value={}
  for key,item in rows:
   require(key not in value,'Duplicate config JSON key');value[key]=item
  return value
 def constant(value):raise Refused('Non-finite config JSON value')
 value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()
def config_contract(raw,plan,result,proof=None):
 current=hashlib.sha256(raw).hexdigest();snapshot=result['source_config_sha256_at_final_sync']
 semantic=canonical_config_hash(raw);used=current!=snapshot
 if used:
  require(isinstance(proof,dict),'Config bytes differ and bound semantic proof is missing')
  require(proof.get('format')=='bmi30-copy-config-semantic-proof-v1','Unknown config semantic proof format')
  bindings={'job_id':plan['job_id'],'host':plan['host'],'cm5':plan['cm5'],
   'target_root_uuid':result['root_uuid'],'target_root_partuuid':result['root_partuuid'],
   'usb_serial':plan['target']['serial'],'snapshot_byte_sha256':snapshot}
  require(all(proof.get(key)==value for key,value in bindings.items()),'Config semantic proof identity or snapshot differs')
  require(proof.get('semantic_equal') is True and proof.get('snapshot_matches_result') is True,'Config semantic proof did not establish equality')
  require(isinstance(proof.get('current_byte_sha256'),str) and re.fullmatch('[0-9a-f]{64}',proof['current_byte_sha256']),'Config proof creation hash missing')
  require(proof.get('config_semantic_sha256')==semantic,'Config values or types differ from copied snapshot')
 return {'config_sha256':current,'source_config_snapshot_sha256':snapshot,'copied_config_sha256':snapshot,
  'config_semantic_sha256':semantic,'config_semantic_proof_used':used}
def verify_config(path,job,plan,result):
 raw=P(path).read_bytes();proof=None;proof_hash=None
 if hashlib.sha256(raw).hexdigest()!=result['source_config_sha256_at_final_sync']:
  name=job/'config-semantic-proof.json'
  require(name.is_file() and not name.is_symlink() and name.stat().st_uid==0,'Trusted config semantic proof missing')
  proof_raw=name.read_bytes();proof=json.loads(proof_raw)
  proof_hash=hashlib.sha256(proof_raw).hexdigest()
 answer=config_contract(raw,plan,result,proof)
 answer['config_semantic_proof_sha256']=proof_hash
 return answer
def run(*args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=12,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
 require(r.returncode==0,'Read command failed: '+args[0])
 return r.stdout.strip()
def properties(unit):
 r=subprocess.run(['systemctl','show',unit,'-p','LoadState','-p','ActiveState','-p','SubState','-p','MainPID','-p','ControlPID','-p','Result'],capture_output=True,text=True,timeout=10)
 rows=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
 require(rows.get('ActiveState') in ('inactive','failed') and rows.get('MainPID','0')=='0' and rows.get('ControlPID','0')=='0','Copy or ExecStopPost still active')
 require(rows.get('Result') in (None,'','success'),'Copy unit has failed result')
 return rows
def get(url):
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with op.open(url,timeout=5) as f:return json.load(f)
def flatten(nodes):
 for n in nodes:
  yield n
  yield from flatten(n.get('children') or [])
def channel_policy(status):
 keys=('enabled','confirm_count','threshold','threshold_high','auto_threshold','burst_gate','burst_blank_s','burst_max_ratio')
 return {ch:{key:status['detector']['channels'][ch][key] for key in keys} for ch in ('upper','lower')}
def healthy_status(status,expected):
 require(status.get('ok') is True and not status.get('stale') and not status.get('busy'),'Core status unavailable or stale')
 connection=status.get('connection') or {}
 require(connection.get('connected') is True and not connection.get('disconnected'),'Core USB disconnected')
 age=connection.get('last_frame_age_s')
 require(isinstance(age,(int,float)) and 0<=age<=3,'Core input stale')
 require(status['service']['engine_source']==expected['plan']['sources']['BMI30_ENGINE_SOURCE']['path'],'Running engine differs from final copy')
 require(float(status['service'].get('uptime_s',0))>=30,'Core warmup under30s')
 worker=status['detector']['worker']
 require(worker.get('alive') is True and worker.get('mode')=='process' and int(worker.get('results',0))>0,'Detector worker not producing results')
 opt=status.get('optimize') or {}
 require(opt.get('fast_adapt_active') is False and opt.get('dc_autotune_active') is False,'Lightning or DC autotune is active')
 for ch in ('upper','lower'):
  c=status['detector']['channels'][ch]
  require(c.get('noise_valid') is True and math.isfinite(float(c.get('noise_avg',0))) and float(c.get('noise_avg',0))>0,'Noise not valid: '+ch)
 frozen=expected['result']['final_runtime']
 require(status['mode']==frozen['mode'],'Mode changed from final copied runtime')
 require(status['detector']['settings']==frozen['settings'],'Detector settings changed from final copied runtime')
 require(channel_policy(status)==frozen['channel_policy'],'Channel policy changed from final copied runtime')
 sync=status.get('sync') or {}
 role,node=('slave',7) if expected['board']=='s07' else ('master',2)
 require(sync.get('device_responded') is True and sync.get('device_id')==node and sync.get('role',sync.get('value'))==role,'STM32 role or node identity differs')
 return {'service':{k:status['service'].get(k) for k in ('version','engine_source','uptime_s')},
  'mode':status['mode'],'settings':status['detector']['settings'],'channel_policy':channel_policy(status),
  'connection':{k:connection.get(k) for k in ('connected','disconnected','last_frame_age_s')},
  'worker':{k:worker.get(k) for k in ('alive','mode','submitted','results','overflows','source_seq0','source_seq1')},
  'stream_stats':{k:(status.get('stream_stats') or {}).get(k) for k in ('rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','crc_bad')},
  'noise':{ch:{k:status['detector']['channels'][ch].get(k) for k in ('noise_valid','noise_avg','noise_samples')} for ch in ('upper','lower')},
  'sync':{k:sync.get(k) for k in ('role','value','device_id','device_responded','role_persisted')},'optimize':opt}
def verify_progress(first,second):
 require(second['worker']['results']>first['worker']['results'],'Worker did not advance')
 deltas={}
 for key in ('rx_cnt_ch0','rx_cnt_ch1'):
  a,b=first['stream_stats'][key],second['stream_stats'][key]
  require(isinstance(a,int) and isinstance(b,int) and b>a,'ADC channel did not advance: '+key)
  deltas[key]=b-a
 deltas['worker_results']=second['worker']['results']-first['worker']['results']
 return deltas
def check(data):
 plan,result=data['plan'],data['result']
 require(result.get('ok') is True and result.get('verified_copy') is True and result.get('safe_to_remove') is True,'Copy is not complete and verified')
 require(result.get('job_id')==plan['job_id'] and result.get('host')==plan['host'] and result.get('usb_serial')==plan['target']['serial'],'Wrong copy result identity')
 require(isinstance(result.get('final_runtime'),dict),'Final copied runtime is missing')
 require(socket.gethostname()==plan['host'],'Hostname changed')
 require(P('/proc/device-tree/serial-number').read_text().strip('\x00\n ')==plan['cm5'],'CM5 identity changed')
 boot_id=P('/proc/sys/kernel/random/boot_id').read_text().strip()
 require(boot_id==plan['boot_id'],'Source rebooted before source verification')
 for mount,device,key in (('/',plan['root'],'root_partuuid'),('/boot/firmware',plan['boot'],'boot_partuuid')):
  require(device==('/dev/mmcblk0p2' if mount=='/' else '/dev/mmcblk0p1'),'Plan source is not eMMC')
  require(run('findmnt','-n','-o','SOURCE',mount)==device,'Actual source is not original eMMC')
  require(run('findmnt','-n','-o','PARTUUID',mount).lower()==plan[key].lower(),'Original eMMC partition identity changed')
 unit=properties(data['unit'])
 require(not P('/run/bmi30-access/busy').exists(),'Maintenance busy remains claimed')
 disk_rows=json.loads(run('lsblk','-b','-J','-p','-o','NAME,PATH,TYPE,TRAN,SIZE,MODEL,SERIAL,WWN,MAJ:MIN,MOUNTPOINTS,UUID,PARTUUID','/dev/sda'))['blockdevices']
 disks=[n for n in flatten(disk_rows) if n.get('path')=='/dev/sda' and n.get('type')=='disk']
 require(len(disks)==1,'Pinned USB missing or ambiguous');disk=disks[0]
 pin=plan['target']
 require(all(disk.get(k)==pin[k] for k in ('path','tran','size','wwn')) and str(disk.get('model') or '').strip()==pin['model'] and str(disk.get('serial') or '').strip()==pin['serial'],'USB identity changed after copy')
 require(disk['maj:min']==pin['maj_min'] and int(P('/sys/class/block/sda/diskseq').read_text())==pin['diskseq'],'USB attachment changed after copy')
 require(not any(m for n in flatten([disk]) for m in (n.get('mountpoints') or [])),'USB is still mounted')
 partitions={n['path']:n for n in flatten(disk_rows)}
 for suffix,key in (('1','boot_partuuid'),('2','root_partuuid')):
  require(partitions['/dev/sda'+suffix]['partuuid'].lower()==result[key].lower(),'Copied partition identity differs')
 for suffix,key in (('1','boot_uuid'),('2','root_uuid')):
  require(partitions['/dev/sda'+suffix]['uuid'].lower()==result[key].lower(),'Copied filesystem identity differs')
 job=P('/var/lib/bmi30-usb-owncopy-'+plan['job_id'])
 require(json.loads((job/'result.json').read_text())==result,'Device and local copy results differ')
 state=json.loads((job/'status.json').read_text())
 require(state.get('stage')=='complete','Device copy status is not complete')
 pause=json.loads((job/'pause-state.json').read_text())
 require(pause.get('job_id')==plan['job_id'] and pause.get('units')==[] and pause.get('resumed') is True,'Copy writer restart is not complete')
 config='/home/techaid/Documents/host/bmi30_config.json'
 env='/home/techaid/Documents/host/bmi30_split_active_version.env'
 config_info=verify_config(config,job,plan,result);env_hash=sha(env)
 require(env_hash==result['source_env_sha256_at_final_sync'],'Own source selection env changed after final copy')
 selected={}
 for line in P(env).read_text().splitlines():
  if not line or line.startswith('#'):continue
  k,sep,v=line.partition('=');require(sep and k not in selected,'Invalid selection env')
  v=shlex.split(v);require(len(v)==1,'Invalid selection env value');selected[k]=v[0]
 require(selected.get('BMI30_SPLIT_VERSION')==plan['expected_source_version'],'Selected version changed')
 hashes={}
 for key,item in plan['sources'].items():
  hashes[key]=sha(item['path']);require(hashes[key]==item['sha256'],'Own source code changed: '+key)
  if key in ('BMI30_CORE_PATH','BMI30_ENGINE_SOURCE'):
   selected_path=P(selected[key]);selected_path=selected_path if selected_path.is_absolute() else P('/home/techaid/Documents')/selected_path
   require(selected_path.resolve()==P(item['path']).resolve(),'Selected runtime path changed')
 fingerprints=json.loads((job/'critical-fingerprints.json').read_text())
 profile_names=[n for n in fingerprints if n.startswith(('etc/NetworkManager/system-connections/','etc/bmi30-network/'))]
 for name in profile_names:
  item=fingerprints[name];path=P('/')/name
  if item['kind']=='file':require(sha(path)==item['sha256'],'Own network profile changed after copy')
  elif item['kind']=='symlink':require(path.is_symlink() and str(path.readlink())==item['target'],'Own network profile symlink changed')
 for name,digest in result['network_source_hashes_at_final_sync'].items():
  require(sha(P('/')/name)==digest,'Network source changed after final copy')
 serial='333F35743032' if data['board']=='s07' else '3342355E3032'
 usb=[p.parent.name for p in P('/sys/bus/usb/devices').glob('*/serial') if p.read_text().strip()==serial]
 require(len(usb)==1,'Expected STM32 USB serial is not uniquely present')
 deadline=time.monotonic()+45;last_reason='Core did not become ready'
 while True:
  try:
   first=healthy_status(get('http://127.0.0.1:8765/api/status'),data);break
  except Exception as exc:
   last_reason=str(exc) if isinstance(exc,Refused) else type(exc).__name__
   require(time.monotonic()<deadline,'Startup check timed out: '+last_reason)
   time.sleep(2)
 time.sleep(2)
 second=healthy_status(get('http://127.0.0.1:8765/api/status'),data)
 deltas=verify_progress(first,second)
 net=get('http://127.0.0.1:8791/status')
 require(net.get('version')=='0.2.10' and net.get('status')=='online' and net.get('routing_tick_ok') is True and not net.get('stale'),'Network service is not healthy0.2.10')
 checked=net.get('checked_at');require(isinstance(checked,(int,float)) and 0<=time.time()-checked<=30,'Network status stale')
 local=net.get('local') or {}
 require(isinstance(local.get('internet'),dict) and local['internet'].get('ok') is True,'Network upstream is not online')
 service_states={unit:run('systemctl','is-active',unit) for unit in ('bmi30-core.service','bmi30-hotspot-info.service','bmi30-agent.service','NetworkManager.service','ssh.service','bmi30-network.service','bmi30-access.service')}
 require(all(v=='active' for v in service_states.values()),'Source service not active after copy')
 # Read consistency at the end; no setting restoration and no other operation is started.
 properties(data['unit']);require(not P('/run/bmi30-access/busy').exists(),'Another maintenance operation is active')
 final_config_info=verify_config(config,job,plan,result)
 require(final_config_info['config_semantic_sha256']==config_info['config_semantic_sha256'] and sha(env)==env_hash,'Source config or selection drifted during verification')
 return {'schema':1,'ok':True,'source_reverified':True,'source_only':True,'usb_boot_tested':False,'physical_sensitivity_verified':False,
  'board':data['board'],'host':plan['host'],'cm5':plan['cm5'],'boot_id':boot_id,'copy_job_id':plan['job_id'],
  'checked_at':time.time(),'provenance':data['provenance'],'source':{'root':plan['root'],'boot':plan['boot'],'root_partuuid':plan['root_partuuid'],'boot_partuuid':plan['boot_partuuid']},
  'usb_unmounted':True,'usb_serial':plan['target']['serial'],'stm32_usb_serial':serial,
  'copy_unit':unit,'maintenance_busy_released':True,'source_hashes':hashes,**final_config_info,'env_sha256':env_hash,
  'own_network_profiles_verified_count':len(profile_names),'network_source_hashes':result['network_source_hashes_at_final_sync'],
  'status_samples':[first,second],'progress':deltas,'service_states':service_states,
  'network':{k:net.get(k) for k in ('version','status','routing_tick_ok','checked_at')},
  'network_local':{k:local.get(k) for k in ('id','iface','uplink','internet','gateway','ssid','bssid','addresses','ap','clients','path','parent')},
  'limits':['Original eMMC only; USB remains unmounted and has not booted.','No physical detection sensitivity or absence of false alarms is asserted.']}
try:
 print(json.dumps(check(json.load(sys.stdin))))
except Exception as exc:
 trace=traceback.extract_tb(exc.__traceback__)
 debug={'function':trace[-1].name,'line':trace[-1].lineno} if trace else {}
 if isinstance(exc,KeyError):debug['missing_key']=str(exc.args[0])[:200]
 print(json.dumps({'ok':False,'source_reverified':False,'source_only':True,'checked_at':time.time(),'error_type':type(exc).__name__,'reason':str(exc) if isinstance(exc,Refused) else 'Read-only source check failed; inspect locally.','debug':debug}))
'''

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify(board):
    path = W / (board + '-source-runtime-verified.json')
    try:
        plan_path = W / (board + '-plan.json')
        result_path = W / (board + '-copy-result.json')
        start_path = W / (board + '-copy-start.json')
        plan = json.loads(plan_path.read_text(encoding='utf8'))
        result = json.loads(result_path.read_text(encoding='utf8'))
        start = json.loads(start_path.read_text(encoding='utf8'))
        if not (result.get('ok') is True and result.get('verified_copy') is True and result.get('safe_to_remove') is True):
            raise RuntimeError('Local copy result is not complete and verified')
        spec = importlib.util.spec_from_file_location('incident_source_check_route', R / '.tmp/m02-network-disappearance-20261104/incident_route.py')
        route = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(route)
        provenance = {'plan_sha256': digest(plan_path), 'copy_result_sha256': digest(result_path),
                      'copy_start_sha256': digest(start_path), 'checker_sha256': digest(Path(__file__).resolve())}
        report = route.remote(board, SOURCE, {'board': board, 'plan': plan, 'result': result,
                            'unit': start['unit'], 'provenance': provenance},
                              address='192.168.0.154' if board == 'm02' else None, timeout=85)
        report.update(board=board)
    except Exception as exc:
        report = {'schema': 1, 'ok': False, 'source_reverified': False, 'source_only': True,
                  'board': board, 'checked_at': time.time(), 'error_type': type(exc).__name__,
                  'reason': str(exc) if isinstance(exc, FileNotFoundError) else 'Source verification could not complete.'}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return {'board': board, 'ok': report.get('ok'), 'source_reverified': report.get('source_reverified'),
            'reason': report.get('reason'), 'path': str(path), 'sha256': digest(path)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', choices=('s07', 'm02', 'both'))
    args = parser.parse_args()
    boards = ('s07', 'm02') if args.board == 'both' else (args.board,)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(boards)) as pool:
        reports = list(pool.map(verify, boards))
    for report in reports: print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if all(r['ok'] for r in reports) else 1)

if __name__ == '__main__': main()
