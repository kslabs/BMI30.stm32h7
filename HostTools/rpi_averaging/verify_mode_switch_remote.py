"""Exercise the reported raw-to-detection transition, restoring the saved mode."""
import hashlib,json,pathlib,struct,time,urllib.request

def api(path, payload=None):
    request=urllib.request.Request('http://127.0.0.1:8765/api/'+path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(request,timeout=5))

def frame():
    raw=urllib.request.urlopen('http://127.0.0.1:8765/api/frame.bin?max_points=200',timeout=5).read()
    size=struct.unpack('<I',raw[4:8])[0];header=json.loads(raw[8:8+size])
    offset=(8+size+1)&~1;count=header['count'];hashes={}
    for name in header['arrays']:
        hashes[name]=hashlib.sha256(raw[offset:offset+count*2]).hexdigest();offset+=count*2
    return {'last_frame_t':header['last_frame_t'],'hashes':hashes}

cfg=pathlib.Path('/home/techaid/Documents/host/bmi30_config.json')
before_cfg=cfg.read_bytes();before=api('status');mode=before['mode']['selected']
assert mode==6 and before['mode']['rpi_avg_n']==12
assert not before['player_recorder']['recording'] and not before['player_recorder']['playback_active']
stages=[]
try:
    assert api('command',{'action':'mode','params':{'idx':3}})['ok']
    time.sleep(8)
    raw_status=api('status');assert raw_status['mode']['selected']==3
    assert raw_status['mode']['base_buf_len']==600 and raw_status['connection']['last_frame_age_s']<2
    stages.append({'stage':'raw','mode':raw_status['mode'],'averaging':raw_status['averaging']})
finally:
    assert api('command',{'action':'mode','params':{'idx':mode}})['ok']

deadline=time.monotonic()+15
while time.monotonic()<deadline:
    first=api('status')
    if first['mode']['selected']==mode and first['averaging']['counts']==[12]*4 and first['detector']['worker']['results']>5:break
    time.sleep(.15)
else:raise RuntimeError('Averaging did not recover: '+str(first['averaging']))
f0=frame();time.sleep(6);after=api('status');f1=frame()
assert after['averaging']['counts']==[12]*4
assert after['averaging']['epoch']==first['averaging']['epoch'],(first['averaging'],after['averaging'])
assert after['detector']['worker']['results']>first['detector']['worker']['results']
assert after['connection']['last_frame_age_s']<2 and after['detector']['worker']['overflows']==0
assert f1['last_frame_t']>f0['last_frame_t']
assert f0['hashes']['prod0']!=f1['hashes']['prod0'] and f0['hashes']['prod1']!=f1['hashes']['prod1']
assert cfg.read_bytes()==before_cfg
errors={k:after['stream_stats'][k]-first['stream_stats'][k]
        for k in ['seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail']}
assert all(v==0 for v in errors.values()),errors
stages.append({'stage':'restored_detection','mode':after['mode'],'averaging':after['averaging'],
               'results_delta':after['detector']['worker']['results']-first['detector']['worker']['results'],
               'stream_errors_delta':errors,'frames':[f0,f1]})
print(json.dumps({'stages':stages,'config_preserved':True}))
