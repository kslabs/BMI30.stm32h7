"""Apply the base-width feature to the audited websplit source; fail on drift."""
from pathlib import Path
import ast
import re

HERE = Path(__file__).resolve().parent


def replace(source, old, new, count=1):
    actual = source.count(old)
    if actual != count:
        raise ValueError(f"Expected {count} anchors, found {actual}: {old[:100]!r}")
    return source.replace(old, new)


def after_line(source, anchor, addition, count=1):
    matches = [line for line in source.splitlines(True) if anchor in line]
    if len(matches) != count:
        raise ValueError(f"Expected {count} lines, got {len(matches)}: {anchor}")
    for line in dict.fromkeys(matches):
        indent = re.match(r"\s*", line).group().replace('\n', '')
        extra = ''.join(indent + text + '\n' for text in addition.splitlines())
        source = source.replace(line, line + extra)
    return source


def patch_engine(source):
    source = after_line(source, 'from bmi30_common_mode import',
                        'from bmi30_common_mode import base_width_decision, base_width_limit, base_width_enabled')
    source = after_line(source, '"peak_index_min": _rt_detector_peak_index_bound(_cfg(',
                        '"base_width_max": base_width_limit(_cfg("base_width_max", 24)),\n'
                        '"base_width_enabled": base_width_enabled(_cfg("base_width_enabled", False)),')
    source = after_line(source, 'self._det_peak_index_min = _rt_detector_peak_index_bound(settings.get(',
                        'self._det_base_width_max = base_width_limit(settings.get("base_width_max", 24))\n'
                        'self._det_base_width_enabled = base_width_enabled(settings.get("base_width_enabled", False))')
    source = after_line(source, '"peak_index_min": _rt_detector_peak_index_bound(getattr(self,',
                        '"base_width_max": base_width_limit(getattr(self, "_det_base_width_max", 24)),\n'
                        '"base_width_enabled": bool(getattr(self, "_det_base_width_enabled", False)),')
    source = after_line(source, 'noise_geometry_changed = False',
                        'old_base_width = (getattr(self, "_det_base_width_enabled", False), getattr(self, "_det_base_width_max", 24))\n'
                        'if "base_width_max" in settings:\n'
                        '\tself._det_base_width_max = base_width_limit(settings["base_width_max"])\n'
                        'if "base_width_enabled" in settings:\n'
                        '\tself._det_base_width_enabled = base_width_enabled(settings["base_width_enabled"])\n'
                        'base_width_changed = old_base_width != (getattr(self, "_det_base_width_enabled", False), getattr(self, "_det_base_width_max", 24))\n'
                        'if base_width_changed:\n'
                        '\tfor ch in (0, 1):\n'
                        '\t\tsetattr(self, f"_det_exceed{ch}", 0)\n'
                        '\t\tsetattr(self, f"_det_exceed_peak{ch}", None)\n'
                        '\t\tfor key in ("peaks", "source_seqs", "products"):\n'
                        '\t\t\tsetattr(self, f"_det_confirm_{key}{ch}", ())\n'
                        '\t\tsetattr(self, f"_det_confirm_step_reason{ch}", "base_width_changed")\n'
                        '\t\tself._clear_web_pre_fire(ch)')
    source = replace(source, 'if common_mode_changed:\n', 'if common_mode_changed or base_width_changed:\n')
    source = replace(source, '"common_mode_settings_changed")', '"detector_gate_settings_changed")')
    # Evaluate in the parent, using the same raw product/peak as this decision.
    # It is independent of named profiles and cannot be bypassed by worker flags.
    gate = ('base_width = base_width_decision(prod_arr, peak_idx,\n'
            '\tgetattr(self, "_det_base_width_max", 24),\n'
            '\tgetattr(self, "_det_base_width_enabled", False), shift)\n'
            'setattr(self, f"_det_last_base_width{ch}", base_width)')
    source = after_line(source, "self._det_last_source = 'prevbuf'", gate)
    source = replace(source, 'candidate_now = bool(adaptive_exceed_now and peak_position_ok)',
                     'candidate_now = bool(adaptive_exceed_now and peak_position_ok and base_width["ok"])')
    source = replace(source, 'exceed_now = bool(level_exceed_now and noise_ok and shape_ok and peak_position_ok and common_ok)',
                     'exceed_now = bool(level_exceed_now and noise_ok and shape_ok and peak_position_ok and common_ok and base_width["ok"])')
    source = after_line(source, "'peak_position_ok': bool(peak_position_ok), 'shape_ok': bool(shape_ok),",
                        "'base_width': dict(base_width),")
    source = after_line(source, '"peak_position_ok": bool(getattr(self,',
                        '"base_width": dict(getattr(self, f"_det_last_base_width{ch}", {}) or {}),')
    # Offline player re-detection and legacy synchronous path use the same gate.
    source = after_line(source, 'candidate = bool(level_candidate and peak_position_ok)',
                        'candidate = bool(candidate and base_width_decision(prod_arr, peak_idx,\n'
                        '\tgetattr(self, "_det_base_width_max", 24),\n'
                        '\tgetattr(self, "_det_base_width_enabled", False), shift)["ok"])')
    source = after_line(source, 'exceed = bool(level_candidate and peak_position_ok and noise_ok)',
                        'base_width = base_width_decision(prod_arr, peak_idx,\n'
                        '\tgetattr(self, "_det_base_width_max", 24),\n'
                        '\tgetattr(self, "_det_base_width_enabled", False),\n'
                        '\tgetattr(self, "_phase_shift_adc0" if ch == 0 else "_phase_shift_adc1", 0))\n'
                        'setattr(self, f"_det_last_base_width{ch}", base_width)\n'
                        'exceed = bool(exceed and base_width["ok"])')
    return source


def patch_portal(source):
    source = after_line(source, '"peak_index_min": 0,',
                        '"base_width_enabled": False,\n"base_width_max": 24,')
    source = after_line(source, '"peak_index_min", "peak_index_max",',
                        '"base_width_enabled", "base_width_max",', count=2)
    source = after_line(source, 'cfg["peak_index_min"] = _int_form_value',
                        'cfg["base_width_enabled"] = _bool_form_value(cfg, "base_width_enabled", False)\n'
                        'cfg["base_width_max"] = _int_form_value(cfg, "base_width_max", 24, 10, 100)')
    source = after_line(source, '"peak_index_min": _int_form_value(form,',
                        '"base_width_enabled": _bool_form_value(form, "base_width_enabled", False),\n'
                        '"base_width_max": _int_form_value(form, "base_width_max", 24, 10, 100),')
    source = after_line(source, '"peak_index_min": int(cfg["peak_index_min"]),',
                        '"base_width_enabled": bool(cfg["base_width_enabled"]),\n'
                        '"base_width_max": int(cfg["base_width_max"]),')
    source = after_line(source, 'tag_peak_index_min_value =',
                        'tag_base_width_checked = "checked" if tag_cfg["base_width_enabled"] else ""\n'
                        'tag_base_width_value = int(tag_cfg["base_width_max"])')
    anchor = '\t              <h2>Named Detection Filters</h2>'
    block = '''              <h2>Pulse Base Width</h2>
              <table class="tag-settings"><tbody><tr>
                <td class="tag-desc"><span class="tag-param">Maximum base width</span></td>
                <td class="tag-channel" colspan="2">
                  <label class="tag-control"><input name="base_width_enabled" type="checkbox" value="1" {tag_base_width_checked}> Enable</label>
                  <label class="tag-control"><input name="base_width_max" type="number" min="10" max="100" step="1" inputmode="numeric" value="{tag_base_width_value}" aria-label="Maximum pulse base width in samples"> samples</label>
                </td>
              </tr></tbody></table>
              <p class="notice">Accept widths at or below this limit (10–100 samples). The base runs between the nearest zeros or valleys on either side of the peak. Both sides must be visible. Applies to both channels, independently of named filters.</p>
            </div>
            <div class="section">
'''
    source = replace(source, anchor, block + anchor)
    return source


DRAW_BASE = '''function drawBaseWidthGate(ctx,info,xs,count,w,plotH,d){
  if(!info)return;
  let color=info.enabled?(info.ok?'#70ffa4':'#ff7785'):'#a8aeba';
  let left=peakSampleMarker(info.left,xs,count,w),right=peakSampleMarker(info.right,xs,count,w);
  ctx.save();ctx.strokeStyle=color;ctx.lineWidth=Math.max(1,1.5*d);ctx.setLineDash([2*d,3*d]);
  for(let marker of [left,right])if(marker){ctx.beginPath();ctx.moveTo(marker.x,0);ctx.lineTo(marker.x,plotH);ctx.stroke()}
  ctx.setLineDash([]);let y=plotH-36*d;
  if(left&&right){ctx.beginPath();ctx.moveTo(left.x,y);ctx.lineTo(right.x,y);ctx.stroke()}
  let text='BASE '+(info.valid?info.width:'?')+(info.enabled?' / '+info.limit:' OFF');
  ctx.font='800 '+Math.max(9,10*d)+'px ui-monospace,Consolas,monospace';
  let tw=ctx.measureText(text).width,x=left&&right?(left.x+right.x-tw)/2:6*d;
  x=Math.max(3*d,Math.min(w-tw-3*d,x));ctx.fillStyle='rgba(4,17,31,.9)';ctx.fillRect(x-2*d,y-15*d,tw+4*d,14*d);
  ctx.fillStyle=color;ctx.textBaseline='alphabetic';ctx.fillText(text,x,y-4*d);ctx.restore();
}
'''


def patch_core(source):
    source = after_line(source, 'import numpy as np',
                        'from bmi30_common_mode import base_width_decision')
    source = after_line(source, '"peak_index_min": ("peak_index_min",',
                        '"base_width_max": ("base_width_max",),\n'
                        '"base_width_enabled": ("base_width_enabled",),')
    source = after_line(source, '"peak_position_ok1": bool(getattr(scope,',
                        '"base_width0": getattr(scope, "_det_last_base_width0", None),\n'
                        '"base_width1": getattr(scope, "_det_last_base_width1", None),')
    source = after_line(source, '"peak_index_min": int(det_settings.get(',
                        '"base_width_max": int(det_settings.get("base_width_max", 24)),\n'
                        '"base_width_enabled": bool(det_settings.get("base_width_enabled", False)),')
    # Measurements travel with each displayed frame, before scaling/decimation.
    source = replace(source, 'def peak_info(product, raw=True):', 'def peak_info(product, raw=True, shift=0):')
    source = replace(source, "product = getattr(scope, f'_xcorr_saved_prod{ch}', None)",
                     "product = getattr(scope, f'_xcorr_saved_prod{ch}', None)\n"
                     "                raw_count = int(getattr(scope, f'_rt_prod_raw_len{ch}', 0) or 0)\n"
                     "                if raw_count >= n and getattr(scope, f'_rt_prod_raw{ch}', None) is not None:\n"
                     "                    product = getattr(scope, f'_rt_prod_raw{ch}')[:n]\n"
                     "                    raw = True")
    source = after_line(source, 'idx = int(np.argmax(arr));value = float(arr[idx])',
                        'base = base_width_decision(arr, idx, getattr(scope, "_det_base_width_max", 24),\n'
                        '    getattr(scope, "_det_base_width_enabled", False), shift)')
    source = replace(source, "'raw_amplitude': value if raw else value * _product_display_scale()}",
                     "'raw_amplitude': value if raw else value * _product_display_scale(), 'base_width': base}")
    source = replace(source, "result[f'prod_peak{ch}'] = peak_info(product, raw)",
                     "result[f'prod_peak{ch}'] = peak_info(product, raw, getattr(scope, f'_phase_shift_adc{ch}', 0) if not anchor else (anchor.get('shifts') or (0, 0))[ch])\n"
                     "            snapshot = anchor.get('snapshot') or {}\n"
                     "            if anchor and ch == sources.get('anchor_channel') and result[f'prod_peak{ch}'] and snapshot.get('base_width'):\n"
                     "                result[f'prod_peak{ch}'].update(sample=snapshot['peak_index'], base_width=snapshot['base_width'])")
    source = replace(source, 'function drawDetectionGate(', DRAW_BASE + 'function drawDetectionGate(')
    source = replace(source, 'drawConfirmationPeakRows(ctx,confirmationProducts,prod,resolvedPeak,c3,xs,count,size.w,plotH,size.d,peakValues,finalPeak)}',
                     'drawConfirmationPeakRows(ctx,confirmationProducts,prod,resolvedPeak,c3,xs,count,size.w,plotH,size.d,peakValues,finalPeak);if(prod&&prod.length>1)drawBaseWidthGate(ctx,finalPeak&&finalPeak.base_width,xs,count,size.w,plotH,size.d)}')
    source = replace(source, '  let confirmPeaks=Array.isArray(snap.confirm_peaks)?',
                     "  let bw=snap.base_width;if(bw&&bw.enabled)parts.push({text:'BASE:'+(bw.valid?bw.width:'?')+'≤'+bw.limit+' '+(bw.ok?'✓':'✗'),className:bw.ok?'pass':'fail',tip:'Base width between nearest zeros or valleys. Both boundaries must exist; width equal to the limit is accepted.'});\n"
                     '  let confirmPeaks=Array.isArray(snap.confirm_peaks)?')
    source = replace(source, 'peak_position_ok:lower?det.peak_position_ok1:det.peak_position_ok0,confirm_peaks:',
                     'peak_position_ok:lower?det.peak_position_ok1:det.peak_position_ok0,base_width:lower?det.base_width1:det.base_width0,confirm_peaks:')
    return source


def patch_project(source, target, version):
    """Write only changed runtime sources; source is an immutable audited copy."""
    helper_source = (HERE/'bmi30_base_width.py').read_text(encoding='utf-8')
    helper_tree = ast.parse(helper_source)
    measure = next(node for node in helper_tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'measure_base_width')
    if any(arg.arg == 'noise_level' for arg in measure.args.args):
        raise ValueError('This historical 2026-09-30 builder lacks noise-aware callers. '
                         'Use the reviewed 2026-11-04-185916 source patch; '
                         'do not combine its helper with the old generated callers.')
    target.mkdir(parents=True, exist_ok=True)
    old = '2026-09-30-1135'
    for prefix, patch in [('BMI30.200.py.', patch_engine), ('BMI30.001.py.', patch_core),
                          ('BMI30.GUI.001.py.', lambda s: s)]:
        text = patch((source/'host'/(prefix+old)).read_text(encoding='utf-8')).replace(old, version)
        ast.parse(text)
        dest = target/'host'/(prefix+version)
        dest.parent.mkdir(exist_ok=True)
        dest.write_text(text, encoding='utf-8', newline='\n')
    text = patch_portal((source/'hotspot_info_server.py').read_text(encoding='utf-8'))
    ast.parse(text)
    (target/'hotspot_info_server.py').write_text(text, encoding='utf-8', newline='\n')
    common = (source/'host/bmi30_common_mode.py').read_text(encoding='utf-8')
    common += '\n\n# Pulse base-width gate (canonical source: HostTools/rpi_detection/bmi30_base_width.py).\n'
    common += (HERE/'bmi30_base_width.py').read_text(encoding='utf-8')
    ast.parse(common)
    (target/'host/bmi30_common_mode.py').write_text(common, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    patch_project(args.source, args.target, args.version)
