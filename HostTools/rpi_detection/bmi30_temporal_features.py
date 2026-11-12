"""Pure experimental temporal estimators; no runtime, I/O or detector outputs.

Inputs are ALREADY STM-averaged/DC-corrected u16 packets, centred at32768.
Mean/trim/median aggregate per phase, then apply the supplied production spatial
filter and product. Coherent energy instead applies the spatial filter to each
original packet as float64 before estimating cross-time coherent power.
"""
from __future__ import annotations

import numpy as np

METHODS = ('mean', 'trim1', 'median', 'coherent_energy')


def _window(raw_by_phase):
    raw = np.asarray(raw_by_phase)
    if raw.ndim != 3 or raw.shape[0] != 2 or raw.shape[1] < 1 or raw.shape[2] < 1:
        raise ValueError('Expected two phase windows with shape(2,N,samples)')
    if not np.all(np.isfinite(raw)) or np.any(raw < 0) or np.any(raw > 65535):
        raise ValueError('Original phase windows must contain finite u16-range values')
    if not np.all(raw == np.rint(raw)):
        raise ValueError('Original phase windows must contain integral samples')
    return raw.astype(np.int64, copy=False)


def temporal_estimate(raw, method='mean'):
    """Pointwise u16-centred estimate; preserves the32768 centre, np.rint policy."""
    raw = np.asarray(raw, dtype=np.int64)
    if raw.ndim != 2 or not raw.shape[0]:
        raise ValueError('Expected one nonempty phase window with shape(N,samples)')
    n = raw.shape[0]
    if method == 'mean':
        values = raw.sum(axis=0)/n
    elif method == 'trim1':
        if n < 5:
            raise ValueError('trim1 requires at least5 original packets per phase')
        values = (raw.sum(axis=0)-raw.min(axis=0)-raw.max(axis=0))/(n-2)
    elif method == 'median':
        values = np.median(raw, axis=0)
    else:
        raise ValueError('Unknown temporal amplitude estimator')
    return np.rint(values).astype(np.int32)


def coherent_energy(raw, smooth, smoothed=None):
    """Unbiased cross-time power U, before rectification; negative U is retained."""
    raw = np.asarray(raw, dtype=np.float64)
    if raw.ndim != 2 or raw.shape[0] < 2:
        raise ValueError('Coherent energy needs at least2 original packets per phase')
    filtered = (np.asarray([smooth(row) for row in raw], dtype=np.float64)
                if smoothed is None else np.asarray(smoothed, dtype=np.float64))
    if filtered.shape != raw.shape or not np.all(np.isfinite(filtered)):
        raise ValueError('Smoothed original windows must match the phase-window shape')
    x = filtered-32768.
    n = x.shape[0]
    total = x.sum(axis=0)
    return (total*total-np.square(x).sum(axis=0))/(n*(n-1))


def make_temporal_product(raw_by_phase, method, smooth, product_display, smoothed_by_phase=None):
    """Return phase display estimates and a full product curve in ADC² units.

    Callers may cache float64 spatially smoothed originals for coherent_energy;
    the optional cache must correspond to these exact two immutable windows.
    No same-sign/opposite-sign veto is added. U combines as sqrt(Ue+*Uo+).
    """
    raw = _window(raw_by_phase)
    if method not in METHODS:
        raise ValueError('Unknown temporal method')
    display = [smooth(temporal_estimate(phase, 'mean' if method == 'coherent_energy' else method))
               for phase in raw]
    result = {'current': display[0], 'previous': display[1], 'method': method,
              'depth': raw.shape[1], 'level_unit': 'raw-product ADC^2'}
    if method == 'coherent_energy':
        if smoothed_by_phase is not None and np.shape(smoothed_by_phase) != raw.shape:
            raise ValueError('Cached spatial windows must match shape(2,N,samples)')
        energy = [coherent_energy(raw[p], smooth, None if smoothed_by_phase is None else smoothed_by_phase[p])
                  for p in (0, 1)]
        result['phase_energy'] = tuple(energy)
        result['product'] = np.sqrt(np.maximum(energy[0], 0.)*np.maximum(energy[1], 0.))
        result['phase_energy_negative_counts'] = [int(np.sum(x < 0)) for x in energy]
        result['display_context_only'] = True
    else:
        result['product'] = product_display(*display, 0)
        result['display_context_only'] = False
    return result
