"""Try own settings from inactive eMMC; an I/O failure must not block USB boot."""
import contextlib,os,signal,shutil,subprocess,sys,time,uuid
from usb_recovery_common import *
CACHE_ROOT=P('/var/lib/bmi30-usb-emmc-settings')
INTERNAL_ROOT=P('/dev/mmcblk0p2')

def capture(root,target,actual):
    from usb_recovery_adopt import own_identity
    own_identity(root,actual)
    if not isinstance(read_json(root/'home/techaid/Documents/host/bmi30_config.json'),dict):
        raise Refused('Собственные настройки недоступны.')
    sources=[]
    for name in ('etc/bmi30-agent','var/lib/bmi30-agent','etc/ssl/bmi30',
                 'etc/NetworkManager/system-connections','home/techaid/.ssh','root/.ssh'):
        folder=root/name
        if folder.is_dir() and not folder.is_symlink():sources.extend(p for p in folder.rglob('*') if p.is_file() and not p.is_symlink())
    for name in ('etc/hostname','etc/machine-id','etc/hosts','etc/bmi30-network/config.json',
                 'etc/bmi30/portal_config.json','var/lib/bmi30-access/settings.json','var/lib/bmi30-network/credentials.json'):
        if (root/name).is_file():sources.append(root/name)
    sources.extend(p for p in (root/'etc/ssh').glob('ssh_host_*') if p.is_file())
    host=root/'home/techaid/Documents/host'
    for pattern in ('*.json','*.npz','*.npy'):
        sources.extend(p for p in host.glob(pattern) if p.is_file() and not p.name.startswith(('bmi30_firmware','bmi30_split','bmi30_server')))
    target.mkdir(parents=True,mode=0o700,exist_ok=True);target.chmod(0o700)
    hashes={};size=0
    for source in set(sources):
        if source.is_symlink():continue
        size+=source.stat().st_size
        if size>64*1024**2:raise Refused('Слишком большой набор настроек.')
        relative=source.relative_to(root);destination=target/relative
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
        os.chown(destination,source.stat().st_uid,source.stat().st_gid)
        hashes[relative.as_posix()]=digest(destination)
    write_json(target/'snapshot.json',{'format':1,'physical_serial':actual,'full_settings':True,'files':hashes})
    verified_snapshot(target,actual)

def try_settings(actual,inv,timeout=8):
    if not INTERNAL_ROOT.exists():return None
    if any(parent_disk(inv,inv[name])=='/dev/mmcblk0' for name in ('root_source','boot_source')):return None
    base=CACHE_ROOT/uuid.uuid4().hex
    base.mkdir(parents=True,mode=0o700)
    process=subprocess.Popen(['/usr/bin/python3',str(P(__file__).resolve()),actual,str(base)],
         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    deadline=time.monotonic()+timeout
    while process.poll() is None and time.monotonic()<deadline:time.sleep(.1)
    if process.poll() is None:
        # Do not wait for a process trapped in failed block-device I/O.
        with contextlib.suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGKILL)
        subprocess.Popen(['umount','-l',str(base/'mount')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return None
    if process.returncode or not read_json(base/'result.json',{}).get('ready'):return None
    return verified_snapshot(base/'snapshot',actual)

def worker(actual,base):
    actual=serial(actual);base=P(base)
    if base.parent!=P('/var/lib/bmi30-usb-emmc-settings') or not re.fullmatch('[0-9a-f]{32}',base.name):raise Refused('Неверная папка настроек.')
    if physical_serial()!=actual:raise Refused('Другая плата.')
    if not command('findmnt','-n','-o','SOURCE','/').startswith('/dev/sd'):raise Refused('Нужна USB-загрузка.')
    mount=base/'mount';mount.mkdir()
    try:
        command('mount','-o','ro,noload','/dev/mmcblk0p2',str(mount),timeout=5)
        capture(mount,base/'snapshot',actual)
    finally:
        subprocess.run(['umount',str(mount)],capture_output=True,timeout=3)
    write_json(base/'result.json',{'ready':True})

if __name__=='__main__':worker(sys.argv[1],sys.argv[2])
