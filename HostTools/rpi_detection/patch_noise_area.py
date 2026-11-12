"""Add configurable reference-noise coverage to an audited websplit source."""
import ast
from pathlib import Path

from patch_base_width import after_line, replace


NOISE_GEOMETRY = '''def _rt_detector_noise_area_percent() -> int:
	try:
		value = int(os.getenv('BMI30_RT_NOISE_AREA_PERCENT', '50'))
	except (TypeError, ValueError, OverflowError):
		value = 50
	return max(5, min(95, value))


def _rt_detector_noise_area_mask(sample_count: int, peak_idx: int, percent=None):
	"""Move both noise boundaries relative to the peak; exclude its central zone."""
	n = int(sample_count)
	if n < 4:
		return np.zeros(max(0, n), dtype=bool)
	if percent is None:
		percent = _rt_detector_noise_area_percent()
	percent = max(5, min(95, int(percent)))
	count = max(1, min(n - 1, (n * percent + 50) // 100))
	start = (n - count + 1) // 2
	offsets = np.mod(np.arange(n, dtype=np.int64) - int(peak_idx), n)
	return (offsets >= start) & (offsets < start + count)


'''


DRAW_NOISE_AREA = '''function noiseAreaMask(n,peak,percent){
  percent=Math.max(5,Math.min(95,Math.round(Number(percent)||50)));
  let count=Math.max(1,Math.min(n-1,Math.floor((n*percent+50)/100))),start=Math.floor((n-count+1)/2);
  return Array.from({length:n},(_,sample)=>{let offset=((sample-peak)%n+n)%n;return offset>=start&&offset<start+count});
}
function drawRelativeNoiseSectors(ctx,peakSample,xs,count,w,plotH,d,geometry){
  if(peakSample===null||peakSample===undefined||peakSample==='')return;
  let peak=Math.round(Number(peakSample)),settings=((lastStatus&&lastStatus.detector)||{}).settings||{};
  geometry=geometry||{};
  let n=Math.round(Number(geometry.sample_count)||Number(((lastStatus||{}).mode||{}).base_buf_len)||count);
  let percent=Number(geometry.noise_area_percent)||Number(settings.noise_area_percent)||50;
  if(!Number.isFinite(peak)||n<4)return;
  let lo=(xs&&xs.length)?Number(xs[0]):0,hi=(xs&&xs.length)?Number(xs[xs.length-1]):n-1;
  if(!Number.isFinite(lo)||!Number.isFinite(hi)||hi<=lo)return;
  let allowed=noiseAreaMask(n,peak,percent);
  ctx.save();ctx.fillStyle='rgba(63,209,255,.08)';ctx.strokeStyle='rgba(63,209,255,.25)';ctx.lineWidth=Math.max(1,d);
  let start=-1;
  for(let sample=0;sample<=n;sample++){
    let on=sample<n&&allowed[sample];if(on&&start<0)start=sample;
    if((!on||sample===n)&&start>=0){
      let visStart=Math.max(start,Math.ceil(lo)),visEnd=Math.min(sample-1,Math.floor(hi));
      if(visEnd>=visStart){let x0=(visStart-lo)/(hi-lo)*w,x1=(visEnd+1-lo)/(hi-lo)*w;
        ctx.fillRect(x0,0,Math.max(d,x1-x0),plotH);ctx.strokeRect(x0,0,Math.max(d,x1-x0),plotH)}
      start=-1;
    }
  }
  let measured=allowed.filter(Boolean).length;
  ctx.font='600 '+Math.max(9,10*d)+'px ui-monospace,Consolas,monospace';ctx.fillStyle='#6bdcff';
  ctx.fillText('NOISE '+Math.round(percent)+'% ('+measured+'/'+n+')',6*d,14*d);ctx.restore();
}
'''


def patch_engine(source):
    source = replace(source, 'def _rt_detector_relative_noise_reference(',
                     NOISE_GEOMETRY + 'def _rt_detector_relative_noise_reference(')
    source = replace(source,
                     '\tguard_radius = _rt_detector_noise_guard_radius(n)\n\tskip_parts = _rt_detector_noise_skip_parts(divisions)\n', '')
    source = replace(source,
                     '\tforward = np.mod(indices - int(peak_idx), n)\n'
                     '\tbackward = np.mod(int(peak_idx) - indices, n)\n'
                     '\tcircular_distance = np.minimum(forward, backward)\n', '')
    source = replace(source,
                     '\tpart_distance = np.minimum(parts, divisions - parts)\n'
                     '\teligible = (circular_distance > guard_radius) & (part_distance > skip_parts)',
                     '\teligible = _rt_detector_noise_area_mask(n, peak_idx)')
    source = after_line(source, '"noise_divisions": _det_clamp_int(_cfg(',
                        '"noise_area_percent": _det_clamp_int(_cfg("noise_area_percent", os.getenv("BMI30_RT_NOISE_AREA_PERCENT", "50")), 50, 5, 95),')
    source = after_line(source, '"noise_divisions": "BMI30_RT_NOISE_DIVISIONS",',
                        '"noise_area_percent": "BMI30_RT_NOISE_AREA_PERCENT",')
    source = after_line(source, 'self._det_noise_window_s = _det_clamp_noise_window(_settings.get(',
                        'self._det_noise_area_percent = _det_clamp_int(_settings.get("noise_area_percent", 50), 50, 5, 95)')
    for anchor in ('\'noise_divisions\': _det_clamp_int(getattr(self,',
                   '"noise_divisions": _det_clamp_int(getattr(self,'):
        source = after_line(source, anchor,
                            '"noise_area_percent": _det_clamp_int(getattr(self, "_det_noise_area_percent", 50), 50, 5, 95),')
    source = after_line(source, '"noise_divisions": int(_rt_detector_noise_divisions()),',
                        '"noise_area_percent": int(_rt_detector_noise_area_percent()),')
    source = after_line(source, 'noise_geometry_changed = False',
                        'if "noise_area_percent" in settings:\n'
                        '\tnew_area = _det_clamp_int(settings["noise_area_percent"], 50, 5, 95)\n'
                        '\tnoise_geometry_changed = new_area != getattr(self, "_det_noise_area_percent", 50)\n'
                        '\tself._det_noise_area_percent = new_area')
    source = replace(source, 'if common_mode_changed or base_width_changed:',
                     'if common_mode_changed or base_width_changed or noise_geometry_changed:')
    # Drop confirmations and queued results computed with the previous region.
    source = replace(source,
                     '\t\tif noise_geometry_changed:\n\t\t\ttry:\n',
                     '\t\tif noise_geometry_changed:\n'
                     '\t\t\tfor ch in (0, 1):\n'
                     '\t\t\t\tfor key in ("peaks", "source_seqs", "products", "frames"):\n'
                     '\t\t\t\t\tsetattr(self, f"_det_confirm_{key}{ch}", ())\n'
                     '\t\t\t\tsetattr(self, f"_det_confirm_step_reason{ch}", "noise_area_changed")\n'
                     '\t\t\ttry:\n')
    return source


def patch_portal(source):
    source = after_line(source, '"noise_window_s": 1.0,', '"noise_area_percent": 50,')
    source = after_line(source, '"noise_window_s", "noise_up0",', '"noise_area_percent",')
    source = after_line(source, 'cfg["noise_window_s"] = round(',
                        'cfg["noise_area_percent"] = _int_form_value(cfg, "noise_area_percent", 50, 5, 95)')
    source = after_line(source, '"noise_window_s": _float_form_value(form,',
                        '"noise_area_percent": _int_form_value(form, "noise_area_percent", 50, 5, 95),')
    source = after_line(source, '"noise_window_s": detector_settings.get(',
                        '"noise_area_percent": detector_settings.get("noise_area_percent", 50),')
    source = after_line(source, '"noise_window_s": float(cfg["noise_window_s"]),',
                        '"noise_area_percent": int(cfg["noise_area_percent"]),')
    source = after_line(source, 'payload["tag_detection"] = _normalize_tag_detection_config(cfg)',
                        'payload["noise_area_percent"] = int(payload["tag_detection"]["noise_area_percent"])')
    source = after_line(source, 'tag_noise_window_s_value =',
                        'tag_noise_area_percent_value = int(tag_cfg["noise_area_percent"])')
    anchor = '                  <tr>\n                    <td class="tag-desc">\n                      <span class="tag-param">Noise averaging window'
    tip = ('Percentage of buffer samples used to measure reference noise (5-95%). '
           'Noise is measured outside the excluded zone around the largest impulse, on both sides in the circular buffer. '
           'Increasing the percentage moves the boundaries closer to the impulse. '
           'Noise is the largest absolute product value in this area. '
           'The configured signal/noise coefficients stay unchanged; a larger noise estimate requires a larger signal. Applies to both channels.')
    block = ('                  <tr>\n                    <td class="tag-desc">\n'
             '                      <span class="tag-param">Noise measurement area '
             '<b class="help tag-help" tabindex="0" aria-label="'+tip+'" data-tip="'+tip+'">?</b></span>\n'
             '                    </td>\n'
             '                    <td class="tag-channel" colspan="2"><label class="tag-control">'
             '<input name="noise_area_percent" type="number" min="5" max="95" step="1" inputmode="numeric" '
             'value="{tag_noise_area_percent_value}" aria-label="Noise measurement area, percent of buffer"> %</label></td>\n'
             '                  </tr>\n')
    return replace(source, anchor, block + anchor)


def patch_core(source):
    source = after_line(source, '"noise_divisions": ("noise_divisions",',
                        '"noise_area_percent": ("noise_area_percent",),')
    source = after_line(source, '"noise_divisions": int(det_settings.get(',
                        '"noise_area_percent": int(det_settings.get("noise_area_percent", 50)),')
    # The frame carries its geometry so Freeze uses the captured setting.
    source = replace(source, 'def peak_info(product, raw=True, shift=0):',
                     'def peak_info(product, raw=True, shift=0, noise_percent=None):')
    anchor = "'base_width': base}"
    source = replace(source, anchor,
                     "'base_width': base, 'sample_count': int(arr.size), "
                     "'noise_area_percent': int(noise_percent if noise_percent is not None else getattr(scope, '_det_noise_area_percent', 50))}")
    old = "result[f'prod_peak{ch}'] = peak_info(product, raw, getattr(scope, f'_phase_shift_adc{ch}', 0) if not anchor else (anchor.get('shifts') or (0, 0))[ch])"
    new = old[:-1] + ", noise_percent=(anchor.get('snapshot') or {}).get('noise_area_percent') if anchor else None)"
    source = replace(source, old, new)
    start = source.index('function drawRelativeNoiseSectors(')
    stop = source.index('\n', start)
    source = source[:start] + DRAW_NOISE_AREA.rstrip() + source[stop:]
    source = replace(source,
                     'drawRelativeNoiseSectors(ctx,resolvedPeak,xs,count,size.w,plotH,size.d);',
                     'drawRelativeNoiseSectors(ctx,resolvedPeak,xs,count,size.w,plotH,size.d,finalPeak);')
    source = replace(source,
                     "noise_window_s:Number(settings.noise_window_s)||0,",
                     "noise_window_s:Number(settings.noise_window_s)||0,noise_area_percent:Number(settings.noise_area_percent)||50,")
    return source


def prepare(source_dir, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, patcher in [('engine', patch_engine), ('portal', patch_portal), ('core', patch_core)]:
        text = patcher((source_dir / (name + '.py')).read_text(encoding='utf8'))
        ast.parse(text)
        (target_dir / (name + '.py')).write_text(text, encoding='utf8', newline='\n')


if __name__ == '__main__':
    import sys
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
