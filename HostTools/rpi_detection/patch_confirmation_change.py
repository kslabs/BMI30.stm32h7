"""Extend the 2026-10-23-1435 websplit gate without changing its window rules.

confirm_change_mode is canonical; confirm_growth_enabled is a compatibility
view. Keep confirm_growth_ratio as the single stored coefficient so upgrades
retain every device's existing value.
"""
import ast
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'FirmwareReleases/RaspberryPi/2026-10-23-1435/review'
OUTPUT = ROOT / '.tmp/confirmation-change-20261023/candidate'
LABEL = 'Confirmation amplitude change'
RATIO_LABEL = 'Minimum amplitude change factor'
UP_LABEL = 'Accept amplitude increase'
DOWN_LABEL = 'Accept amplitude decrease'
HELP = ('Compare the last and first peak amplitudes in a full window of consecutive confirmations, independently for each antenna. '
        'Up: last >= first × factor. Down: first >= last × factor. Either: accept either direction. '
        'Both checkboxes: accept either direction. Neither: skip this check. One factor is shared by all directions. Uses unrounded product peaks at their detected positions; '
        'intermediate peaks need not change monotonically. At least two confirmations are required. Existing HOLD release rules remain in effect. '
        'Default off; factor 2.0. Save and Apply to Core activates the change.')

MODE_FUNCTION = '''def _det_confirmation_change_mode(value=None, legacy_enabled=False):
	"""An explicit mode wins over the legacy enabled flag, including explicit off."""
	if value is None:
		return "up" if str(legacy_enabled).strip().lower() in ("true", "1", "yes", "on") else "off"
	mode = str(value).strip().lower()
	if mode not in ("up", "down", "either", "off"):
		raise ValueError("Invalid confirmation amplitude change mode")
	return mode


'''

def once(source, old, new):
    assert source.count(old) == 1, (source.count(old), old[:140])
    return source.replace(old, new, 1)


def patch_engine(source):
    source = once(source, 'def _rt_detector_confirmation_growth(', MODE_FUNCTION + 'def _rt_detector_confirmation_growth(')
    source = once(source, '\tenabled = _det_parse_bool(enabled, False)\n\tratio = _det_clamp_float',
                  '\tmode = _det_confirmation_change_mode(enabled if isinstance(enabled, str) else None, enabled)\n'
                  '\tenabled = mode != "off"\n\tratio = _det_clamp_float')
    source = once(source, 'diag = {"enabled": enabled, "required_ratio": ratio,',
                  'diag = {"enabled": enabled, "mode": mode, "required_ratio": ratio,')
    source = once(source, "\t\tok = bool(last >= first * ratio)\n\t\treturn dict(diag, first=first, last=last, ratio=observed, ok=ok,\n\t\t            reason='growth_confirmed' if ok else 'insufficient_growth')",
                  '\t\tup, down = bool(last >= first * ratio), bool(first >= last * ratio)\n'
                  '\t\tok = up if mode == "up" else down if mode == "down" else up or down\n'
                  '\t\treturn dict(diag, first=first, last=last, ratio=observed, ok=ok,\n'
                  '\t\t            direction=("up" if up else "down") if ok else None,\n'
                  '\t\t            reason=(("growth_confirmed" if mode == "up" else "decrease_confirmed" if mode == "down" else "change_confirmed")\n'
                  '\t\t                    if ok else "insufficient_growth" if mode == "up" else "insufficient_change"))')
    # Pass the explicit direction through realtime, parent recheck, event and Player gates.
    old = 'getattr(self, "_det_confirm_growth_enabled", False), getattr(self, "_det_confirm_growth_ratio", 2.0))'
    assert source.count(old) == 5, source.count(old)
    source = source.replace(old, 'getattr(self, "_det_confirm_change_mode", getattr(self, "_det_confirm_growth_enabled", False)), getattr(self, "_det_confirm_growth_ratio", 2.0))')
    anchor = 'def save_det_channel_settings(settings: dict):\n\t"""Save per-channel detector settings to config file (best-effort merge)."""\n'
    source = once(source, anchor, anchor + '\tsettings = dict(settings or {})\n'
                  '\tif "confirm_change_mode" in settings or "confirm_growth_enabled" in settings:\n'
                  '\t\tmode = _det_confirmation_change_mode(settings.get("confirm_change_mode"), settings.get("confirm_growth_enabled", False))\n'
                  '\t\tsettings.update(confirm_change_mode=mode, confirm_growth_enabled=mode != "off")\n')
    source = source.replace('for key in ("confirm_growth_enabled", "confirm_growth_ratio"):',
                            'for key in ("confirm_change_mode", "confirm_growth_enabled", "confirm_growth_ratio"):')
    source = once(source, '\t\t"confirm_growth_enabled": _det_parse_bool(_cfg("confirm_growth_enabled", False), False),',
                  '\t\t"confirm_change_mode": _det_confirmation_change_mode(_cfg("confirm_change_mode"), _cfg("confirm_growth_enabled", False)),\n'
                  '\t\t"confirm_growth_enabled": _det_confirmation_change_mode(_cfg("confirm_change_mode"), _cfg("confirm_growth_enabled", False)) != "off",')
    source = once(source, '\t\tself._det_confirm_growth_enabled = _det_parse_bool(self._det_channel_settings.get("confirm_growth_enabled", False), False)',
                  '\t\tself._det_confirm_change_mode = _det_confirmation_change_mode(self._det_channel_settings.get("confirm_change_mode"), self._det_channel_settings.get("confirm_growth_enabled", False))\n'
                  '\t\tself._det_confirm_growth_enabled = self._det_confirm_change_mode != "off"')
    source = once(source, '\t\t\t"confirm_growth_enabled": bool(getattr(self, "_det_confirm_growth_enabled", False)),',
                  '\t\t\t"confirm_change_mode": _det_confirmation_change_mode(getattr(self, "_det_confirm_change_mode", None), getattr(self, "_det_confirm_growth_enabled", False)),\n'
                  '\t\t\t"confirm_growth_enabled": bool(getattr(self, "_det_confirm_growth_enabled", False)),')
    source = once(source, '\t\tgrowth_enabled = _det_parse_bool(settings.get("confirm_growth_enabled", getattr(self, "_det_confirm_growth_enabled", False)), False)',
                  '\t\told_mode = _det_confirmation_change_mode(getattr(self, "_det_confirm_change_mode", None), getattr(self, "_det_confirm_growth_enabled", False))\n'
                  '\t\tmode = _det_confirmation_change_mode(settings.get("confirm_change_mode"), settings.get("confirm_growth_enabled", False)) if ("confirm_change_mode" in settings or "confirm_growth_enabled" in settings) else old_mode\n'
                  '\t\tgrowth_enabled = mode != "off"')
    source = once(source, '\t\tgrowth_changed = (growth_enabled, growth_ratio) != (bool(getattr(self, "_det_confirm_growth_enabled", False)), getattr(self, "_det_confirm_growth_ratio", 2.0))',
                  '\t\tgrowth_changed = (mode, growth_ratio) != (old_mode, getattr(self, "_det_confirm_growth_ratio", 2.0))\n'
                  '\t\tself._det_confirm_change_mode = mode')
    source = source.replace('Peak growth requires at least two confirmations', 'Amplitude change requires at least two confirmations')
    ast.parse(source)
    return source


def patch_portal(source):
    source = once(source, 'def _normalize_tag_detection_config(', MODE_FUNCTION.replace('\t', '    ') + 'def _normalize_tag_detection_config(')
    # None in defaults permits migration of enabled=true configurations to up.
    source = once(source, '    "confirm_growth_enabled": False,', '    "confirm_change_mode": None,\n    "confirm_growth_enabled": False,')
    source = once(source, '"impulse_guard_enabled", "confirm_growth_enabled", "confirm_growth_ratio",',
                  '"impulse_guard_enabled", "confirm_change_mode", "confirm_growth_enabled", "confirm_growth_ratio",')
    source = once(source, '    cfg["confirm_growth_enabled"] = _bool_form_value(cfg, "confirm_growth_enabled", False)',
                  '    cfg["confirm_change_mode"] = _det_confirmation_change_mode(cfg.get("confirm_change_mode"), cfg.get("confirm_growth_enabled", False))\n'
                  '    cfg["confirm_growth_enabled"] = cfg["confirm_change_mode"] != "off"')
    source = once(source, 'for key in ("confirm_growth_enabled", "confirm_growth_ratio"):',
                  'for key in ("confirm_change_mode", "confirm_growth_enabled", "confirm_growth_ratio"):')
    anchor = 'def tag_detection_config_from_form(form: dict[str, str]) -> dict[str, Any]:\n'
    source = once(source, anchor, anchor +
                  '    mode = form.get("confirm_change_mode")\n'
                  '    if "confirm_change_up" in form or "confirm_change_down" in form:\n'
                  '        up = _bool_form_value(form, "confirm_change_up", False)\n'
                  '        down = _bool_form_value(form, "confirm_change_down", False)\n'
                  '        mode = ("either" if down else "up") if up else ("down" if down else "off")\n')
    source = once(source, '        "confirm_growth_enabled": _bool_form_value(form, "confirm_growth_enabled", False),',
                  '        "confirm_change_mode": mode,\n'
                  '        "confirm_growth_enabled": _bool_form_value(form, "confirm_growth_enabled", False),')
    source = once(source, '            "confirm_growth_enabled": detector_settings.get("confirm_growth_enabled", False),',
                  '            "confirm_change_mode": detector_settings.get("confirm_change_mode"),\n'
                  '            "confirm_growth_enabled": detector_settings.get("confirm_growth_enabled", False),')
    source = once(source, '\t\t\t"confirm_growth_enabled": bool(cfg["confirm_growth_enabled"]),',
                  '\t\t\t"confirm_change_mode": cfg["confirm_change_mode"],\n'
                  '\t\t\t"confirm_growth_enabled": bool(cfg["confirm_growth_enabled"]),')
    source = once(source, '    tag_confirm_growth_checked = " checked" if tag_cfg["confirm_growth_enabled"] else ""',
                  '    tag_confirm_change_up_checked = " checked" if tag_cfg["confirm_change_mode"] in ("up", "either") else ""\n'
                  '    tag_confirm_change_down_checked = " checked" if tag_cfg["confirm_change_mode"] in ("down", "either") else ""')
    start = source.index('                  <tr>\n                    <td class="tag-desc"><span class="tag-param">Confirmation peak growth')
    end = source.index('                  <tr>', start + 30)
    row = '''                  <tr>
                    <td class="tag-desc"><span class="tag-param">LABEL <b class="help tag-help" tabindex="0" aria-label="HELP" data-tip="HELP">?</b></span></td>
                    <td class="tag-channel" colspan="2"><div class="tag-control" style="display:flex;align-items:center;justify-content:center;gap:12px;flex-wrap:wrap"><label class="tag-control"><input type="hidden" name="confirm_change_up" value="0"><input type="checkbox" name="confirm_change_up" value="1"{tag_confirm_change_up_checked} aria-label="UP_LABEL">↑</label><label class="tag-control"><input type="hidden" name="confirm_change_down" value="0"><input type="checkbox" name="confirm_change_down" value="1"{tag_confirm_change_down_checked} aria-label="DOWN_LABEL">↓</label><label class="tag-control"><span aria-hidden="true">×</span><input name="confirm_growth_ratio" type="number" min="1" max="100" step="0.1" value="{tag_confirm_growth_ratio}" aria-label="RATIO_LABEL"></label></div></td>
                  </tr>
'''.replace('UP_LABEL', UP_LABEL).replace('DOWN_LABEL', DOWN_LABEL).replace('RATIO_LABEL', RATIO_LABEL).replace('LABEL', LABEL).replace('HELP', HELP)
    source = source[:start] + row + source[end:]
    source = source.replace('Peak growth requires at least two confirmations', 'Amplitude change requires at least two confirmations')
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'BMI30_I18N_BOOTSTRAP' for t in n.targets))
    bootstrap = ast.literal_eval(node.value)
    start = bootstrap.index('const catalogs = ') + len('const catalogs = ')
    catalogs, length = json.JSONDecoder().raw_decode(bootstrap[start:])
    catalogs['ru'].update({LABEL: 'Изменение амплитуды подтверждений', RATIO_LABEL: 'Минимальный коэффициент изменения амплитуды',
        UP_LABEL: 'Разрешить рост амплитуды', DOWN_LABEL: 'Разрешить снижение амплитуды',
        HELP: 'Сравниваются последний и первый максимумы в полном окне последовательных подтверждений, независимо для каждой антенны. ↑: последний ≥ первый × коэффициент. ↓: первый ≥ последний × коэффициент. Обе галочки: любое из этих условий. Ни одной: проверка отключена. Коэффициент один для всех направлений. Используются неокруглённые амплитуды произведения в позициях обнаруженных пиков; промежуточные максимумы не обязаны изменяться монотонно. Требуется не менее двух подтверждений. Прежние правила удержания сохраняются. По умолчанию выключено; коэффициент 2,0. Изменение активируется кнопкой сохранения и применения к Core.'})
    bootstrap = bootstrap[:start] + json.dumps(catalogs, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + bootstrap[start+length:]
    lines = source.splitlines(keepends=True)
    source = ''.join(lines[:node.lineno-1]) + 'BMI30_I18N_BOOTSTRAP = ' + repr(bootstrap) + '\n' + ''.join(lines[node.end_lineno:])
    ast.parse(source)
    return source


def patch_core(source):
    source = once(source, '        "confirm_growth_enabled": ("confirm_growth_enabled",),',
                  '        "confirm_change_mode": ("confirm_change_mode",),\n        "confirm_growth_enabled": ("confirm_growth_enabled",),')
    source = once(source, '                        "confirm_growth_enabled": bool(det_settings.get("confirm_growth_enabled", False)),',
                  '                        "confirm_change_mode": det_settings.get("confirm_change_mode", "up" if det_settings.get("confirm_growth_enabled", False) else "off"),\n'
                  '                        "confirm_growth_enabled": bool(det_settings.get("confirm_growth_enabled", False)),')
    ast.parse(source)
    return source


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, patch in (('engine.py', patch_engine), ('core.py', patch_core), ('portal.py', patch_portal)):
        (OUTPUT/name).write_text(patch((BASE/name).read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
    for name in ('capabilities.py', 'usb_stream.py', 'tx_phase.py', 'relay_control.py'):
        shutil.copy2(BASE/name, OUTPUT/name)
    print(str(OUTPUT))


if __name__ == '__main__':
    main()
