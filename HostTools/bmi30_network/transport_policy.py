"""Persistent selection of group transports; never changes radio/IP settings."""
import json
from pathlib import Path
import threading
import uuid

import network

MODES = {'both': ('wifi', 'bluetooth'), 'wifi': ('wifi',),
         'bluetooth': ('bluetooth',), 'off': ()}


class Selection:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / 'service-transports.json'
        self.lock = threading.RLock()
        self.mode, self.revision, self.error = 'both', 'initial', ''
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('mode') not in MODES or not isinstance(value.get('revision'), str):
                raise ValueError('Invalid selection')
            self.mode, self.revision = value['mode'], value['revision']
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError):
            self.mode, self.revision, self.error = 'off', 'invalid', 'saved-selection-invalid'

    def allows(self, medium):
        with self.lock:
            return medium in MODES[self.mode]

    def snapshot(self):
        with self.lock:
            return {'mode': self.mode, 'revision': self.revision,
                    'allowed': list(MODES[self.mode]), 'error': self.error}

    def set(self, mode, expected_revision):
        with self.lock:
            if not isinstance(mode, str) or mode not in MODES:
                raise ValueError('Choose Wi-Fi, Bluetooth, both or off')
            if expected_revision != self.revision:
                raise ValueError('Selection changed; refresh before retrying')
            if mode == self.mode and not self.error:
                return False
            revision = uuid.uuid4().hex
            network.atomic(self.path, json.dumps({'mode': mode, 'revision': revision}))
            self.mode, self.revision, self.error = mode, revision, ''
            return True


def allows(agent, medium):
    selection = getattr(agent, 'transports', None)
    return selection is None or selection.allows(medium)
