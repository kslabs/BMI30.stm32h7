"""CPU-only validation of the exact post-copy status checker; no remote invocation."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

W=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('source_checker',W/'check_source_runtime.py')
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
tree=ast.parse(checker.SOURCE)
tree.body=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom,ast.FunctionDef,ast.ClassDef,ast.Assign))]
ns={};exec(compile(tree,'<actual-source-checker-library>','exec'),ns)

class Tests(unittest.TestCase):
 def setUp(self):
  self.plan=json.loads((W/'s07-plan.json').read_text())
  self.result=json.loads((W/'s07-copy-result.json').read_text())
  frozen=self.result['final_runtime']
  channels={ch:{**frozen['channel_policy'][ch],'noise_valid':True,'noise_avg':42,'noise_samples':50} for ch in ('upper','lower')}
  self.status={'ok':True,'busy':False,'stale':False,'connection':{'connected':True,'disconnected':False,'last_frame_age_s':0.05},
   'service':{'version':'001','engine_source':self.plan['sources']['BMI30_ENGINE_SOURCE']['path'],'uptime_s':45},
   'detector':{'settings':copy.deepcopy(frozen['settings']),'channels':channels,'worker':{'alive':True,'mode':'process','results':100}},
   'mode':copy.deepcopy(frozen['mode']),'sync':{'device_responded':True,'device_id':7,'role':'slave'},
   'optimize':{'fast_adapt_active':False,'dc_autotune_active':False},'stream_stats':{'rx_cnt_ch0':1000,'rx_cnt_ch1':1000}}
  self.data={'board':'s07','plan':self.plan,'result':self.result}
 def validate(self):return ns['healthy_status'](self.status,self.data)
 def test_real_final_contract_accepts_fresh_status(self):self.assertEqual(self.validate()['mode'],self.result['final_runtime']['mode'])
 def test_not_old_inventory_settings(self):
  self.status['detector']['settings']['base_width_max']+=1
  with self.assertRaises(ns['Refused']):self.validate()
 def test_mode_change_rejected(self):
  self.status['mode']['rpi_avg_n']=1
  with self.assertRaises(ns['Refused']):self.validate()
 def test_channel_policy_change_rejected(self):
  self.status['detector']['channels']['lower']['threshold']+=0.5
  with self.assertRaises(ns['Refused']):self.validate()
 def test_warmup_lightning_and_both_noise_valid_required(self):
  for field in ('warmup','lightning','noise_upper','noise_lower'):
   with self.subTest(field=field):
    old=copy.deepcopy(self.status)
    if field=='warmup':self.status['service']['uptime_s']=29.9
    elif field=='lightning':self.status['optimize']['fast_adapt_active']=True
    else:self.status['detector']['channels'][field[6:]]['noise_valid']=False
    with self.assertRaises(ns['Refused']):self.validate()
    self.status=old
 def test_wrong_stm_role_or_dead_worker_rejected(self):
  for field in ('role','worker'):
   old=copy.deepcopy(self.status)
   if field=='role':self.status['sync']['device_id']=2
   else:self.status['detector']['worker']['alive']=False
   with self.assertRaises(ns['Refused']):self.validate()
   self.status=old
 def test_requires_both_adc_and_worker_progress(self):
  first=self.validate();second=copy.deepcopy(first)
  second['worker']['results']+=10
  for key in ('rx_cnt_ch0','rx_cnt_ch1'):second['stream_stats'][key]+=100
  self.assertEqual(ns['verify_progress'](first,second)['worker_results'],10)
  for field in ('worker','rx_cnt_ch0','rx_cnt_ch1'):
   altered=copy.deepcopy(second)
   if field=='worker':altered['worker']['results']=first['worker']['results']
   else:altered['stream_stats'][field]=first['stream_stats'][field]
   with self.assertRaises(ns['Refused']):ns['verify_progress'](first,altered)
 def test_remote_read_command_set_has_no_writes(self):
  literals={n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run' and n.args and isinstance(n.args[0],ast.Constant)}
  self.assertEqual(literals,{'findmnt','lsblk','systemctl'})
  self.assertNotIn('systemctl\',\'start',checker.SOURCE)
  self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('write_text','write_bytes','unlink','replace') for n in ast.walk(tree)))
 def test_lsblk_tree_requested_and_flat_or_tree_rows_keep_partitions(self):
  call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run'
            and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value=='lsblk')
  columns=next(n.value for n in call.args if isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith('NAME,'))
  self.assertIn('PATH',columns.split(','));self.assertIn('PARTUUID',columns.split(','))
  root={'name':'/dev/sda','path':'/dev/sda','type':'disk'}
  parts=[{'path':'/dev/sda1','type':'part','partuuid':'new-01'}, {'path':'/dev/sda2','type':'part','partuuid':'new-02'}]
  for rows in ([root,*parts],[{**root,'children':parts}]):
   lookup={n['path']:n for n in ns['flatten'](rows)}
   self.assertEqual(lookup['/dev/sda1']['partuuid'],'new-01')
   self.assertEqual(lookup['/dev/sda2']['partuuid'],'new-02')
 def test_network_internet_requires_actual_probe_dict_ok_true(self):
  require=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='require'
               and len(n.args)>1 and isinstance(n.args[1],ast.Constant) and n.args[1].value=='Network upstream is not online')
  expression=compile(ast.Expression(require.args[0]),'<actual-network-health-guard>','eval')
  self.assertTrue(eval(expression,ns,{'local':{'internet':{'ok':True,'latency_ms':5}}}))
  for value in ({'ok':False},{'ok':1},{},True,False,None,'online'):
   with self.subTest(value=value):self.assertFalse(eval(expression,ns,{'local':{'internet':value}}))

class ConfigTests(unittest.TestCase):
 def setUp(self):
  self.plan=json.loads((W/'s07-plan.json').read_text())
  self.result=json.loads((W/'s07-copy-result.json').read_text())
  self.original='{"label":"метка","count":1,"enabled":true,"array":[1,2]}'.encode('utf8')
  self.result['source_config_sha256_at_final_sync']=hashlib.sha256(self.original).hexdigest()
  self.formatted=' { "enabled": true, "array": [1, 2], "count": 1, "label": "метка" }\n'.encode('utf8')
  self.proof={'format':'bmi30-copy-config-semantic-proof-v1','job_id':self.plan['job_id'],'host':self.plan['host'],'cm5':self.plan['cm5'],
   'target_root_uuid':self.result['root_uuid'],'target_root_partuuid':self.result['root_partuuid'],'usb_serial':self.plan['target']['serial'],
   'snapshot_byte_sha256':self.result['source_config_sha256_at_final_sync'],'current_byte_sha256':hashlib.sha256(self.formatted).hexdigest(),
   'config_semantic_sha256':ns['canonical_config_hash'](self.original),'semantic_equal':True,'snapshot_matches_result':True}
 def test_exact_bytes_need_no_semantic_proof(self):
  value=ns['config_contract'](self.original,self.plan,self.result)
  self.assertFalse(value['config_semantic_proof_used'])
  self.assertEqual(value['config_sha256'],self.result['source_config_sha256_at_final_sync'])
 def test_format_and_key_order_allowed_only_with_bound_proof(self):
  with self.assertRaises(ns['Refused']):ns['config_contract'](self.formatted,self.plan,self.result)
  value=ns['config_contract'](self.formatted,self.plan,self.result,self.proof)
  self.assertTrue(value['config_semantic_proof_used'])
  self.assertNotEqual(value['config_sha256'],value['copied_config_sha256'])
  expected=json.dumps(json.loads(self.original),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')
  self.assertEqual(value['config_semantic_sha256'],hashlib.sha256(expected).hexdigest())
 def test_changed_values_types_and_list_order_are_rejected(self):
  for old,new in [('"count":1','"count":2'),('"count":1','"count":1.0'),('true','1'),('[1,2]','[2,1]')]:
   raw=self.original.decode('utf8').replace(old,new).encode('utf8')
   with self.subTest(change=new),self.assertRaises(ns['Refused']):ns['config_contract'](raw,self.plan,self.result,self.proof)
 def test_other_job_board_usb_uuid_or_snapshot_proof_rejected(self):
  for key in ('job_id','host','cm5','target_root_uuid','target_root_partuuid','usb_serial','snapshot_byte_sha256'):
   proof={**self.proof,key:'different'}
   with self.subTest(key=key),self.assertRaises(ns['Refused']):ns['config_contract'](self.formatted,self.plan,self.result,proof)
 def test_proof_equality_flags_and_algorithm_cannot_be_assumed(self):
  for key,value in [('semantic_equal',False),('snapshot_matches_result',False),('format','unknown'),('config_semantic_sha256','0'*64),('current_byte_sha256',None)]:
   with self.subTest(key=key),self.assertRaises(ns['Refused']):ns['config_contract'](self.formatted,self.plan,self.result,{**self.proof,key:value})
 def test_duplicate_and_nonfinite_json_rejected(self):
  for raw in (b'{"x":1,"x":1}',b'{"x":NaN}',b'{"x":Infinity}'):
   with self.subTest(raw=raw),self.assertRaises(ns['Refused']):ns['canonical_config_hash'](raw)
 def test_later_format_only_rewrite_still_matches_same_snapshot(self):
  later=json.dumps(json.loads(self.original),ensure_ascii=True,indent=4).encode('ascii')
  value=ns['config_contract'](later,self.plan,self.result,self.proof)
  self.assertEqual(value['config_semantic_sha256'],self.proof['config_semantic_sha256'])
  self.assertNotEqual(value['config_sha256'],self.proof['current_byte_sha256'])

if __name__=='__main__':unittest.main(verbosity=2)
