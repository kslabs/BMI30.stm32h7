"""Display the exact reference-noise estimate used to set the detection threshold."""
import ast
from pathlib import Path
import re


def once(source, old, new):
    assert source.count(old) == 1, (old[:100], source.count(old))
    return source.replace(old, new)


def patch_engine(source):
    anchor = "\t\treview_frame['decision'] = {"
    source = once(source, anchor,
        "\t\t# Passive scalar context for the reference antenna's frozen noise line.\n"
        "\t\t# Do not substitute live noise when a saved event is displayed later.\n"
        "\t\treview_frame['noise_levels'] = tuple(\n"
        "\t\t\tfloat(getattr(self, f'_det_noise_avg{peer}', 0.) or 0.) for peer in (0, 1))\n"
        + anchor)
    ast.parse(source)
    return source


def patch_core(source, version):
    source = once(source,
        "'synchronized': bool(anchor.get('synchronized'))}",
        "'synchronized': bool(anchor.get('synchronized')),\n"
        "                    'noise_avg': (anchor.get('noise_levels') or (None, None))[ch],\n"
        "                    'event_timestamp': (anchor.get('snapshot') or {}).get('timestamp')}")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n,ast.Assign)
                and any(getattr(t,'id','')=='SERVICE_HTML' for t in n.targets))
    page = ast.literal_eval(node.value)
    anchor = 'function detectorPlotSnapshot(channel)'
    page = once(page, anchor, Path(__file__).with_name('scope_noise_line.js').read_text(encoding='utf8')+'\n'+anchor)
    page = once(page, '\t\tlastFrame=f||null;', '\t\tcapturePlotNoise(f);lastFrame=f||null;')
    page = once(page,
        "snap.context_frames+'/'+snap.context_required,tip:snap.synchronized?'Measured reference waveforms from the same source frames. No asynchronous detector decision is attached.':\"Reference antenna captured with this detector request. The source frames may differ from the firing antenna; no detection is implied.\"}];",
        "snap.context_frames+'/'+snap.context_required,tip:snap.synchronized?'Measured reference waveforms from the same source frames. No asynchronous detector decision is attached.':\"Reference antenna captured with this detector request. The source frames may differ from the firing antenna; no detection is implied.\"}].concat(detectorEventTimeParts(snap));")
    page = once(page, 'let parts=[],rawLevel=Number(snap.level)', 'let parts=detectorEventTimeParts(snap),rawLevel=Number(snap.level)')
    page = once(page, 'content:" AUTO FIRE";', 'display:none;content:"";')
    page = once(page, 'parts.push({text:kind,className:snap.fired?', 'if(!snap.fired)parts.push({text:kind,className:snap.fired?')
    for name in ['detectorPlotThreshold','detectorPlotThresholdLabel']:
        start = page.index('function '+name+'(')
        end = page.index('\n',start)
        page = page[:start]+page[start:end].replace('detectorPlotSnapshot(channel)','detectorPlotLevelSnapshot(channel)')+page[end:]
    page = once(page,
        'function drawPlot(canvas,empty,xs,a,b,prod,c1,c2,c3,win,markers,frameMark,thresholdLevel,thresholdLabel,peakSample,confirmationProducts,peakValues,finalPeak)',
        'function drawPlot(canvas,empty,xs,a,b,prod,c1,c2,c3,win,markers,frameMark,thresholdLevel,thresholdLabel,peakSample,confirmationProducts,peakValues,finalPeak,noiseLine)')
    page = once(page,
        "drawLevelLine(ctx,thresholdLevel,'rgba(255,79,216,.95)',size.w,plotH,size.d,thresholdLabel);",
        "drawLevelLine(ctx,thresholdLevel,'rgba(255,79,216,.95)',size.w,plotH,size.d,thresholdLabel);"
        "if(noiseLine)drawLevelLine(ctx,noiseLine.level,'#76e4a4',size.w,plotH,size.d,noiseLine.label,true,'left');")
    page = once(page,
        'function drawLevelLine(ctx,value,color,w,plotH,d,label){let v=Number(value);if(!Number.isFinite(v)||v<=0)return;',
        "function drawLevelLine(ctx,value,color,w,plotH,d,label,allowZero,labelSide){if(value===null||value===undefined||value==='')return;let v=Number(value);if(!Number.isFinite(v)||v<0||(!allowZero&&v===0))return;")
    page = once(page,
        'x=Math.max(2*d,w-boxW-6*d),yy=',
        "x=labelSide==='left'?6*d:Math.max(2*d,w-boxW-6*d),yy=")
    page = once(page,
        "ctx.font='800 '+fs+'px ui-monospace,SFMono-Regular,Consolas,monospace';let tw=",
        "ctx.font='800 '+fs+'px ui-monospace,SFMono-Regular,Consolas,monospace';ctx.textAlign='left';ctx.textBaseline='alphabetic';let tw=")
    page = once(page, "'DETECT '+Math.round(minimum)+'..'+Math.round(maximum)", "Math.round(minimum)+'..'+Math.round(maximum)")
    for ch, channel in [(0,'upper'),(1,'lower')]:
        page = once(page, f'Confirmations,f.confirmation_peak_values{ch},f.prod_peak{ch});',
                    f"Confirmations,f.confirmation_peak_values{ch},f.prod_peak{ch},(showProd&&!playerState.playback)?detectorPlotNoise('{channel}',f.prod_norm{ch},f):null);")
    # The fifth swatch follows the threshold and is hidden outside product views.
    old = '<span><i class="sw" style="background:#ff4fd8"></i>thr</span></div>'
    assert page.count(old) == 2
    page = page.replace(old, old.replace('</div>','<span><i class="sw" style="background:#76e4a4"></i>noise</span></div>'))
    for channel in ['upper','lower']:
        page = once(page,
            f"('det ×'+{channel}Confirmations.length):'det','thr']:['1','0','det']",
            f"('det ×'+{channel}Confirmations.length):'det','thr'].concat(playerState.playback?[]:['noise']):['1','0','det']")
    start = page.index('\tfunction setScopeLegend(')
    end = page.index('\n\tfunction applyModeVisibility(',start)
    page = page[:start]+'''\tfunction setScopeLegend(cardId,labels){
        let card=byId(cardId),spans=card?card.querySelectorAll('.legend span'):[];
        spans.forEach(function(span,idx){
            let visible=idx<(labels||[]).length;span.style.display=visible?'':'none';if(!visible)return;
            let sw=span.querySelector('.sw');span.textContent=String(labels[idx]||'');
            if(sw)span.insertBefore(sw,span.firstChild);
        });
    }'''+page[end:]
    lines = source.splitlines(keepends=True)
    result = ''.join(lines[:node.lineno-1])+'SERVICE_HTML = '+repr(page)+'\n'+''.join(lines[node.end_lineno:])
    result, count = re.subn(r'(DEFAULT_ENGINE_FILE\s*=\s*HOST_DIR / )[\'"][^\'\"]+[\'\"]',
        lambda m:m.group(1)+repr('BMI30.200.py.'+version),result)
    assert count == 1
    ast.parse(result)
    return result,page


if __name__=='__main__':
    import sys
    folder,version = Path(sys.argv[1]),sys.argv[2]
    candidate = folder/'candidate';candidate.mkdir(exist_ok=True)
    core,page = patch_core((folder/'core.py').read_text(encoding='utf8'),version)
    (candidate/'core.py').write_text(core,encoding='utf8',newline='\n')
    (candidate/'scope.html').write_text(page,encoding='utf8',newline='\n')
    (candidate/'engine.py').write_text(patch_engine((folder/'engine.py').read_text(encoding='utf8')),encoding='utf8',newline='\n')
    (candidate/'portal.py').write_bytes((folder/'portal.py').read_bytes())
