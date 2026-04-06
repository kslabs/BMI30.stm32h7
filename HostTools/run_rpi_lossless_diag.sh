#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SECS="${SECS:-120}"
STATUS_INTERVAL="${STATUS_INTERVAL:-1.0}"
LOG_DIR="${LOG_DIR:-${SCRIPT_DIR}/logs}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/rpi_lossless_diag_${STAMP}.log"

mkdir -p "${LOG_DIR}"
cd "${REPO_ROOT}"

echo "BMI30 lossless ROI diagnostic" | tee "${LOG_FILE}"
echo "secs=${SECS} status_interval=${STATUS_INTERVAL} python=${PYTHON_BIN}" | tee -a "${LOG_FILE}"
echo "log=${LOG_FILE}" | tee -a "${LOG_FILE}"

if command -v lsusb >/dev/null 2>&1; then
  echo "" | tee -a "${LOG_FILE}"
  echo "===== USB ENUM =====" | tee -a "${LOG_FILE}"
  lsusb | tee -a "${LOG_FILE}" || true
  echo "" | tee -a "${LOG_FILE}"
  echo "===== USB TREE =====" | tee -a "${LOG_FILE}"
  lsusb -t | tee -a "${LOG_FILE}" || true
fi

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
  --ctrl-status \
  --status-interval "${STATUS_INTERVAL}" \
  --status-print \
  --secs "${SECS}" \
  --quiet | tee -a "${LOG_FILE}"

echo "" | tee -a "${LOG_FILE}"
echo "Finished. Diagnostic log: ${LOG_FILE}" | tee -a "${LOG_FILE}"
