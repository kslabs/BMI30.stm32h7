"""Immutable owner-event + group-push + portal WebSocket release."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from deploy import remote, SSH, CONFIG, FILES
from inspect_realtime import WORK
from build_display_release import HERE, ROOT, BUILDER, BUILD_PORTAL

VERSION='2026-10-05-1540'
NETWORK_RELEASE='2026-10-05-network-11'
BASE_VERSION='2026-10-05-1530'
LABEL='Immediate sensor changes and authenticated group WebSocket'
STREAM='host/usb_vendor/usb_stream.py'
OWNER='host/usb_vendor/owner_events.py'
ENGINE='host/BMI30.200.py.2026-09-30-1650'

def embed_js(js):return js.replace('\\','\\\\').replace('{','{{').replace('}','}}')

def patch_stream(source):
    marker='    def set_det_adc('
    start=source.index(marker)
    source=source[:start]+'''    def publish_det_adc(self, detadc1, detadc2):
        """Publish detector edges before a USB command can block or wait."""
        self._write_device_state_cache({"_host_only": True,
            "host_detection": {"detadc1": bool(detadc1), "detadc2": bool(detadc2), "at": time.time()}})

'''+source[start:]
    marker='        written_at = time.time()\n'
    assert source.count(marker)==1
    source=source.replace(marker,marker+'''        payload["cache_generation"] = generation
        publisher = getattr(self, "_group_event_publisher", None)
        if publisher is not None:
            payload["owner_event_boot"] = publisher.boot
''')
    marker='            self._device_state_cache_writer_thread = thread\n'
    assert source.count(marker)==1
    source=source.replace(marker,'''            try:
                from .owner_events import Publisher
                self._group_event_publisher = Publisher()
            except Exception:
                self._group_event_publisher = None
'''+marker)
    marker='''                if not was_dirty:
                    cv.notify()
            return True'''
    assert source.count(marker)==1
    source=source.replace(marker,'''                publisher = getattr(self, "_group_event_publisher", None)
                if publisher is not None:
                    # Only enqueue a small immutable projection; socket I/O
                    # and serialization belong to the separate event worker.
                    publisher.update(payload, self._device_state_cache_generation)
                if not was_dirty:
                    cv.notify()
            return True''')
    marker='''            thread = getattr(self, "_device_state_cache_writer_thread", None)

        # A partially constructed object'''
    assert source.count(marker)==1
    source=source.replace(marker,'''            thread = getattr(self, "_device_state_cache_writer_thread", None)
        publisher = getattr(self, "_group_event_publisher", None)
        if publisher is not None:
            publisher.close()

        # A partially constructed object''')
    ast.parse(source);return source

def patch_engine(source):
    if '# Accepted ADC result is queued before DC-adapt, capture, relay and LED work.' not in source:
        marker='\t\t\tif fire1:\n\t\t\t\tself._det_hold1 = True\n\t\t\t\tself._det_exceed1 = 0\n'
        assert source.count(marker)==2
        source=source.replace(marker,marker+'''\t\t\t# Accepted ADC result is queued before DC-adapt, capture, relay and LED work.
\t\t\ttry:
\t\t\t\tstream = self.stream
\t\t\t\tinhibited = bool(getattr(self, "_recovery_outputs_inhibited", False))
\t\t\t\tif stream is not None and hasattr(stream, "publish_det_adc"):
\t\t\t\t\tstream.publish_det_adc(bool(self._det_output_active(0)) and not inhibited,
\t\t\t\t\t\tbool(self._det_output_active(1)) and not inhibited)
\t\t\texcept Exception:
\t\t\t\tpass
''')
    # Publish before ALL output work, including optic-gate reads and LED/relay I/O.
    marker='\t\tactive1 = bool(self._det_output_active(1, now))\n'
    early='''\t\t# Fast ADC indication precedes every STM32-dependent output operation.
\t\ttry:
\t\t\tstream = self.stream
\t\t\tinhibited = bool(getattr(self, "_recovery_outputs_inhibited", False))
\t\t\tif stream is not None and hasattr(stream, "publish_det_adc"):
\t\t\t\tstream.publish_det_adc(active0 and not inhibited, active1 and not inhibited)
\t\texcept Exception:
\t\t\tpass
'''
    if '# Fast ADC indication precedes every STM32-dependent output operation.' not in source:
        assert source.count(marker)==1;source=source.replace(marker,marker+early)
        marker='\tdef _rt_stop_detection_outputs(self):\n'
        assert source.count(marker)==1
        source=source.replace(marker,marker+'''\t\ttry:
\t\t\tstream = self.stream
\t\t\tif stream is not None and hasattr(stream, "publish_det_adc"):
\t\t\t\tstream.publish_det_adc(False, False)
\t\texcept Exception:
\t\t\tpass
''')
    if '# The fast lane publishes the detector result before any STM32 write.' in source:
        ast.parse(source);return source
    marker='\t\t\tbits = (1 if bool(detadc1) else 0) | (2 if bool(detadc2) else 0)'
    assert source.count(marker)==1
    source=source.replace(marker,'''\t\t\t# The fast lane publishes the detector result before any STM32 write.
\t\t\ttry:
\t\t\t\tif hasattr(stream, "publish_det_adc"):
\t\t\t\t\tstream.publish_det_adc(detadc1, detadc2)
\t\t\texcept Exception:
\t\t\t\tpass
'''+marker)
    ast.parse(source);return source

def patch_portal(source):
    start=source.index('// The RS485 builder remains the fallback.')
    end=source.index('    function _groupRebuildColumns(devices)',start)
    source=source[:start]+embed_js((HERE/'group_ui.js').read_text(encoding='utf-8'))+'\n'+source[end:]
    marker='    function _pollSensors() {{'
    assert source.count(marker)==1
    source=source.replace(marker,embed_js((HERE/'live_ui.js').read_text(encoding='utf-8'))+'\n'+marker+'''
      if (groupLiveHealthy() && !groupLiveFallback() && Date.now()-groupLiveHTTPAt < 5000) {{ return; }}
      groupLiveHTTPAt=Date.now();''')
    marker='    function _updateDeviceSensors(device) {{'
    assert source.count(marker)==1
    source=source.replace(marker,marker+'''
      groupLiveDevice=device;
      if (groupLiveHealthy() && groupLiveSnapshot) {{ device.bmi30_group=groupLiveSnapshot; }}''')
    marker='<div class="group-head-actions">'
    assert source.count(marker)==1
    source=source.replace(marker,marker+'\n                <span class="group-update" data-group-live-status>Connecting…</span>')
    marker='        if path == "/api/sensors":'
    assert source.count(marker)==1
    source=source.replace(marker,'''        if path == "/api/group/live":
            if not send_body:
                self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            try:
                import sys
                if "/opt/bmi30-network" not in sys.path:
                    sys.path.insert(0, "/opt/bmi30-network")
                from realtime import serve_portal
                serve_portal(self)
            except ImportError:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return

'''+marker)
    marker='class HotspotInfoHandler(BaseHTTPRequestHandler):'
    assert source.count(marker)==1
    source=source.replace(marker,'''_PORTAL_ORIGINAL_URLOPEN = urlopen

def urlopen(request, *args, **kwargs):
    response = _PORTAL_ORIGINAL_URLOPEN(request, *args, **kwargs)
    # A completed core command wakes publication immediately; failures retain
    # the normal periodic reconciliation. No USB is opened from the portal.
    if isinstance(request, Request) and request.get_method() == "POST" and request.full_url == CORE_SERVICE_URL.rstrip("/") + "/api/command":
        try:
            import sys
            if "/opt/bmi30-network" not in sys.path:
                sys.path.insert(0, "/opt/bmi30-network")
            from realtime import notify_refresh
            notify_refresh()
        except ImportError:
            pass
    return response

'''+marker)
    ast.parse(source);return source

def candidates():
    baseline=WORK.parent/'group-display-20261005'
    portal=patch_portal((baseline/'portal-led-final.py').read_text(encoding='utf-8')).encode()
    stream=patch_stream((baseline/'decoder.py').read_text(encoding='utf-8')).encode()
    (WORK/'portal-candidate.py').write_bytes(portal);(WORK/'usb-stream-candidate.py').write_bytes(stream)
    engine=patch_engine((WORK/'engine-before.py').read_text(encoding='utf-8')).encode()
    (WORK/'engine-candidate.py').write_bytes(engine)
    return portal,stream,engine

def main():
    portal,stream,engine=candidates()
    script=BUILD_PORTAL.replace('fleet-portal-20261002','group-realtime-20261005')
    script=script.replace("src=d/'host/bmi30_split_bundles/2026-09-30-1650/project'", "src=d/'host/bmi30_split_bundles/"+BASE_VERSION+"/project'")
    script=script.replace("(stage/'hotspot_info_server.py').write_bytes(portal)",
      "(stage/'hotspot_info_server.py').write_bytes(portal)\n(stage/'"+STREAM+"').write_bytes(base64.b64decode(p['stream']))\n(stage/'"+OWNER+"').write_bytes(base64.b64decode(p['owner']))\n(stage/'"+ENGINE+"').write_bytes(base64.b64decode(p['engine']))")
    script=script.replace('Portal TLS client isolation and timeouts',LABEL)
    script=script.replace('Based on 2026-09-30-1650; portal-only fix; acquisition bytes unchanged',
        'Based on '+BASE_VERSION+'; detector result push before USB writes; detection rules and STM32 unchanged')
    info=json.loads(remote(BUILDER,script,{'host':BUILDER,'version':VERSION,'portal':base64.b64encode(portal).decode(),
        'stream':base64.b64encode(stream).decode(),'engine':base64.b64encode(engine).decode(),
        'owner':base64.b64encode((HERE/'owner_events.py').read_bytes()).decode()},timeout=110))
    dest=ROOT/VERSION;dest.mkdir(exist_ok=False)
    name='BMI30-RPi-'+VERSION+'.tar.gz'
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',
        BUILDER+':/home/techaid/Documents/.tmp/group-realtime-20261005/'+name,str(dest/name)],check=True)
    assert hashlib.sha256((dest/name).read_bytes()).hexdigest()==info['sha256']
    (dest/'release.json').write_text(json.dumps(info,indent=2)+'\n');(dest/'SHA256SUMS').write_text(info['sha256']+'  '+name+'\n')
    (WORK/'release.json').write_text(json.dumps(info,indent=2))
    net=ROOT/NETWORK_RELEASE;net.mkdir(exist_ok=False)
    names=[*FILES,'test_stability.py','group_ui.js','test_group_ui.js','test_live_ui.js','portal_help.js','owner_events.py','realtime.py','live_ui.js','test_realtime.py']
    manifest={'version':'0.1.11','release_id':NETWORK_RELEASE,'app_bundle':VERSION,'scope':LABEL,
        'hashes':{n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names}}
    (net/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive=net/('BMI30-Network-'+NETWORK_RELEASE+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tf:
        for n in names:tf.add(HERE/n,arcname=NETWORK_RELEASE+'/'+n)
        tf.add(net/'release.json',arcname=NETWORK_RELEASE+'/release.json')
    (net/'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(json.dumps({'bundle':VERSION,'network':manifest['version'],'release':info['release']}))

def build_network_only():
    net=ROOT/NETWORK_RELEASE;net.mkdir(exist_ok=False)
    names=[*FILES,'test_stability.py','group_ui.js','test_group_ui.js','test_live_ui.js','portal_help.js','owner_events.py','realtime.py','live_ui.js','test_realtime.py']
    manifest={'version':'0.1.11','release_id':NETWORK_RELEASE,'app_bundle':VERSION,'scope':LABEL,
        'hashes':{n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names}}
    (net/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive=net/('BMI30-Network-'+NETWORK_RELEASE+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tf:
        for n in names:tf.add(HERE/n,arcname=NETWORK_RELEASE+'/'+n)
        tf.add(net/'release.json',arcname=NETWORK_RELEASE+'/release.json')
    (net/'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(json.dumps({'network':manifest['version'],'bundle':VERSION}))

if __name__=='__main__':
    if '--network-only' in sys.argv:build_network_only()
    else:main()
