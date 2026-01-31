# RPi environment (build + flash)

## 1) Clone / import repo
**Option A (bundle):**
```bash
git clone BMI30_rpi_state_2026-01-31.bundle BMI30.stm32h7
cd BMI30.stm32h7
```

**Option B (external git):**
```bash
git clone <REMOTE_URL> BMI30.stm32h7
cd BMI30.stm32h7
```

## 2) System packages (Raspberry Pi OS / Debian)
```bash
sudo apt-get update
sudo apt-get install -y \
  git make build-essential \
  gcc-arm-none-eabi gdb-multiarch \
  openocd \
  python3 python3-venv python3-pip \
  libusb-1.0-0-dev
```

## 3) Udev rules (ST-Link, USB access)
Create `/etc/udev/rules.d/49-stlinkv2.rules`:
```
# ST-Link
SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="3748", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="374b", MODE="0666", GROUP="plugdev"
```
Then:
```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

## 4) Python venv for HostTools
```bash
cd HostTools
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## 5) Build firmware
```bash
cd Debug
make -j4 all
```

## 6) Flash (OpenOCD)
```bash
openocd \
  -f interface/stlink.cfg \
  -f target/stm32h7x.cfg \
  -c "program Debug/BMI30.stm32h7.elf verify reset exit"
```

## 7) Quick host check (USB vendor)
```bash
cd HostTools
source .venv/bin/activate
python3 vendor_quick_status.py
```
