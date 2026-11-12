"""Sample-wise rolling mean of received AVG_ROI packets, per ADC and phase."""
import json
import os
from pathlib import Path

import numpy as np


def normalize_depth(value):
    if isinstance(value, bool):
        raise ValueError('Raspberry averaging must be an integer from 1 to 12')
    try:
        depth = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Raspberry averaging must be an integer from 1 to 12') from None
    if str(value).strip() != str(depth) or not 1 <= depth <= 12:
        raise ValueError('Raspberry averaging must be an integer from 1 to 12')
    return depth


class RollingMean:
    def __init__(self):
        self.epoch = 0
        self.reset()

    def reset(self):
        self.key = None
        self.slots = {}
        self.last_seq = [None, None]
        self.epoch += 1

    def push(self, channel, phase, samples, depth, key, seq=None, seq_step=1):
        depth = normalize_depth(depth)
        channel, phase = int(channel), int(phase) & 1
        context = (key, depth, len(samples))
        if context != self.key:
            self.reset()
            self.key = context
        if seq is not None:
            seq = int(seq) & 0xffffffff
            previous = self.last_seq[channel]
            if previous is not None and ((seq - previous) & 0xffffffff) != int(seq_step):
                self.reset()
                self.key = context
            self.last_seq[channel] = seq
        if depth == 1:
            return samples, True
        slot_key = (channel, phase)
        if slot_key not in self.slots:
            self.slots[slot_key] = [np.zeros((depth, len(samples)), dtype=np.int32),
                                    np.zeros(len(samples), dtype=np.int64), 0, 0]
        slot = self.slots[slot_key]
        ring, total, pos, count = slot
        total -= ring[pos]
        ring[pos] = samples
        total += ring[pos]
        count = min(depth, count + 1)
        slot[2], slot[3] = (pos + 1) % depth, count
        mean = np.rint(total / count).astype(np.int32)
        other = self.slots.get((channel, 1 - phase))
        ready = count == depth and other is not None and other[3] == depth
        return mean, ready

    def state(self, depth):
        return {'depth': int(depth), 'policy': 'last_packets_per_channel_and_phase',
                'counts': [int(self.slots.get((ch, phase), [None, None, 0, 0])[3])
                           for ch in (0, 1) for phase in (0, 1)],
                'epoch': self.epoch,
                'bytes': sum(s[0].nbytes + s[1].nbytes for s in self.slots.values())}


def initialize(scope):
    path = Path(os.environ.get('BMI30_CONFIG_JSON') or '/home/techaid/Documents/host/bmi30_config.json')
    try:
        config = json.loads(path.read_text())
        operation = config.get('operation', {})
        fallback = operation.get('rpi_avg_n', 1) if isinstance(operation, dict) else 1
        scope.rpi_avg_n = normalize_depth(config.get('rpi_avg_n', fallback))
    except (OSError, ValueError, TypeError, AttributeError):
        scope.rpi_avg_n = 1
    scope._rpi_averaging = RollingMean()


def set_depth(scope, value):
    depth = normalize_depth(value)
    if depth != getattr(scope, 'rpi_avg_n', 1):
        with scope.data_lock:
            scope.rpi_avg_n = depth
            scope._rpi_averaging.reset()
        scope._rt_detector_worker_reset_for_stream(reason='raspberry_averaging')
    return depth


def apply_frame(scope, channel, phase, samples, frame, stream):
    # Raw/full acquisition modes retain their original sample stream.
    depth = scope.rpi_avg_n if int(getattr(scope, 'stream_mode', 0)) == 2 else 1
    key = (id(stream), int(getattr(scope, '_rt_det_worker_generation', 0)),
           int(getattr(scope, 'stream_mode', 0)), int(getattr(scope, 'avg_n', 24)),
           int(getattr(scope, 'freq_hz', 200)))
    # AVG_ROI uses consecutive stream_seq headers (the original DMA sequence
    # is in reserved). Legacy step_a/step_b can retain a larger raw/latest-mode
    # stride and must not reset this window on every valid averaged packet.
    result, ready = scope._rpi_averaging.push(channel, phase, samples, depth, key,
        seq=getattr(frame, 'seq', None), seq_step=1)
    return result, ready


def state(scope):
    with scope.data_lock:
        result = scope._rpi_averaging.state(scope.rpi_avg_n)
        result['active'] = int(getattr(scope, 'stream_mode', 0)) == 2
        return result
