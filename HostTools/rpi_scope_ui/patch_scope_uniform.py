"""Uniform display timing and a session-protected portal waveform stream."""
import ast
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent

def once(source,old,new):
    assert source.count(old)==1,old[:100]
    return source.replace(old,new)

def patch_core(source):
    before=ast.parse(source)
    start=source.index('// Independent, bounded display requests.')
    end=source.index('\n\tfunction playerUpdateVoiceUi()',start)
    source=source[:start]+(HERE/'scope_uniform.js').read_text(encoding='utf-8')+source[end:]
    source=once(source,"paintAutoFreezeSources()}updateFreezeButton()}","paintAutoFreezeSources()}updateFreezeButton();scopeLiveConnect()}")
    source=once(source,"t:f&&f.last_frame_t||0,ch:f&&f._channels||''", "t:f&&f.last_frame_t||0,revision:f&&f._scopeRevision||0,ch:f&&f._channels||''")
    source=once(source,"last_frame_t:Number(f.last_frame_t)||0,_channels:", "last_frame_t:Number(f.last_frame_t)||0,_channel_frame_t0:f._channel_frame_t0||null,_channel_frame_t1:f._channel_frame_t1||null,_channels:")
    source=once(source,"if(document.hidden){cancelScopeFrames();if(currentRefreshController)","if(document.hidden){cancelScopeFrames();scopeLiveStop();if(currentRefreshController)")
    # A frame being buffered must also advance HTTP's since token.
    source=once(source,"Number(lastFrame.last_frame_t):0),url='/api/frame.bin?",
                "Math.max(Number(lastFrame.last_frame_t),liveSource):liveSource),url='/api/frame.bin?")
    diag=(HERE/'scope_diagnostics.js').read_text(encoding='utf-8').replace('\\','\\\\')
    source=once(source,'</body>', '<script>\n'+diag+'\n</script>\n</body>')
    after=ast.parse(source)
    def runtime(tree):
        return ast.dump(ast.Module(body=[n for n in tree.body if not (isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='SERVICE_HTML' for t in n.targets))],type_ignores=[]))
    assert runtime(before)==runtime(after),'Python acquisition code changed'
    return source

def patch_portal(source):
    old='        if path == "/api/group/live":'
    new='''        if path == "/api/scope/live":
            if not send_body:
                self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            try:
                import sys
                if "/opt/bmi30-network" not in sys.path:
                    sys.path.insert(0, "/opt/bmi30-network")
                from scope_live import serve_scope
                serve_scope(self)
            except ImportError:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return

'''+old
    result=once(source,old,new)
    old='    def do_POST(self) -> None:\n        path = self.path.split("?", 1)[0]'
    new=old+'''
        if path in ("/api/scope/offer", "/api/scope/lease"):
            try:
                import sys
                if "/opt/bmi30-network" not in sys.path:
                    sys.path.insert(0, "/opt/bmi30-network")
                from scope_rtc_proxy import signaling
                signaling(self, path.rsplit("/", 1)[-1])
            except ImportError:
                self._send_json_response({"ok": False, "error": "Display transport unavailable"}, 503)
            return
'''
    result=once(result,old,new);ast.parse(result);return result

if __name__=='__main__':
    before,after=map(Path,sys.argv[2:])
    after.write_text({'core':patch_core,'portal':patch_portal}[sys.argv[1]](before.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
