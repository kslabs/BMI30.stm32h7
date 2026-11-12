"""Portable emergency USB primitives; importing this module never writes media."""
import configparser, hashlib, io, json, os, pathlib, re, subprocess, time, uuid
P = pathlib.Path
DOCS = P('/home/techaid/Documents')
STATE = P('/var/lib/bmi30-usb-recovery')
MANIFEST = P('/etc/bmi30-usb-recovery-image.json')
SNAPSHOT = P('/var/lib/bmi30-usb-source-snapshot')
MODULE = P(__file__).resolve().parent
ACTIVE_STAGES = {'queued', 'preparing', 'formatting', 'copying', 'verifying', 'finishing'}

class Refused(RuntimeError):
    pass

def command(*args, timeout=30, input=None, pass_fds=()):
    r = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout,
                       pass_fds=pass_fds, env={**os.environ, 'LC_ALL':'C'})
    if r.returncode:
        raise Refused('Не выполнено: '+P(args[0]).name+' ('+str(r.returncode)+').')
    return r.stdout.strip()

def atomic(path, data, mode=0o600):
    path=P(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex)
    with temporary.open('wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    temporary.chmod(mode);temporary.replace(path)

def write_json(path, value):
    atomic(path,(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))

def read_json(path, fallback=None):
    try:return json.loads(P(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return fallback

def serial(value):
    value=str(value).strip('\0 \t\r\n').lower()
    if not re.fullmatch('[0-9a-f]{16}',value) or not int(value,16):raise Refused('Не определён физический serial CM5.')
    return value

def physical_serial():return serial(P('/proc/device-tree/serial-number').read_text())
def hostname(value):return 'BMI30-'+serial(value)[-9:].upper()
def config():return read_json(MODULE/'config.json',{})
def digest(path):return hashlib.sha256(P(path).read_bytes()).hexdigest()

def flat(nodes, parent=None):
    out=[]
    for node in nodes:
        out.append((node,parent));out.extend(flat(node.get('children',[]),node))
    return out

def inventory():
    nodes=json.loads(command('lsblk','-b','-J','-p','-o',
       'NAME,PATH,TYPE,TRAN,SIZE,MODEL,SERIAL,RO,RM,MAJ:MIN,MOUNTPOINTS'))['blockdevices']
    for node,_ in flat(nodes):
        if node.get('type')=='disk':
            try:node['diskseq']=(P('/sys/class/block')/P(node['path']).name/'diskseq').read_text().strip()
            except OSError:node['diskseq']=None
    root=command('findmnt','-n','-o','SOURCE','/');boot=command('findmnt','-n','-o','SOURCE','/boot/firmware')
    # Only inspect filesystem identifiers of mounted, active USB/root filesystems.
    # Never probe the contents of an inactive (possibly broken) eMMC.
    for mount,path in (('/',root),('/boot/firmware',boot)):
        for node,_ in flat(nodes):
            if node['path']==path:
                node['partuuid']=command('findmnt','-n','-o','PARTUUID',mount)
                node['uuid']=command('findmnt','-n','-o','UUID',mount)
    return {'physical_serial':physical_serial(),'boot_id':P('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'root_source':root,'boot_source':boot,'blockdevices':nodes}

def parent_disk(inv, path):
    by_path={node['path']:(node,parent) for node,parent in flat(inv['blockdevices'])}
    if path not in by_path:raise Refused('Не удалось определить активный системный носитель.')
    node,parent=by_path[path]
    while parent is not None:node,parent=by_path[parent['path']]
    return node['path']


def copy_direction(inv):
    """Choose one direction from the mounted root and boot, never an image marker."""
    root=parent_disk(inv,inv['root_source']);boot=parent_disk(inv,inv['boot_source'])
    if root!=boot:raise Refused('Root and boot must be on the same system drive.')
    disks={n['path']:n for n,_ in flat(inv['blockdevices']) if n.get('type')=='disk'}
    disk=disks.get(root)
    if disk and root=='/dev/mmcblk0' and disk.get('tran')!='usb':return 'usb'
    if disk and re.fullmatch('/dev/sd[a-z]+',root) and disk.get('tran')=='usb':return 'emmc-restore'
    raise Refused('The current boot drive does not support system copying.')

def binding(inv, disk):
    if not disk.get('diskseq'):raise Refused('У USB нет идентификатора текущего подключения.')
    fields={key:disk.get(key) for key in ('path','maj:min','model','serial','size','diskseq')}
    fields['boot_id']=inv['boot_id']
    return hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()

def require_copy_ready(inv,cfg):
    """Do not clone a release while its separate system transaction is pending."""
    manifest_path=MODULE.parent/'bmi30_full_update/manifest.json'
    if not manifest_path.exists():return
    manifest=read_json(manifest_path,{})
    version=cfg.get('version')
    if version and manifest.get('bundle_id')==version:
        portable=read_json('/var/lib/bmi30-portable-runtime.json',{})
        if (portable.get('stage')=='adopted' and portable.get('bundle_id')==version
            and portable.get('physical_serial')==serial(inv['physical_serial'])):return
        record=read_json(P('/var/lib/bmi30-legacy-menu')/version/'state.json',{})
        if (record.get('stage')=='committed' and record.get('bundle_id')==version
            and record.get('kind')=='full-system'
            and record.get('expected_id')==hostname(inv['physical_serial'])):return
    raise Refused('Дождитесь завершения установки системных служб перед копированием.')


def validate(inv, target, cfg, required_bytes=0, mounted_ok=False, kind='usb'):
    require_copy_ready(inv,cfg)
    serial(inv['physical_serial'])
    if cfg.get('mode')!='portable-emergency':raise Refused('Нужна текущая версия функции аварийной USB-системы.')
    direction=copy_direction(inv)
    if kind!=direction:
        if direction=='emmc-restore':raise Refused('When booted from USB, only USB → eMMC is allowed.')
        raise Refused('When booted from eMMC, only eMMC → USB is allowed.')
    protected={parent_disk(inv,inv['root_source']),parent_disk(inv,inv['boot_source'])}
    disks={n['path']:n for n,_ in flat(inv['blockdevices']) if n.get('type')=='disk'}
    if target not in disks:raise Refused('Выберите подключённый USB-носитель целиком.')
    disk=disks[target]
    if target in protected:raise Refused('Активный системный диск нельзя перезаписывать.')
    if kind=='emmc-restore':
        source=parent_disk(inv,inv['root_source']);boot=parent_disk(inv,inv['boot_source'])
        if source!=boot or disks[source].get('tran')!='usb':raise Refused('Копия на eMMC разрешена только после загрузки целиком с USB.')
        if target!='/dev/mmcblk0':raise Refused('Для восстановления разрешена только внутренняя eMMC.')
        mounted_ok=False
    elif kind!='usb' or disk.get('tran')!='usb' or not re.fullmatch('/dev/sd[a-z]+',target):
        raise Refused('Системный или неподходящий носитель нельзя использовать для записи.')
    if int(disk.get('size',0))<int(required_bytes):raise Refused('На USB недостаточно места для системы.')
    for node,_ in flat([disk]):
        if node.get('ro'):raise Refused('Носитель защищён от записи.')
        for mount in node.get('mountpoints') or []:
            if mount and (not mounted_ok or not str(mount).startswith('/media/')):
                raise Refused('USB занят или смонтирован системой. Освободите носитель.')
    binding(inv,disk)
    return disk

def make_plan(inv, target, cfg, required_bytes, now=None, kind='usb'):
    disk=validate(inv,target,cfg,required_bytes,mounted_ok=True,kind=kind)
    return {'id':uuid.uuid4().hex,'target_path':target,'binding':binding(inv,disk),
            'source_serial':inv['physical_serial'],'source_boot_id':inv['boot_id'],
            'source_firmware_version':cfg.get('version'),
            'root_source':inv['root_source'],'boot_source':inv['boot_source'],
            'created_mono':time.monotonic() if now is None else now,'required_bytes':int(required_bytes),
            'model':str(disk.get('model') or ('eMMC' if kind=='emmc-restore' else 'USB')).strip(),'serial':disk.get('serial') or '',
            'size':int(disk['size']),'kind':kind,'purpose':'portable-emergency','authorized':False}

def revalidate(plan, inv, cfg, now=None, mounted_ok=False):
    now=time.monotonic() if now is None else now
    if not 0<=now-plan['created_mono']<=300:raise Refused('Выбор USB устарел. Выберите носитель заново.')
    disk=validate(inv,plan['target_path'],cfg,plan['required_bytes'],mounted_ok=mounted_ok,kind=plan.get('kind','usb'))
    if binding(inv,disk)!=plan['binding'] or inv['boot_id']!=plan['source_boot_id']:
        raise Refused('USB переподключён или плата перезагружена. Выберите носитель заново.')
    if any(plan.get(key)!=inv[key] for key in ('root_source','boot_source')):raise Refused('Источник системы изменился.')
    if plan.get('source_firmware_version')!=cfg.get('version'):raise Refused('Источник системы изменился.')
    if plan['source_serial']!=serial(inv['physical_serial']):raise Refused('План создан на другой плате.')
    return disk

def verified_snapshot(snapshot, actual):
    """Own backup may replace a failed eMMC; a donor backup never supplies identity."""
    record=read_json(P(snapshot)/'snapshot.json',{})
    if record.get('format')!=1 or record.get('physical_serial')!=serial(actual):
        raise Refused('Сохранённые личные данные принадлежат другому прибору.')
    hashes=record.get('files')
    if not isinstance(hashes,dict) or not hashes:raise Refused('Резервная копия личных данных пуста.')
    for name,expected in hashes.items():
        relative=P(name)
        if relative.is_absolute() or '..' in relative.parts:raise Refused('Неверный путь резервной копии.')
        path=P(snapshot)/relative
        if path.is_symlink() or not path.is_file() or digest(path)!=expected:
            raise Refused('Резервная копия личных данных повреждена.')
    return P(snapshot)

def new_keyfile(text, device_id, *, access_point=False, cidr=None):
    """Produce a portable profile without the donor's hardware or IP binding."""
    parser=configparser.ConfigParser(interpolation=None,strict=False)
    parser.optionxform=str;parser.read_string(text)
    if not parser.has_section('connection') or not parser.has_section('wifi'):return None
    wifi=parser['wifi'];connection=parser['connection']
    ap=wifi.get('mode','infrastructure')=='ap'
    if ap!=access_point:return None
    if not ap and str(wifi.get('ssid','')).upper().startswith('BMI30-'):return None
    connection['uuid']=str(uuid.uuid4());connection['id']='recovery-'+('hotspot-'+device_id if ap else connection.get('id','wifi'))
    connection['interface-name']='wlan0ap' if ap else 'wlan0';connection['autoconnect']='true'
    for key in ('permissions','master','slave-type','stable-id'):
        connection.pop(key,None)
    for key in ('mac-address','cloned-mac-address','bssid','mac-address-blacklist'):
        wifi.pop(key,None)
    if ap:
        wifi['ssid']=device_id;wifi['mode']='ap'
        wifi['band']='bg';wifi['channel']='6'
        parser['ipv4']={'method':'shared','address1':cidr}
    else:
        for key in ('band','channel'):wifi.pop(key,None)
        parser['ipv4']={'method':'auto'}
    parser['ipv6']={'method':'disabled' if ap else 'auto'}
    output=io.StringIO();parser.write(output,space_around_delimiters=False)
    return output.getvalue()

def rewrite_boot(cmdline, fstab, root_partuuid, boot_partuuid):
    cmdline,count=re.subn(r'(?<!\S)root=\S+','root=PARTUUID='+root_partuuid,cmdline)
    if count!=1:raise Refused('Неоднозначная ссылка на корневую систему в cmdline.txt.')
    lines=[];found=set()
    for line in fstab.splitlines():
        fields=line.split()
        if fields and not line.lstrip().startswith('#') and len(fields)>1 and fields[1] in ('/','/boot/firmware'):
            found.add(fields[1]);fields[0]='PARTUUID='+(root_partuuid if fields[1]=='/' else boot_partuuid)
            line='\t'.join(fields)
        elif fields and not line.lstrip().startswith('#') and len(fields)>2:
            if fields[2] not in ('proc','sysfs','tmpfs','devtmpfs','devpts','cgroup2'):
                line='# Disabled on emergency USB: '+line
        lines.append(line)
    if found!={'/','/boot/firmware'}:raise Refused('Не определены корневой и boot-разделы в fstab.')
    return cmdline,'\n'.join(lines)+'\n'
