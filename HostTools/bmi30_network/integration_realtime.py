"""Exercise isolated Unix -> signed peer TCP -> portal WebSocket, without USB."""
import base64,json
from pathlib import Path
from deploy import remote,FILES,HERE
from inspect_realtime import WORK
from build_realtime_release import candidates

SCRIPT=r'''
import ast,base64,contextlib,io,json,os,pathlib,runpy,socket,struct,subprocess,sys,tempfile,threading,time,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import Mock
p=json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix='bmi30-realtime-test-',dir='/dev/shm') as directory:
 d=pathlib.Path(directory)
 for name,raw in p['files'].items():(d/name).write_bytes(base64.b64decode(raw))
 (d/'usb_vendor').mkdir();(d/'usb_vendor/__init__.py').write_text('')
 (d/'usb_vendor/usb_stream.py').write_bytes(base64.b64decode(p['stream']))
 (d/'usb_vendor/owner_events.py').write_bytes((d/'owner_events.py').read_bytes())
 (d/'engine.py').write_bytes(base64.b64decode(p['engine']))
 env=dict(os.environ,BMI30_TEST_ENGINE=str(d/'engine.py'))
 r=subprocess.run(['python3','-m','unittest','test_network','test_group','test_stability','test_realtime'],cwd=d,env=env,capture_output=True,text=True)
 assert r.returncode==0,r.stderr
 sys.path.insert(0,str(d));import group,service,realtime
 from protocol import Protocol
 from usb_vendor.usb_stream import USBStream
 key='17'*32
 def agent(ident):
  a=Mock(config={'id':ident,'state_dir':str(d),'group_state':str(d/(ident+'.json'))},lock=threading.RLock(),protocol=Protocol(key),local={},peers={})
  a.group=group.Group(a);return a
 origin=agent('BMI30-TEST-A');receiver=agent('BMI30-TEST-B')
 state={'available':True,'sensors':{'detadc1':False,'detadc2':False},'optic':{},'kind':'group-v1',
        'id':origin.config['id'],'boot':origin.group.boot,'seq':1,'issued':time.time()}
 origin.group.records[origin.config['id']]=origin.protocol.sign(state);origin.group.seq=1;origin.group.publish()
 # Use the real bounded HTTP stream server on an ephemeral fixture port.
 reserved=socket.socket();reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1];reserved.close();service.PORT=port
 threading.Thread(target=service.serve,args=(origin,),daemon=True).start()
 actual=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 class Opener:
  def open(self,request,*a,**kw):
   if isinstance(request,urllib.request.Request):
    request=urllib.request.Request(request.full_url.replace(':8791/',':'+str(port)+'/'),headers=dict(request.header_items()))
   return actual.open(request,*a,**kw)
 receiver.opener=Opener()
 stop=threading.Event();threading.Thread(target=receiver.group.peer_stream,args=('127.0.0.1',stop),daemon=True).start()
 # The portal endpoint is the production RFC6455 implementation with fixture auth.
 (d/'portal.py').write_bytes(base64.b64decode(p['portal']))
 namespace=runpy.run_path(str(d/'portal.py'),run_name='fixture_validation')
 class Handler(namespace['HotspotInfoHandler']):
  def _get_portal_session(self):return {'username':'fixture'}
  def log_message(self,*a):pass
 # A second streaming server exposes the receiving group's actual hub.
 reserved=socket.socket();reserved.bind(('127.0.0.1',0));localport=reserved.getsockname()[1];reserved.close();service.PORT=localport
 threading.Thread(target=service.serve,args=(receiver,),daemon=True).start()
 realtime.urlopen=lambda request,**kw:actual.open(urllib.request.Request(request.full_url.replace(':8791/',':'+str(localport)+'/'),headers=dict(request.header_items())),**kw)
 portal=ThreadingHTTPServer(('127.0.0.1',0),Handler);portal.daemon_threads=True
 threading.Thread(target=portal.serve_forever,daemon=True).start();webport=portal.server_port
 channel=socket.create_connection(('127.0.0.1',webport));channel.settimeout(3)
 host='127.0.0.1:'+str(webport)
 channel.sendall(('GET /api/group/live HTTP/1.1\r\nHost: '+host+'\r\nOrigin: http://'+host+'\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n').encode())
 reader=channel.makefile('rb');headers=[]
 while True:
  line=reader.readline();headers.append(line)
  if line==b'\r\n':break
 assert headers[0].startswith(b'HTTP/1.1 101'),headers
 def read_frame():
  a,b=reader.read(2);size=b&127
  if size==126:size=struct.unpack('!H',reader.read(2))[0]
  elif size==127:size=struct.unpack('!Q',reader.read(8))[0]
  raw=reader.read(size)
  return json.loads(raw) if a&15==1 else None
 initial=read_frame()
 unix=d/'owner.sock';threading.Thread(target=origin.group.owner_loop,args=(str(unix),),daemon=True).start()
 until=time.monotonic()+2
 while not unix.exists() and time.monotonic()<until:time.sleep(.005)
 stream=USBStream.__new__(USBStream);stream._device_state_path=lambda:str(d/'cache.json');stream._start_device_state_cache_writer()
 stream._group_event_publisher.path=str(unix)
 stream._write_device_state_cache({'sensors':{'local':{'node_id':12,'detadc1':False,'detadc2':False}}})
 time.sleep(.03)
 sent={};received=[]
 def edges():
  for i in range(20):
   value=bool((i+1)%2);sent[len(sent)+1]=time.monotonic()
   stream.publish_det_adc(value,False);time.sleep(.02)
 sender=threading.Thread(target=edges);sender.start()
 deadline=time.monotonic()+5;last_seq=0
 while len(received)<20 and time.monotonic()<deadline:
  item=read_frame()
  if not item:continue
  row=next((n for n in item['group']['nodes'] if n['id']=='BMI30-TEST-A'),None)
  if row and row.get('sensor_event_seq',0)>last_seq:
   seq=row['sensor_event_seq'];last_seq=seq
   if seq>=2:received.append({'on':row['sensors']['detadc1'],'ms':round((time.monotonic()-sent[seq-1])*1000,2)})
 sender.join();assert [e['on'] for e in received]==[bool((i+1)%2) for i in range(20)],received
 stream._stop_device_state_cache_writer();stop.set();reader.close();channel.close()
 print(json.dumps({'tests':r.stderr.strip().splitlines()[-3:],'edges':len(received),'period_ms':20,
  'max_ms':max(e['ms'] for e in received),'average_ms':round(sum(e['ms'] for e in received)/len(received),2),'received':received}),flush=True)
 # Daemon fixture servers exit with this process; no production port or flags changed.
 os._exit(0)
'''

if __name__=='__main__':
    portal,stream,engine=candidates()
    names=[n for n in FILES if n.endswith('.py')]+['test_stability.py','owner_events.py','realtime.py','test_realtime.py']
    result=remote('BMI30-7AA5CED27',SCRIPT,{'files':{n:base64.b64encode((HERE/n).read_bytes()).decode() for n in names},
        'portal':base64.b64encode(portal).decode(),'stream':base64.b64encode(stream).decode(),'engine':base64.b64encode(engine).decode()},timeout=45)
    (WORK/'integration.json').write_bytes(result);print(result.decode())
