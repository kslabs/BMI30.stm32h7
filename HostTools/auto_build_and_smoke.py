#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автоматизированный smoke-тест:
 1. Сборка (make -C Debug)
 2. (Опц.) Прошивка через OpenOCD
 3. Тест №1: полный кадр (без усечения) – фиксируем TEST + пары ADC0/ADC1
 4. Тест №2: усечённый кадр (--trunc 256)
 5. Валидация: для каждой последовательности seq должны быть оба канала (ADC0 и ADC1) с одинаковым seq и одинаковым ns.
 6. Отчёт.

Запуск примеры:
  py HostTools\auto_build_and_smoke.py --openocd "c:/path/to/openocd.exe" --flash
  py HostTools\auto_build_and_smoke.py --no-flash  (пропустить прошивку)

Ограничения: требуется присутствие уже подключённого устройства USB.
"""
import argparse, subprocess, sys, time, re, shutil, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEBUG_DIR = ROOT / 'Debug'
OPENOCD_DEFAULT = 'c:/Users/TEST/Documents/Work/BMI20/STM32/xpack-openocd-0.12.0-2-win32-x64/xpack-openocd-0.12.0-2/bin/openocd.exe'
ELF_PATH = ROOT / 'Debug' / 'BMI30.stm32h7.elf'

VENDOR_START_SCRIPT = ROOT / 'HostTools' / 'vendor_start_real_frames.py'

def run_cmd(cmd, cwd=None, timeout=None, capture=True):
    start = time.time()
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if capture else None, timeout=timeout, shell=False)
    dur = time.time() - start
    out = proc.stdout.decode('utf-8', errors='replace') if capture else ''
    return proc.returncode, out, dur

FRAME_LINE_RE = re.compile(r'^\[(\d+)\]\s+(ADC0|ADC1|TEST)\s+seq=(\d+)\s+ns=(\d+)\s+len=(\d+)')

def run_frame_capture(trunc: int|None, count: int = 10, profile: int = 2, timeout=25.0):
    cmd = ['py', str(VENDOR_START_SCRIPT)]
    if trunc:
        cmd += ['--trunc', str(trunc)]
    if profile != 2:
        cmd += ['--profile', str(profile)]
    cmd += ['--count', str(count)]
    rc, out, dur = run_cmd(cmd, cwd=ROOT)
    return rc, out, dur

def parse_frames(output: str):
    frames = []
    for line in output.splitlines():
        m = FRAME_LINE_RE.match(line.strip())
        if m:
            idx, ftype, seq, ns, length = m.groups()
            frames.append({
                'idx': int(idx),
                'type': ftype,
                'seq': int(seq),
                'ns': int(ns),
                'len': int(length)
            })
    return frames

def validate_pairs(frames):
    errors = []
    seq_map = {}
    for fr in frames:
        if fr['type'] not in ('ADC0','ADC1'): continue
        seq_map.setdefault(fr['seq'], {})[fr['type']] = fr
    for seq, mp in seq_map.items():
        if 'ADC0' not in mp or 'ADC1' not in mp:
            errors.append(f"seq {seq}: missing channel(s) {sorted(mp.keys())}")
        else:
            if mp['ADC0']['ns'] != mp['ADC1']['ns']:
                errors.append(f"seq {seq}: ns mismatch {mp['ADC0']['ns']} vs {mp['ADC1']['ns']}")
    return errors, seq_map

def flash(openocd_path: str):
    if not ELF_PATH.exists():
        return False, f"ELF not found: {ELF_PATH}"
    script_args = [openocd_path,
                   '-s', str(pathlib.Path(openocd_path).parent.parent / 'scripts'),
                   '-f', 'interface/stlink.cfg',
                   '-f', 'target/stm32h7x.cfg',
                   '-c', f"program {{{ELF_PATH}}} verify reset exit"
                  ]
    rc, out, dur = run_cmd(script_args, cwd=ROOT)
    return rc == 0, out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--openocd', default=OPENOCD_DEFAULT)
    ap.add_argument('--flash', action='store_true')
    ap.add_argument('--no-flash', action='store_true')
    ap.add_argument('--skip-build', action='store_true')
    ap.add_argument('--trunc', type=int, default=256)
    args = ap.parse_args()

    report = { 'build':{}, 'flash':{}, 'tests':[] }

    # Build
    if not args.skip_build:
        if not DEBUG_DIR.exists():
            report['build'] = {'ok': False, 'error':'Debug dir not found'}
        else:
            rc, out, dur = run_cmd(['make','-C','Debug','-j'], cwd=ROOT)
            report['build'] = {'ok': rc==0, 'rc':rc, 'seconds':round(dur,2)}
            if rc!=0:
                report['build']['tail'] = '\n'.join(out.splitlines()[-40:])
                print(json.dumps(report, indent=2, ensure_ascii=False))
                return 1
    else:
        report['build'] = {'ok': True, 'skipped': True}

    # Flash
    if args.flash and not args.no_flash:
        ok, out = flash(args.openocd)
        report['flash'] = {'ok':ok}
        if not ok:
            report['flash']['log_tail'] = '\n'.join(out.splitlines()[-60:])
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 2
    else:
        report['flash'] = {'ok': True, 'skipped': True}

    # Test 1: full frame capture (no trunc)
    rc1, out1, dur1 = run_frame_capture(trunc=None, count=10)
    frames1 = parse_frames(out1)
    errs1, seq_map1 = validate_pairs(frames1)
    report['tests'].append({
        'name':'full', 'rc':rc1 if frames1 else (rc1 or 5), 'duration_s':round(dur1,2),
        'frames': len(frames1), 'seqs': len(seq_map1), 'errors': errs1[:6] + ([] if frames1 else ['no_frames'])
    })
    # Test 2: truncated
    rc2, out2, dur2 = run_frame_capture(trunc=args.trunc, count=10)
    frames2 = parse_frames(out2)
    errs2, seq_map2 = validate_pairs(frames2)
    # Проверка ns
    trunc_ns_ok = all(fr['ns']==args.trunc or fr['type']=='TEST' for fr in frames2 if fr['type'] in ('ADC0','ADC1')) if frames2 else False
    report['tests'].append({
        'name':'trunc', 'rc':rc2 if frames2 else (rc2 or 5), 'duration_s':round(dur2,2),
        'frames': len(frames2), 'seqs': len(seq_map2), 'errors': errs2[:6] + ([] if frames2 else ['no_frames']), 'all_ns_match_trunc': trunc_ns_ok,
        'expected_trunc': args.trunc
    })

    # Summary verdict
    report['ok'] = all(t['rc']==0 and not t['errors'] for t in report['tests']) and any(t['frames']>0 for t in report['tests'])
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report['ok']:
        # При ошибках — вывести контекст для анализа
        print("\n--- RAW OUTPUT (full) ---\n", out1[-2000:])
        print("\n--- RAW OUTPUT (trunc) ---\n", out2[-2000:])
        return 3
    return 0

if __name__ == '__main__':
    sys.exit(main())
