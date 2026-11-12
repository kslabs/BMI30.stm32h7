"""Prepare backward-compatible websplit source without touching live hardware."""
import ast


def once(source, before, after):
    if source.count(before) != 1:
        raise ValueError('Unexpected compatibility patch anchor: ' + before[:100])
    return source.replace(before, after, 1)


def tx_phase(source):
    source = once(source, 'import time\n', 'import time\nfrom .capabilities import for_stream, FeatureUnavailable\n')
    source = once(source, '        if self.serial is None:',
        '''        identity = (id(self.stream.dev), getattr(self.stream, 'connected_t', None))
        if getattr(self, '_identity_generation', None) != identity:
            self.serial = None
            self._identity_generation = identity
        if self.serial is None:''')
    source = once(source,
        '            raw = self.stream._ep0_ctrl_transfer(0xC0, GET_TX_PHASE, 0, 0, 16, timeout=300)',
        '            state = for_stream(self.stream).read("tx_phase", parse_tx_phase, 0xC0, GET_TX_PHASE, 0, 0, 16, timeout=300)')
    source = once(source, '        except Exception as exc:\n            if getattr(exc, "errno", None) == 32:',
        '        except FeatureUnavailable:\n            raise\n        except Exception as exc:\n            if getattr(exc, "errno", None) == 32:')
    source = once(source, '        state = parse_tx_phase(raw)\n', '        state.update(available=True, supported=True)\n')
    source = once(source,
        '                self.stream._ep0_ctrl_transfer(0x40, command, expected[index], 0, None, timeout=300)',
        '                for_stream(self.stream).transfer("tx_phase", 0x40, command, expected[index], 0, None, timeout=300)')
    source = once(source, '            state = self._read()\n            return self._apply(expected, state) if expected is not None else state',
        '''            try:
                state = self._read()
                return self._apply(expected, state) if expected is not None else state
            except FeatureUnavailable:
                # Saved preferences survive. An absent optional feature must
                # never prevent START_STREAM on an older STM32.
                return None''')
    return source


def usb(source):
    source = once(source, '    def get_communication_recovery(self, force=False):',
        '''    def capabilities(self):
        from .capabilities import for_stream
        return for_stream(self).snapshot()

    def get_communication_recovery(self, force=False):''')
    source = once(source, '            packet = self._ep0_ctrl_transfer(0xC0, 0x4F, 0, 0, 24, timeout=120)\n            self._comm_recovery_cache = parse_communication_recovery(packet)',
        '''            from .capabilities import for_stream
            self._comm_recovery_cache = for_stream(self).read('communication_recovery', parse_communication_recovery,
                                                            0xC0, 0x4F, 0, 0, 24, timeout=120)''')
    source = once(source, '        self._ep0_ctrl_transfer(0x40, 0x50, generation, 0, None, timeout=120)',
        '''        from .capabilities import for_stream
        for_stream(self).transfer('communication_recovery', 0x40, 0x50, generation, 0, None, timeout=120)''')
    return source


def relay(source):
    source = once(source, 'import time\n', 'import time\nfrom .capabilities import for_stream, FeatureUnavailable\n')
    source = once(source, '        result = dict(self._cache)',
        '''        result = dict(self._cache)
        stream = getattr(self.scope, 'stream', None)
        if stream is not None:
            features = for_stream(stream)
            result['supported'] = features.supported('relay')
            result['test_supported'] = features.supported('relay_test')''')
    source = once(source, "        state = decode_status(stream._ep0_ctrl_transfer(0xC0, 0x4C, 0, 0, 20, timeout=100))",
        "        state = for_stream(stream).read('relay', decode_status, 0xC0, 0x4C, 0, 0, 20, timeout=100)")
    source = once(source, "        if command == 0x4B:\n            stream._ep0_ctrl_transfer", "        feature = 'relay_test' if command == 0x4D else 'relay'\n        if command == 0x4B:\n            for_stream(stream).transfer")
    source = once(source, "            for_stream(stream).transfer(0x40, command, 0, 0, struct.pack('<I', value), timeout=100)",
        "            for_stream(stream).transfer(feature, 0x40, command, 0, 0, struct.pack('<I', value), timeout=100)")
    source = once(source, "            stream._ep0_ctrl_transfer(0x40, command, int(value), 0, None, timeout=100)",
        "            for_stream(stream).transfer(feature, 0x40, command, int(value), 0, None, timeout=100)")
    source = once(source, '        # Existing USB owner\'s lock; never wait behind higher-priority work.',
        '''        if for_stream(stream).supported('relay') is False:
            self._cache = dict(self._cache, available=False, supported=False,
                               error='Relay is unavailable on this STM32 firmware')
            while True:
                try:
                    pending = self._requests.get_nowait()
                except queue.Empty:
                    break
                pending['result'] = dict(ok=False, supported=False,
                                         error='Relay is unavailable on this STM32 firmware', relay=self.snapshot())
                pending['done'].set()
            return
        # Existing USB owner's lock; never wait behind higher-priority work.''')
    return source


def core(source):
    source = once(source, '    def tx_phase(self, params=None) -> dict[str, Any]:',
        '    def tx_phase(self, params=None) -> dict[str, Any]:')
    source = once(source, '        # USB readback runs on the HTTP thread, never on the detector event loop.',
        '''        # USB readback runs on the HTTP thread, never on the detector event loop.
        from usb_vendor.capabilities import FeatureUnavailable
        from bmi30_phase_adaptation import state as rx_phase_state''')
    source = once(source,
        '            return {"ok": True, "tx_phase": state, "phase_adaptation": rx_phase_state(self.scope), "persisted": params is not None}\n        except Exception as exc:',
        '''            return {"ok": True, "available": True, "supported": True, "tx_phase": state,
                    "phase_adaptation": rx_phase_state(self.scope), "persisted": params is not None}
        except FeatureUnavailable as exc:
            return {"ok": params is None, "available": False, "supported": False,
                    "tx_phase": {"available": False, "supported": False},
                    "phase_adaptation": rx_phase_state(self.scope), "persisted": False,
                    "error": str(exc), "reason": "unsupported"}
        except Exception as exc:''')
    source = once(source, '                "communication_recovery": communication_recovery,',
        '''                "communication_recovery": communication_recovery,
                "stm32_capabilities": stream.capabilities() if connected and hasattr(stream, 'capabilities') else {},''')
    return source


def portal(source):
    source = once(source, '      var txPhaseReady = false;', '      var txPhaseReady = false;\n      var phaseAdaptReady = false;')
    source = once(source, 'phaseAdaptInput.disabled = txPhaseBusy || !txPhaseReady;',
        'phaseAdaptInput.disabled = txPhaseBusy || !phaseAdaptReady;')
    source = once(source, "          if (!j.ok || !j.tx_phase) {{ throw new Error(j.error || 'Unable to read TX phases'); }}",
        '''          phaseAdaptReady = !!j.phase_adaptation;
          if (phaseAdaptReady) {{ phaseAdaptInput.checked = j.phase_adaptation.enabled; }}
          if (j.supported === false) {{
            txPhaseReady = false;
            txPhaseInputs.forEach(function(el) {{ el.value = ''; }});
            var noFeature = window.BMI30I18n && window.BMI30I18n.language === 'ru' ?
              'Управление фазой TX недоступно в этой прошивке STM32' : 'TX phase control is unavailable on this STM32 firmware';
            txPhaseMessage(noFeature, false);
            return;
          }}
          if (!j.ok || !j.tx_phase) {{ throw new Error(j.error || 'Unable to read TX phases'); }}''')
    source = once(source, 'relayTest.disabled = relayBusy || !available || !cfg.enabled;',
        'relayTest.disabled = relayBusy || !available || !cfg.enabled || cfg.test_supported === false;')
    return source


def validate(source):
    ast.parse(source, feature_version=(3, 11))
    return source
