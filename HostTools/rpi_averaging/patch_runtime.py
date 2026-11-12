"""Patch the inspected complete BMI30 runtime; anchors fail if it has changed."""
from pathlib import Path
import ast


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Expected one patch anchor: ' + old[:100])
    return text.replace(old, new, 1)


def engine(text):
    text = once(text, 'import os\n', 'import os\nfrom bmi30_averaging import initialize as initialize_rpi_averaging, apply_frame as average_rpi_frame, RollingMean as RpiRollingMean\n')
    text = once(text, '\t\tinitialize_rx_phase(self)\n', '\t\tinitialize_rx_phase(self)\n\t\tinitialize_rpi_averaging(self)\n')
    for ch, frame in ((0,'a'), (1,'b')):
        text = once(text, f'\t\t\t\t\t\tch{ch} = self._apply_input_smoothing(ch{ch})',
            f'\t\t\t\t\t\tch{ch}, rpi_ready{ch} = average_rpi_frame(self, {ch}, par, ch{ch}, {frame}, stream)\n'
            f'\t\t\t\t\t\tch{ch} = self._apply_input_smoothing(ch{ch})')
        old = f"if rt_history.feed({ch}, ch{ch}, par, stamp{ch}, getattr(self, '_rt_det_worker_generation', 0)):"
        text = once(text, old,
            f"if rpi_ready{ch} and rt_history.feed({ch}, ch{ch}, par, stamp{ch}, (int(getattr(self, '_rt_det_worker_generation', 0)) << 32) + self._rpi_averaging.epoch):")
        text = once(text, f'\t\t\t\t\t\trt_pending.append(({ch}, ch{ch}, seqv))',
            f'\t\t\t\t\t\tif rpi_ready{ch}:\n\t\t\t\t\t\t\trt_pending.append(({ch}, ch{ch}, seqv))')
    # Ensure a hardware-window change also fences in-flight detector work.
    text = once(text, '\t\tself.avg_n = new_n\n',
        "\t\tprevious_n = self.avg_n\n\t\tself.avg_n = new_n\n\t\tif previous_n != new_n:\n\t\t\tself._rt_detector_worker_reset_for_stream(reason='stm32_averaging')\n")
    # Raw recordings keep their packets unchanged and carry the averaging depth.
    anchor = "\t\t\tavg_n=np.array([int(getattr(self, 'avg_n', 0) or 0)], dtype=np.int32),"
    text = once(text, anchor, anchor+"\n\t\t\trpi_avg_n=np.array([int(getattr(self, 'rpi_avg_n', 1))], dtype=np.int32),")
    anchor = "\t\t\tavg_n = scalar('avg_n', int(getattr(self, 'avg_n', 0) or 0))"
    text = once(text, anchor, anchor+"\n\t\t\trpi_avg_n = max(1, min(12, int(scalar('rpi_avg_n', 1))))")
    text = once(text, '\t\t\tfor i in range(packet_count):\n',
        '\t\t\treplay_average = RpiRollingMean()\n\t\t\tfor i in range(packet_count):\n')
    text = once(text, '\t\t\t\t\tstate[key] = self._apply_input_smoothing(raw_frame)',
        "\t\t\t\t\taveraged, average_ready = replay_average.push(ch, phase, raw_frame, rpi_avg_n if int(stream_mode) == 2 else 1, 'replay')\n\t\t\t\t\tstate[key] = self._apply_input_smoothing(averaged)")
    text = once(text, '\t\t\t\tif base_len > 0:\n\t\t\t\t\tprocess_offline_detection(i, ch, base_len)',
        '\t\t\t\tif base_len > 0 and n > 0 and average_ready:\n\t\t\t\t\tprocess_offline_detection(i, ch, base_len)')
    anchor = "\t\t\t\t\t\t'avg_n': int(avg_n),"
    text = once(text, anchor, anchor+"\n\t\t\t\t\t\t'rpi_avg_n': int(rpi_avg_n),")
    return text


def core(text):
    text = once(text, '            avg_n = _set_scope_avg_n(scope, params.get("avg_n", params.get("n", 24)))\n            scope._on_avg_change(0)\n            return {"ok": True, "avg_n": avg_n}',
        '''            from bmi30_averaging import normalize_depth, set_depth
            rpi_n = normalize_depth(params.get("rpi_avg_n", getattr(scope, "rpi_avg_n", 1)))
            old_avg = scope.avg_n
            avg_n = _set_scope_avg_n(scope, params.get("avg_n", params.get("n", old_avg)))
            if avg_n != old_avg:
                scope._rt_detector_worker_reset_for_stream(reason="stm32_averaging")
                scope._on_avg_change(0)
            rpi_n = set_depth(scope, rpi_n)
            return {"ok": True, "avg_n": avg_n, "rpi_avg_n": rpi_n}''')
    text = once(text, '                    "avg_n": int(getattr(scope, "avg_n", 0) or 0),',
        '                    "avg_n": int(getattr(scope, "avg_n", 0) or 0),\n                    "rpi_avg_n": int(getattr(scope, "rpi_avg_n", 1)),')
    text = once(text, '                "communication_recovery": communication_recovery,',
        '                "averaging": __import__("bmi30_averaging").state(scope),\n                "communication_recovery": communication_recovery,')
    old = '<select id="avgSel" title="Averaging" aria-label="Averaging" onchange="sendAvg()"><option>8</option><option>16</option><option>24</option><option>32</option><option>40</option><option>48</option><option>56</option><option>64</option></select>'
    text = once(text, old, '<span id="avgReadout" style="display:flex;align-items:center;justify-content:center;align-self:stretch;text-align:center;min-width:0;line-height:1;font-variant-numeric:tabular-nums" title="Averaging: STM32 / Raspberry. Change in 3Operating Mode." aria-label="Averaging STM32 / Raspberry">24/1</span>')
    text = once(text, "function sendAvg(){cmd('avg',{avg_n:parseInt(byId('avgSel').value,10)||24})}\n", '')
    text = once(text, 'avgValue=mode.avg_n||24,', "avgValue=String(mode.avg_n||24)+'/'+String(mode.rpi_avg_n||1),")
    text = once(text, "setSelectValue('avgSel',avgValue)", "setText('avgReadout',avgValue)")
    text = once(text, 'avg_n:Number(mode.avg_n)||0,working_window:',
        'avg_n:Number(mode.avg_n)||0,rpi_avg_n:Number(mode.rpi_avg_n)||1,working_window:')
    return text


def portal(text):
    text = once(text, 'def save_default_avg_n(avg_n: int) -> None:',
        'def save_default_avg_n(avg_n: int, rpi_avg_n: int = 1) -> None:')
    text = once(text, '    payload["avg_n"] = avg_n\n',
        '    rpi_avg_n = _normalize_rpi_avg_n(rpi_avg_n)\n    payload["avg_n"] = avg_n\n    payload["rpi_avg_n"] = rpi_avg_n\n')
    text = once(text, '    operation["avg_n"] = avg_n\n', '    operation["avg_n"] = avg_n\n    operation["rpi_avg_n"] = rpi_avg_n\n')
    text = once(text, 'def avg_n_from_form(form: dict[str, str]) -> int:', '''def _normalize_rpi_avg_n(value) -> int:
    depth = int(value)
    if isinstance(value, bool) or str(value).strip() != str(depth) or not 1 <= depth <= 12:
        raise ValueError("Raspberry averaging must be an integer from 1 to 12")
    return depth


def load_default_rpi_avg_n() -> int:
    payload = _load_config_json()
    operation = payload.get("operation", {})
    fallback = operation.get("rpi_avg_n", 1) if isinstance(operation, dict) else 1
    try:
        return _normalize_rpi_avg_n(payload.get("rpi_avg_n", fallback))
    except (ValueError, TypeError):
        return 1


def avg_n_from_form(form: dict[str, str]) -> int:''')
    text = once(text, 'def apply_avg_n_to_core(avg_n: int) -> tuple[bool, str]:',
        'def apply_avg_n_to_core(avg_n: int, rpi_avg_n: int = 1) -> tuple[bool, str]:')
    text = once(text, '        "params": {"avg_n": avg_n},', '        "params": {"avg_n": avg_n, "rpi_avg_n": rpi_avg_n},')
    text = once(text, 'return True, f"Averaging set to {avg_n}"', 'return True, f"Averaging set to {avg_n}/{rpi_avg_n}"')
    text = once(text, '    sound_cfg = load_sound_config()\n    lcd_role_cfg = load_lcd_role_overlay()\n',
        '''    default_rpi_avg_n = load_default_rpi_avg_n()
    rpi_avg_n_options = "".join(f'<option value="{value}"{" selected" if default_rpi_avg_n == value else ""}>{value}</option>' for value in range(1, 13))
    sound_cfg = load_sound_config()
    lcd_role_cfg = load_lcd_role_overlay()
''')
    text = once(text, '<select name="avg_n">{avg_n_options}</select>',
        '<select name="avg_n" aria-label="STM32 averaging">{avg_n_options}</select>')
    text = once(text, '<small>Used by BMI30 core on startup; live apply updates the running core.</small>\n              </label>',
        '<small>STM32 buffers; applied at startup or by Save and Apply.</small>\n              </label>\n              <label class="field operation-avg-field">\n                <span>Raspberry averaging</span>\n                <select name="rpi_avg_n" aria-label="Raspberry averaging">{rpi_avg_n_options}</select>\n                <small>Last packets per channel and phase; 1 = off.</small>\n              </label>')
    text = once(text, '            avg_n = avg_n_from_form(form)\n',
        '            avg_n = avg_n_from_form(form)\n            rpi_avg_n = _int_form_value(form, "rpi_avg_n", load_default_rpi_avg_n(), 1, 12)\n')
    text = once(text, '                save_default_avg_n(avg_n)', '                save_default_avg_n(avg_n, rpi_avg_n)')
    text = once(text, 'apply_avg_n_to_core(avg_n))', 'apply_avg_n_to_core(avg_n, rpi_avg_n))')
    text = once(text, "'Default averaging': 'Number of frames averaged by the acquisition core. Saved for startup; Save and Apply also updates the running core.',",
        "'Default averaging': 'STM32: 8-64 original buffers. Raspberry: 1-12 received packets per channel and phase; 1 disables extra averaging. Detection waits for the full window, then updates on every packet. Longer windows reduce random noise but can weaken short tag passes and delay detection. Saved for startup; Save and Apply also updates the running core.',")
    return text


def prepare(source, target):
    target.mkdir(parents=True, exist_ok=True)
    for name, patcher in [('engine',engine), ('core',core), ('portal',portal)]:
        text = patcher((source/(name+'.py')).read_text(encoding='utf8'))
        ast.parse(text)
        (target/(name+'.py')).write_text(text, encoding='utf8', newline='\n')


if __name__ == '__main__':
    import sys
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
