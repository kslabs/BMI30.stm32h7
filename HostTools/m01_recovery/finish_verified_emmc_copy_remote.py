"""Finish S10's already checksum-verified copy after correcting its JS audit path.

Reuses the protected full checksum log and verified Firefox content repair,
checks for later persistent changes, then performs namespace and boot checks.
"""
import ast,hashlib,importlib.util,json,os,subprocess,sys,time
from pathlib import Path

sys.dont_write_bytecode=True
PLAN=json.loads(Path(sys.argv[1]).read_text())
assert PLAN['host']=='BMI30-D5EB8F26C' and PLAN['stamp']=='20261007-113251'
assert PLAN.get('resume_existing_backup') is True
spec=importlib.util.spec_from_file_location('clone',Path(sys.argv[1]).with_name('clone.py'))
clone=importlib.util.module_from_spec(spec);spec.loader.exec_module(clone)
BACKUP=clone.BACKUP;MOUNT=clone.MOUNT;BOOT=clone.BOOT
REPAIRED='usr/share/firefox-esr/browser/defaults/preferences/firefox-branding.js'

def meaningful(text):
    return [line for line in text.splitlines() if '|' in line and not
            (line.startswith('.f..t......|') or line.startswith('.d..t......|'))]

def main():
    clone.assert_identity()
    state=json.loads(clone.STATUS.read_text())
    assert state['stage']=='failed' and state['time']=='2026-10-07T12:50:27+0200'
    assert 'chroot' in state['error'] and 'scope_uniform.js' in state['error']
    root_log=BACKUP/'verify-full-root.log'
    assert meaningful(root_log.read_text())==['>fc........|'+REPAIRED]
    assert (BACKUP/'checksum-repair.log').read_text().strip()=='>fc........|'+REPAIRED
    proof={'original_full_checksum_time':state['time'],
           'full_checksum_log_sha256':hashlib.sha256(root_log.read_bytes()).hexdigest(),
           'content_repairs_verified':[REPAIRED],
           'later_static_changes_checksum_verified':[]}
    # The package state must remain stable through namespace/boot validation.
    import fcntl
    locks=[]
    for name in ('/var/lib/dpkg/lock-frontend','/var/lib/dpkg/lock'):
        lock=open(name,'a');fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(lock)
    for dev in ('/dev/mmcblk0p1','/dev/mmcblk0p2'):
        assert subprocess.run(['findmnt','-rn','-S',dev],stdout=subprocess.DEVNULL).returncode!=0
    MOUNT.mkdir(exist_ok=False)
    try:
        clone.run('mount','/dev/mmcblk0p2',str(MOUNT));clone.run('mount','/dev/mmcblk0p1',str(BOOT))
        assert clone.run('findmnt','-n','-o','SOURCE','-T',str(MOUNT))=='/dev/mmcblk0p2'
        assert clone.run('findmnt','-n','-o','SOURCE','-T',str(BOOT))=='/dev/mmcblk0p1'
        assert (MOUNT/'etc/hostname').read_text().strip()==PLAN['host']
        assert Path('/'+REPAIRED).read_bytes()==(MOUNT/REPAIRED).read_bytes()
        assert Path('/var/lib/dpkg/status').read_bytes()==(MOUNT/'var/lib/dpkg/status').read_bytes()
        clone.stage('completing_namespace_and_boot_checks')
        # Only maintenance code changed after the previous full content scan.
        clone.run('rsync','-aHAXc','--numeric-ids','/var/lib/bmi30-emmc-copy/',
                  str(MOUNT/'var/lib/bmi30-emmc-copy')+'/')
        root_id,boot_id=clone.rewrite_boot_references()
        count=clone.snapshot_settings()
        # Recover the same explicit mutable-path list used for that full scan.
        tree=ast.parse(Path(sys.argv[1]).with_name('clone.py').read_text())
        dynamic=next(ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n,ast.Assign)
                     and any(isinstance(t,ast.Name) and t.id=='dynamic' for t in n.targets))
        base=['ionice','-c','3','nice','-n','15','rsync','-aHAXxn','--numeric-ids',
              '--delete','--out-format=%i|%n']+['--exclude='+x for x in clone.EXCLUDES+dynamic]
        later=clone.run(*base,'/',str(MOUNT)+'/')
        (BACKUP/'verify-post-checksum-metadata.log').write_text(later+'\n')
        changed=meaningful(later)
        if changed:
            names=[]
            for line in changed:
                kind,name=line.split('|',1);name=clone.rsync_name(name).rstrip('/')
                assert len(kind)>1 and kind[1]=='f','Later non-file change: '+name
                assert not Path(name).is_absolute() and '..' not in Path(name).parts
                source=Path('/'+name);target=MOUNT/name
                assert source.is_file() and not source.is_symlink() and not target.is_symlink()
                assert target.parent.resolve().is_relative_to(MOUNT.resolve())
                names.append(name)
            listing=BACKUP/'post-checksum-changes.files'
            listing.write_bytes(b''.join(os.fsencode(name)+b'\0' for name in names))
            cmd=['rsync','-aHAXxc','--numeric-ids','--from0','--files-from='+str(listing),'--out-format=%i|%n']
            clone.run(*cmd,'/',str(MOUNT)+'/')
            assert not meaningful(clone.run(*cmd,'--dry-run','/',str(MOUNT)+'/'))
            proof['later_static_changes_checksum_verified']=names
        runtime=clone.verify_target_runtime()
        # Import the actual acquisition interpreter's dependencies in eMMC.
        pid=clone.run('systemctl','show','bmi30-core','-p','MainPID','--value')
        python=Path('/proc/'+pid+'/cmdline').read_bytes().split(b'\0')[0].decode()
        if python=='python3':python='/usr/bin/python3'
        elif not python.startswith('/'):python='/home/techaid/Documents/'+python
        runtime['acquisition_dependency_imports']=clone.run('chroot',str(MOUNT),python,'-c',
            'import numpy,usb,pyqtgraph;print(numpy.__version__,usb.__version__,pyqtgraph.__version__)')
        runtime['acquisition_interpreter']=python
        assert not clone.run('rsync','-rcn','--delete','--exclude=/cmdline.txt','--out-format=%i|%n',
                             '/boot/firmware/',str(BOOT)+'/')
        clone.run('sync','-f',str(BOOT));clone.run('sync','-f',str(MOUNT))
        result={'host':PLAN['host'],'source':'/dev/sda2','target':'/dev/mmcblk0p2',
                'source_usb_serial':PLAN['serial'],'emmc_cid':PLAN['cid'],'copied':True,
                'critical_verified':{'full_persistent_root':'checksum_and_metadata_match',
                                     'checksum_checkpoint':proof,'live_mutable_paths_not_compared':dynamic,**runtime},
                'verified_identity_and_settings_files':count,'boot_files_verified':True,
                'root_partuuid':root_id,'boot_partuuid':boot_id,'boot_tested':False,
                'user_boots_manually':True,'reboot_performed':False,'version':PLAN['version'],
                'copy_excludes':clone.EXCLUDES,'consistent_settings_pause_seconds':0,'services_stopped':[],
                'rollback':str(BACKUP),'rollback_bytes':550942849,
                'finished':time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        (BACKUP/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    finally:
        for path in (BOOT,MOUNT):
            if subprocess.run(['mountpoint','-q',str(path)]).returncode==0:clone.run('umount',str(path))
        MOUNT.rmdir()
    clone.stage('complete',result=result)

if __name__=='__main__':
    try:main()
    except BaseException as error:
        clone.stage('failed',error=str(error));raise
