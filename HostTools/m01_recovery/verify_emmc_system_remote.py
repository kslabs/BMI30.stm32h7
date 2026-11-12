"""Read-only final filesystem/boot audit of the completed S10 eMMC copy.

Receives the clone plan; never boots, repairs filesystems, or changes EEPROM.
"""
import hashlib,json,os,re,subprocess,sys,time
from pathlib import Path

PLAN=json.loads(Path(sys.argv[1]).read_text())
assert PLAN['host']=='BMI30-D5EB8F26C' and PLAN['user_boots_manually'] is True
assert re.fullmatch(r'\d{8}-\d{6}',PLAN['stamp'])
BACKUP=Path('/var/backups/bmi30-emmc-sync')/PLAN['stamp']
STATE=Path('/run/bmi30-emmc-audit.json')
MOUNT=Path('/mnt/bmi30-emmc-audit');BOOT=MOUNT/'boot/firmware'

def run(*args,**kwargs):
    return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,**kwargs).strip()

def status(stage,**extra):
    data={'stage':stage,'time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'boot_tested':False,**extra}
    STATE.write_text(json.dumps(data));print(json.dumps(data),flush=True)

def main():
    assert run('hostname')==PLAN['host']
    assert run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2'
    assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==PLAN['serial']
    assert Path('/sys/block/mmcblk0/device/cid').read_text().strip()==PLAN['cid']
    clone=json.loads(Path('/run/bmi30-emmc-copy.json').read_text())
    assert clone['stage']=='complete' and clone['result']['version']==PLAN['version']
    assert Path(clone['backup'])==BACKUP
    for device in ('/dev/mmcblk0p1','/dev/mmcblk0p2'):
        assert subprocess.run(['findmnt','-rn','-S',device],stdout=subprocess.DEVNULL).returncode!=0
    checks={}
    for label,args in [('root',('e2fsck','-f','-n','/dev/mmcblk0p2')),
                       ('boot',('fsck.fat','-n','/dev/mmcblk0p1'))]:
        status('checking_'+label+'_filesystem_read_only')
        with (BACKUP/('fsck-'+label+'.log')).open('w') as log:
            result=subprocess.run(['ionice','-c','3','nice','-n','15',*args],stdout=log,stderr=subprocess.STDOUT)
        checks[label]={'exit_code':result.returncode,'log':str(BACKUP/('fsck-'+label+'.log'))}
        assert result.returncode==0,label+' filesystem requires attention; no repair performed'
    status('auditing_read_only_emmc')
    MOUNT.mkdir(exist_ok=False)
    try:
        run('mount','-o','ro,noload','/dev/mmcblk0p2',str(MOUNT))
        run('mount','-o','ro','/dev/mmcblk0p1',str(BOOT))
        assert run('findmnt','-n','-o','SOURCE','-T',str(MOUNT))=='/dev/mmcblk0p2'
        assert run('findmnt','-n','-o','SOURCE','-T',str(BOOT))=='/dev/mmcblk0p1'
        root_id=run('blkid','-s','PARTUUID','-o','value','/dev/mmcblk0p2')
        boot_id=run('blkid','-s','PARTUUID','-o','value','/dev/mmcblk0p1')
        cmdline=(BOOT/'cmdline.txt').read_text()
        assert re.findall(r'(?<!\S)root=(\S+)',cmdline)==['PARTUUID='+root_id]
        mounts={}
        for line in (MOUNT/'etc/fstab').read_text().splitlines():
            if line.lstrip().startswith('#'):continue
            fields=line.split()
            if len(fields)>1 and fields[1] in ('/','/boot/firmware'):mounts[fields[1]]=fields[0]
        assert mounts=={'/':'PARTUUID='+root_id,'/boot/firmware':'PARTUUID='+boot_id}
        # The only intentional FAT difference is the root partition argument.
        source_cmdline=Path('/boot/firmware/cmdline.txt').read_text()
        normalize=lambda text:re.sub(r'(?<!\S)root=\S+','root=TARGET',text)
        assert normalize(source_cmdline)==normalize(cmdline)
        boot_diff=run('rsync','-rcn','--delete','--exclude=/cmdline.txt','--out-format=%i|%n',
                      '/boot/firmware/',str(BOOT)+'/')
        assert not boot_diff,'Boot checksum mismatch'
        boot_order=next(l.split('=',1)[1] for l in run('vcgencmd','bootloader_config').splitlines() if l.startswith('BOOT_ORDER='))
        assert boot_order=='0xf2614'
        settings=json.loads((BACKUP/'settings-hashes.json').read_text())
        for name,digest in settings.items():
            assert hashlib.sha256((MOUNT/name).read_bytes()).hexdigest()==digest,'Copied settings changed: '+name
        identity_names=['etc/hostname','etc/machine-id','var/lib/bmi30-agent/device_api_token',
                        'var/lib/bmi30-agent/bound_raspberry_serial','etc/bmi30-agent/id_ed25519',
                        'etc/bmi30-agent/id_ed25519.pub','var/lib/dpkg/status']
        for name in identity_names:
            source=Path('/'+name)
            if source.exists():assert source.read_bytes()==(MOUNT/name).read_bytes(),name
        selection='home/techaid/Documents/host/bmi30_sel.json'
        selections=[json.loads(p.read_text()) for p in (Path('/'+selection),MOUNT/selection)]
        for item in selections:item.pop('ts',None)
        selection_matches=selections[0]==selections[1]
        # Chroot prevents absolute links accidentally using libraries on USB.
        target_info=json.loads(run('chroot',str(MOUNT),'/usr/bin/python3','-c',
            "import json,pathlib,sys;p=json.load(sys.stdin);d=pathlib.Path('/home/techaid/Documents');env=(d/'host/bmi30_split_active_version.env').read_text();assert 'BMI30_SPLIT_VERSION='+p['version'] in env;assert pathlib.Path('/usr/lib/modules/'+p['kernel']).is_dir();print(json.dumps({'version':p['version'],'kernel_modules':p['kernel']}))",
            input=json.dumps(PLAN)))
        deps=run('chroot',str(MOUNT),'/opt/bmi30-scope-rtc/bin/python','-c',
                 'import aiortc,aiohttp,av;print(aiortc.__version__,aiohttp.__version__,av.__version__)')
        fs=os.statvfs(MOUNT)
        result={'host':PLAN['host'],'version':PLAN['version'],'filesystems':checks,
                'root_partuuid':root_id,'boot_partuuid':boot_id,'bootloader_order':boot_order,
                'boot_files_checksum_match':True,'normalized_cmdline_matches_usb':True,
                'snapshot_settings_hashes_verified':len(settings),'identity_and_dpkg_match':True,
                'current_selection_matches_except_ts':selection_matches,
                'target_namespace':target_info,'rtc_dependency_imports':deps,
                'target_available_bytes':fs.f_bavail*fs.f_frsize,
                'source_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'boot_tested':False,'reboot_performed':False,'user_boots_manually':True}
        (BACKUP/'offline-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    finally:
        for path in (BOOT,MOUNT):
            if subprocess.run(['mountpoint','-q',str(path)]).returncode==0:run('umount',str(path))
        MOUNT.rmdir()
    status('complete',result=result,emmc_unmounted=True)

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        status('failed',error=str(exc));raise
