"""Add a live, persistent RX adaptation switch to the existing full runtime."""
def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Expected one patch anchor: ' + old[:100])
    return text.replace(old, new, 1)


def engine(text):
    text = once(text, 'import os\n', 'import os\nfrom bmi30_phase_adaptation import initialize as initialize_rx_phase, fixed_shift as fixed_rx_shift, state as rx_phase_state\n')
    text = once(text, '\t\tself._det_last_shift1 = 0\n', '\t\tself._det_last_shift1 = 0\n\t\tinitialize_rx_phase(self)\n')
    text = once(text,
        'def _rt_detector_product_current_prev(current: np.ndarray, previous: np.ndarray, max_shift: int, source: str, level_scale: float):',
        'def _rt_detector_product_current_prev(current: np.ndarray, previous: np.ndarray, max_shift: int, source: str, level_scale: float, fixed_shift=None):')
    anchor = '\tif max_shift <= 0:\n\t\tshift = 0\n\t\tprod_abs = np.empty(n, dtype=np.int64)'
    text = once(text, anchor, '''\tif fixed_shift is not None:
\t\tshift = max(-(n - 1), min(n - 1, int(fixed_shift)))
\t\tprod_abs = _rt_detector_product_display(current, previous, shift)
\t\tpeak_idx, peak_value, supported = _segment_peak(prod_abs, 0)
\t\tlevel = int(_rt_detector_level_from_peak(peak_value, source, level_scale)) if supported else 0
\t\treturn level, shift, peak_idx, peak_value, prod_abs
''' + anchor)
    text = once(text,
        "made = _rt_detector_product_current_prev(cur, prev_arr, int(max_shift), str(source or 'prod'), float(level_scale or _rt_product_default_scale()))",
        "made = _rt_detector_product_current_prev(cur, prev_arr, int(max_shift), str(source or 'prod'), float(level_scale or _rt_product_default_scale()), fixed_shift=fixed_rx_shift(meta, ch_i))")
    text = once(text,
        "made = _rt_detector_product_current_prev(cur, prev[ch], int(max_shift), str(source or 'prod'), float(level_scale or _rt_product_default_scale()))",
        "made = _rt_detector_product_current_prev(cur, prev[ch], int(max_shift), str(source or 'prod'), float(level_scale or _rt_product_default_scale()), fixed_shift=fixed_rx_shift(meta, ch))")
    text = once(text,
        "made = _rt_detector_product_current_prev(even, odd, int(max_shift), 'prod', float(level_scale))",
        "made = _rt_detector_product_current_prev(even, odd, int(max_shift), 'prod', float(level_scale), fixed_shift=fixed_rx_shift(self, ch))")
    text = once(text, '\tdef _rt_product_current_prev(self, current: np.ndarray, previous: np.ndarray):',
        '\tdef _rt_product_current_prev(self, current: np.ndarray, previous: np.ndarray, channel=0):')
    text = once(text,
        'made = _rt_detector_product_current_prev(current[:n], previous[:n], int(max_shift), source, float(level_scale))',
        'made = _rt_detector_product_current_prev(current[:n], previous[:n], int(max_shift), source, float(level_scale), fixed_shift=fixed_rx_shift(self, channel))')
    text = once(text, 'made = self._rt_product_current_prev(arr, prev)', 'made = self._rt_product_current_prev(arr, prev, ch)')
    text = once(text, "\t\t\t**common_mode_settings(getattr(self, '_det_common_mode', {})),",
        "\t\t\t**common_mode_settings(getattr(self, '_det_common_mode', {})),\n\t\t\t'rx_phase_fixed_shifts': rx_phase_state(self)['fixed_shifts'],")
    text = once(text, '\t\t\tbest_shift = 0\n\t\t\tmax_sum = -np.inf',
        "\t\t\tfixed = fixed_rx_shift(self, 1 if channel_name == 'ADC1' else 0)\n\t\t\tif fixed is not None:\n\t\t\t\tshift_start = shift_end = max(-(N - 1), min(N - 1, fixed))\n\t\t\tbest_shift = 0\n\t\t\tmax_sum = -np.inf")
    return text


def core(text):
    text = once(text, '            return {"ok": True, "tx_phase": state, "persisted": params is not None}',
        '            from bmi30_phase_adaptation import state as rx_phase_state\n            return {"ok": True, "tx_phase": state, "phase_adaptation": rx_phase_state(self.scope), "persisted": params is not None}')
    text = once(text, '        scope = self.scope\n        action = action.strip().lower()',
        '''        scope = self.scope
        action = action.strip().lower()
        if action == "phase_adaptation":
            from bmi30_phase_adaptation import set_enabled
            policy = set_enabled(scope, params.get("enabled"), persist=True)
            return {"ok": True, "phase_adaptation": policy, "persisted": True}''')
    return text


def portal(text):
    text = once(text, '<h3>TX phase</h3>', '''<h3>TX phase</h3>
              <label class="lcd-role-field"><input type="checkbox" data-phase-adaptation aria-label="Auto phase adaptation" disabled><span>Auto phase adaptation</span></label>''')
    text = once(text, "      var txPhaseBusy = false;", "      var phaseAdaptInput = txPhaseForm.querySelector('[data-phase-adaptation]');\n      var txPhaseBusy = false;")
    text = once(text, '        txPhaseRefresh.disabled = txPhaseBusy;', '        txPhaseRefresh.disabled = txPhaseBusy;\n        phaseAdaptInput.disabled = txPhaseBusy || !txPhaseReady;')
    text = once(text, "JSON.stringify({{action:'tx_phase', params:params}})",
        "JSON.stringify({{action:params.adaptation ? 'phase_adaptation' : 'tx_phase', params:params}})")
    text = once(text, "          return r.json();\n        }}).then(function(j) {{\n          if (!j.ok || !j.tx_phase)",
        "          return r.json();\n        }}).then(function(j) {{\n          if (j.ok && j.phase_adaptation && !j.tx_phase) {{ return fetch('portal-core/api/tx-phase', {{cache:'no-store'}}).then(function(r){{return r.json();}}); }}\n          return j;\n        }}).then(function(j) {{\n          if (!j.ok || !j.tx_phase)")
    text = once(text, '          var state = j.tx_phase;', '          var state = j.tx_phase;\n          var policy = j.phase_adaptation || {{enabled:true}};\n          phaseAdaptInput.checked = policy.enabled;')
    text = once(text, "((saved ? 'Applied · saved' : 'Current device phases') + (state.tx_enabled ? '' : ' · TX is off')), false);",
        "((saved ? 'Applied · saved' : 'Current device phases') + (state.tx_enabled ? '' : ' · TX is off') + (policy.enabled ? ' · RX auto' : ' · RX fixed A/B: ' + policy.fixed_shifts.join('/') + ' samples')), false);")
    text = once(text, "      txPhaseRefresh.addEventListener('click', function() {{ txPhaseRequest(null); }});",
        "      phaseAdaptInput.addEventListener('change', function() {{ txPhaseRequest({{adaptation:true,enabled:phaseAdaptInput.checked}}); }});\n      txPhaseRefresh.addEventListener('click', function() {{ txPhaseRequest(null); }});")
    text = once(text, 'Each transmitter is set relative to the shared synchronization. Changes apply immediately and are saved automatically for this device.',
        'TX1/TX2 phases apply and save immediately. With auto phase adaptation off, RX A/B retain their current alignment while TX remains under manual control.')
    return text
