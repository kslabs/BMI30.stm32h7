#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUNS="${RUNS:-3}"
SECS="${SECS:-300}"
LOG_DIR="${LOG_DIR:-${SCRIPT_DIR}/logs}"
MIN_PAIRS_PER_SEC="${MIN_PAIRS_PER_SEC:-380}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/rpi_3x5min_lossless_${STAMP}.log"

fail() {
  echo "[RPI-TEST][ERR] $*" >&2
  exit 1
}

check_host_env() {
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || \
    fail "Не найден интерпретатор '${PYTHON_BIN}'. Укажите PYTHON_BIN=python3 или установите Python 3."

  [[ -f "${REPO_ROOT}/HostTools/vendor_stream_read.py" ]] || \
    fail "Не найден файл HostTools/vendor_stream_read.py. Запускайте скрипт из репозитория BMI30."

  "${PYTHON_BIN}" - <<'PY' || exit 1
import sys

try:
    import usb.core  # type: ignore
    import usb.util  # type: ignore
except Exception as e:
    print("[RPI-TEST][ERR] Не импортируется PyUSB:", e, file=sys.stderr)
    print("[RPI-TEST][ERR] Установите зависимости:", file=sys.stderr)
    print("  sudo apt update", file=sys.stderr)
    print("  sudo apt install -y python3 python3-pip libusb-1.0-0", file=sys.stderr)
    print("  python3 -m pip install --user -r HostTools/requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    from usb.backend import libusb1  # type: ignore
    backend = libusb1.get_backend()
except Exception as e:
    print("[RPI-TEST][ERR] Ошибка инициализации backend libusb:", e, file=sys.stderr)
    print("[RPI-TEST][ERR] Убедитесь, что установлен пакет libusb-1.0-0.", file=sys.stderr)
    sys.exit(1)

if backend is None:
    print("[RPI-TEST][ERR] PyUSB установлен, но backend libusb не найден.", file=sys.stderr)
    print("[RPI-TEST][ERR] Выполните:", file=sys.stderr)
    print("  sudo apt update", file=sys.stderr)
    print("  sudo apt install -y libusb-1.0-0", file=sys.stderr)
    sys.exit(1)

print("[RPI-TEST] Python/PyUSB/libusb: OK", file=sys.stderr)
PY
}

mkdir -p "${LOG_DIR}"
cd "${REPO_ROOT}"
check_host_env

echo "BMI30 lossless ROI stress test" | tee "${LOG_FILE}"
echo "runs=${RUNS} secs=${SECS} python=${PYTHON_BIN} min_pairs_per_sec=${MIN_PAIRS_PER_SEC}" | tee -a "${LOG_FILE}"
echo "log=${LOG_FILE}" | tee -a "${LOG_FILE}"

if command -v lsusb >/dev/null 2>&1; then
  echo "" | tee -a "${LOG_FILE}"
  echo "===== USB ENUM =====" | tee -a "${LOG_FILE}"
  lsusb | tee -a "${LOG_FILE}" || true
  echo "" | tee -a "${LOG_FILE}"
  echo "===== USB TREE =====" | tee -a "${LOG_FILE}"
  lsusb -t | tee -a "${LOG_FILE}" || true
fi

overall_fail=0

for ((i=1; i<=RUNS; i++)); do
  echo "" | tee -a "${LOG_FILE}"
  echo "===== RUN ${i}/${RUNS} =====" | tee -a "${LOG_FILE}"
  run_tmp="$(mktemp)"
  "${PYTHON_BIN}" HostTools/vendor_stream_read.py \
    --vid 0xCAFE --pid 0x4001 \
    --intf 2 --ep-in 0x83 --ep-out 0x03 \
    --profile 1 \
    --full-mode 1 \
    --stream-mode 1 \
    --win0-start 280 --win0-len 200 \
    --win1-start 280 --win1-len 200 \
    --chmode 2 \
    --async-mode 0 \
    --ab-strict \
    --seq-strict \
    --warmup-pairs 50 \
    --status-interval 0 \
    --secs "${SECS}" \
    --quiet | tee "${run_tmp}" | tee -a "${LOG_FILE}"

  done_line="$(grep 'Done\.' "${run_tmp}" | tail -n 1 || true)"
  if [[ -z "${done_line}" ]]; then
    echo "[RPI-TEST][FAIL] RUN ${i}: не найдена итоговая строка Done." | tee -a "${LOG_FILE}"
    overall_fail=1
    rm -f "${run_tmp}"
    continue
  fi

  a_count="$(sed -n 's/.* A=\([0-9][0-9]*\) .*/\1/p' <<< "${done_line}")"
  b_count="$(sed -n 's/.* B=\([0-9][0-9]*\) .*/\1/p' <<< "${done_line}")"
  run_time="$(sed -n 's/.* time=\([0-9.][0-9.]*\)s .*/\1/p' <<< "${done_line}")"
  in_errors="$(sed -n 's/.* in_errors=\([0-9][0-9]*\) .*/\1/p' <<< "${done_line}")"
  missing_a="$(sed -n 's/.* missingA=\([0-9][0-9]*\) .*/\1/p' <<< "${done_line}")"
  missing_b="$(sed -n 's/.* missingB=\([0-9][0-9]*\) .*/\1/p' <<< "${done_line}")"
  seq_gaps="$(sed -n 's/.* seq_gaps=\([0-9][0-9]*\) .*/\1/p' <<< "${done_line}")"
  seq_dupes="$(sed -n 's/.* seq_dupes=\([0-9][0-9]*\) .*/\1/p' <<< "${done_line}")"
  seq_reorders="$(sed -n 's/.* seq_reorders=\([0-9][0-9]*\) .*/\1/p' <<< "${done_line}")"
  pairs_ps="$(awk -v a="${a_count:-0}" -v b="${b_count:-0}" -v t="${run_time:-1}" 'BEGIN { m=(a<b)?a:b; if (t<=0) t=1; printf "%.2f", m/t }')"

  echo "[RPI-TEST] RUN ${i} summary: A=${a_count:-?} B=${b_count:-?} time=${run_time:-?}s pairs_per_sec=${pairs_ps}" | tee -a "${LOG_FILE}"

  run_fail=0
  if [[ "${in_errors:-1}" != "0" ]]; then run_fail=1; fi
  if [[ "${missing_a:-1}" != "0" ]]; then run_fail=1; fi
  if [[ "${missing_b:-1}" != "0" ]]; then run_fail=1; fi
  if [[ "${seq_gaps:-1}" != "0" ]]; then run_fail=1; fi
  if [[ "${seq_dupes:-1}" != "0" ]]; then run_fail=1; fi
  if [[ "${seq_reorders:-1}" != "0" ]]; then run_fail=1; fi
  if ! awk -v pps="${pairs_ps}" -v minpps="${MIN_PAIRS_PER_SEC}" 'BEGIN { exit !(pps+0 >= minpps+0) }'; then
    run_fail=1
    echo "[RPI-TEST][FAIL] RUN ${i}: низкая скорость ${pairs_ps} пар/с, ожидалось >= ${MIN_PAIRS_PER_SEC}" | tee -a "${LOG_FILE}"
  fi

  if [[ "${run_fail}" == "0" ]]; then
    echo "[RPI-TEST][PASS] RUN ${i}" | tee -a "${LOG_FILE}"
  else
    overall_fail=1
    echo "[RPI-TEST][FAIL] RUN ${i}: провал по целостности и/или производительности" | tee -a "${LOG_FILE}"
  fi

  rm -f "${run_tmp}"
done

echo "" | tee -a "${LOG_FILE}"
if [[ "${overall_fail}" == "0" ]]; then
  echo "Finished: PASS. Summary log: ${LOG_FILE}" | tee -a "${LOG_FILE}"
else
  echo "Finished: FAIL. Summary log: ${LOG_FILE}" | tee -a "${LOG_FILE}"
  exit 2
fi
