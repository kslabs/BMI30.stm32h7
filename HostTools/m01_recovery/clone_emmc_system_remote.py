"""Copy S10 live USB to its identified inactive eMMC; never formats or reboots.

Run as root on S10 with a JSON plan. Rollback stays on the USB source.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import shutil
import tempfile

PLAN = json.loads(Path(sys.argv[1]).read_text())
STAMP = PLAN['stamp']
assert re.fullmatch(r'\d{8}-\d{6}', STAMP)
assert PLAN['host'] == 'BMI30-D5EB8F26C'
assert PLAN.get('user_boots_manually') is True
BACKUP = Path('/var/backups/bmi30-emmc-sync') / STAMP
MOUNT = Path('/mnt/bmi30-emmc-copy')
BOOT = MOUNT / 'boot/firmware'
STATUS = Path('/run/bmi30-emmc-copy.json')
EXCLUDES = PLAN['excludes']
PAUSED = []
PACKAGE_LOCKS = []
PROTECTED_UNITS = {'NetworkManager.service','ssh.service','sshd.service',
                   'xrdp.service','xrdp-sesman.service','bmi30-network.service',
                   'bmi30-channel-policy.service'}


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, **kwargs).strip()


def stage(name, **extra):
    record = {'host':PLAN['host'], 'stage':name, 'time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'backup':str(BACKUP), 'source_usb_serial':PLAN['serial'], 'boot_tested':False, **extra}
    STATUS.write_text(json.dumps(record))
    print(json.dumps(record), flush=True)


def assert_identity():
    assert run('hostname') == PLAN['host']
    assert run('findmnt','-n','-o','SOURCE','/') == '/dev/sda2'
    assert run('findmnt','-n','-o','SOURCE','/boot/firmware') == '/dev/sda1'
    assert run('lsblk','-dn','-o','SERIAL','/dev/sda') == PLAN['serial']
    assert run('blkid','-s','UUID','-o','value','/dev/mmcblk0p2') == PLAN['uuid']
    assert run('blkid','-s','UUID','-o','value','/dev/sda2') != PLAN['uuid']
    assert Path('/sys/block/mmcblk0/device/cid').read_text().strip() == PLAN['cid']
    env=Path('/home/techaid/Documents/host/bmi30_split_active_version.env').read_text()
    assert 'BMI30_SPLIT_VERSION='+PLAN['version'] in env


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


def rsync_name(text):
    encoded=re.sub(rb'\\#([0-7]{3})',lambda m:bytes([int(m[1],8)]),os.fsencode(text))
    return os.fsdecode(encoded)


def archive_previous(names, root, filename):
    listing = BACKUP/(filename+'.files')
    listing.write_bytes(b''.join(os.fsencode(n)+b'\0' for n in names))
    dense = filename == 'previous-root' and PLAN.get('backup_compression') == 'xz'
    destination = BACKUP/(filename+('.tar.xz' if dense else '.tar.gz'))
    available = os.statvfs('/').f_bavail * os.statvfs('/').f_frsize
    budget = available - 700*1024*1024
    assert budget > 0, 'Insufficient rollback space'
    command = ['ionice','-c','3','nice','-n','15','tar','--acls','--xattrs','--numeric-owner',
               '-I','xz -T1 -6' if dense else 'gzip -3','-cf','-','-C',str(root),'--no-recursion','--null','-T',str(listing)]
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
                    raise RuntimeError('Rollback archive exceeds free-space budget; eMMC not modified')
                output.write(data)
            assert process.wait() == 0, 'Rollback archive failed; eMMC not modified'
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    subprocess.run(['xz' if dense else 'gzip','-t',str(destination)], check=True)
    return size


def pause():
    # Retained for future maintenance callers; the copy itself never pauses.
    services=('bmi30-communication-recovery.service','bmi30-scope-rtc.service',
              'bmi30-hotspot-info.service','bmi30-agent.service','bmi30-core.service')
    guard_stop_dependencies(services)
    for service in services:
        if subprocess.run(['systemctl','is-active','--quiet',service]).returncode == 0:
            run('systemctl','stop',service)
            PAUSED.append(service)


def guard_stop_dependencies(services, lookup=None):
    """Reject a stop transaction whose propagated closure includes networking."""
    if lookup is None:
        def lookup(unit):
            properties=run('systemctl','show',unit,'-p','RequiredBy','-p','BoundBy',
                           '-p','ConsistsOf','-p','PropagatesStopTo')
            return set(name for line in properties.splitlines()
                       for name in line.partition('=')[2].split())
    visited=set();pending=list(services)
    while pending:
        unit=pending.pop()
        if unit in visited:continue
        visited.add(unit)
        assert unit not in PROTECTED_UNITS, 'Stop would disconnect access: '+unit
        pending.extend(lookup(unit)-visited)
    return sorted(visited)


def resume():
    while PAUSED:
        service = PAUSED[-1]
        run('systemctl','start',service)
        PAUSED.pop()


def rewrite_boot_references():
    root_id = run('blkid','-s','PARTUUID','-o','value','/dev/mmcblk0p2')
    boot_id = run('blkid','-s','PARTUUID','-o','value','/dev/mmcblk0p1')
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


def snapshot_settings():
    paths = ['etc/machine-id','etc/hostname','var/lib/bmi30-agent/device_api_token',
             'var/lib/bmi30-agent/bound_raspberry_serial','etc/bmi30-agent/id_ed25519',
             'etc/bmi30-agent/id_ed25519.pub']
    host = Path('/home/techaid/Documents/host')
    paths += [str(p).lstrip('/') for p in host.iterdir() if p.is_file() and
              p.suffix in ('.json','.npz','.npy','.env')]
    for directory in ('/etc/bmi30-agent','/etc/NetworkManager/system-connections'):
        paths += [str(p).lstrip('/') for p in Path(directory).rglob('*') if p.is_file() and not p.is_symlink()]
    # Configuration writers stay online. Capture a stable set of bytes twice,
    # then write that point-in-time snapshot atomically into the inactive root.
    names=sorted({str(Path('/'+name).resolve()).lstrip('/') for name in paths
                  if Path('/'+name).is_file()})
    for attempt in range(5):
        snapshot={name:Path('/'+name).read_bytes() for name in names}
        if all(Path('/'+name).read_bytes()==data for name,data in snapshot.items()):break
        time.sleep(.05)
    else:raise RuntimeError('Configuration is changing continuously; retry copy later')
    verified={}
    for name,data in snapshot.items():
        src=Path('/'+name);dst=MOUNT/name
        assert not dst.is_symlink(), 'Unexpected target setting symlink: '+name
        dst.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dst.parent,delete=False) as temp:
            temporary=Path(temp.name);temp.write(data);temp.flush();os.fsync(temp.fileno())
        try:
            shutil.copystat(src,temporary)
            st=src.stat();os.chown(temporary,st.st_uid,st.st_gid)
            os.replace(temporary,dst)
        finally:
            temporary.unlink(missing_ok=True)
        assert dst.read_bytes()==data,'Snapshot differs: '+name
        verified[name]=hashlib.sha256(data).hexdigest()
    (BACKUP/'settings-hashes.json').write_text(json.dumps(verified,indent=2))
    return len(verified)


def verify_critical():
    # Check every persistent file by content and metadata. Mutable runtime
    # logs/caches were copied, but can change again in the running USB system.
    dynamic=['/etc/fstab','/etc/fake-hwclock.data','/var/log/***',
             '/var/lib/bmi30-agent/state.json','/var/lib/systemd/timesync/***',
             '/var/lib/NetworkManager/NetworkManager.state','/var/lib/NetworkManager/timestamps',
             '/var/lib/NetworkManager/seen-bssids','/home/*/.cache/***',
             '/home/*/.config/Code/logs/***','/home/*/.vscode-server/data/logs/***',
             '/home/*/.local/share/gvfs-metadata/***',
             '/home/techaid/Documents/host/bmi30_sel.json',
             '/home/techaid/Documents/.bmi30_cloud_sync/***',
             '/var/tmp/***','**/__pycache__/***','**/.lgd-nfy*','**/bmi30_faults.log',
             '/home/*/.config/Code/Local Storage/***',
             '/home/*/.config/Code/WebStorage/***',
             '/home/*/.config/Code/Cache/***','/home/*/.config/Code/CachedData/***',
             '/home/*/.config/Code/GPUCache/***',
             '/home/*/.mozilla/firefox/**/cookies.sqlite*',
             '/home/*/.mozilla/firefox/**/AlternateServices.bin',
             '/home/*/.mozilla/firefox/**/storage/***']
    command=['ionice','-c','3','nice','-n','15','rsync','-aHAXxnc','--numeric-ids',
             '--delete','--out-format=%i|%n']
    command+=['--exclude='+name for name in EXCLUDES+dynamic]
    with (BACKUP/'verify-full-root.log').open('w') as log:
        r=subprocess.run(command+['/',str(MOUNT)+'/'],stdout=log,stderr=subprocess.STDOUT)
    assert r.returncode in (0,24), 'Full-root checksum check failed'
    differences=(BACKUP/'verify-full-root.log').read_text().splitlines()
    bad=[line for line in differences if '|' in line and not
         (line.startswith('.f..t......|') or line.startswith('.d..t......|'))]
    repaired=[]
    if bad:
        # A file can retain the same size and mtime after its content changed.
        # Repair such regular files by checksum rather than hiding differences.
        names=[]
        for line in bad:
            kind,name=line.split('|',1);name=rsync_name(name).rstrip('/')
            assert len(kind)>1 and kind[1]=='f','Non-file difference requires inspection: '+name
            assert not Path(name).is_absolute() and '..' not in Path(name).parts
            src=Path('/'+name);dst=MOUNT/name
            assert src.is_file() and not src.is_symlink()
            assert not dst.is_symlink() and dst.parent.resolve().is_relative_to(MOUNT.resolve())
            names.append(name)
        listing=BACKUP/'checksum-repair.files'
        listing.write_bytes(b''.join(os.fsencode(name)+b'\0' for name in names))
        stage('repairing_verified_content_differences', files=len(names))
        base=['ionice','-c','3','nice','-n','15','rsync','-aHAXxc','--numeric-ids',
              '--from0','--files-from='+str(listing),'--out-format=%i|%n']
        with (BACKUP/'checksum-repair.log').open('w') as log:
            subprocess.run(base+['/',str(MOUNT)+'/'],stdout=log,stderr=subprocess.STDOUT,check=True)
        check=run(*base,'--dry-run','/',str(MOUNT)+'/')
        assert not [line for line in check.splitlines() if '|' in line and not
                    (line.startswith('.f..t......|') or line.startswith('.d..t......|'))], 'Checksum repair differs'
        for name in names:
            assert hashlib.sha256(Path('/'+name).read_bytes()).digest()==hashlib.sha256((MOUNT/name).read_bytes()).digest(),name
        repaired=names
    return {'full_persistent_root':'checksum_and_metadata_match',
            'content_repairs_verified':repaired,
            'live_mutable_paths_not_compared':dynamic,**verify_target_runtime()}


def verify_target_runtime():
    # Run checks in the target namespace: absolute symlinks cannot point back
    # into the running USB root and falsely validate missing eMMC content.
    code="""
import hashlib,json,pathlib,sys
p=json.load(sys.stdin);d=pathlib.Path('/home/techaid/Documents');active=d/'host/bmi30_active_runtime/project'
env=(d/'host/bmi30_split_active_version.env').read_text()
assert 'BMI30_SPLIT_VERSION='+p['version'] in env
assert pathlib.Path('/etc/hostname').read_text().strip()==p['host']
count=0
for name,digest in p['hashes'].items():
 if name.startswith('host/') and '.py' in pathlib.Path(name).name:
  assert hashlib.sha256((active/name).read_bytes()).hexdigest()==digest,name;count+=1
for name in ('scope_codec.py','scope_rtc.py','scope_uniform.js'):
 rel='utilities/bmi30_scope/'+name
 path=(d/'host/bmi30_split_bundles'/p['version']/'project'/rel) if name.endswith('.js') else pathlib.Path('/opt/bmi30-network')/name
 assert hashlib.sha256(path.read_bytes()).hexdigest()==p['hashes'][rel],name
assert pathlib.Path('/usr/lib/modules/'+p['kernel']).is_dir()
print(json.dumps({'acquisition_modules_verified':count,'version':p['version']}))
"""
    info=json.loads(run('chroot',str(MOUNT),'/usr/bin/python3','-c',code,input=json.dumps(PLAN)))
    imports=run('chroot',str(MOUNT),'/opt/bmi30-scope-rtc/bin/python','-c',
                'import aiortc,aiohttp,av;print(aiortc.__version__,aiohttp.__version__,av.__version__)')
    services={}
    for service in ('bmi30-core','bmi30-hotspot-info','bmi30-network','bmi30-agent',
                    'bmi30-tunnel','bmi30-antenna-check','bmi30-scope-rtc'):
        services[service]=run('systemctl','--root',str(MOUNT),'is-enabled',service)
        assert services[service] in ('enabled','enabled-runtime','static'), service
    assert 'dtparam=ant2' in (BOOT/'config.txt').read_text()
    return {'acquisition':info,'rtc_dependency_imports':imports,'services':services}


def main():
    assert_identity()
    for dev in ('/dev/mmcblk0p1','/dev/mmcblk0p2'):
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
    resumed=PLAN.get('resume_existing_backup') is True
    if resumed:
        assert BACKUP.is_dir() and not BACKUP.is_symlink()
        assert (BACKUP/'previous-root.tar.gz').stat().st_size==481122821
        assert (BACKUP/'previous-boot.tar.gz').stat().st_size==69820028
    else:
        BACKUP.mkdir(parents=True, mode=0o700, exist_ok=False)
    BACKUP.parent.chmod(0o700)
    MOUNT.mkdir(exist_ok=False)
    source_fs = os.statvfs('/')
    source_used = (source_fs.f_blocks-source_fs.f_bfree)*source_fs.f_frsize
    try:
        run('mount','/dev/mmcblk0p2',str(MOUNT))
        run('mount','/dev/mmcblk0p1',str(BOOT))
        assert run('findmnt','-n','-o','SOURCE','-T',str(MOUNT))=='/dev/mmcblk0p2'
        assert run('findmnt','-n','-o','SOURCE','-T',str(BOOT))=='/dev/mmcblk0p1'
        assert (MOUNT/'etc/hostname').read_text().strip()==PLAN['host']
        target_fs = os.statvfs(MOUNT)
        assert source_used+256*1024*1024 < target_fs.f_blocks*target_fs.f_frsize
        if resumed:
            stage('verifying_existing_rollback')
            for name in ('previous-root.tar.gz','previous-boot.tar.gz'):
                run('gzip','-t',str(BACKUP/name))
            old_size=(BACKUP/'previous-root.tar.gz').stat().st_size
            boot_size=(BACKUP/'previous-boot.tar.gz').stat().st_size
        else:
            stage('planning_rollback')
            differences = rsync_root('before', dry=True)
            names = set()
            for line in differences.splitlines():
                if '|' not in line:continue
                kind, name = line.split('|',1)
                name = rsync_name(name).rstrip('/')
                if name in ('','.'):continue
                candidate = MOUNT/name
                assert '..' not in Path(name).parts and not Path(name).is_absolute()
                if candidate.exists() or candidate.is_symlink():names.add(name)
            stage('backing_up_previous_emmc', entries=len(names))
            old_size = archive_previous(sorted(names), MOUNT, 'previous-root')
            boot_names = ['.']+[str(p.relative_to(BOOT)) for p in BOOT.rglob('*')]
            boot_size = archive_previous(boot_names, BOOT, 'previous-boot')
        assert_identity()
        stage('copying_root', rollback_bytes=old_size+boot_size)
        rsync_root('bulk')
        stage('copying_boot')
        run('ionice','-c','3','nice','-n','15','rsync','-rt','--delete','--modify-window=1',
            '/boot/firmware/',str(BOOT)+'/')
        stage('final_copy_with_services_running')
        rsync_root('final')
        root_id, boot_id = rewrite_boot_references()
        stage('snapshotting_settings_with_services_running')
        count = snapshot_settings()
        pause_seconds=0.0
        stage('verifying_full_persistent_root_with_acquisition_running')
        reports = verify_critical()
        stage('flushing_emmc')
        run('sync','-f',str(BOOT))
        run('sync','-f',str(MOUNT))
        boot_result = run('rsync','-rcn','--delete','--exclude=/cmdline.txt','--out-format=%i|%n',
                          '/boot/firmware/',str(BOOT)+'/')
        assert not boot_result, 'Boot files differ'
        result = {'host':PLAN['host'],'source':'/dev/sda2','target':'/dev/mmcblk0p2',
                  'source_usb_serial':PLAN['serial'],'emmc_cid':PLAN['cid'],'copied':True,'critical_verified':reports,
                  'verified_identity_and_settings_files':count,'boot_files_verified':True,
                  'root_partuuid':root_id,'boot_partuuid':boot_id,'boot_tested':False,
                  'user_boots_manually':True,'reboot_performed':False,'version':PLAN['version'],
                  'copy_excludes':EXCLUDES,
                  'consistent_settings_pause_seconds':pause_seconds,
                  'services_stopped':[],
                  'rollback':str(BACKUP),'rollback_bytes':old_size+boot_size,
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
