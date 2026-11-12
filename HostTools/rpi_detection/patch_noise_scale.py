"""Keep detector precision and show native product levels throughout the scope."""
import ast
from pathlib import Path

from patch_base_width import after_line, replace


def patch_engine(source):
    # Detection inputs remain int64 products. Floating histories must retain doubles.
    source = replace(source, 'arr = arr.astype(np.float32, copy=False)',
                     'arr = arr.astype(np.float64, copy=False)', count=1)
    source = replace(source, 'return np.asarray([], dtype=np.float32)',
                     'return np.asarray([], dtype=np.float64)', count=1)
    for ch in (0, 1):
        source = replace(source, f'self._rt_prod_raw{ch} = np.zeros(self.max_samples, dtype=np.float32)',
                         f'self._rt_prod_raw{ch} = np.zeros(self.max_samples, dtype=np.float64)')
    source = replace(source,
        '\t\treturn bool(peak > (ref_noise * ratio)), float(ref_noise), int(ref_part), tuple(float(x) for x in noises)',
        '\t\treturn bool(ref_noise > 0.0 and peak > (ref_noise * ratio)), float(ref_noise), int(ref_part), tuple(float(x) for x in noises)')
    start = source.index('\t\tif int(trigger_level) <= 1 and lvl > 0 and float(noise_avg) <= 0.0:')
    end = source.index('\t\tsetattr(self, thr_key, int(trigger_level))', start)
    source = source[:start] + (
        '\t\t# An absent/zero reference is not an infinite signal/noise ratio.\n'
        '\t\tnoise_valid = bool(np.isfinite(noise_far) and float(noise_far) > 0.0\n'
        '\t\t                   and np.isfinite(noise_avg) and float(noise_avg) > 0.0\n'
        '\t\t                   and int(noise_part) >= 0)\n'
        '\t\tsetattr(self, f"_det_noise_valid{ch}", noise_valid)\n'
        '\t\tnoise_ok = bool(noise_valid)\n'
    ) + source[end:]
    source = replace(source,
        'candidate_now = bool(adaptive_exceed_now and peak_position_ok and base_width["ok"])',
        'candidate_now = bool(noise_valid and adaptive_exceed_now and peak_position_ok and base_width["ok"])')
    source = replace(source, '\t\tif filters_active and candidate_now and bool(noise_ok):',
                     '\t\tnoise_valid = bool(noise_valid and np.isfinite(noise_far) and float(noise_far) > 0.0)\n'
                     '\t\tsetattr(self, f"_det_noise_valid{ch}", noise_valid)\n'
                     '\t\tnoise_ok = bool(noise_ok and noise_valid)\n'
                     '\t\tif filters_active and candidate_now and bool(noise_ok):', count=1)
    source = after_line(source, 'self._det_noise_samples0 = self._det_noise_samples1 = 0',
                        'self._det_noise_valid0 = self._det_noise_valid1 = False')
    source = after_line(source, "'_det_noise_samples0', '_det_noise_samples1',",
                        "'_det_noise_valid0', '_det_noise_valid1',")
    source = after_line(source, '"noise_estimator": "max_mean_p95",',
                        '"noise_valid": bool(getattr(self, f"_det_noise_valid{ch}", False)),\n'
                        '"level_unit": "raw-product",\n"arithmetic": "int64/float64",', count=1)
    source = after_line(source, "'noise_estimator': 'max_mean_p95',",
                        "'noise_valid': bool(noise_valid), 'level_unit': 'raw-product',\n"
                        "'arithmetic': 'int64/float64',", count=1)
    return source


def patch_core(source):
    source = replace(source, '"display_unit": "u16",',
                     '"display_unit": "raw-product",\n'
                     '                    "level_unit": "raw-product",\n'
                     '                    "plot_unit": "u16",\n'
                     '                    "arithmetic": "int64/float64",')
    for ch in (0, 1):
        source = after_line(source, f'"noise_samples{ch}": det_noise_samples{ch},',
                            f'"noise_valid{ch}": bool(getattr(scope, "_det_noise_valid{ch}", False)),')
        source = after_line(source, f'"noise_samples": det_noise_samples{ch},',
                            f'"noise_valid": bool(getattr(scope, "_det_noise_valid{ch}", False)),')
    source = replace(source,
        'function detectorLevelNumber(v){v=Number(v);if(!Number.isFinite(v))return\'---\';return String(Math.round(v))}',
        "function detectorRawValue(v){if(v===null||v===undefined)return NaN;v=Number(v);return Number.isFinite(v)&&v>=0?v:NaN}\n"
        "function detectorLevelNumber(v){v=detectorRawValue(v);if(!Number.isFinite(v))return'---';if(Number.isInteger(v))return String(v);if(v>0&&v<0.001)return v.toExponential(6);return String(Number(v.toFixed(3)))}")
    source = replace(source,
        "function detectorLevelText(v,det){v=Number(v);if(!Number.isFinite(v)||v<0)return'-----';let x=v/detectorDisplayScale(det);return String(Math.round(Math.max(0,Math.min(65535,x))))}",
        'function detectorLevelText(v,det){return detectorLevelNumber(v)}')
    source = replace(source, 'let x=detectorDisplayLevel(v,det);return Number.isFinite(x)?x:0',
                     'let x=detectorRawValue(v);return Number.isFinite(x)?x:0')
    source = replace(source,
        'let v=auto?clampDetectorDisplayLevel(value):clampDetectorThreshold(value);if(auto&&v>=100)return String(Math.round(v));return v.toFixed(1)',
        'if(auto)return detectorLevelNumber(value);let v=clampDetectorThreshold(value);return v.toFixed(1)')
    source = replace(source, "+' display'];if(Number.isFinite(floor))bits.push('min '+floor)",
                     "+' raw-product'];if(Number.isFinite(floor))bits.push('min '+detectorLevelNumber(floor*detectorDisplayScale(det)))")
    source = replace(source, "input.max=auto?'65535':'100';", "input.max=auto?'9223372036854775807':'100';")
    # Keep the threshold line's coordinate transform, change its textual label only.
    source = replace(source,
        "let level=detectorDisplayValue(raw,snap||det),txt=Number.isFinite(level)?detectorThresholdText(level,true):'';",
        "let level=detectorRawValue(raw),txt=Number.isFinite(level)?detectorLevelNumber(level):'';")
    source = replace(source,
        'function detectorNoiseHtml(lvl,thr,snr,det){return',
        'function detectorNoiseHtml(lvl,noise,thr,snr,det){return')
    source = replace(source, '<i class="det-noise-label">p/t/SN</i>', '<i class="det-noise-label">p/n/t/SN</i>')
    source = replace(source,
        "+detectorLevelText(lvl,det)+'</i><i class=\"det-noise-sep\">/</i><i class=\"det-noise-num\">'+detectorLevelText(thr,det)",
        "+detectorLevelText(lvl,det)+'</i><i class=\"det-noise-sep\">/</i><i class=\"det-noise-num\">'+detectorLevelText(noise,det)+'</i><i class=\"det-noise-sep\">/</i><i class=\"det-noise-num\">'+detectorLevelText(thr,det)")
    source = replace(source,
        'let upSnr=Number(det.snr_avg0||upper.snr_avg)||0,loSnr=Number(det.snr_avg1||lower.snr_avg)||0;',
        'let upSnr=Number(det.noise_avg0)>0?Number(det.lvl0)/Number(det.noise_avg0):NaN,loSnr=Number(det.noise_avg1)>0?Number(det.lvl1)/Number(det.noise_avg1):NaN;')
    for side, ch in [('up', 0), ('lo', 1)]:
        source = replace(source, f'detectorNoiseHtml(det.lvl{ch},det.trigger{ch}||det.thr{ch},{side}Snr,det)',
                         f'detectorNoiseHtml(det.lvl{ch},{side}NoiseAvg,det.trigger{ch}||det.thr{ch},{side}Snr,det)')
    source = replace(source,
        "let ratioTitle='detector peak / calculated trigger / averaged signal-to-reference-noise ratio'+(noiseWindow>0?(' over '+normNumber(noiseWindow)+' s'):'')+'; S/N is mean detector peak divided by mean positive reference noise';",
        "let ratioTitle='detector peak / noise estimate / calculated trigger / current peak-to-estimate ratio; levels in raw-product units; noise estimate max(mean, P95)'+(noiseWindow>0?(' over '+normNumber(noiseWindow)+' s'):'');")
    source = replace(source, "', peak and trigger scale 0..65535'", "', only the graph is scaled to 0..65535'", count=2)
    for label, ch in [('up', 0), ('lo', 1)]:
        source = replace(source,
            f"+detectorLevelText({label}NoiseAvg,det)+', only the graph is scaled to 0..65535';",
            f"+detectorLevelText({label}NoiseAvg,det)+'; current reference noise '+detectorLevelText(det.noise_ref{ch},det)+'; noise valid '+String(det.noise_valid{ch}!==false)+', only the graph is scaled to 0..65535';")
    # Peak-marker coordinates still use the display waveform; text uses its raw metadata.
    source = replace(source, 'row:i,amplitude:info.amplitude',
                     'row:i,amplitude:info.raw_amplitude===undefined?info.amplitude:info.raw_amplitude')
    source = replace(source, 'row:0,amplitude:info.amplitude',
                     'row:0,amplitude:info.raw_amplitude===undefined?info.amplitude:info.raw_amplitude')
    start = source.index('function detectorDecisionParts(snap){')
    end = source.index('function renderScopeReadoutParts(', start)
    section = source[start:end].replace('detectorSnapshotDisplay(', 'detectorRawValue(')
    section = replace(section, 'autoFloor=Number(snap.auto_floor_u16)',
                      'autoFloor=Number(snap.auto_floor_u16)*detectorDisplayScale(snap)')
    section = replace(section, "text:'NOISE P95'", "text:'N:'+detectorLevelNumber(noiseAvg)+' P95'")
    section = replace(section, 'Reference-noise estimate = max(mean, 95th percentile) in the selected time window.',
                      'Reference-noise estimate = max(mean, 95th percentile) in raw-product units over the selected time window.')
    section = replace(section, '  if(rawRef===0&&rawLevel>0)instantSnr=Infinity;',
                      "  if(!(rawRef>0)){instantSnr=NaN;parts.push({text:'S/Ninst:invalid noise ✗',className:'fail',tip:'Reference noise is zero or unavailable. No infinite S/N is accepted.'})}")
    source = source[:start] + section + source[end:]
    source = replace(source,
        "let waveMax=detectorLevelNumber(stats.display_max),sharedMax=Number(stats.normalization_display_max)",
        "let waveMax=detectorLevelNumber(stats.max),sharedMax=Number(stats.normalization_peak)")
    source = replace(source,
        "tip:'Maximum of the final frozen waveform before normalization, display scale 0..65535: '+waveMax+'. Raw product maximum: '+detectorLevelNumber(stats.max)+'.'+scaleTip",
        "tip:'Maximum of the final frozen waveform in raw-product units: '+waveMax+'. Only the graphical waveform is scaled to 0..65535.'+scaleTip")
    source = replace(source, "display_unit:String(det.display_unit||'u16')",
                     "display_unit:String(det.display_unit||'raw-product')")
    source = replace(source, '</style>', '''/* Native levels need room for 10-digit products and fractional noise. */
:root{--det-noise-w:340px!important}
.metric span.det-noise{grid-template-columns:7ch minmax(10ch,1fr) 1ch minmax(12ch,1fr) 1ch minmax(10ch,1fr) 1ch 6ch!important;font-size:10px!important}
@media(max-width:1180px){:root{--det-noise-w:320px!important}.metric span.det-noise{font-size:9px!important}.status-grid{flex-wrap:wrap!important}}
@media(max-width:640px){.det-metric{grid-template-columns:var(--det-state-w,78px) var(--det-btn-w,31px)!important}.det-metric .det-noise-stack{grid-column:1/3!important;grid-row:3!important;max-width:100%}.metric span.det-noise{width:320px!important}.status-grid{row-gap:8px!important}}
</style>''', count=1)
    return source


def patch_portal(source):
    return replace(source, 'The current-packet signal/noise check is also retained.',
                   'The current-packet signal/noise check is also retained. Detection uses int64 products and float64 noise statistics in native product units; zero reference noise cannot confirm a tag.')


def prepare(source_dir, target_dir, version):
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, patcher in [('engine', patch_engine), ('core', patch_core), ('portal', patch_portal)]:
        source = patcher((source_dir / (name + '.py')).read_text(encoding='utf8'))
        if name == 'core':
            source = replace(source, 'BMI30.200.py.2026-10-15-1455', 'BMI30.200.py.' + version)
        ast.parse(source)
        (target_dir / (name + '.py')).write_text(source, encoding='utf8', newline='\n')


if __name__ == '__main__':
    import sys
    prepare(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
