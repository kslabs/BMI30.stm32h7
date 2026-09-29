#!/usr/bin/env python3
"""Offline comparison of measured ringing phase across TX switch experiments.

No hardware access. Reports a phase angle relative to ADC frame start, NOT a
universal calibrated 0/180 polarity. A frequency selected from baseline remains
fixed across the experiment. Clipping and mixed sources limit interpretation.
Requires numpy; --plot also requires matplotlib.
"""
import argparse
import json
from pathlib import Path

import numpy as np


def wrap(angle):
    return (angle + 180) % 360 - 180


def projection(waves, frequency, fs, start=20, stop=240):
    """Windowed complex projection with a constant/linear background removed."""
    x = np.asarray(waves, dtype=float)[:, start:stop]
    t = np.arange(start, stop) / fs
    trend = np.column_stack((np.ones(len(t)), (t - t.mean()) / np.ptp(t)))
    weight = np.hanning(len(t))
    coefficients = np.linalg.lstsq(trend * np.sqrt(weight[:, None]),
                                  (x * np.sqrt(weight)).T, rcond=None)[0]
    residual = x - (trend @ coefficients).T
    return residual @ (weight * np.exp(-2j * np.pi * frequency * t))


def estimate_frequency(even, odd, fs):
    d = (even.mean(axis=0) - odd.mean(axis=0)) / 2
    frequencies = np.arange(1500, 12001, 10)
    power = [abs(projection(d[None, :], f, fs)[0]) for f in frequencies]
    return float(frequencies[int(np.argmax(power))])


def measure(even, odd, frequency, fs):
    qe, qo = projection(even, frequency, fs), projection(odd, frequency, fs)
    q = (qe.mean() - qo.mean()) / 2
    aligned = np.r_[qe, -qo]
    magnitude = np.abs(aligned)
    valid = magnitude > 1e-9
    unit = aligned[valid] / magnitude[valid]
    phase = float(np.degrees(np.angle(q)))
    # This is descriptive scatter of the recorded sparse frames, not accuracy.
    scatter = abs(wrap(np.degrees(np.angle(aligned[valid])) - phase))
    d = (even.mean(axis=0) - odd.mean(axis=0)) / 2
    windows = [(20, 140), (100, 220)]
    window_angles = [float(np.degrees(np.angle(projection(d[None, :], frequency, fs, a, b)[0])))
                     for a, b in windows]
    return dict(phase_deg=phase, complex_real=float(q.real), complex_imag=float(q.imag),
                phase_scatter_p95_deg=float(np.percentile(scatter, 95)),
                unit_vector_coherence=float(abs(unit.mean())),
                even_odd_separation_deg=float(wrap(np.degrees(np.angle(qe.mean() / qo.mean())))),
                subwindow_angles_deg=window_angles,
                subwindow_disagreement_deg=float(abs(wrap(window_angles[0] - window_angles[1]))),
                clipped_fraction=float(np.mean((np.r_[even[:,20:240],odd[:,20:240]] <= 0) |
                                               (np.r_[even[:,20:240],odd[:,20:240]] >= 65535))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture_root', type=Path)
    parser.add_argument('--sample-rate', type=float, default=600 * 400.29)
    parser.add_argument('--plot', action='store_true')
    args = parser.parse_args()
    stages = list(json.loads((args.capture_root / 'results.json').read_text()))
    results = {}
    for path in sorted((args.capture_root / 'baseline').glob('*/frames.npz')):
        for ch in (0, 1):
            records = {}
            for stage in stages:
                with np.load(args.capture_root / stage / path.parent.name / 'frames.npz') as z:
                    w, p = z['wave'][:, ch].astype(float), z['parity']
                    records[stage] = (w[p == 0], w[p == 1])
            frequency = estimate_frequency(*records['baseline'], args.sample_rate)
            channel = dict(frequency_hz=frequency, stages={})
            for stage, (e, o) in records.items():
                r = measure(e, o, frequency, args.sample_rate)
                r['change_from_baseline_deg'] = float(wrap(r['phase_deg'] -
                    channel['stages'].get('baseline', r)['phase_deg']))
                channel['stages'][stage] = r
            results[path.parent.name + '/' + 'AB'[ch]] = channel
    report = dict(method='windowed complex ringing projection; not a calibrated binary classifier',
                  sample_rate=args.sample_rate, window_samples=[20,240], channels=results)
    (args.capture_root / 'ringing_analysis.json').write_text(json.dumps(report, indent=2))
    for name, c in results.items():
        print(name, 'Hz', c['frequency_hz'], 'changes',
              {s: round(r['change_from_baseline_deg'], 1) for s, r in c['stages'].items()})
    if args.plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        names = list(results)
        data = [[results[n]['stages'][s]['change_from_baseline_deg'] for s in stages[1:]] for n in names]
        fig, ax = plt.subplots(figsize=(max(8, len(stages)), 6))
        im = ax.imshow(data, vmin=-180, vmax=180, cmap='coolwarm')
        ax.set_xticks(range(len(stages)-1), [s.replace('_inverted','').replace('_','\n') for s in stages[1:]])
        ax.set_yticks(range(len(names)), names)
        for i, row in enumerate(data):
            for j, value in enumerate(row):
                ax.text(j, i, f'{value:.1f}', ha='center', va='center', color='white' if abs(value)>120 else 'black')
        fig.colorbar(im, ax=ax, label='Change of measured ringing phase (degrees)')
        ax.set_title('Measured ringing phase changes; one fixed frequency per receiver')
        fig.tight_layout()
        fig.savefig(args.capture_root / 'ringing_phase_changes.png', dpi=150)


if __name__ == '__main__':
    main()
