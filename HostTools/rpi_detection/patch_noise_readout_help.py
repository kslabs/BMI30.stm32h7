"""Explain p/n/t/SN on the existing two-second hover tooltip."""
import ast
from pathlib import Path

from patch_base_width import replace


def patch_core(source, version):
    anchor = 'function detectorNoiseHtml('
    start = source.index(anchor)
    source = source[:start] + '''function detectorNoiseDescription(channel,seconds){return[(channel==='lower'?'Нижний':'Верхний')+' канал: p / n / t / SN',
'p (peak) — пик сигнала текущего кадра.',
'n (noise) — оценка шума: max(среднее, P95) за '+normNumber(seconds)+' с. P95 — 95-й процентиль истории измерений.',
't (trigger) — рассчитанный порог детекции.',
'SN — текущее отношение p / n, в разах.',
'Для показа p, n, t и SN округляются до целых. В расчётах сохраняется полная точность среднего и P95.',
'Уровни p, n и t показаны в исходных единицах произведения. Только график масштабируется в 0–65535.',
'Нулевой или недостоверный шум не может подтвердить метку.'].join(String.fromCharCode(10))}
''' + source[start:]
    for side, channel in [('up','upper'),('lo','lower')]:
        old = next(line for line in source.splitlines() if f'let {side}NoiseTitle=' in line)
        source = replace(source, old, f"  let {side}NoiseTitle=detectorNoiseDescription('{channel}',noiseWindow);")
    # Every hit within the changing readout resolves to the stable parent zone.
    # Replacing a number every status refresh must not restart its hover timer.
    source = replace(source, '</style>', '''#detUpperNoise,#detLowerNoise{cursor:help}
#detUpperNoise>*,#detLowerNoise>*{pointer-events:none}
.custom-tooltip{white-space:pre-line}
</style>''')
    replacements = {
        'detectorLevelNumber': "function detectorLevelNumber(v){v=detectorRawValue(v);if(!Number.isFinite(v))return'---';if(v>0&&v<0.5)return'<1';return String(Math.round(v))}",
        'detectorSnrText': "function detectorSnrText(v){v=Number(v);if(!Number.isFinite(v)||v<=0)return'-----';if(v<0.5)return'<1x';return String(Math.round(v))+'x'}",
        'detectorRatioText': "function detectorRatioText(value){value=Number(value);if(!Number.isFinite(value))return'---';if(value>0&&value<0.5)return'<1';return String(Math.round(value))}",
        'normNumber': "function normNumber(v){v=Number(v);return Number.isFinite(v)?String(Math.round(v)):'---'}",
        'fpsFixedText': "function fpsFixedText(value){let v=Number(value||0);if(!Number.isFinite(v))v=0;return String(Math.round(v)).padStart(3,' ')}",
    }
    for name, new in replacements.items():
        old = next(line for line in source.splitlines() if line.startswith('function '+name+'('))
        source = replace(source, old, new)
    # Editable coefficients retain meaningful tenths, but no redundant trailing .0.
    source = replace(source, 'let v=clampDetectorThreshold(value);return v.toFixed(1)',
                     'let v=clampDetectorThreshold(value);return String(v)')
    source = replace(source, 'input.value=clampDetectorThreshold(detectorThresholds[key]).toFixed(1)',
                     'input.value=String(clampDetectorThreshold(detectorThresholds[key]))')
    # Rounded ratios can coincide; do not show a false "3 > 3" inequality.
    source = replace(source,
        "text:'S/Ninst:'+detectorRatioText(instantSnr)+'>'+detectorRatioText(instantRequiredRatio)+' '+(instantPassed?'✓':'✗')",
        "text:'S/Ninst:≈'+detectorRatioText(instantSnr)+' (min≈'+detectorRatioText(instantRequiredRatio)+') '+(instantPassed?'✓':'✗')")
    source = replace(source, 'BMI30.200.py.2026-10-16-0934', 'BMI30.200.py.'+version)
    ast.parse(source)
    return source


def prepare(source_dir, target_dir, version):
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in ['core','engine','portal']:
        data = (source_dir/(name+'.py')).read_bytes()
        if name == 'core': data = patch_core(data.decode('utf8'), version).encode('utf8')
        (target_dir/(name+'.py')).write_bytes(data)


if __name__ == '__main__':
    import sys
    prepare(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
