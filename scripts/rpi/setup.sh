#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y \
  git make build-essential \
  gcc-arm-none-eabi gdb-multiarch \
  openocd \
  python3 python3-venv python3-pip \
  libusb-1.0-0-dev

cat <<'EOF'

Next steps:
1) Add udev rules for ST-Link (see RPI_ENVIRONMENT.md)
2) Create venv in HostTools and install requirements
EOF
