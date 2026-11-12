"""Explicitly selected USB only. Source eMMC, network and detector stay running."""
import contextlib, fcntl, os, re, shutil, stat, subprocess, sys, time, uuid
from usb_recovery_common import *
from usb_recovery_progress import fields as progress_fields
from usb_recovery_finish import finish as finish_filesystems,cleanup_unmount,FinishTimeout

EXCLUDES=['/boot/firmware/***','/dev/***','/proc/***','/sys/***','/run/***','/tmp/***',
 '/mnt/***','/media/***','/lost+found/***','/swapfile','/var/swap','/var/backups/***',
 '/var/cache/***','/var/log/***','/var/lib/bmi30-usb-recovery/***',
 '/var/lib/bmi30-usb-source-snapshot/***','/var/lib/bmi30-usb-emmc-settings/***','/var/lib/bmi30-usb-adoption.json','/var/lib/bmi30-portable-runtime.json',
 '/home/*/thinclient_drives/***','/home/*/.cache/***','/home/*/.gvfs/***',
 '/home/*/.ssh/***','/root/.ssh/***','/home/*/.config/rclone/***','/root/.config/rclone/***',
 '/home/*/.mozilla/***','/home/*/.config/chromium/***','/home/*/.config/google-chrome/***']

def stage(job, name, progress, message, **extra):
    previous=read_json(job/'status.json',{})
    progress=max(int(previous.get('progress',0)),int(progress))
    progress=min(100 if name=='complete' else 99,max(0,progress))
    metrics=progress_fields(previous,bytes_copied=extra.pop('bytes_copied',None),finished=name in ('complete','failed'))
    write_json(job/'status.json',{'stage':name,'progress':progress,'message':message,
        'kind':read_json(job/'plan.json',{}).get('kind','usb'),
        'safe_to_remove':False,'boot_tested':False,**metrics,**extra})

def prepare_system_directories(mount):
    # Excluded virtual trees still need mountpoints on a fresh filesystem,
    # before initramfs can start PID 1.
    for name in ('dev','proc','sys','run','mnt','media','var/log','var/cache','var/backups','boot/firmware','tmp','var/tmp'):
        path=mount/name
        if path.is_symlink():raise Refused('Недопустимая ссылка системного каталога USB.')
        path.mkdir(parents=True,exist_ok=True)
        path.chmod(0o1777 if name in ('tmp','var/tmp') else 0o755)
        os.chown(path,0,0)
    # Keep diagnostics on the USB if a later boot fails before networking.
    journal=mount/'var/log/journal';journal.mkdir(exist_ok=True)
    journal.chmod(0o2755);shutil.chown(journal,user='root',group='systemd-journal')

def check_binding(plan,cfg):
    inv=inventory();disk=validate(inv,plan['target_path'],cfg,plan['required_bytes'],mounted_ok=False,kind=plan.get('kind','usb'))
    if inv['boot_id']!=plan['source_boot_id'] or binding(inv,disk)!=plan['binding']:
        raise Refused('USB был отключён или заменён. Запись остановлена.')
    return inv,disk

def install_no_emmc_overlay(boot):
    overlay=MODULE/'bmi30-usb-no-emmc.dtbo'
    if not overlay.is_file():raise Refused('В комплекте отсутствует защита аварийной загрузки от eMMC.')
    target=boot/'overlays/bmi30-usb-no-emmc.dtbo';target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(overlay,target)
    path=boot/'config.txt';text=path.read_text()
    text=re.sub(r'\n# BMI30_EMERGENCY_EMMC_BEGIN.*?# BMI30_EMERGENCY_EMMC_END\n?', '\n',text,flags=re.S)
    text+='\n# BMI30_EMERGENCY_EMMC_BEGIN\n[all]\ndtoverlay=bmi30-usb-no-emmc\n# BMI30_EMERGENCY_EMMC_END\n'
    atomic(path,text.encode(),0o644)

def copy_root(job, mount, low, high):
    args=['ionice','-c','3','nice','-n','15','rsync','-aHAXx','--numeric-ids','--delete-delay',
          '--no-inc-recursive','--info=progress2','--stats','--outbuf=L']+['--exclude='+value for value in EXCLUDES]+['/',str(mount)+'/']
    baseline=int(read_json(job/'status.json',{}).get('bytes_copied',0));phase_bytes=0;transferred=None
    with (job/'copy-errors.log').open('a') as errors:
        process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=errors,env={**os.environ,'LC_ALL':'C'})
        buffer=b'';last=0
        try:
            while True:
                chunk=os.read(process.stdout.fileno(),4096)
                if not chunk:break
                buffer+=chunk
                parts=re.split(b'[\r\n]',buffer);buffer=parts.pop()
                for line in parts:
                    amount=re.match(rb'\s*([\d,]+)\s+\d{1,3}%',line)
                    if amount:phase_bytes=max(phase_bytes,int(amount[1].replace(b',',b'')))
                    total=re.search(rb'Total transferred file size:\s*([\d,]+)',line)
                    if total:transferred=int(total[1].replace(b',',b''))
                    match=re.search(rb'\s(\d{1,3})%',line)
                    if match and time.monotonic()-last>1:
                        percent=min(100,int(match.group(1)));last=time.monotonic()
                        stage(job,'copying',low+(high-low)*percent//100,
                              'Копирование системы.' if low<70 else 'Повторная сверка изменившихся файлов.',
                              bytes_copied=baseline+phase_bytes if low<70 else baseline)
            if process.wait() not in (0,24):raise Refused('Не удалось скопировать систему. USB не готов.')
            # The delta pass's progress counter includes unchanged files. Its
            # stats report alone tells us how much was actually transferred.
            amount=transferred if transferred is not None else phase_bytes
            stage(job,'copying',high,'Копирование системы.' if low<70 else 'Повторная сверка изменившихся файлов.',bytes_copied=baseline+amount)
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=15)

def static_files():
    result={}
    for directory in ('/etc/systemd/system','/opt','/usr/local','/var/lib/dpkg'):
        for path in P(directory).rglob('*'):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts:
                result[str(path).lstrip('/')]=digest(path)
    return result

def save_source_snapshot(mount, actual):
    """Capture identity and parameters ON USB so an unreadable eMMC is irrelevant."""
    from usb_recovery_adopt import own_identity
    own_identity(P('/'),actual)
    target=mount/str(SNAPSHOT).lstrip('/');target.mkdir(parents=True,mode=0o700,exist_ok=True);target.chmod(0o700)
    sources=[]
    for directory in ('/etc/bmi30-agent','/etc/ssl/bmi30',
                      '/etc/NetworkManager/system-connections','/home/techaid/.ssh','/root/.ssh'):
        if P(directory).is_dir():sources.extend(path for path in P(directory).rglob('*') if path.is_file() and not path.is_symlink())
    for name in ('/etc/hostname','/etc/machine-id','/etc/hosts','/etc/bmi30-network/config.json',
                 '/etc/bmi30/portal_config.json','/var/lib/bmi30-access/settings.json',
                 '/var/lib/bmi30-agent/bound_raspberry_serial','/var/lib/bmi30-agent/device_api_token',
                 '/var/lib/bmi30-network/credentials.json'):
        if P(name).is_file():sources.append(P(name))
    sources.extend(path for path in P('/etc/ssh').glob('ssh_host_*') if path.is_file())
    for pattern in ('*.json','*.npz','*.npy'):
        sources.extend(path for path in (DOCS/'host').glob(pattern) if path.is_file()
                       and not path.name.startswith(('bmi30_firmware','bmi30_split','bmi30_server')))
    saved={}
    for path in set(sources):
        if path.is_symlink():continue
        relative=str(path).lstrip('/');destination=target/relative
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
        os.chown(destination,path.stat().st_uid,path.stat().st_gid)
        saved[relative]=digest(destination)
    previous=read_json('/var/lib/bmi30-usb-adoption.json',{})
    write_json(target/'snapshot.json',{'format':1,'physical_serial':actual,
      'full_settings':previous.get('mode') not in ('network-only','factory-settings'),'files':saved})
    verified_snapshot(target,actual)

def install_selected_portal(mount):
    portal=DOCS/'host/bmi30_active_runtime/project/hotspot_info_server.py'
    if not (mount/str(portal).lstrip('/')).is_file():raise Refused('Отсутствует портал выбранного выпуска.')
    atomic(mount/'etc/systemd/system/bmi30-hotspot-info.service.d/06-bmi30-runtime.conf',
           ('[Service]\nExecStart=\nExecStart=/usr/bin/python3 '+str(portal)+'\n').encode(),0o644)
    shutil.copy2(mount/str(portal).lstrip('/'),mount/str(DOCS/'hotspot_info_server.py').lstrip('/'))

def copy_live_configuration(source,target):
    metadata=source.stat()
    atomic(target,source.read_bytes(),stat.S_IMODE(metadata.st_mode))
    os.chown(target,metadata.st_uid,metadata.st_gid)

def copy_native_login_access(mount,source_root=P('/')):
    # General USB images quarantine login data for early personalisation.
    # A native restore instead needs this board's already-personalised access.
    for name in ('home/techaid/.ssh','root/.ssh'):
        source=source_root/name;target=mount/name
        if not source.is_dir():continue
        if source.is_symlink() or target.is_symlink():raise Refused('Недопустимая ссылка каталога доступа.')
        target.mkdir(parents=True,exist_ok=True)
        command('rsync','-aHAX','--numeric-ids','--delete',str(source)+'/',str(target)+'/',timeout=30)
        metadata=source.stat();target.chmod(stat.S_IMODE(metadata.st_mode));os.chown(target,metadata.st_uid,metadata.st_gid)

def remove_donor_rollback_units(mount):
    # The new board has no donor transaction/backup. Never launch its guardian.
    system=mount/'etc/systemd/system'
    for folder in (system,system/'multi-user.target.wants'):
        for path in folder.glob('bmi30-legacy-guard-*.service'):
            if path.is_file() or path.is_symlink():path.unlink()


def remove_copied_update_transactions(mount):
    # Saved rollback archives are excluded from a system clone. Its source
    # transaction and guardian must not try to restore those missing archives.
    remove_donor_rollback_units(mount)
    for name in ('var/lib/bmi30-legacy-menu','home/techaid/Documents/.bmi30_cloud_sync/server'):
        path=mount/name
        if path.is_dir() and not path.is_symlink():shutil.rmtree(path)
    for name in ('host/bmi30_rollback.env','.bmi30_cloud_sync/rollback_state.env'):
        (mount/str(DOCS).lstrip('/')/name).unlink(missing_ok=True)

def prepare_image(mount, boot, cfg, root_partuuid, boot_partuuid, root_uuid, boot_uuid, hashes, wifi_seed=None):
    actual=physical_serial();save_source_snapshot(mount,actual)
    cmdline,fstab=rewrite_boot((boot/'cmdline.txt').read_text(),(mount/'etc/fstab').read_text(),root_partuuid,boot_partuuid)
    atomic(boot/'cmdline.txt',cmdline.encode(),0o644);atomic(mount/'etc/fstab',fstab.encode(),0o644)
    from usb_recovery_emmc import remove_usb_emmc_block
    atomic(boot/'config.txt',remove_usb_emmc_block((boot/'config.txt').read_text()).encode(),0o644)
    destination=mount/'opt/bmi30-usb-recovery';destination.mkdir(parents=True,exist_ok=True)
    for name in ('usb_recovery_adopt.py','usb_recovery_common.py','usb_recovery_emmc_settings.py','config.json'):
        shutil.copy2(MODULE/name,destination/name)
    # Boot setup can redeploy the old workspace portal over /usr/local/bin.
    # Select the verified release directly and keep the setup source in sync.
    identity_script=mount/'usr/local/sbin/bmi30-refresh-network-identity.sh'
    if identity_script.is_file():
        from patch_hotspot_identity import identity_script as keep_peer_profiles
        atomic(identity_script,keep_peer_profiles(identity_script.read_text()).encode(),identity_script.stat().st_mode&0o777)
    install_selected_portal(mount)
    unit='''[Unit]
Description=BMI30 adopt own identity before starting removable system
After=local-fs.target
Before=NetworkManager.service bmi30-core.service bmi30-agent.service bmi30-hotspot-info.service ssh.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/bmi30-usb-recovery/usb_recovery_adopt.py
RemainAfterExit=yes
TimeoutStartSec=180
[Install]
WantedBy=multi-user.target
'''
    atomic(mount/'etc/systemd/system/bmi30-usb-adopt.service',unit.encode(),0o644)
    for name in ('NetworkManager','bmi30-core','bmi30-agent','bmi30-hotspot-info','ssh','sshd',
                 'bmi30-channel-policy','bmi30-access','bmi30-communication-recovery','bmi30-hotspot','xrdp'):
        atomic(mount/('etc/systemd/system/'+name+'.service.d/05-bmi30-usb-adopt.conf'),
               b'[Unit]\nRequires=bmi30-usb-adopt.service\nAfter=bmi30-usb-adopt.service\n',0o644)
    link=mount/'etc/systemd/system/multi-user.target.wants/bmi30-usb-adopt.service'
    link.parent.mkdir(parents=True,exist_ok=True);link.unlink(missing_ok=True);link.symlink_to('../bmi30-usb-adopt.service')
    # Donor credentials must not be activated on a different physical CM5.
    for directory in ('etc/bmi30-agent','var/lib/bmi30-agent','var/lib/bmi30-legacy-menu',
                      'home/techaid/Documents/.bmi30_cloud_sync/server'):
        target=mount/directory
        if target.is_dir() and not target.is_symlink():shutil.rmtree(target)
    remove_donor_rollback_units(mount)
    for path in (mount/'etc/ssh').glob('ssh_host_*'):path.unlink()
    for path in (mount/'etc/ssl/bmi30').glob('*.key'):path.unlink()
    (mount/'etc/ssl/bmi30/portal.crt').unlink(missing_ok=True)
    # Seed before PID 1 starts; do not leave a transient machine-id mount.
    atomic(mount/'etc/machine-id',(uuid.uuid4().hex+'\n').encode(),0o444)
    dbus=mount/'var/lib/dbus/machine-id'
    if dbus.exists() or dbus.is_symlink():dbus.unlink()
    dbus.parent.mkdir(parents=True,exist_ok=True);dbus.symlink_to('/etc/machine-id')
    for name in ('var/lib/systemd/random-seed','var/lib/NetworkManager/secret_key'):
        (mount/name).unlink(missing_ok=True)
    for path in (mount/'etc/NetworkManager/system-connections').iterdir():
        if not path.is_file():continue
        if not path.name.endswith('.nmconnection'):
            path.unlink();continue
        text=path.read_text(errors='replace');portable=new_keyfile(text,'BMI30-EMERGENCY')
        if portable is None:path.unlink()
        else:atomic(path,portable.encode(),0o600)
    for name,text in (wifi_seed or {}).get('profiles',{}).items():
        if name not in ('bmi30-recovery-router.nmconnection','bmi30-recovery-via-source.nmconnection'):raise Refused('Cannot read the current Wi-Fi settings.')
        atomic(mount/'etc/NetworkManager/system-connections'/name,text.encode(),0o600)
    atomic(mount/'etc/hostname',b'BMI30-RECOVERY\n',0o644)
    manifest={'format':2,'mode':'portable-emergency','source_serial':actual,
       'version':cfg['version'],'root_partuuid':root_partuuid,
       'boot_partuuid':boot_partuuid,'root_uuid':root_uuid,'boot_uuid':boot_uuid,
       'verified_static_files':len(hashes),'verified_copy':True,'boot_tested':False,'emmc_controller':'read-own-settings-if-available',
       'wifi_seed':(wifi_seed or {}).get('summary',{})}
    write_json(mount/'etc/bmi30-usb-recovery-image.json',manifest)
    atomic(boot/'BMI30-RECOVERY.txt',('BMI30 portable emergency USB system\nCreated on: '+hostname(actual)+
       '\nVersion: '+cfg['version']+'\nOwn settings are read from eMMC only if available; USB continues if reading fails.\nVerified copy; actual USB boot not yet tested.\n').encode(),0o644)
    return manifest['wifi_seed']

def main(job):
    job=P(job).resolve()
    if job.parent!=STATE/'jobs' or not re.fullmatch('[0-9a-f]{32}',job.name):raise Refused('Неверное задание USB.')
    plan=read_json(job/'plan.json',{});cfg=config()
    if plan.get('authorized') is not True:raise Refused('Запись этого USB не подтверждена.')
    inv=inventory();disk=revalidate(plan,inv,cfg,mounted_ok=True)
    if b'raspberrypi,5-compute-module' not in P('/proc/device-tree/compatible').read_bytes():raise Refused('Нужна совместимая плата BMI30 на CM5.')
    kind=plan.get('kind','usb')
    mounts=[point for node,_ in flat([disk]) for point in (node.get('mountpoints') or []) if point]
    with contextlib.ExitStack() as stack:
        for name,lock_kind in ((STATE/'worker.lock','flock'),(DOCS/'.bmi30_cloud_sync/server_operation.lock','flock'),
                          (P('/var/lib/dpkg/lock-frontend'),'lockf'),(P('/var/lib/dpkg/lock'),'lockf')):
            name.parent.mkdir(parents=True,exist_ok=True)
            stream=stack.enter_context(name.open('a'))
            (fcntl.flock if lock_kind=='flock' else fcntl.lockf)(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        stage(job,'preparing',2,'Проверка выбранного USB и готовности системы.')
        require_copy_ready(inv,cfg)
        initial_wifi=None
        if kind=='usb':
            from usb_recovery_wifi_seed import capture_source_wifi
            initial_wifi=capture_source_wifi(hostname(inv['physical_serial']))
        for point in sorted(mounts,key=len,reverse=True):
            current=inventory();revalidate(plan,current,cfg,mounted_ok=True)
            command('umount',point)
        check_binding(plan,cfg)
        descriptor=os.open(plan['target_path'],os.O_RDWR|os.O_CLOEXEC)
        stack.callback(os.close,descriptor)
        if not stat.S_ISBLK(os.fstat(descriptor).st_mode):raise Refused('Цель не является блочным USB-устройством.')
        inv,disk=check_binding(plan,cfg)
        if os.fstat(descriptor).st_rdev!=os.stat(plan['target_path']).st_rdev:raise Refused('USB изменился.')
        disk_id=uuid.uuid4().hex[:8]
        while disk_id=='00000000' or any(str(node.get('partuuid','')).startswith(disk_id) for node,_ in flat(inv['blockdevices'])):
            disk_id=uuid.uuid4().hex[:8]
        stage(job,'formatting',5,'Создание загрузочных разделов на выбранном USB.')
        table='label: dos\nlabel-id: 0x'+disk_id+'\n\nstart=1MiB,size=512MiB,type=c,bootable\nstart=513MiB,type=83\n'
        command('sfdisk','--lock=yes','--wipe','always','/proc/self/fd/'+str(descriptor),
                input=table,pass_fds=(descriptor,),timeout=45)
        command('udevadm','settle',timeout=30)
        prefix=plan['target_path']+('p' if kind=='emmc-restore' else '')
        partitions=[prefix+'1',prefix+'2']
        for index,path in enumerate(partitions):
            for _ in range(30):
                if P(path).exists():break
                time.sleep(.2)
            check_binding(plan,cfg)
            ident=command('blkid','-s','PARTUUID','-o','value',path)
            if ident.lower()!=disk_id+'-0'+str(index+1):raise Refused('Разделы нового USB не подтверждены.')
            fd=os.open(path,os.O_RDWR|os.O_CLOEXEC);stack.callback(os.close,fd)
            check_binding(plan,cfg)
            if index==0:command('mkfs.vfat','-F','32','-n','BMI30BOOT','/proc/self/fd/'+str(fd),pass_fds=(fd,),timeout=90)
            else:command('mkfs.ext4','-F','-m','1','-L','BMI30ROOT','/proc/self/fd/'+str(fd),pass_fds=(fd,),timeout=180)
        root_uuid=command('blkid','-s','UUID','-o','value',partitions[1]);boot_uuid=command('blkid','-s','UUID','-o','value',partitions[0])
        mount=job/'mount';mount.mkdir();boot=mount/'boot/firmware'
        check_binding(plan,cfg)
        command('mount','-U',root_uuid,str(mount));stack.callback(cleanup_unmount,mount)
        boot.mkdir(parents=True,exist_ok=True)
        command('mount','-U',boot_uuid,str(boot));stack.callback(cleanup_unmount,boot)
        stage(job,'copying',12,'Копирование системы. Прибор и веб‑страница продолжают работать.')
        copy_root(job,mount,12,70)
        stage(job,'copying',72,'Повторная сверка изменившихся файлов.')
        copy_root(job,mount,72,82)
        prepare_system_directories(mount)
        boot_stats=command('rsync','-rt','--delete','--modify-window=1','--stats','/boot/firmware/',str(boot)+'/',timeout=300)
        amount=re.search(r'Total transferred file size:\s*([\d,]+)',boot_stats)
        if amount:
            stage(job,'copying',83,'Копирование загрузочных файлов.',bytes_copied=int(read_json(job/'status.json',{}).get('bytes_copied',0))+int(amount[1].replace(',','')))
        stage(job,'verifying',84,'Проверка системных файлов и загрузки.')
        hashes=static_files()
        for name,expected in hashes.items():
            target=mount/name
            if target.is_symlink() or not target.is_file() or digest(target)!=expected:
                raise Refused('Системные файлы изменились во время копирования. Повторите запись.')
        # Capture atomic configuration files without stopping the detector or portal.
        for source in list((DOCS/'host').glob('*.json'))+list((DOCS/'host').glob('*.npz'))+list((DOCS/'host').glob('*.npy')):
            if source.is_file():copy_live_configuration(source,mount/str(source).lstrip('/'))
        source_boot={str(path.relative_to('/boot/firmware')):digest(path) for path in P('/boot/firmware').rglob('*') if path.is_file() and not path.is_symlink()}
        if any(digest(boot/name)!=value for name,value in source_boot.items()):raise Refused('Проверка boot-раздела не пройдена.')
        wifi_info={}
        if kind=='emmc-restore':
            from usb_recovery_emmc import prepare_native_image
            copy_native_login_access(mount)
            prepare_native_image(mount,boot,disk_id+'-02',disk_id+'-01')
        else:
            wifi_seed=capture_source_wifi(hostname(inv['physical_serial']))
            if initial_wifi['summary']['active_wifi_saved'] and not wifi_seed['summary']['active_wifi_saved']:
                raise Refused('The source Wi-Fi connection changed during copying.')
            wifi_info=prepare_image(mount,boot,cfg,disk_id+'-02',disk_id+'-01',root_uuid,boot_uuid,hashes,wifi_seed=wifi_seed)
        finish_filesystems(job,boot,mount,stage)
        stage(job,'complete',100,'eMMC записана и проверена. Для загрузки выключите питание и извлеките USB.' if kind=='emmc-restore' else 'Аварийная USB-система готова. Копирование и проверка завершены.',
              kind=kind,safe_to_remove=kind!='emmc-restore',verified_copy=True,verified_static_files=len(hashes),
              purpose='portable-emergency',root_partuuid=disk_id+'-02',wifi_seed=wifi_info)

if __name__=='__main__':
    directory=P(sys.argv[1])
    try:main(directory)
    except Exception as error:
        message=str(error) if isinstance(error,Refused) else 'Запись не завершена ('+type(error).__name__+'). Повторите после проверки USB.'
        previous=read_json(directory/'status.json',{})
        details={'error_type':type(error).__name__,**{k:previous[k] for k in ('finishing_step','step_elapsed_s','step_timeout_s') if k in previous}}
        if isinstance(error,FinishTimeout):details.update(finishing_step=error.operation,step_timeout_s=error.timeout_s)
        stage(directory,'failed',0,message,safe_to_remove=False,**details)
        raise SystemExit(1)
