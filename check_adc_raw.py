#!/usr/bin/env python3
import usb.core, usb.util, struct, time

dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
try: dev.set_configuration()
except: pass
try: usb.util.claim_interface(dev, 2)
except: pass
try: dev.set_interface_altsetting(2, 1)
except: pass

dev.write(0x03, [0x21], timeout=500)
time.sleep(0.2)
dev.write(0x03, [0x18, 0x80], timeout=500)
time.sleep(0.05)
dev.write(0x03, [0x19, 0x02], timeout=500)
time.sleep(0.05)
dev.write(0x03, [0x13, 0x01], timeout=500)
time.sleep(0.05)
dev.write(0x03, [0x14, 0x00], timeout=500)
time.sleep(0.05)
dev.write(0x03, [0x20], timeout=500)
time.sleep(0.5)

for i in range(5):
    chunk = dev.read(0x83, 4096, timeout=500)
    ns = struct.unpack('<H', chunk[12:14])[0]
    payload = chunk[32:32+ns*2]
    samples = struct.unpack(f'<{ns}H', payload)
    print(f'[Frame {i}] min={min(samples)} max={max(samples)} zeros={samples.count(0)} first10={samples[:10]}')

dev.write(0x03, [0x21], timeout=500)
