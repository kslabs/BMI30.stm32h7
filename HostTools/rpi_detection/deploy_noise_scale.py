"""M02-only native-level update using the guarded mean/P95 deployment workflow."""
import json
from pathlib import Path
import sys

import deploy_noise_percentile as base

ROOT, HERE = base.ROOT, base.HERE
WORK = ROOT / '.tmp/noise-scale-20261016'
WORK.mkdir(exist_ok=True)
_previous_script = base.script


def script(name):
    direct = HERE / ('noise_scale_' + name + '_remote.py')
    if direct.exists():
        return direct.read_text(encoding='utf8')
    source = _previous_script(name).replace('.tmp/noise-percentile-20261015', '.tmp/noise-scale-20261016')
    source = source.replace('Temporal noise estimate: mean and P95', 'Native 64-bit detector levels and zero-noise rejection')
    source = source.replace('Noise estimate max(mean, linear P95); preserve area, window, and configured S/N coefficients',
                            'Native int64/float64 levels, unscaled numeric readouts, reject zero reference noise')
    source = source.replace('max(mean, P95) temporal noise estimate; retain instantaneous noise check',
                            'Native int64/float64 arithmetic and readouts; reject zero reference noise')
    source = source.replace('validated-M02-noise-percentile-update', 'validated-M02-noise-scale-update')
    if name == 'test':
        source = source.replace("['test_noise_percentile', 'test_noise_area', 'test_base_width', 'test_averaging']",
                                "['test_noise_scale', 'test_noise_percentile', 'test_noise_area', 'test_base_width', 'test_averaging']")
    if name == 'verify':
        source = base.once(source, "assert det['settings']['noise_percentile'] == 95.0",
                           "assert det['settings']['noise_percentile'] == 95.0\n"
                           "    assert det['display_unit']==det['level_unit']=='raw-product'\n"
                           "    assert det['plot_unit']=='u16' and det['arithmetic']=='int64/float64'")
        source = base.once(source, "'noise_samples': [sample1['detector']['noise_samples' + str(ch)] for ch in (0, 1)],",
                           "'noise_samples': [sample1['detector']['noise_samples' + str(ch)] for ch in (0, 1)],\n"
                           "        'signal': [sample1['detector']['lvl' + str(ch)] for ch in (0, 1)],\n"
                           "        'trigger': [sample1['detector']['trigger' + str(ch)] for ch in (0, 1)],\n"
                           "        'reference_noise': [sample1['detector']['noise_ref' + str(ch)] for ch in (0, 1)],\n"
                           "        'noise_valid': [sample1['detector']['noise_valid' + str(ch)] for ch in (0, 1)],")
        source = base.once(source, "frame = get('/api/frame?max_points=200')",
                           "assert all(any(item['noise_valid'][ch] for item in diagnostics) for ch in (0,1))\n"
                           "frame = get('/api/frame?max_points=200')\n"
                           "assert all(0<=value<=65535 for ch in (0,1) for value in frame['prod'+str(ch)])\n"
                           "core_html=urllib.request.urlopen('http://127.0.0.1:8765/',timeout=5).read().decode()\n"
                           "assert 'p/n/t/SN' in core_html and 'function detectorRawValue(' in core_html\n"
                           "assert \"return detectorLevelNumber(v)\" in core_html")
        source = base.once(source, "'portal_fields': [area_field, window_field], 'portal_render_ok': True,",
                           "'portal_fields': [area_field, window_field], 'portal_render_ok': True,\n"
                           "    'numeric_unit':'raw-product','arithmetic':'int64/float64','plot_unit':'u16',\n"
                           "    'live_frame_peaks':[frame['prod_peak'+str(ch)] for ch in (0,1)],")
    return source


base.WORK, base.script = WORK, script
call = base.call

if __name__ == '__main__':
    name = sys.argv[1]
    payload = json.loads(Path(sys.argv[2]).read_text(encoding='utf8')) if len(sys.argv) > 2 else None
    result = call(name, payload, timeout=210 if name in ('test', 'build') else 120)
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ('inline_scripts', 'diagnostics', 'sources', 'agents', 'packager', 'switcher', 'config', 'status')}, indent=2))
