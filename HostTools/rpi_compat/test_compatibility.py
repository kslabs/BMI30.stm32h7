"""Exercise the patched release with older and newer simulated USB peers."""
import ast,contextlib,errno,importlib.util,io,json,pathlib,queue,shutil,struct,sys,threading,time,types,unittest,uuid
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[2]
WORK=ROOT/'.tmp/stm32-1253-compat-20261023'
CANDIDATE=WORK/'candidate'
package=types.ModuleType('usb_vendor');package.__path__=[str(CANDIDATE/'host/usb_vendor')]
sys.modules['usb_vendor']=package
from usb_vendor.capabilities import for_stream,FeatureUnavailable
from usb_vendor.tx_phase import TxPhaseControl
from usb_vendor.relay_control import RelayService


@contextlib.contextmanager
def directory():
 p=WORK/('test-'+uuid.uuid4().hex);p.mkdir()
 try:yield p
 finally:shutil.rmtree(p)


class Stream:
 def __init__(self,reply):
  self.dev=types.SimpleNamespace(serial_number='own-board',write=self.write)
  self.connected_t=10;self._running=True;self.disconnected=False
  self._ep0_lock=threading.RLock();self._ep_out_lock=threading.RLock()
  self.reply=reply;self.calls=[];self.writes=[]
 def _ep0_ctrl_transfer(self,*args,**kwargs):
  self.calls.append(args)
  if isinstance(self.reply,Exception):raise self.reply
  return self.reply
 def write(self,endpoint,packet,**kwargs):self.writes.append((endpoint,packet));return len(packet)


TXP=b'TXP1\x01\x00\x00\x00\x00\x07\x01\x00\x01\x01\x00\x00'


class CompatibilityTests(unittest.TestCase):
 def test_explicit_stall_is_cached_without_repeated_usb_requests(self):
  s=Stream(OSError(errno.EPIPE,'STALL'));control=TxPhaseControl(s)
  for _ in range(10):
   with self.assertRaises(FeatureUnavailable):control.read()
  self.assertEqual(len(s.calls),1)
  self.assertFalse(for_stream(s).supported('tx_phase'))
 def test_windows_libusb_pipe_is_also_optional(self):
  error=RuntimeError('PIPE');error.backend_error_code=-9
  s=Stream(error)
  with self.assertRaises(FeatureUnavailable):TxPhaseControl(s).read()
  self.assertFalse(for_stream(s).supported('tx_phase'))
 def test_timeout_disconnect_and_malformed_reply_remain_retryable(self):
  for error in (OSError(errno.ETIMEDOUT,'timeout'),OSError(errno.ENODEV,'gone'),b'broken'):
   s=Stream(error);control=TxPhaseControl(s)
   with self.assertRaises(RuntimeError):control.read()
   self.assertIsNone(for_stream(s).supported('tx_phase'))
   s.reply=TXP
   self.assertTrue(control.read()['available'])
   self.assertEqual(len(s.calls),2)
 def test_snapshot_does_not_probe_hardware(self):
  s=Stream(TXP);features=for_stream(s)
  self.assertEqual(features.snapshot()['relay']['supported'],None)
  self.assertFalse(s.calls)
 def test_reconnection_rechecks_capabilities_and_device_identity(self):
  s=Stream(OSError(errno.EPIPE,'STALL'));control=TxPhaseControl(s)
  with self.assertRaises(FeatureUnavailable):control.read()
  s.dev=types.SimpleNamespace(serial_number='replacement-board');s.reply=TXP
  self.assertEqual(control.read()['usb_serial'],'replacement-board')
  self.assertTrue(for_stream(s).supported('tx_phase'))
 def test_same_board_after_firmware_update_is_probed_again_on_reconnect(self):
  s=Stream(OSError(errno.EPIPE,'STALL'));control=TxPhaseControl(s)
  with self.assertRaises(FeatureUnavailable):control.read()
  s.connected_t+=1;s.reply=TXP
  self.assertTrue(control.read()['supported'])
  self.assertEqual(len(s.calls),2)
 def test_saved_missing_tx_feature_does_not_prevent_actual_start_command(self):
  source=(CANDIDATE/'host/usb_vendor/usb_stream.py').read_text(encoding='utf-8');tree=ast.parse(source)
  cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='USBStream')
  method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='send_cmd')
  scope={'time':time,'CMD_START_STREAM':0x20,'CMD_DEVICE_RESET':0x22,'EP_OUT':3}
  exec(compile(ast.Module(body=[method],type_ignores=[]),'actual-send-cmd','exec'),scope)
  with directory() as p:
   config=p/'config.json';config.write_text(json.dumps({'tx_phase_by_usb_serial':{'own-board':[1,1]},'other':42}))
   original=config.read_bytes();s=Stream(OSError(errno.EPIPE,'STALL'));s._tx_phase_config_path=config
   control=TxPhaseControl(s,config);s.tx_phase_control=lambda:control
   with contextlib.redirect_stdout(io.StringIO()):scope['send_cmd'](s,0x20,b'')
   self.assertEqual(s.writes,[(3,b'\x20')])
   self.assertEqual(config.read_bytes(),original)
   self.assertFalse(s.disconnected)
 def test_absent_tx_set_does_not_save_or_apply_preferences(self):
  with directory() as p:
   config=p/'config.json';config.write_text('{}');s=Stream(OSError(errno.EPIPE,'STALL'))
   with self.assertRaises(FeatureUnavailable):TxPhaseControl(s,config).set_phase(1,1)
   self.assertEqual(config.read_text(),'{}');self.assertEqual([c[0] for c in s.calls],[0xc0])
 def test_existing_tx_readback_still_returns_actual_state(self):
  s=Stream(TXP);state=TxPhaseControl(s).read()
  self.assertTrue(state['supported']);self.assertEqual(state['applied'],[0,0])
 def test_missing_relay_does_not_poll_or_send_commands(self):
  with directory() as p:
   s=Stream(OSError(errno.EPIPE,'STALL'));scope=types.SimpleNamespace(stream=s)
   relay=RelayService(scope,p/'relay.json',start=False)
   with self.assertRaises(FeatureUnavailable):relay.step()
   request={'params':{'enabled':True},'done':threading.Event(),'deadline':time.monotonic()+2,'result':None}
   relay._requests.put_nowait(request)
   for _ in range(20):relay.step()
   self.assertEqual(len(s.calls),1);self.assertTrue(request['done'].is_set())
   self.assertFalse(request['result']['ok']);self.assertFalse(relay.snapshot()['supported'])
   self.assertFalse((p/'relay.json').exists())
 def test_optional_failure_does_not_disable_other_features(self):
  s=Stream(OSError(errno.EPIPE,'STALL'))
  with self.assertRaises(FeatureUnavailable):TxPhaseControl(s).read()
  self.assertIsNone(for_stream(s).supported('relay'))
  self.assertIsNone(for_stream(s).supported('communication_recovery'))
 def test_missing_k1_request_is_cached_and_never_acknowledged(self):
  tree=ast.parse((CANDIDATE/'host/usb_vendor/usb_stream.py').read_text(encoding='utf-8'))
  cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='USBStream')
  nodes=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('get_communication_recovery','ack_communication_recovery')]
  parser=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='parse_communication_recovery')
  scope={'time':time,'struct':struct,'__package__':'usb_vendor'}
  exec(compile(ast.Module(body=[parser,*nodes],type_ignores=[]),'actual-comm-recovery','exec'),scope)
  s=Stream(OSError(errno.EPIPE,'STALL'))
  s.get_communication_recovery=types.MethodType(scope['get_communication_recovery'],s)
  for _ in range(5):self.assertIsNone(s.get_communication_recovery(force=True))
  self.assertFalse(scope['ack_communication_recovery'](s,1))
  self.assertEqual(len(s.calls),1)
  self.assertFalse(for_stream(s).supported('communication_recovery'))
 def test_core_api_reports_missing_tx_without_disabling_raspberry_rx_policy(self):
  core=ast.parse((CANDIDATE/'host/BMI30.001.py.2026-10-23-1317').read_text(encoding='utf-8'))
  method=next(n for n in ast.walk(core) if isinstance(n,ast.FunctionDef) and n.name=='tx_phase')
  scope={'Any':object};exec(compile(ast.Module(body=[method],type_ignores=[]),'actual-core-tx-api','exec'),scope)
  stream=Stream(OSError(errno.EPIPE,'STALL'));control=TxPhaseControl(stream);stream.tx_phase_control=lambda:control
  api=types.SimpleNamespace(scope=types.SimpleNamespace(stream=stream))
  rx=types.ModuleType('bmi30_phase_adaptation');rx.state=lambda s:{'enabled':False,'fixed_shifts':[12,12]}
  with patch.dict(sys.modules,{'bmi30_phase_adaptation':rx}):
   reply=scope['tx_phase'](api)
   self.assertTrue(reply['ok']);self.assertFalse(reply['supported']);self.assertFalse(reply['available'])
   self.assertEqual(reply['phase_adaptation']['fixed_shifts'],[12,12])
   reply=scope['tx_phase'](api,{'channel':1,'phase':1})
   self.assertFalse(reply['ok']);self.assertFalse(reply['persisted'])


if __name__=='__main__':unittest.main()
