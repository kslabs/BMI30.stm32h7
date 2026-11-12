#!/bin/bash
# Quick Start Script for BMI30 USB Oscilloscope

set -e

echo "🚀 BMI30 USB Oscilloscope — Quick Start"
echo "=========================================="
echo ""

# Check if device is connected
echo "📡 Checking for BMI30 device (0xCAFE:0x4001)..."
if lsusb | grep -q "cafe:4001"; then
    echo "✅ Device found!"
else
    echo "⚠️  Device not found. Continuing anyway (will fail at runtime if not connected)..."
fi

# Check Python packages
echo ""
echo "📦 Checking Python packages..."
python3 << 'PY'
import sys
required = ['pyusb', 'pyqtgraph', 'numpy']
try:
    from PyQt5 import QtWidgets
    print("✅ PyQt5 found")
except:
    try:
        from PySide6 import QtWidgets
        print("✅ PySide6 found (fallback)")
    except:
        print("❌ Neither PyQt5 nor PySide6 found!")
        print("   Install: pip install PyQt5 pyqtgraph pyusb numpy")
        sys.exit(1)

for pkg in required:
    try:
        __import__(pkg)
        print(f"✅ {pkg} found")
    except ImportError:
        print(f"❌ {pkg} NOT found!")
        print(f"   Install: pip install {pkg}")
        sys.exit(1)
PY

if [ $? -ne 0 ]; then
    echo ""
    echo "Install missing packages:"
    echo "  pip install pyusb pyqtgraph PyQt5 numpy"
    exit 1
fi

# Check Python version
echo ""
echo "🐍 Checking Python version..."
PYVER=$(python3 --version | awk '{print $2}' | cut -d. -f1,2)
echo "   Python $PYVER"
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)"; then
    echo "✅ Python version OK (>= 3.7)"
else
    echo "❌ Python version too old (need >= 3.7)"
    exit 1
fi

# Check USB access
echo ""
echo "🔐 Checking USB access..."
if [ -e /dev/bus/usb ] && lsusb > /dev/null 2>&1; then
    echo "✅ USB access OK"
elif sudo -n lsusb > /dev/null 2>&1; then
    echo "⚠️  Will need to use 'sudo' for USB access"
    echo "   To avoid sudo, install udev rule:"
    echo "     sudo tee /etc/udev/rules.d/99-bmi30.rules << 'EOF'"
    echo "     SUBSYSTEMS==\"usb\", ATTRS{idVendor}==\"cafe\", ATTRS{idProduct}==\"4001\", MODE=\"0666\""
    echo "     EOF"
    echo "     sudo udevadm control --reload-rules"
    echo "     sudo udevadm trigger"
    NEED_SUDO=1
else
    echo "❌ Cannot access USB devices"
    exit 1
fi

# Launch GUI
echo ""
echo "🎬 Launching BMI30 Oscilloscope GUI..."
echo ""

cd "$(dirname "$0")"

: "${BMI30_BEEP_REALTIME:=1}"
: "${BMI30_DETECT_IN_READER:=1}"
: "${BMI30_BEEP_HOLD_ENABLE:=0}"
: "${BMI30_BEEP_HOLD_DELAY:=0.0}"
: "${BMI30_DETECT_LOSS_SEC:=0.0}"
: "${BMI30_DETECT_COOLDOWN:=0.05}"
: "${BMI30_BEEP_SWEEP:=0}"
: "${BMI30_BEEP_INFO_TONE:=0.150}"
: "${BMI30_BEEP_INFO_GAP:=0.0}"
: "${BMI30_BEEP_CHANNEL_GAP:=0.0}"
: "${BMI30_BEEP_REPEAT_GAP:=0.05}"
: "${BMI30_RESET_ON_START:=1}"
: "${BMI30_STARTUP_RESET_MODE:=fast_gpio}"
: "${BMI30_STARTUP_RESET_PULSE_MS:=120}"
: "${BMI30_STARTUP_RESET_WAIT_S:=0.0}"
: "${BMI30_STARTUP_READY_TIMEOUT_S:=3.0}"
: "${BMI30_STARTUP_READY_POLL_S:=0.05}"
: "${BMI30_CONNECT_TIMEOUT_S:=4.0}"
: "${BMI30_DEFAULT_SEL:=5}"
: "${BMI30_HOST_RX_ACK:=1}"
: "${BMI30_HOST_RX_ACK_INTERVAL:=1.0}"
export BMI30_BEEP_REALTIME BMI30_DETECT_IN_READER BMI30_BEEP_HOLD_ENABLE BMI30_BEEP_HOLD_DELAY
export BMI30_DETECT_LOSS_SEC BMI30_DETECT_COOLDOWN BMI30_BEEP_SWEEP
export BMI30_BEEP_INFO_TONE BMI30_BEEP_INFO_GAP BMI30_BEEP_CHANNEL_GAP BMI30_BEEP_REPEAT_GAP
export BMI30_RESET_ON_START BMI30_STARTUP_RESET_MODE BMI30_STARTUP_RESET_PULSE_MS BMI30_STARTUP_RESET_WAIT_S
export BMI30_STARTUP_READY_TIMEOUT_S BMI30_STARTUP_READY_POLL_S BMI30_CONNECT_TIMEOUT_S BMI30_DEFAULT_SEL
export BMI30_HOST_RX_ACK BMI30_HOST_RX_ACK_INTERVAL

ACTIVE_VERSION_ENV="host/bmi30_active_version.env"
if [ -z "${BMI30_APP_PATH:-}" ] && [ -f "$ACTIVE_VERSION_ENV" ]; then
    # shellcheck disable=SC1090
    . "$ACTIVE_VERSION_ENV"
fi

APP_PATH="${BMI30_APP_PATH:-host/BMI30.200.py.2026-06-03-realtime-prevbuf}"
if [ ! -f "$APP_PATH" ] && [ -f "host/BMI30.200.py.2026-06-03-realtime-prevbuf" ]; then
    APP_PATH="host/BMI30.200.py.2026-06-03-realtime-prevbuf"
elif [ ! -f "$APP_PATH" ] && [ -f "host/BMI30.200.py.2026-05-30-realtime-prevbuf" ]; then
    APP_PATH="host/BMI30.200.py.2026-05-30-realtime-prevbuf"
elif [ ! -f "$APP_PATH" ] && [ -f "host/BMI30.200.py.2026-05-24-work" ]; then
    APP_PATH="host/BMI30.200.py.2026-05-24-work"
elif [ ! -f "$APP_PATH" ] && [ -f "host/BMI30.200.py" ]; then
    APP_PATH="host/BMI30.200.py"
fi

if [ "$NEED_SUDO" = "1" ]; then
    sudo env \
        BMI30_BEEP_REALTIME="$BMI30_BEEP_REALTIME" \
        BMI30_DETECT_IN_READER="$BMI30_DETECT_IN_READER" \
        BMI30_BEEP_HOLD_ENABLE="$BMI30_BEEP_HOLD_ENABLE" \
        BMI30_BEEP_HOLD_DELAY="$BMI30_BEEP_HOLD_DELAY" \
        BMI30_DETECT_LOSS_SEC="$BMI30_DETECT_LOSS_SEC" \
        BMI30_DETECT_COOLDOWN="$BMI30_DETECT_COOLDOWN" \
        BMI30_BEEP_SWEEP="$BMI30_BEEP_SWEEP" \
        BMI30_BEEP_INFO_TONE="$BMI30_BEEP_INFO_TONE" \
        BMI30_BEEP_INFO_GAP="$BMI30_BEEP_INFO_GAP" \
        BMI30_BEEP_CHANNEL_GAP="$BMI30_BEEP_CHANNEL_GAP" \
        BMI30_BEEP_REPEAT_GAP="$BMI30_BEEP_REPEAT_GAP" \
        BMI30_RESET_ON_START="$BMI30_RESET_ON_START" \
        BMI30_STARTUP_RESET_MODE="$BMI30_STARTUP_RESET_MODE" \
        BMI30_STARTUP_RESET_PULSE_MS="$BMI30_STARTUP_RESET_PULSE_MS" \
        BMI30_STARTUP_RESET_WAIT_S="$BMI30_STARTUP_RESET_WAIT_S" \
        BMI30_STARTUP_READY_TIMEOUT_S="$BMI30_STARTUP_READY_TIMEOUT_S" \
        BMI30_STARTUP_READY_POLL_S="$BMI30_STARTUP_READY_POLL_S" \
        BMI30_CONNECT_TIMEOUT_S="$BMI30_CONNECT_TIMEOUT_S" \
        BMI30_DEFAULT_SEL="$BMI30_DEFAULT_SEL" \
        BMI30_HOST_RX_ACK="$BMI30_HOST_RX_ACK" \
        BMI30_HOST_RX_ACK_INTERVAL="$BMI30_HOST_RX_ACK_INTERVAL" \
        python3 "$APP_PATH" "$@"
else
    python3 "$APP_PATH" "$@"
fi
