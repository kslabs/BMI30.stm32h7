"""Copy this device's live eMMC to its identified, inactive USB; never formats disks.

Run as root on the device with a JSON plan. Rollback stays on that same eMMC.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

PLAN = json.loads(Path(sys.argv[1]).read_text())
STAMP = PLAN['stamp']
BACKUP = Path('/var/backups/bmi30-media-sync') / STAMP
MOUNT = Path('/mnt/bmi30-media-sync-copy')
BOOT = MOUNT / 'boot/firmware'
STATUS = Path('/run/bmi30-media-sync.json')
EXCLUDES = PLAN['excludes']
PAUSED = []
PACKAGE_LOCKS = []


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, **kwargs).strip()


def stage(name, **extra):
    record = {'host':PLAN['host'], 'stage':name, 'time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'backup':str(BACKUP), 'usb_serial':PLAN['serial'], 'boot_tested':False, **extra}
    STATUS.write_text(json.dumps(record))
    print(json.dumps(record), flush=True)


def assert_identity():
    assert run('hostname') == PLAN['host']
    assert run('findmnt','-n','-o','SOURCE','/') == '/dev/mmcblk0p2'
    assert run('findmnt','-n','-o','SOURCE','/boot/firmware') == '/dev/mmcblk0p1'
    assert run('lsblk','-dn','-o','SERIAL','/dev/sda') == PLAN['serial']
    assert run('blkid','-s','UUID','-o','value','/dev/sda2') == PLAN['uuid']
    assert run('blkid','-s','UUID','-o','value','/dev/mmcblk0p2') != PLAN['uuid']
    assert Path('/proc/device-tree/serial-number').read_text() == PLAN['cm_serial']
    assert Path('/sys/block/mmcblk0/device/cid').read_text() == PLAN['emmc_cid']
    assert 'BMI30_SPLIT_VERSION='+PLAN['version']+'\n' in Path('/home/techaid/Documents/host/bmi30_split_active_version.env').read_text()


def rsync_root(name, dry=False):
    command = ['ionice','-c','3','nice','-n','15','rsync','-aHAXx','--numeric-ids',
               '--delete-before','--itemize-changes','--out-format=%i|%n','--stats']
    if dry:
        command.append('--dry-run')
    command += ['--exclude='+x for x in EXCLUDES] + ['/', str(MOUNT)+'/']
    with (BACKUP/(name+'.log')).open('w') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    assert result.returncode in (0,24), f'{name}: rsync exit {result.returncode}'
    return (BACKUP/(name+'.log')).read_text()


def archive_previous(names, root, filename):
    listing = BACKUP/(filename+'.files')
    listing.write_bytes(b''.join(os.fsencode(n)+b'\0' for n in names))
    destination = BACKUP/(filename+'.tar.gz')
    available = os.statvfs('/').f_bavail * os.statvfs('/').f_frsize
    budget = available - 700*1024*1024
    assert budget > 0, 'Insufficient rollback space'
    command = ['ionice','-c','3','nice','-n','15','tar','--acls','--xattrs','--numeric-owner',
               '-I','gzip -3','-cf','-','-C',str(root),'--no-recursion','--null','-T',str(listing)]
    size = 0
    with destination.open('xb') as output, (BACKUP/(filename+'.stderr')).open('w') as errors:
        destination.chmod(0o600)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        try:
            while True:
                data = process.stdout.read(1024*1024)
                if not data:
                    break
                size += len(data)
                if size > budget:
                    raise RuntimeError('Rollback archive exceeds free-space budget; USB not modified')
                output.write(data)
            assert process.wait() == 0, 'Rollback archive failed; USB not modified'
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    subprocess.run(['gzip','-t',str(destination)], check=True)
    return size


def pause():
    # No network manager, SSH, or desktop sessions are stopped.
    for service in ('bmi30-hotspot-info.service','bmi30-core.service','bmi30-agent.service'):
        if subprocess.run(['systemctl','is-active','--quiet',service]).returncode == 0:
            run('systemctl','stop',service)
            PAUSED.append(service)


def resume():
    while PAUSED:
        service = PAUSED[-1]
        run('systemctl','start',service)
        PAUSED.pop()


def rewrite_boot_references():
    root_id = run('blkid','-s','PARTUUID','-o','value','/dev/sda2')
    boot_id = run('blkid','-s','PARTUUID','-o','value','/dev/sda1')
    cmdline = BOOT/'cmdline.txt'
    text, count = re.subn(r'(?<!\S)root=\S+', 'root=PARTUUID='+root_id, cmdline.read_text())
    assert count == 1
    cmdline.write_text(text)
    fstab = MOUNT/'etc/fstab'
    lines = fstab.read_text().splitlines()
    found = set()
    for index, line in enumerate(lines):
        if line.lstrip().startswith('#'):
            continue
        fields = line.split()
        if len(fields) >= 2 and fields[1] in ('/','/boot/firmware'):
            found.add(fields[1])
            fields[0] = 'PARTUUID='+ (root_id if fields[1]=='/' else boot_id)
            lines[index] = '\t'.join(fields)
    assert found == {'/','/boot/firmware'}
    fstab.write_text('\n'.join(lines)+'\n')
    return root_id, boot_id


def verify_critical():
    reports = {}
    # Compare system services, network configuration, programs, and installed package state.
    for name in ('etc','opt','usr/local','var/lib/dpkg',
                 'home/techaid/Documents/host/bmi30_active_runtime',
                 'home/techaid/Documents/host/bmi30_split_bundles/'+PLAN['version']):
        command = ['ionice','-c','3','nice','-n','15','rsync','-aHAXnci','--delete',
                   '--out-format=%i|%n']
        command += ['--exclude=__pycache__','--exclude=*.pyc','--exclude=.lgd-*']
        if name == 'etc':
            command += ['--exclude=/fstab','--exclude=/fake-hwclock.data','--exclude=/resolv.conf']
        result = run(*command, '/'+name+'/', str(MOUNT/name)+'/')
        (BACKUP/('verify-'+name.replace('/','-')+'.log')).write_text(result)
        # Content, missing files, permissions and symlinks matter; timestamps alone can move live.
        bad = [line for line in result.splitlines() if line and not
               (line.startswith('.f..t......|') or line.startswith('.d..t......|'))]
        assert not bad, 'Critical verification differs: '+name+'; see protected log'
        reports[name] = 'match'
    paths = ['etc/machine-id','etc/hostname','var/lib/bmi30-agent/device_api_token',
             'var/lib/bmi30-agent/bound_raspberry_serial','etc/bmi30-agent/id_ed25519',
             'etc/bmi30-agent/id_ed25519.pub']
    host = Path('/home/techaid/Documents/host')
    paths += [str(p).lstrip('/') for p in host.iterdir() if p.is_file() and
              (p.suffix in ('.json','.npz','.npy','.env') or p.name.endswith('2026-09-30-1650'))]
    for name in paths:
        assert Path('/'+name).read_bytes() == (MOUNT/name).read_bytes(), 'File differs: '+name
    run('chroot',str(MOUNT),'/bin/bash','/home/techaid/Documents/switch_bmi30_split_versions.sh','--validate',PLAN['version'])
    for service in ('bmi30-network','bmi30-agent','bmi30-antenna-check'):
        assert (MOUNT/'etc/systemd/system/multi-user.target.wants'/(service+'.service')).is_symlink()
    assert 'dtparam=ant2' in (BOOT/'config.txt').read_text()
    return reports, len(paths)


def main():
    assert_identity()
    for dev in ('/dev/sda1','/dev/sda2'):
        assert subprocess.run(['findmnt','-rn','-S',dev],stdout=subprocess.DEVNULL).returncode != 0
    # The unattended-upgrade-shutdown process normally waits all day. Use the
    # real package-manager locks to prevent changes during the snapshot.
    import fcntl
    for name in ('/var/lib/dpkg/lock-frontend','/var/lib/dpkg/lock'):
        lock = open(name, 'a')
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('An active package transaction holds '+name)
        PACKAGE_LOCKS.append(lock)
    BACKUP.mkdir(parents=True, mode=0o700, exist_ok=False)
    BACKUP.parent.chmod(0o700)
    MOUNT.mkdir(exist_ok=False)
    source_fs = os.statvfs('/')
    source_used = (source_fs.f_blocks-source_fs.f_bfree)*source_fs.f_frsize
    try:
        run('mount','/dev/sda2',str(MOUNT))
        run('mount','/dev/sda1',str(BOOT))
        target_fs = os.statvfs(MOUNT)
        assert source_used+256*1024*1024 < target_fs.f_blocks*target_fs.f_frsize
        stage('planning_rollback')
        differences = rsync_root('before', dry=True)
        names = set()
        for line in differences.splitlines():
            if '|' not in line:
                continue
            kind, name = line.split('|',1)
            name = name.rstrip('/')
            if name in ('','.'): continue
            candidate = MOUNT/name
            assert '..' not in Path(name).parts and not Path(name).is_absolute()
            if candidate.exists() or candidate.is_symlink():
                names.add(name)
        stage('backing_up_previous_usb', entries=len(names))
        old_size = archive_previous(sorted(names), MOUNT, 'previous-root')
        boot_names = ['.']+[str(p.relative_to(BOOT)) for p in BOOT.rglob('*')]
        boot_size = archive_previous(boot_names, BOOT, 'previous-boot')
        assert_identity()
        stage('copying_root', rollback_bytes=old_size+boot_size)
        rsync_root('bulk')
        stage('copying_boot')
        run('ionice','-c','3','nice','-n','15','rsync','-rt','--delete','--modify-window=1',
            '/boot/firmware/',str(BOOT)+'/')
        stage('final_copy_with_detection_paused')
        pause()
        try:
            config_path=Path('/home/techaid/Documents/host/bmi30_config.json')
            saved_config=config_path.read_bytes()
            (BACKUP/'source-config.json').write_bytes(saved_config)
            rsync_root('final')
            root_id, boot_id = rewrite_boot_references()
            stage('verifying_settings')
            reports, count = verify_critical()
            assert config_path.read_bytes()==saved_config
        finally:
            resume()
        stage('flushing_usb')
        run('sync','-f',str(BOOT))
        run('sync','-f',str(MOUNT))
        boot_result = run('rsync','-rcn','--delete','--exclude=/cmdline.txt','--out-format=%i|%n',
                          '/boot/firmware/',str(BOOT)+'/')
        assert not boot_result, 'Boot files differ'
        result = {'host':PLAN['host'],'source':'/dev/mmcblk0p2','target':'/dev/sda2',
                  'serial':PLAN['serial'],'copied':True,'critical_verified':reports,
                  'verified_identity_and_settings_files':count,'boot_files_verified':True,
                  'root_partuuid':root_id,'boot_partuuid':boot_id,'boot_tested':False,
                  'rollback':str(BACKUP),'rollback_bytes':old_size+boot_size,
                  'version':PLAN['version'],'config_sha256':hashlib.sha256(saved_config).hexdigest(),
                  'mode':{k:json.loads(saved_config).get(k) for k in ('avg_n','rpi_avg_n','rx_phase_locked_shifts')},
                  'finished':time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        (BACKUP/'result.json').write_text(json.dumps(result,indent=2))
        return result
    finally:
        resume()
        for path in (BOOT,MOUNT):
            if subprocess.run(['mountpoint','-q',str(path)]).returncode == 0:
                run('umount',str(path))
        MOUNT.rmdir()


if __name__ == '__main__':
    try:
        result = main()
        stage('complete', result=result)
    except BaseException as exc:
        stage('failed', error=str(exc))
        raise
