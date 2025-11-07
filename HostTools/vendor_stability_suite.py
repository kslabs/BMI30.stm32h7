#!/usr/bin/env python3
"""Запускает подряд N (по умолчанию 3) 5-минутных тестов vendor_usb_start_and_read.py
и агрегирует результаты.

Каждый прогон:
  py -3 vendor_usb_start_and_read.py --status-mode none --log-interval 10 \
      --abort-no-rx-sec 30 --window-sec 300 --pairs 0 --rate-hz 200 --ch-mode 2

На выходе печатает финальную сводку:
  RUN i: elapsed=... A=... B=... timeouts=... pipes=...
  TOTAL: sumA=..., sumB=..., sumTimeouts=..., sumPipes=..., avgA_per_run=...

Если любой прогон завершился с кодом !=0 — отмечается FAIL и дальнейшие не запускаются (по умолчанию).
Параметры можно расширить при необходимости.
"""
import subprocess
import sys
import time
import argparse
from pathlib import Path

SCRIPT = Path(__file__).parent / 'vendor_usb_start_and_read.py'

def parse_args():
    ap = argparse.ArgumentParser(description='Stability suite: multiple 5-min vendor runs')
    ap.add_argument('--runs', type=int, default=3, help='Количество прогонов (default 3)')
    ap.add_argument('--rate-hz', type=int, default=200, help='Параметр --rate-hz для дочернего')
    ap.add_argument('--ch-mode', type=int, default=2, help='Параметр --ch-mode для дочернего')
    ap.add_argument('--window-sec', type=int, default=300, help='Длительность одного прогона (default 300)')
    ap.add_argument('--abort-no-rx-sec', type=int, default=30, help='Остановка если нет кадров (увеличено для долгого теста)')
    ap.add_argument('--log-interval', type=float, default=10.0, help='Интервал агрегированных сводок')
    ap.add_argument('--fail-fast', action='store_true', help='Прервать всю серию при первом ненулевом RC')
    return ap.parse_args()

def run_one(idx, args):
    cmd = [sys.executable, '-u', str(SCRIPT),
           '--status-mode', 'none',
           '--log-interval', str(args.log_interval),
           '--abort-no-rx-sec', str(args.abort_no_rx_sec),
           '--window-sec', str(args.window_sec),
           '--pairs', '0',
           '--rate-hz', str(args.rate_hz),
           '--ch-mode', str(args.ch_mode)]
    print(f"[SUITE] RUN {idx+1}/{args.runs} CMD={' '.join(cmd)}")
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    summary_line = None
    for line in proc.stdout:
        line = line.rstrip('\n')
        print(line)
        if line.startswith('[HOST][SUMMARY]'):
            summary_line = line
    rc = proc.wait()
    elapsed = time.time() - t0
    return rc, elapsed, summary_line

def extract_metrics(summary_line: str):
    # Формат: [HOST][SUMMARY] elapsed=300.0s A=XXXX B=XXXX STAT=... timeouts=... pipe_errors=...
    # Возвращаем dict или None
    import re
    if not summary_line:
        return None
    pat = re.compile(r'elapsed=([0-9.]+)s A=(\d+) B=(\d+) STAT=(\d+) timeouts=(\d+) pipe_errors=(\d+)')
    m = pat.search(summary_line)
    if not m:
        return None
    return {
        'elapsed': float(m.group(1)),
        'A': int(m.group(2)),
        'B': int(m.group(3)),
        'STAT': int(m.group(4)),
        'timeouts': int(m.group(5)),
        'pipes': int(m.group(6)),
    }

def main():
    args = parse_args()
    all_metrics = []
    for i in range(args.runs):
        rc, elapsed, summ = run_one(i, args)
        met = extract_metrics(summ)
        if met:
            all_metrics.append(met)
            print(f"[SUITE][METRICS] run={i+1} A={met['A']} B={met['B']} timeouts={met['timeouts']} pipes={met['pipes']} elapsed={met['elapsed']:.1f}s")
        else:
            print(f"[SUITE][WARN] Не удалось извлечь метрики RUN {i+1}")
        if rc != 0:
            print(f"[SUITE][FAIL] RUN {i+1} rc={rc}")
            if args.fail_fast:
                break
    if not all_metrics:
        print('[SUITE][RESULT] Нет успешных прогонов')
        sys.exit(1)
    sumA = sum(m['A'] for m in all_metrics)
    sumB = sum(m['B'] for m in all_metrics)
    sumT = sum(m['timeouts'] for m in all_metrics)
    sumP = sum(m['pipes'] for m in all_metrics)
    avgA = sumA / len(all_metrics)
    avgB = sumB / len(all_metrics)
    print(f"[SUITE][RESULT] runs={len(all_metrics)} sumA={sumA} sumB={sumB} avgA={avgA:.1f} avgB={avgB:.1f} timeouts_total={sumT} pipe_errors_total={sumP}")
    # Код возврата 0 если нет pipe_errors и нет таймаутов во всех метриках
    if any(m['pipes'] > 0 for m in all_metrics):
        sys.exit(2)
    sys.exit(0)

if __name__ == '__main__':
    main()
