"""Prepare an optional first-to-last confirmation peak growth gate.

Patches audited websplit engine/portal copies; never edits a running device.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'FirmwareReleases/RaspberryPi/2026-10-21-1501/project'
DEFAULT_OUTPUT = ROOT / '.tmp/confirmation-growth-20261021/candidate'
KEYS = ('confirm_growth_enabled', 'confirm_growth_ratio')
LABEL = 'Confirmation peak growth'
RATIO_LABEL = 'Minimum last / first peak ratio'
HELP = ('Require the last peak amplitude to be at least the first peak amplitude times the configured ratio '
        'in the last full window of consecutive confirmations, independently for each antenna. '
        'Uses unrounded product peaks at their detected positions. Does not require intermediate peaks to rise. '
        'At least two confirmations are required. Existing HOLD release rules remain in effect after acceptance. '
        'Default off; ratio 2.0. Save and Apply to Core activates the change.')


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'Expected exactly one patch anchor: {old[:100]!r}; found {source.count(old)}')
    return source.replace(old, new, 1)


GROWTH = '''def _rt_detector_confirmation_growth(products, peaks, confirmations, enabled=False, ratio=2.0):
	"""Compare unrounded product amplitudes at the first/last detected peaks."""
	enabled = _det_parse_bool(enabled, False)
	ratio = _det_clamp_float(ratio, 2.0, 1.0, 100.0)
	diag = {"enabled": enabled, "required_ratio": ratio, "first": None,
	        "last": None, "ratio": None, "ok": not enabled,
	        "reason": "incomplete_window" if enabled else "disabled"}
	if not enabled:
		return diag
	count = _det_clamp_confirm(confirmations, 2)
	try:
		products, peaks = tuple(products)[-count:], tuple(peaks)[-count:]
		if count < 2 or len(products) != count or len(peaks) != count:
			return diag
		values = []
		for product, peak in ((products[0], peaks[0]), (products[-1], peaks[-1])):
			arr = np.asarray(product, dtype=np.float64)
			index = int(peak)
			if arr.ndim != 1 or not 0 <= index < arr.size:
				raise ValueError('invalid product peak')
			value = float(arr[index])
			if not np.isfinite(value) or value <= 0.0:
				raise ValueError('invalid amplitude')
			values.append(value)
		first, last = values
		observed = last / first
		# Keep diagnostics JSON-safe even for a malformed extreme input.
		if not np.isfinite(observed):
			raise ValueError('invalid amplitude ratio')
		ok = bool(last >= first * ratio)
		return dict(diag, first=first, last=last, ratio=observed, ok=ok,
		            reason='growth_confirmed' if ok else 'insufficient_growth')
	except (TypeError, ValueError, IndexError, OverflowError):
		return dict(diag, reason='invalid_amplitude')


'''


def growth_call(ch, count):
    return (f'_rt_detector_confirmation_growth(getattr(self, f"_det_confirm_products{ch}", ()), '
            f'getattr(self, f"_det_confirm_peaks{ch}", ()), {count}, '
            'getattr(self, "_det_confirm_growth_enabled", False), '
            'getattr(self, "_det_confirm_growth_ratio", 2.0))')


def patch_engine(source):
    source = once(source, 'def _rt_detector_confirmation_step(', GROWTH + 'def _rt_detector_confirmation_step(')
    anchor = '\t\tif "impulse_guard_enabled" in (settings or {}) and isinstance(data.get("tag_detection"), dict):'
    source = once(source, anchor, '\t\tfor key in ("confirm_growth_enabled", "confirm_growth_ratio"):\n'
                  '\t\t\tif key in (settings or {}) and isinstance(data.get("tag_detection"), dict):\n'
                  '\t\t\t\tdata["tag_detection"][key] = settings[key]\n' + anchor)
    source = once(source, '\t\t"impulse_guard_enabled": _det_parse_bool(_cfg("impulse_guard_enabled", False), False),',
                  '\t\t"confirm_growth_enabled": _det_parse_bool(_cfg("confirm_growth_enabled", False), False),\n'
                  '\t\t"confirm_growth_ratio": _det_clamp_float(_cfg("confirm_growth_ratio", 2.0), 2.0, 1.0, 100.0),\n'
                  '\t\t"impulse_guard_enabled": _det_parse_bool(_cfg("impulse_guard_enabled", False), False),')
    anchor = '\t\tself._det_impulse_guard_enabled = _det_parse_bool(self._det_channel_settings.get("impulse_guard_enabled", False), False)'
    source = once(source, anchor, '\t\tself._det_confirm_growth_enabled = _det_parse_bool(self._det_channel_settings.get("confirm_growth_enabled", False), False)\n'
                  '\t\tself._det_confirm_growth_ratio = _det_clamp_float(self._det_channel_settings.get("confirm_growth_ratio", 2.0), 2.0, 1.0, 100.0)\n' + anchor)
    # A rejected full window slides forward with exactly C consecutive candidates.
    old = "if getattr(self, '_det_confirm_sample_diversity', 0) else None"
    assert source.count(old) == 3
    source = source.replace(old, "if (getattr(self, '_det_confirm_sample_diversity', 0) or getattr(self, '_det_confirm_growth_enabled', False)) else None")
    anchor = "\t\treview_frame['decision']['confirm_diversity'] = dict(diversity)"
    source = once(source, anchor, anchor + '\n\t\tgrowth = ' + growth_call('{ch}', 'confirm_count') + '\n'
                  '\t\tsetattr(self, f"_det_last_confirm_growth{ch}", growth)\n'
                  '\t\tif exceed_now and cnt >= confirm_count and not growth["ok"] and not getattr(self, hold_key, False):\n'
                  '\t\t\tsetattr(self, f"_det_growth_rejected{ch}", int(getattr(self, f"_det_growth_rejected{ch}", 0)) + 1)\n'
                  '\t\treview_frame["decision"]["confirm_growth"] = dict(growth)')
    source = once(source, "cnt >= confirm_count and diversity['ok']", "cnt >= confirm_count and diversity['ok'] and growth['ok']")
    for ch in (0, 1):
        anchor = next(line for line in source.splitlines() if line.startswith(f'\t\tfire{ch} = bool(int(getattr(self, \'_det_exceed{ch}\''))
        source = once(source, anchor, anchor + ' and ' + growth_call(str(ch), f'self._det_channel_confirm_count({ch})') + '["ok"]')
    anchor = '\t\t\t\t\t"confirm_source_sequences": confirm_source_sequences,'
    source = once(source, anchor, anchor + '\n\t\t\t\t\t"confirm_growth": ' + growth_call('{ch}', 'self._det_channel_confirm_count(ch)') + ',')
    anchor = '\t\t\t"impulse_guard_enabled": bool(getattr(self, "_det_impulse_guard_enabled", False)),'
    source = once(source, anchor, '\t\t\t"confirm_growth_enabled": bool(getattr(self, "_det_confirm_growth_enabled", False)),\n'
                  '\t\t\t"confirm_growth_ratio": _det_clamp_float(getattr(self, "_det_confirm_growth_ratio", 2.0), 2.0, 1.0, 100.0),\n' + anchor)
    anchor = '\t\tdiversity_value = _det_clamp_int(settings.get("confirm_sample_diversity", getattr(self, "_det_confirm_sample_diversity", 0)), 0, 0, 5)'
    validation = '''		growth_enabled = _det_parse_bool(settings.get("confirm_growth_enabled", getattr(self, "_det_confirm_growth_enabled", False)), False)
		growth_ratio = _det_clamp_float(settings.get("confirm_growth_ratio", getattr(self, "_det_confirm_growth_ratio", 2.0)), 2.0, 1.0, 100.0)
		if growth_enabled:
			for ch in (0, 1):
				enabled = next((settings[k] for k in (f"enabled{ch}", ("upper_enabled" if ch == 0 else "lower_enabled"), f"det_channel_enabled{ch}") if k in settings), getattr(self, f"_det_channel_enabled{ch}", True))
				count = next((settings[k] for k in (f"confirm{ch}", f"count{ch}", ("upper_confirm" if ch == 0 else "lower_confirm"), f"det_confirm_count{ch}") if k in settings), self._det_channel_confirm_count(ch))
				if _det_parse_bool(enabled, True) and _det_clamp_confirm(count, 2) < 2:
					raise ValueError("Peak growth requires at least two confirmations for each enabled channel.")
'''
    source = once(source, anchor, validation + anchor)
    anchor = '\t\tdiversity_changed = diversity_value != getattr(self, "_det_confirm_sample_diversity", 0)'
    source = once(source, anchor, '\t\tgrowth_changed = (growth_enabled, growth_ratio) != (bool(getattr(self, "_det_confirm_growth_enabled", False)), getattr(self, "_det_confirm_growth_ratio", 2.0))\n'
                  '\t\tself._det_confirm_growth_enabled, self._det_confirm_growth_ratio = growth_enabled, growth_ratio\n' + anchor)
    source = once(source, 'if diversity_changed or impulse_guard_changed:', 'if diversity_changed or impulse_guard_changed or growth_changed:')
    source = once(source, 'self._det_break_confirmation_chains("impulse_guard_changed" if impulse_guard_changed else "sample_diversity_changed")',
                  'self._det_break_confirmation_chains("confirm_growth_changed" if growth_changed else ("impulse_guard_changed" if impulse_guard_changed else "sample_diversity_changed"))')
    source = once(source, 'or diversity_changed or impulse_guard_changed:', 'or diversity_changed or impulse_guard_changed or growth_changed:')
    anchor = "\t\t\tsetattr(self, f'_det_last_confirm_diversity{ch}', {})"
    source = once(source, anchor, anchor + '\n\t\t\tsetattr(self, f"_det_last_confirm_growth{ch}", {})')
    # Player uses its own immutable product window and the identical gate.
    source = once(source, '\t\t\tdet_confirm_peaks = [[], []]', '\t\t\tdet_confirm_peaks = [[], []]\n\t\t\tdet_confirm_products = [[], []]')
    source = once(source, '\t\t\t\t\tdet_confirm_peaks[ch] = []\n\t\t\t\t\tif det_hold[ch]:',
                  '\t\t\t\t\tdet_confirm_peaks[ch] = []\n\t\t\t\t\tdet_confirm_products[ch] = []\n\t\t\t\t\tif det_hold[ch]:')
    for old, new in [
        ('\t\t\t\tif det_cnt[ch] <= 0:\n\t\t\t\t\tdet_confirm_peaks[ch] = []',
         '\t\t\t\tif det_cnt[ch] <= 0:\n\t\t\t\t\tdet_confirm_peaks[ch] = []\n\t\t\t\t\tdet_confirm_products[ch] = []'),
        ('\t\t\t\t\tdet_confirm_peaks[ch].append(int(peak_idx))',
         '\t\t\t\t\tdet_confirm_peaks[ch].append(int(peak_idx))\n\t\t\t\t\tdet_confirm_products[ch].append(np.array(prod_arr, dtype=np.float64, copy=True))'),
        ('\t\t\t\t\tdet_confirm_peaks[ch] = [int(peak_idx)]',
         '\t\t\t\t\tdet_confirm_peaks[ch] = [int(peak_idx)]\n\t\t\t\t\tdet_confirm_products[ch] = [np.array(prod_arr, dtype=np.float64, copy=True)]'),
    ]:
        source = once(source, old, new)
    anchor = '\t\t\t\tdet_confirm_peaks[ch] = det_confirm_peaks[ch][-confirm_limit:]'
    source = once(source, anchor, anchor + '\n\t\t\t\tdet_confirm_products[ch] = det_confirm_products[ch][-confirm_limit:]\n'
                  '\t\t\t\tgrowth = _rt_detector_confirmation_growth(det_confirm_products[ch], det_confirm_peaks[ch], confirm_limit, getattr(self, "_det_confirm_growth_enabled", False), getattr(self, "_det_confirm_growth_ratio", 2.0))')
    source = once(source, "fire = bool(exceed and diversity['ok'] and (det_cnt[ch]", "fire = bool(exceed and diversity['ok'] and growth['ok'] and (det_cnt[ch]")
    ast.parse(source)
    return source


def growth_rows():
    return f'''                  <tr>
                    <td class="tag-desc"><span class="tag-param">{LABEL} <b class="help tag-help" tabindex="0" aria-label="{HELP}" data-tip="{HELP}">?</b></span></td>
                    <td class="tag-channel" colspan="2"><div class="tag-control" style="display:flex;align-items:center;justify-content:center;gap:12px;flex-wrap:wrap"><label class="tag-control"><input type="hidden" name="confirm_growth_enabled" value="0"><input type="checkbox" name="confirm_growth_enabled" value="1"{{tag_confirm_growth_checked}}>Enabled</label><label class="tag-control"><span aria-hidden="true">×</span><input name="confirm_growth_ratio" type="number" min="1" max="100" step="0.1" value="{{tag_confirm_growth_ratio}}" aria-label="{RATIO_LABEL}"></label></div></td>
                  </tr>
'''


def patch_growth_layout(source):
    start = source.index('                  <tr>\n                    <td class="tag-desc"><span class="tag-param">'+LABEL)
    end = source.index('                  <tr>\n                    <td class="tag-desc"><span class="tag-param">Single-impulse protection', start)
    section = source[start:end]
    assert section.count('type="checkbox" name="confirm_growth_enabled"') == 1
    assert section.count('<input name="confirm_growth_ratio"') == 1
    source = source[:start] + growth_rows() + source[end:]
    ast.parse(source)
    return source


def patch_portal(source):
    anchor = '    "impulse_guard_enabled": False,'
    source = once(source, anchor, '    "confirm_growth_enabled": False,\n    "confirm_growth_ratio": 2.0,\n' + anchor)
    source = once(source, '"confirm_phase_gate", "confirm_sample_diversity", "impulse_guard_enabled",',
                  '"confirm_phase_gate", "confirm_sample_diversity", "impulse_guard_enabled", "confirm_growth_enabled", "confirm_growth_ratio",')
    source = once(source, '    cfg["impulse_guard_enabled"] = _bool_form_value(cfg, "impulse_guard_enabled", False)',
                  '    cfg["confirm_growth_enabled"] = _bool_form_value(cfg, "confirm_growth_enabled", False)\n'
                  '    try:\n        growth_ratio = float(cfg.get("confirm_growth_ratio", 2.0))\n'
                  '    except (TypeError, ValueError, OverflowError):\n        growth_ratio = 2.0\n'
                  '    if not -float("inf") < growth_ratio < float("inf"):\n        growth_ratio = 2.0\n'
                  '    cfg["confirm_growth_ratio"] = round(max(1.0, min(100.0, growth_ratio)), 4)\n'
                  '    cfg["impulse_guard_enabled"] = _bool_form_value(cfg, "impulse_guard_enabled", False)')
    source = once(source, 'def _validate_sample_diversity(cfg):', 'def _validate_sample_diversity(cfg):\n'
                  '    if cfg.get("confirm_growth_enabled", False) and any(cfg[f"enabled{ch}"] and int(cfg[f"confirm{ch}"]) < 2 for ch in (0, 1)):\n'
                  '        raise ValueError("Peak growth requires at least two confirmations for each enabled channel.")')
    anchor = '    payload["impulse_guard_enabled"] = bool(payload["tag_detection"]["impulse_guard_enabled"])'
    source = once(source, anchor, '    for key in ("confirm_growth_enabled", "confirm_growth_ratio"):\n        payload[key] = payload["tag_detection"][key]\n' + anchor)
    source = once(source, '        "impulse_guard_enabled": _bool_form_value(form, "impulse_guard_enabled", False),',
                  '        "confirm_growth_enabled": _bool_form_value(form, "confirm_growth_enabled", False),\n'
                  '        "confirm_growth_ratio": form.get("confirm_growth_ratio", 2.0),\n'
                  '        "impulse_guard_enabled": _bool_form_value(form, "impulse_guard_enabled", False),')
    anchor = '            "impulse_guard_enabled": detector_settings.get("impulse_guard_enabled", False),'
    source = once(source, anchor, '            "confirm_growth_enabled": detector_settings.get("confirm_growth_enabled", False),\n'
                  '            "confirm_growth_ratio": detector_settings.get("confirm_growth_ratio", 2.0),\n' + anchor)
    anchor = '\t\t\t"impulse_guard_enabled": bool(cfg["impulse_guard_enabled"]),'
    source = once(source, anchor, '\t\t\t"confirm_growth_enabled": bool(cfg["confirm_growth_enabled"]),\n'
                  '\t\t\t"confirm_growth_ratio": float(cfg["confirm_growth_ratio"]),\n' + anchor)
    anchor = '    tag_impulse_guard_checked = " checked" if tag_cfg["impulse_guard_enabled"] else ""'
    source = once(source, anchor, '    tag_confirm_growth_checked = " checked" if tag_cfg["confirm_growth_enabled"] else ""\n'
                  '    tag_confirm_growth_ratio = float(tag_cfg["confirm_growth_ratio"])\n' + anchor)
    anchor = '                  <tr>\n                    <td class="tag-desc"><span class="tag-param">Single-impulse protection'
    source = once(source, anchor, growth_rows() + anchor)
    # Extend the existing offline dictionary without replacing prior translations.
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'BMI30_I18N_BOOTSTRAP' for t in n.targets))
    bootstrap = ast.literal_eval(node.value)
    translations = {LABEL: 'Рост амплитуды подтверждений', RATIO_LABEL: 'Минимальное отношение последнего пика к первому',
                    HELP: 'Последний максимум должен быть не меньше первого, умноженного на коэффициент, в последнем полном окне последовательных подтверждений. Проверка независима для каждой антенны. Используются неокруглённые амплитуды произведения в позициях обнаруженных пиков. Промежуточные максимумы не обязаны расти. Требуется не менее двух подтверждений. После принятия события действуют прежние правила удержания. По умолчанию выключено; коэффициент 2,0. Изменение активируется кнопкой сохранения и применения к Core.'}
    start = bootstrap.index('const catalogs = ') + len('const catalogs = ')
    catalogs, length = json.JSONDecoder().raw_decode(bootstrap[start:])
    catalogs['ru'].update(translations)
    bootstrap = bootstrap[:start] + json.dumps(catalogs, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + bootstrap[start+length:]
    lines = source.splitlines(keepends=True)
    source = ''.join(lines[:node.lineno-1]) + 'BMI30_I18N_BOOTSTRAP = ' + repr(bootstrap) + '\n' + ''.join(lines[node.end_lineno:])
    ast.parse(source)
    return source


def patch_core(source, version=None):
    anchor = '        "impulse_guard_enabled": ("impulse_guard_enabled",),'
    source = once(source, anchor,
        '        "confirm_growth_enabled": ("confirm_growth_enabled",),\n'
        '        "confirm_growth_ratio": ("confirm_growth_ratio",),\n' + anchor)
    anchor = '                        "impulse_guard_enabled": bool(det_settings.get("impulse_guard_enabled", False)),'
    source = once(source, anchor,
        '                        "confirm_growth_enabled": bool(det_settings.get("confirm_growth_enabled", False)),\n'
        '                        "confirm_growth_ratio": float(det_settings.get("confirm_growth_ratio", 2.0)),\n' + anchor)
    anchor = '                    "confirm_diversity0": dict(getattr(scope, "_det_last_confirm_diversity0", {}) or {}),'
    source = once(source, anchor,
        '                    "confirm_growth0": dict(getattr(scope, "_det_last_confirm_growth0", {}) or {}),\n'
        '                    "confirm_growth1": dict(getattr(scope, "_det_last_confirm_growth1", {}) or {}),\n'
        '                    "growth_rejected0": int(getattr(scope, "_det_growth_rejected0", 0)),\n'
        '                    "growth_rejected1": int(getattr(scope, "_det_growth_rejected1", 0)),\n' + anchor)
    if version:
        source = once(source, "DEFAULT_ENGINE_FILE = HOST_DIR / 'BMI30.200.py.2026-10-16-1743'",
                      f"DEFAULT_ENGINE_FILE = HOST_DIR / 'BMI30.200.py.{version}'")
    ast.parse(source)
    return source


def build(project=BASE, output=DEFAULT_OUTPUT, version=None):
    project, output = Path(project), Path(output)
    engine = project / 'host/BMI30.200.py.2026-10-16-1743'
    portal = project / 'hotspot_info_server.py'
    sources = {'engine': engine, 'portal': portal, 'core':project/'host/BMI30.001.py.2026-10-16-1743'}
    result = {name: fn(sources[name].read_text(encoding='utf8'))
              for name, fn in [('engine', patch_engine), ('portal', patch_portal)]}
    result['core'] = patch_core(sources['core'].read_text(encoding='utf8'), version)
    output.mkdir(parents=True, exist_ok=True)
    for name, source in result.items():
        (output / f'{name}.py').write_text(source, encoding='utf8', newline='\n')
    report = {'base_project': str(project), 'deployed': False,
              'defaults': {'confirm_growth_enabled': False, 'confirm_growth_ratio': 2.0},
              'formula': 'last >= first * confirm_growth_ratio',
              'source_sha256': {k: hashlib.sha256(v.read_bytes()).hexdigest() for k, v in sources.items()},
              'candidate_sha256': {k: hashlib.sha256((output/f'{k}.py').read_bytes()).hexdigest() for k in result}}
    (output/'candidate.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=BASE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--version', help='New neutral paired core/engine release suffix')
    args = parser.parse_args()
    print(json.dumps(build(args.project, args.output, args.version), ensure_ascii=False, indent=2))
