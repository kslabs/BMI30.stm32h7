"""Zero only unallocated blocks of S10's offline USB; never touch its live eMMC.

Run as root from a low-priority systemd unit. No service stops or reboots.
The supplied task directory contains the SHA-checked extracted Debian zerofree.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

WORK = Path('/var/lib/bmi30-storage-cleanup-20261007')
MOUNT = Path('/mnt/bmi30-s10-usb-storage')
BOOT = Path('/mnt/bmi30-s10-usb-boot-storage')
TOOL = WORK / 'tool/usr/sbin/zerofree'
HOST = 'BMI30-D5EB8F26C'
SERIAL = '25072933770324'
ROOT_UUID = 'd24efc0b-47e6-444f-bc0a-aff5691c7266'
BOOT_UUID = '7CAE-93A5'
CID = '150100424a5444345203c60d4af97b00'
VERSION = '2026-10-07-0959'


def command(*args, allowed=(0,), timeout=120):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode not in allowed:
        raise RuntimeError(str(args) + ': ' + p.stdout[-1000:] + p.stderr[-1000:])
    return {'rc': p.returncode, 'output': p.stdout.strip(), 'error': p.stderr.strip()}


def text(*args):
    return command(*args)['output']


def state(stage, **extra):
    report = {'stage': stage, 'host': HOST, 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), **extra}
    temporary = WORK / 'zero-status.json.tmp'
    temporary.write_text(json.dumps(report) + '\n')
    temporary.replace(WORK / 'zero-status.json')
    print(json.dumps(report), flush=True)


def guard_unmounted():
    assert text('hostname') == HOST
    assert text('findmnt', '-n', '-o', 'SOURCE', '/') == '/dev/mmcblk0p2'
    assert Path('/sys/block/mmcblk0/device/cid').read_text().strip() == CID
    disks = json.loads(text('lsblk', '-J', '-o', 'NAME,SERIAL,UUID,MOUNTPOINTS'))['blockdevices']
    usb = next(d for d in disks if d['name'] == 'sda')
    assert usb['serial'] == SERIAL
    parts = {p['name']: p for p in usb['children']}
    assert parts['sda2']['uuid'] == ROOT_UUID and parts['sda1']['uuid'] == BOOT_UUID
    assert all(not any(p['mountpoints']) for p in parts.values()), 'USB must be offline'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def space(path):
    f = os.statvfs(path)
    return {'total': f.f_blocks * f.f_frsize, 'available': f.f_bavail * f.f_frsize,
            'used': (f.f_blocks - f.f_bfree) * f.f_frsize}


def capture_usb():
    guard_unmounted()
    for path in (MOUNT, BOOT):
        assert path.resolve() == path.absolute() and not path.is_symlink()
        path.mkdir(mode=0o700, exist_ok=True)
        assert not any(path.iterdir())
    mounts = []
    try:
        text('mount', '-o', 'ro,noload,nosuid,nodev', '/dev/sda2', str(MOUNT)); mounts.append(MOUNT)
        text('mount', '-o', 'ro,nosuid,nodev', '/dev/sda1', str(BOOT)); mounts.append(BOOT)
        doc = MOUNT / 'home/techaid/Documents'
        assert (MOUNT / 'etc/hostname').read_text().strip() == HOST
        assert 'BMI30_SPLIT_VERSION=' + VERSION in (doc / 'host/bmi30_split_active_version.env').read_text()
        paths = [p for p in BOOT.rglob('*') if p.is_file() and not p.is_symlink()]
        paths += [p for p in (doc / 'host/bmi30_active_runtime/project').rglob('*')
                  if p.is_file() and not p.is_symlink() and
                  '__pycache__' not in p.parts and p.suffix in ('.py', '.js', '.env')]
        paths += [p for p in (MOUNT / 'opt/bmi30-network').glob('*.py')]
        paths += [doc / 'host/bmi30_config.json', doc / 'host/bmi30_sel.json',
                  doc / 'host/bmi30_split_active_version.env', MOUNT / 'etc/fstab', MOUNT / 'etc/machine-id']
        hashes = {}
        for path in paths:
            base, prefix = (BOOT, 'boot/') if path.is_relative_to(BOOT) else (MOUNT, 'root/')
            hashes[prefix + str(path.relative_to(base))] = digest(path)
        return {'usb_root': space(MOUNT), 'usb_boot': space(BOOT), 'protected_hashes': hashes}
    finally:
        for path in reversed(mounts):
            text('umount', str(path))


def zero_fat_free():
    guard_unmounted()
    assert BOOT.resolve() == BOOT.absolute() and not any(BOOT.iterdir())
    text('mount', '-o', 'rw,nosuid,nodev', '/dev/sda1', str(BOOT))
    temporary = BOOT / '.bmi30-zero-free.tmp'
    created = False
    fd = None
    try:
        assert text('findmnt', '-n', '-o', 'SOURCE', '-T', str(BOOT)) == '/dev/sda1'
        before = space(BOOT)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
        remaining = before['available']
        written = 0
        chunk = bytes(1024 * 1024)
        while remaining:
            try:
                count = os.write(fd, chunk[:min(remaining, len(chunk))])
            except OSError as exc:
                if exc.errno == errno.ENOSPC:
                    assert remaining <= len(chunk)
                    break
                raise
            written += count
            remaining -= count
            if written % (32 * 1024 * 1024) == 0 or remaining == 0:
                state('zeroing_usb_boot_free', written_bytes=written)
        os.fsync(fd)
        os.close(fd); fd = None
        temporary.unlink(); created = False
        text('sync', '-f', str(BOOT))
        assert space(BOOT)['available'] == before['available']
        return {'zero_bytes': written, 'space_unchanged': True}
    finally:
        if fd is not None:
            os.close(fd)
        if created:
            assert temporary.parent == BOOT and not temporary.is_symlink()
            temporary.unlink()
            text('sync', '-f', str(BOOT))
        text('umount', str(BOOT))


def zerofree(mode, logfile):
    guard_unmounted()
    state('zeroing_usb_root_free' if mode == 'apply' else 'verifying_all_usb_root_free_blocks')
    args = [str(TOOL), '-v'] + (['-n'] if mode == 'verify' else []) + ['/dev/sda2']
    with (WORK / logfile).open('wb') as log:
        p = subprocess.run(args, stdout=log, stderr=log, timeout=10800)
    assert p.returncode == 0, logfile
    raw = (WORK / logfile).read_bytes().decode(errors='replace')
    counts = re.findall(r'(\d+)/(\d+)/(\d+)', raw)
    assert counts, 'zerofree block summary missing'
    changed, free, total = map(int, counts[-1])
    if mode == 'verify':
        assert changed == 0, 'A free block still contains nonzero data'
    return {'nonzero_free_blocks': changed, 'free_blocks': free, 'total_blocks': total,
            'block_bytes': 4096, 'nonzero_free_bytes': changed * 4096}


def verify_fat_clean():
    guard_unmounted()
    check = command('fsck.fat', '-n', '/dev/sda1', allowed=(0, 1))
    if check['rc'] == 0:
        return check, None
    # Only repair the known dirty flag, not arbitrary FAT inconsistencies.
    lines = check['output'].splitlines()
    allowed = ('fsck.fat 4.2 (2021-01-31)',
               'There are differences between boot sector and its backup.',
               'This is mostly harmless. Differences: (offset:original/backup)',
               '65:01/00', 'Not automatically fixing this.', 'Dirty bit is set. ',
               'Automatically removing dirty bit.', 'Leaving filesystem unchanged.',
               '/dev/sda1: ')
    assert 'Dirty bit is set.' in check['output']
    assert all(not line.strip() or any(line.strip().startswith(prefix) for prefix in allowed) for line in lines), \
        'Unexpected FAT inconsistency; do not auto-repair'
    repair = command('fsck.fat', '-a', '/dev/sda1', allowed=(0, 1))
    return command('fsck.fat', '-n', '/dev/sda1'), repair


def main():
    guard_unmounted()
    assert TOOL.is_file() and not TOOL.is_symlink()
    assert WORK.resolve() == WORK.absolute()
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    pids = {s: text('systemctl', 'show', s, '-p', 'MainPID', '--value')
            for s in ('NetworkManager', 'bmi30-core')}
    state('checking_usb_before_zero')
    before = capture_usb()
    (WORK / 'usb-before-zero.json').write_text(json.dumps(before, indent=2) + '\n')
    guard_unmounted()
    ext4_before = command('e2fsck', '-fn', '/dev/sda2')
    root_zero = zerofree('apply', 'zerofree-apply.log')
    boot_zero = zero_fat_free()
    all_free_verified = zerofree('verify', 'zerofree-verify.log')
    guard_unmounted()
    ext4_after = command('e2fsck', '-fn', '/dev/sda2')
    fat_after, fat_repair = verify_fat_clean()
    after = capture_usb()
    assert before['protected_hashes'] == after['protected_hashes'], 'An existing USB file changed'
    assert before['usb_root']['available'] == after['usb_root']['available']
    assert before['usb_boot']['available'] == after['usb_boot']['available']
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == boot_id
    assert pids == {s: text('systemctl', 'show', s, '-p', 'MainPID', '--value') for s in pids}
    result = {'host': HOST, 'boot_id': boot_id, 'live_root': '/dev/mmcblk0p2', 'version': VERSION,
              'usb_serial': SERIAL, 'root_zero': root_zero, 'boot_zero': boot_zero,
              'all_free_verified': all_free_verified, 'protected_files_identical': len(before['protected_hashes']),
              'usb_root': after['usb_root'], 'usb_boot': after['usb_boot'],
              'ext4_before': ext4_before, 'ext4_after': ext4_after, 'fat_after': fat_after,
              'fat_dirty_flag_repair': fat_repair,
              'source_services_not_restarted': True, 'network_core_pids': pids}
    (WORK / 'zero-result.json').write_text(json.dumps(result, indent=2) + '\n')
    state('complete', result=result)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        state('failed', error=str(exc))
        raise
