#!/usr/bin/env python3
"""Version activation state policy, without hardware/network/service operations.

New version: remove known Raspberry detector/device preferences; keep auth and
unknown settings. Same version or failed-transaction recovery: retain state.
Old recordings are verified into pending-server storage, never deleted/uploaded.
The caller must stop Core/Portal before prepare, and call rollback on failure.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,os,re,shutil,stat,tempfile
from pathlib import Path

PREFERENCE_KEYS=frozenset(('desired_profile','desired_freq','desired_mode','mode','avg_n','rpi_avg_n',
 'operation','tag_detection','tag_detection_updated_at','dc_config','dc_config_updated_at',
 'rx_phase_adaptation','smooth_mode','xcorr_norm_enabled','sound_enabled','sound_settings',
 'sound_settings_updated_at','non_addressable_led_enabled','lcd_role_overlay','group_led_patterns',
 'group_led_patterns_updated_at','bluetooth_audio','bluetooth_audio_updated_at','ui_sync_mode',
 'sync_mode','sync_mode_updated_at','base_width','base_width_enabled','base_width_limit',
 'noise_skip_parts','peak_index_min','peak_index_max'))
DETECTOR_SETTING_KEYS=frozenset(('auto0', 'auto1', 'auto_floor_u16', 'auto_slope', 'barkhausen_all_quarter_frac', 'barkhausen_frac', 'barkhausen_max_product_level', 'barkhausen_max_span', 'barkhausen_max_total_fraction', 'barkhausen_min_product_level', 'barkhausen_min_width', 'barkhausen_radius', 'base_width_enabled', 'base_width_max', 'burst_blank_s0', 'burst_blank_s1', 'burst_gate0', 'burst_gate1', 'burst_max_ratio0', 'burst_max_ratio1', 'common_mode_balance', 'common_mode_enabled', 'common_mode_min_snr', 'common_mode_radius', 'common_mode_require_pair', 'common_mode_residual_threshold', 'common_mode_same_lag', 'common_mode_strength', 'confirm0', 'confirm1', 'confirm_change_mode', 'confirm_growth_enabled', 'confirm_growth_ratio', 'confirm_phase_gate', 'confirm_sample_diversity', 'det_auto_floor_u16', 'det_auto_ratio', 'det_auto_slope', 'det_burst_blank_s0', 'det_burst_blank_s1', 'det_burst_gate0', 'det_burst_gate1', 'det_burst_max_ratio0', 'det_burst_max_ratio1', 'det_channel_enabled0', 'det_channel_enabled1', 'det_confirm_count0', 'det_confirm_count1', 'det_noise_down0', 'det_noise_down1', 'det_noise_unit0', 'det_noise_unit1', 'det_noise_up0', 'det_noise_up1', 'det_noise_window_s', 'det_ratio', 'det_ratio0', 'det_ratio1', 'det_ratio_high0', 'det_ratio_high1', 'det_ratio_noise_max_u16', 'det_threshold0', 'det_threshold1', 'det_threshold_auto0', 'det_threshold_auto1', 'enabled0', 'enabled1', 'filter_barkhausen', 'filter_casino', 'filter_microwire', 'filter_paper', 'impulse_guard_enabled', 'locality_max_outside_peaks', 'locality_outside_peak_frac', 'lower_burst_blank_s', 'lower_burst_gate', 'lower_burst_max_ratio', 'mark_gap', 'mark_gap_tol', 'mark_multi_max_humps', 'mark_second_frac', 'mark_valley_frac', 'mark_window_end_frac', 'mark_window_start_frac', 'noise_area_percent', 'noise_divisions', 'noise_down0', 'noise_down1', 'noise_guard_radius', 'noise_skip_parts', 'noise_unit0', 'noise_unit1', 'noise_up0', 'noise_up1', 'noise_window_s', 'peak_index_max', 'peak_index_min', 'peak_thirds_limit_enabled', 'peak_thirds_limit_type', 'phase_max_shift', 'phase_shift_penalty', 'ratio_noise_max_u16', 'smooth_mode', 'tag_detection', 'tag_filter_slot1', 'tag_filter_slot2', 'tag_filter_slot3', 'threshold0', 'threshold1', 'threshold_high0', 'threshold_high1', 'upper_burst_blank_s', 'upper_burst_gate', 'upper_burst_max_ratio'))
DEVICE_FILES=('dc_offset_samples.npz','dc_offset_samples.npz.bak','USB_config.json','plot_config.json')
# Explicit generic new-version seed; remaining detector knobs use production fallbacks.
FACTORY_CONFIG={'avg_n':8,'rpi_avg_n':12,'operation':{'avg_n':8,'rpi_avg_n':12}}
FACTORY_MODE={'sel':6}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def pairs(rows):
 result={}
 for key,value in rows:
  if key in result:raise ValueError('Duplicate JSON key')
  result[key]=value
 return result
def load(path):
 data=json.loads(Path(path).read_text(encoding='utf8'),object_pairs_hook=pairs,
                 parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Non-finite JSON')))
 if not isinstance(data,dict):raise ValueError('Expected JSON object')
 return data
def encoded(data):return(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf8')
def fresh_config(old):
 result={key:value for key,value in old.items()if key not in PREFERENCE_KEYS and key not in DETECTOR_SETTING_KEYS}
 result.update(copy.deepcopy(FACTORY_CONFIG));return result
def regular(path):
 if path.is_symlink() or (path.exists()and not path.is_file()):raise ValueError('State path must be a regular file')
def atomic(path,data,metadata=None):
 path=Path(path);regular(path);path.parent.mkdir(parents=True,exist_ok=True)
 fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
 try:
  with os.fdopen(fd,'wb')as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
  os.chmod(name,(metadata or {}).get('mode',0o640))
  if metadata and os.name=='posix':os.chown(name,metadata['uid'],metadata['gid'])
  os.replace(name,path)
  if os.name=='posix':
   directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
   try:os.fsync(directory)
   finally:os.close(directory)
 finally:
  if os.path.exists(name):os.unlink(name)
def meta(path):
 s=path.stat();return{'mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid}
def _inside(path,parent):
 if not path.resolve().is_relative_to(parent.resolve()):raise ValueError('Path outside runtime host')
def _copy_recordings(source,target,host):
 if not source.exists():return[]
 _inside(source,host);_inside(target,host)
 records=[]
 for src in sorted(source.rglob('*')):
  if src.is_symlink():raise ValueError('Recording symlink requires explicit migration')
  if not src.is_file():continue
  dst=target/src.relative_to(source);dst.parent.mkdir(parents=True,exist_ok=True)
  if dst.is_symlink():raise ValueError('Pending recording is a symlink')
  if dst.exists()and sha(dst)!=sha(src):raise ValueError('Pending recording conflict')
  if not dst.exists():shutil.copy2(src,dst)
  if src.stat().st_size!=dst.stat().st_size or sha(src)!=sha(dst):raise ValueError('Recording copy verification failed')
  records.append({'relative':str(src.relative_to(source)),'sha256':sha(src),'size':src.stat().st_size})
 return records

def prepare(*,stage_host,active_host,config,mode,transaction,host,current,target,recovery=False):
 stage_host,active_host,config,mode,transaction,host=map(Path,(stage_host,active_host,config,mode,transaction,host))
 if not re.fullmatch(r'[A-Za-z0-9._-]+',target)or(current and not re.fullmatch(r'[A-Za-z0-9._-]+',current)):raise ValueError('Invalid bundle identity')
 for item in (stage_host,active_host,config,mode):_inside(item,host)
 if stage_host.is_symlink()or not stage_host.is_dir():raise ValueError('Invalid staged runtime')
 transaction.mkdir(parents=True,exist_ok=True);os.chmod(transaction,0o700)
 marker=transaction/'runtime-policy.json'
 if marker.exists():raise ValueError('Policy transaction already exists')
 # Validate all JSON before making the first central-state change.
 for path in(config,mode):regular(path)
 old=load(config)if config.exists()else{}
 if mode.exists():load(mode)
 keep=bool(current==target or recovery)
 record={'format':'bmi30-runtime-policy-v1','current':current,'target':target,'preserved':keep,'files':[],'recordings':[]}
 for index,path in enumerate((config,mode)):
  entry={'path':str(path),'exists':path.exists()}
  if path.exists():
   entry.update(meta(path));entry['backup']=f'state-{index}.before';shutil.copy2(path,transaction/entry['backup'])
  record['files'].append(entry)
 atomic(marker,encoded(record),{'mode':0o600,'uid':os.getuid()if hasattr(os,'getuid')else 0,'gid':os.getgid()if hasattr(os,'getgid')else 0})
 try:
  if keep:
   for name in DEVICE_FILES:
    src=active_host/name;dst=stage_host/name
    if src.is_symlink():raise ValueError('Device state is a symlink')
    if src.is_file():shutil.copy2(src,dst)
   filters=active_host/'tag_response_filters'
   if filters.is_dir():
    if filters.is_symlink()or any(item.is_symlink()for item in filters.rglob('*')):raise ValueError('Filter state is a symlink')
    shutil.copytree(filters,stage_host/'tag_response_filters',dirs_exist_ok=True)
  else:
   # Private local holding area until an authenticated server confirms receipt.
   # Never link these historical captures into the new active recording list.
   pending=host/'pending_server_recordings'/(current or 'unselected')
   pending.mkdir(parents=True,exist_ok=True);os.chmod(pending.parent,0o700);os.chmod(pending,0o700)
   for label,src in(('runtime',active_host/'player_recordings'),('central',host/'player_recordings')):
    record['recordings'].append({'source':label,'files':_copy_recordings(src,pending/label,host)})
   receipt=pending/'migration-manifest.json'
   batches=load(receipt)if receipt.exists()else{'format':'bmi30-pending-server-recordings-v1','batches':[]}
   if batches.get('format')!='bmi30-pending-server-recordings-v1' or not isinstance(batches.get('batches'),list):raise ValueError('Invalid pending recording manifest')
   batch={'from_bundle':current,'to_bundle':target,'sources':record['recordings']}
   if batch not in batches['batches']:batches['batches'].append(batch)
   atomic(receipt,encoded(batches))
   for name in DEVICE_FILES:
    dst=stage_host/name
    if dst.exists()or dst.is_symlink():
     regular(dst);dst.unlink()
  atomic(config,config.read_bytes()if keep and config.exists()else encoded(old if keep else fresh_config(old)),meta(config)if config.exists()else None)
  atomic(mode,mode.read_bytes()if keep and mode.exists()else encoded({}if keep else FACTORY_MODE),meta(mode)if mode.exists()else None)
  for name,path in(('bmi30_config.json',config),('bmi30_sel.json',mode)):
   link=stage_host/name
   if link.is_symlink()or link.is_file():link.unlink()
   elif link.exists():raise ValueError('State destination is not a file')
   link.symlink_to(path)
  player=stage_host/'player_recordings'
  if player.exists()or player.is_symlink():
   _inside(player.parent,stage_host)
   if player.is_symlink():player.unlink()
   elif player.is_dir():shutil.rmtree(player)
   else:raise ValueError('Recording destination is not a directory')
  if keep:
   central=host/'player_recordings';central.mkdir(exist_ok=True)
   _copy_recordings(active_host/'player_recordings',central,host)
   player.symlink_to(central,target_is_directory=True)
  else:player.mkdir()
  atomic(marker,encoded(record),meta(marker));return record
 except Exception:
  rollback(transaction);raise

def rollback(transaction):
 transaction=Path(transaction);marker=transaction/'runtime-policy.json'
 if not marker.exists():return
 record=load(marker)
 if record.get('format')!='bmi30-runtime-policy-v1':raise ValueError('Invalid policy rollback')
 for entry in record['files']:
  target=Path(entry['path']);regular(target)
  if entry['exists']:atomic(target,(transaction/entry['backup']).read_bytes(),entry)
  else:target.unlink(missing_ok=True)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=('prepare','rollback'))
 for key in('stage-host','active-host','config','mode','transaction','host','current','target'):parser.add_argument('--'+key,default='')
 parser.add_argument('--recovery',action='store_true');args=vars(parser.parse_args());action=args.pop('action')
 if action=='rollback':rollback(args['transaction'])
 else:prepare(**{key.replace('-','_'):value for key,value in args.items()})
if __name__=='__main__':main()
