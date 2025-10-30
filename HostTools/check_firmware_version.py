#!/usr/bin/env python3
"""
Reset STM32 and read firmware version from COM9
"""
import serial
import time
import sys
import subprocess

PORT = "COM9"
BAUD = 115200
OPENOCD = r"c:\Users\TEST\Documents\Work\BMI20\STM32\xpack-openocd-0.12.0-2-win32-x64\xpack-openocd-0.12.0-2\bin\openocd.exe"
SCRIPTS = r"c:/Users/TEST/Documents/Work/BMI20/STM32/xpack-openocd-0.12.0-2-win32-x64/xpack-openocd-0.12.0-2/scripts"

def reset_mcu():
    """Reset MCU via OpenOCD"""
    print("[INFO] Resetting MCU via OpenOCD...")
    cmd = [
        OPENOCD,
        "-s", SCRIPTS,
        "-f", "interface/stlink.cfg",
        "-f", "target/stm32h7x.cfg",
        "-c", "init",
        "-c", "reset run",
        "-c", "exit"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if "Error" in result.stderr or result.returncode != 0:
            print(f"[WARN] OpenOCD returned: {result.returncode}")
        else:
            print("[OK] Reset command sent")
        return True
    except Exception as e:
        print(f"[ERROR] Reset failed: {e}")
        return False

def read_version():
    """Read version from COM port"""
    print(f"\n[INFO] Opening {PORT} @ {BAUD} baud...")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=0.5)
        print(f"[OK] Port opened. Reading for 10 seconds...")
        print("=" * 70)
        
        start = time.time()
        fw_version_found = False
        
        while time.time() - start < 10:
            if ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(line)
                    if "Firmware:" in line or "FW:" in line or "__DATE__" in line or "__TIME__" in line:
                        fw_version_found = True
                    
        print("=" * 70)
        
        if fw_version_found:
            print("[SUCCESS] Firmware version information found!")
        else:
            print("[INFO] No explicit firmware version found in output")
            print("[HINT] Firmware is running (USB works), but may need physical reset to see boot messages")
            
        ser.close()
        return 0
        
    except serial.SerialException as e:
        print(f"[ERROR] Cannot open {PORT}: {e}")
        print("[HINT] Check if port exists and is not in use")
        return 1
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted")
        return 0

def main():
    print("=" * 70)
    print("STM32 FIRMWARE VERSION READER")
    print("=" * 70)
    
    # Reset MCU
    reset_mcu()
    
    # Wait for reset
    print("[INFO] Waiting 2 seconds for MCU to boot...")
    time.sleep(2)
    
    # Read version
    return read_version()

if __name__ == "__main__":
    sys.exit(main())
