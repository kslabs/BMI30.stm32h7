"""Flash only the explicitly identified STM32L431CB, verify, then start debug.

The debugger start does not replace a final cold-start test without ST-Link.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parent
RELEASE = ROOT.parents[1] / 'FirmwareReleases/XM125/0.1.2'


def verified_image(release):
    """Verify the sealed archive before any access to a programming probe."""
    release = release.resolve()
    checks = json.loads((release / 'SHA256_MANIFEST.json').read_text(encoding='utf-8'))
    for name, expected in checks.items():
        path = (release / name).resolve()
        if not path.is_relative_to(release) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Release checksum mismatch: ' + name)
    elf = release / 'firmware/bmi30_xm125_gate_0.1.2.elf'
    if 'firmware/' + elf.name not in checks:
        raise RuntimeError('ELF is missing from the release manifest')
    binary = elf.with_suffix('.bin').read_bytes()
    manifest = json.loads((elf.parent / 'build-manifest.json').read_text(encoding='utf-8'))
    if (manifest['firmware'] != '0.1.2' or manifest['gpio0_mode'] != 'open_drain' or
            hashlib.sha256(binary).hexdigest() != manifest['bin_sha256']):
        raise RuntimeError('Firmware identity/hash mismatch')
    sp, pc = struct.unpack('<II', binary[:8])
    if not (len(binary) <= 124 * 1024 and 0x20000000 < sp <= 0x20010000 and
            0x08000000 < pc < 0x0801F000 and pc & 1):
        raise RuntimeError('Unexpected firmware size/vector table')
    return elf, manifest


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--serial', help='Explicit ST-Link serial number')
    p.add_argument('--release', type=Path, default=RELEASE)
    p.add_argument('--check', action='store_true', help='Verify release without accessing hardware')
    p.add_argument('--programmer', type=Path, help='STM32_Programmer_CLI executable')
    p.add_argument('--backup', type=Path, help='New file for full 128 KiB pre-flash backup')
    p.add_argument('--openocd', type=Path)
    p.add_argument('--scripts', type=Path)
    args = p.parse_args()
    elf, manifest = verified_image(args.release)
    if args.check:
        print(json.dumps({'firmware': manifest['firmware'], 'bin_sha256': manifest['bin_sha256'],
                          'release_verified': True}))
        return
    if not args.serial or not re.fullmatch(r'[A-Za-z0-9]+', args.serial):
        p.error('--serial must explicitly identify one ST-Link')
    if not args.backup or args.backup.suffix.lower() != '.bin' or args.backup.exists():
        p.error('--backup must name a new .bin file')
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    if args.openocd and (not args.scripts or not args.openocd.is_file() or not args.scripts.is_dir()):
        p.error('Valid --openocd and --scripts paths are required together')
    cli = str(args.programmer) if args.programmer else shutil.which('STM32_Programmer_CLI')
    if not cli:
        p.error('STM32_Programmer_CLI is required; use --programmer or PATH')
    connect = [cli, '-c', 'port=SWD', 'sn=' + args.serial, 'mode=HOTPLUG', 'freq=1000']
    result = subprocess.run(connect, capture_output=True, check=True)
    identity = result.stdout.decode(errors='replace')
    if 'Device ID   : 0x435' not in identity or 'Flash size  : 128 KBytes' not in identity:
        raise RuntimeError('Selected target is not STM32L431CB: ' + identity)
    subprocess.run([*connect, '-u', '0x08000000', '131072', str(args.backup)], check=True)
    if args.backup.stat().st_size != 128 * 1024:
        raise RuntimeError('Incomplete pre-flash backup; programming cancelled')
    print(json.dumps({'backup': str(args.backup),
                      'backup_sha256': hashlib.sha256(args.backup.read_bytes()).hexdigest()}))
    subprocess.run([cli, '-c', 'port=SWD', 'sn=' + args.serial, 'mode=UR', 'freq=1000',
                    '-d', str(elf), '-v', '-rst'], check=True)
    if args.openocd:
        subprocess.run([str(args.openocd), '-s', str(args.scripts), '-f', 'interface/stlink.cfg',
                        '-c', 'adapter serial ' + args.serial, '-f', 'target/stm32l4x.cfg',
                        '-c', 'init; halt; set flash_status [mrw 0x40022010]; '
                        'if {($flash_status & 0x20000) != 0} {mww 0x40022010 0x20000}; '
                        'reset run; shutdown'], check=True)


if __name__ == '__main__':
    main()
