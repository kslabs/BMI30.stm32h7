"""Separate waveform rendering from status updates in the active websplit core."""
import ast
from pathlib import Path
import sys


def replace_once(source, old, new):
    assert source.count(old) == 1, old[:120]
    return source.replace(old, new)


def patch_core(source):
    tree = ast.parse(source)
    before = {node.name: ast.dump(node) for node in tree.body
              if not isinstance(node, (ast.Assign, ast.AnnAssign)) and hasattr(node, 'name')}
    start = source.index('function refresh(){')
    end = source.index('\n', start)
    source = source[:start] + (Path(__file__).with_name('scope_refresh.js').read_text(encoding='utf-8')) + source[end:]
    source = replace_once(source, 'function setFreezeMode(mode){freezeMode=',
                          'function setFreezeMode(mode){cancelScopeFrames();freezeMode=')
    source = replace_once(source, 'if(modeChanged&&!playerState.playback){frameRedrawNeeded=true;',
                          'if(modeChanged&&!playerState.playback){cancelScopeFrames();frameRedrawNeeded=true;')
    start = source.index('function stopDisplayAndOpenPortal(ev){')
    end = source.index('\n', start)
    source = source[:start] + "function stopDisplayAndOpenPortal(ev){stopScopeDisplay();setFreezeMode('freeze');updateFreezeButton();setText('serviceState','paused');if(ev&&ev.preventDefault)ev.preventDefault();let target=(ev&&ev.currentTarget&&ev.currentTarget.href)?ev.currentTarget.href:'/portal';window.location.assign(target)}" + source[end:]
    source = replace_once(source, 'refreshTimer=setInterval(refresh,500);',
                          'refreshTimer=setInterval(refreshStatus,SCOPE_STATUS_INTERVAL_MS);frameTimer=setInterval(refreshFrames,SCOPE_FRAME_INTERVAL_MS);')
    source = replace_once(source, "document.addEventListener('visibilitychange',function(){if(!document.hidden)refresh()});",
                          "document.addEventListener('visibilitychange',function(){if(document.hidden){cancelScopeFrames();if(currentRefreshController)currentRefreshController.abort()}else refresh()});\nwindow.addEventListener('pagehide',stopScopeDisplay);")
    # Layout and legends change with the mode or confirmation view, not every sample.
    old = "\t\tapplyModeVisibility(mode);\n\t\tsetScopeLegend('upperCard'"
    new = "\t\tlet layoutKey=stableKey({mode:mode,v7:showV7,prod:showProd,u:upperConfirmations.length,l:lowerConfirmations.length});\n\t\tif(force||layoutKey!==lastScopeLayoutKey){lastScopeLayoutKey=layoutKey;applyModeVisibility(mode);\n\t\tsetScopeLegend('upperCard'"
    source = replace_once(source, old, new)
    source = replace_once(source, "\t\tif(!force&&key===lastRenderedFrameKey)return;",
                          "\t\t}\n\t\tif(!force&&key===lastRenderedFrameKey)return;")
    source = replace_once(source, '\t\tupdateNormReadouts(showProd&&playerState.playback?',
                          '\t\tlet readoutNow=performance.now();\n\t\tif(force||playerState.playback||readoutNow-lastFrameReadoutAt>=SCOPE_STATUS_INTERVAL_MS){lastFrameReadoutAt=readoutNow;updateNormReadouts(showProd&&playerState.playback?')
    source = replace_once(source, ':f,showProd,mode);\n\t\tif(!f||!f.available)',
                          ':f,showProd,mode);}\n\t\tif(!f||!f.available)')
    after_tree = ast.parse(source)
    after = {node.name: ast.dump(node) for node in after_tree.body
             if not isinstance(node, (ast.Assign, ast.AnnAssign)) and hasattr(node, 'name')}
    assert before == after, 'Python runtime code changed'
    # Only the web page constant may differ, including all other module constants.
    def without_html(t):
        return ast.dump(ast.Module(body=[n for n in t.body if not
            (isinstance(n, ast.Assign) and any(isinstance(k, ast.Name) and k.id == 'SERVICE_HTML' for k in n.targets))], type_ignores=[]))
    assert without_html(tree) == without_html(after_tree)
    return source


if __name__ == '__main__':
    before_path, after_path = map(Path, sys.argv[1:])
    after_path.write_text(patch_core(before_path.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
