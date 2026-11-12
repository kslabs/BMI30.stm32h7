"""Plan/apply authorized S10 disk cleanup after the USB-to-eMMC copy.

Keep settings, active software, recordings, and backups dated 2026-10-01 onward.
Never reboots or stops the desktop. Run as root with clone plan and plan/apply.
"""
import datetime,json,os,re,shutil,stat,subprocess,sys,time
from pathlib import Path

PLAN=json.loads(Path(sys.argv[1]).read_text());MODE=sys.argv[2]
assert MODE in ('plan','apply') and PLAN['host']=='BMI30-D5EB8F26C'
CUTOFF=datetime.date(2026,10,1)
CUTOFF_EPOCH=datetime.datetime(2026,10,1).timestamp()
MOUNT=Path('/mnt/bmi30-s10-cleanup')
BACKUP=Path('/var/backups/bmi30-emmc-sync')/PLAN['stamp']
DATED_ROOTS=('home/techaid/Documents/host/bmi30_split_bundles',
             'home/techaid/Documents/backups','home/techaid/.codex/maintenance-backups')
CACHES=('home/techaid/.cache/pip','home/techaid/.cache/mozilla/firefox',
        'home/techaid/.config/Code/CachedExtensionVSIXs','home/techaid/.config/Code/CachedData',
        'home/techaid/.config/Code/Cache','home/techaid/.config/Code/Code Cache',
        'home/techaid/.config/Code/GPUCache')

def run(*args):
    return subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()

def dates(text):
    result=[]
    for year,month,day in re.findall(r'(20\d{2})-?(\d{2})-?(\d{2})',text):
        try:result.append(datetime.date(int(year),int(month),int(day)))
        except ValueError:pass
    return result

def free(base):
    fs=os.statvfs(base);return fs.f_bavail*fs.f_frsize

def opened_paths():
    result=set()
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            for fd in (proc/'fd').iterdir():
                try:result.add(Path(os.readlink(fd).removesuffix(' (deleted)')))
                except OSError:pass
            for name in ('cwd','exe'):
                try:result.add(Path(os.readlink(proc/name)))
                except OSError:pass
        except OSError:pass
    return result

def recent_files(path):
    if path.is_file():return path.stat().st_mtime>=CUTOFF_EPOCH
    for parent,dirs,files in os.walk(path,followlinks=False):
        for name in files:
            item=Path(parent)/name
            if item.is_symlink():continue
            if item.stat().st_mtime>=CUTOFF_EPOCH:return True
    return False

def candidates(base):
    items=[]
    for relative in DATED_ROOTS:
        root=base/relative
        if not root.exists():continue
        for item in root.iterdir():
            if item.is_symlink():continue
            stamps=dates(item.name)
            if stamps and max(stamps)<CUTOFF and not recent_files(item):
                items.append((item,'old_snapshot'))
            elif item.name=='server_releases' and item.is_dir():
                for child in item.iterdir():
                    stamps=dates(child.name)
                    if not child.is_symlink() and stamps and max(stamps)<CUTOFF and not recent_files(child):
                        items.append((child,'old_snapshot'))
    for relative in CACHES:
        root=base/relative
        if root.is_dir() and not root.is_symlink():
            items.extend((child,'generated_cache') for child in root.iterdir() if not child.is_symlink())
    archive=base/'var/cache/apt/archives'
    if archive.is_dir():items.extend((p,'downloaded_package_cache') for p in archive.glob('*.deb') if p.is_file() and not p.is_symlink())
    return items

def guard(path,kind,base,active,opened):
    absolute=path.absolute();resolved=path.resolve()
    assert not path.is_symlink() and resolved==absolute,'Resolved cleanup path differs'
    relative=str(path.relative_to(base))
    allowed=(any(relative.startswith(root+'/') for root in DATED_ROOTS) if kind=='old_snapshot'
             else any(relative.startswith(root+'/') for root in CACHES) if kind=='generated_cache'
             else relative.startswith('var/cache/apt/archives/') and path.suffix=='.deb')
    assert allowed and '..' not in Path(relative).parts
    if kind=='old_snapshot':
        stamps=dates(path.name);assert stamps and max(stamps)<CUTOFF and not recent_files(path)
    for protected in active:
        assert protected!=resolved and not protected.is_relative_to(resolved),'Active software protected'
    return not any(p==resolved or p.is_relative_to(resolved) for p in opened)

def main():
    assert run('hostname')==PLAN['host'] and run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2'
    assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==PLAN['serial']
    assert Path('/sys/block/mmcblk0/device/cid').read_text().strip()==PLAN['cid']
    state=Path('/run/bmi30-emmc-copy.json')
    if state.exists():
        copy=json.loads(state.read_text())
        assert copy['stage'] in ('complete','failed') and copy['backup']==str(BACKUP)
    else:
        assert PLAN.get('resume_existing_backup') is True
    assert (BACKUP/'previous-root.tar.gz').stat().st_size==481122821
    assert (BACKUP/'previous-boot.tar.gz').stat().st_size==69820028
    for service in ('bmi30-emmc-copy','bmi30-emmc-audit'):
        assert subprocess.run(['systemctl','is-active','--quiet',service]).returncode!=0,service+' still running'
    assert subprocess.run(['findmnt','-rn','-S','/dev/mmcblk0p2'],stdout=subprocess.DEVNULL).returncode!=0
    env=Path('/home/techaid/Documents/host/bmi30_split_active_version.env').read_text()
    values=dict(line.split('=',1) for line in env.splitlines() if line and not line.startswith('#'))
    assert values['BMI30_SPLIT_VERSION']==PLAN['version']
    active=[Path('/home/techaid/Documents/host/bmi30_active_runtime').resolve()]
    active.extend(Path(v).resolve() for v in values.values() if v.startswith('/'))
    opened=opened_paths();MOUNT.mkdir(exist_ok=False)
    try:
        run('mount',*(['-o','ro,noload'] if MODE=='plan' else []),'/dev/mmcblk0p2',str(MOUNT))
        assert run('findmnt','-n','-o','SOURCE','-T',str(MOUNT))=='/dev/mmcblk0p2'
        assert (MOUNT/'etc/hostname').read_text().strip()==PLAN['host']
        results={}
        for label,base in (('usb',Path('/')),('emmc',MOUNT)):
            before=free(base);records=[];skipped=[]
            target_active=[base/str(p).lstrip('/') for p in active]
            for path,kind in candidates(base):
                if not guard(path,kind,base,target_active,opened if label=='usb' else set()):
                    skipped.append(str(path.relative_to(base)));continue
                size=int(run('du','-x','-B1','-s',str(path)).split()[0])
                records.append({'path':str(path.relative_to(base)),'kind':kind,'allocated_bytes':size})
                if MODE=='apply':
                    if path.is_dir():shutil.rmtree(path)
                    else:path.unlink()
            if MODE=='apply':run('sync','-f',str(base))
            results[label]={'items':len(records),'estimated_bytes':sum(r['allocated_bytes'] for r in records),
                            'free_before':before,'free_after':free(base),'freed_bytes':free(base)-before,
                            'skipped_open_paths':skipped,'records':records}
        report={'mode':MODE,'cutoff':'before 2026-10-01','results':results,'active_version':PLAN['version'],
                'backups_from_october_preserved':True,'time':time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        (BACKUP/('disk-cleanup-'+MODE+'.json')).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({**report,'results':{k:{a:b for a,b in v.items() if a!='records'} for k,v in results.items()}}))
    finally:
        run('umount',str(MOUNT));MOUNT.rmdir()

if __name__=='__main__':main()
