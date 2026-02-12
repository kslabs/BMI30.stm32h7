#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
ELF="$ROOT_DIR/Debug/BMI30.stm32h7.elf"

openocd \
  -f interface/stlink.cfg \
  -f target/stm32h7x.cfg \
  -c "program $ELF verify reset exit"
