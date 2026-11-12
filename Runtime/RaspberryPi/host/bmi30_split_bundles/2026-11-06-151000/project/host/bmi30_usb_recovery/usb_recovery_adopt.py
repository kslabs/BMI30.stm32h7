"""USB-only boot personalization. Never mount, scan, or read inactive eMMC."""
import configparser, hashlib, io, ipaddress, os, shutil, subprocess, uuid
from usb_recovery_common import *
LIVE=P('/')
def live(name):return LIVE/str(name).lstrip('/')

def own_identity(root, actual):
    bound=root/'var/lib/bmi30-agent/bound_raspberry_serial'
    if bound.is_file() and not bound.is_symlink():
        value=bound.read_text().strip().lower().removeprefix('bmi30-')
        if value!=actual:raise Refused('Идентичность Agent принадлежит другой плате.')
        return True
    name=(root/'etc/hostname').read_text().strip().upper()
    if name not in (hostname(actual),'BMI30-'+actual.upper()):raise Refused('USB-копия не подтвердила собственную идентичность.')
    return False

def copy_file(source, target):
    if source.is_symlink():raise Refused('Неожиданная ссылка в сохранённых настройках.')
    atomic(target,source.read_bytes(),source.stat().st_mode&0o777)
    os.chown(target,source.stat().st_uid,source.stat().st_gid)

def user_ownership(relative, *, directory=False):
    path=live(relative)
    account='techaid' if str(relative).lstrip('/').startswith('home/techaid/') else 'root'
    passwd=live('/etc/passwd')
    records=passwd.read_text().splitlines() if passwd.is_file() else ['root:x:0:0::/:/bin/sh','techaid:x:1000:1000::/home/techaid:/bin/sh']
    record=next(line.split(':') for line in records if line.split(':')[0]==account)
    os.chown(path,int(record[2]),int(record[3]));path.chmod(0o700 if directory else 0o600)

def mask(name):
    path=live('/etc/systemd/system')/(name+'.service');backup=live('/var/lib/bmi30-usb-disabled-units')/(name+'.service')
    if path.is_file() and not path.is_symlink() and not backup.exists():
        backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,backup)
    path.parent.mkdir(parents=True,exist_ok=True);path.unlink(missing_ok=True);path.symlink_to('/dev/null')

def unmask(name):
    path=live('/etc/systemd/system')/(name+'.service');backup=live('/var/lib/bmi30-usb-disabled-units')/(name+'.service')
    if path.is_symlink() and os.readlink(path)=='/dev/null' and backup.is_file():path.unlink();copy_file(backup,path)

def own_hotspot(root, actual, address):
    accepted={hostname(actual),'BMI30-'+actual.upper()};candidates=[]
    for source in (root/'etc/NetworkManager/system-connections').glob('*.nmconnection'):
        if not source.is_file() or source.is_symlink():continue
        try:
            text=source.read_text();parser=configparser.ConfigParser(interpolation=None,strict=False);parser.read_string(text)
            name=parser.get('wifi','ssid',fallback='').upper()
            if name not in accepted or parser.get('wifi','mode',fallback='')!='ap':continue
            candidate=new_keyfile(text,hostname(actual),access_point=True,cidr=address)
            if candidate:candidates.append((name==hostname(actual),source.stat().st_mtime_ns,candidate))
        except (ValueError,configparser.Error,UnicodeError):continue
    if not candidates:raise Refused('Не найден сохранённый профиль хотспота.')
    return sorted(candidates,key=lambda item:item[:2],reverse=True)[0][2]

def configure_host(root, actual, *, own):
    for path in live('/etc/ssh').glob('ssh_host_*'):path.unlink()
    if own:
        for source in (root/'etc/ssh').glob('ssh_host_*'):
            if source.is_file():copy_file(source,live('/etc/ssh')/source.name)
    for relative in ('home/techaid/.ssh','root/.ssh'):
        source=root/relative;target=live(relative)
        if target.is_dir():shutil.rmtree(target)
        if own and source.is_dir():shutil.copytree(source,target,symlinks=False)
        elif (source/'authorized_keys').is_file():copy_file(source/'authorized_keys',target/'authorized_keys')
        if target.is_dir():
            user_ownership(relative,directory=True)
            for path in target.rglob('*'):
                if path.is_file() and not path.is_symlink():user_ownership(path.relative_to(LIVE))
    command('ssh-keygen','-A',timeout=30)
    tls=live('/etc/ssl/bmi30');tls.mkdir(parents=True,exist_ok=True)
    if not own:
        for path in tls.glob('*.key'):path.unlink()
        (tls/'portal.crt').unlink(missing_ok=True)
    if own and (root/'etc/ssl/bmi30').is_dir():shutil.copytree(root/'etc/ssl/bmi30',tls,dirs_exist_ok=True)
    if not (tls/'portal.key').is_file() or not (tls/'portal.crt').is_file():
        command('openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','365','-subj','/CN='+hostname(actual),
                '-keyout',str(tls/'portal.key'),'-out',str(tls/'portal.crt'),timeout=35)
        (tls/'portal.key').chmod(0o600)
    machine=live('/etc/machine-id');value=machine.read_text().strip() if machine.is_file() else ''
    # Preserve the ID already used by PID 1. Each image is seeded before boot.
    if not re.fullmatch('[0-9a-f]{32}',value):
        atomic(machine,(uuid.uuid4().hex+'\n').encode(),0o444)
    dbus=live('/var/lib/dbus/machine-id')
    if dbus.exists() or dbus.is_symlink():dbus.unlink()
    dbus.parent.mkdir(parents=True,exist_ok=True);dbus.symlink_to('/etc/machine-id')
    atomic(live('/etc/hostname'),(hostname(actual)+'\n').encode(),0o644);command('hostname',hostname(actual))
    hosts=live('/etc/hosts');text=hosts.read_text() if hosts.is_file() else '127.0.0.1 localhost\n127.0.1.1 BMI30-RECOVERY\n'
    text=re.sub(r'^127\.0\.1\.1\s+.*$', '127.0.1.1\t'+hostname(actual),text,flags=re.M);atomic(hosts,text.encode(),0o644)

def configure_identity(root, actual):
    agent_verified=own_identity(root,actual);own_host=root/'home/techaid/Documents/host'
    if not isinstance(read_json(own_host/'bmi30_config.json'),dict):raise Refused('Сохранённые настройки не читаются.')
    copied=0
    for pattern in ('*.json','*.npz','*.npy'):
        for source in own_host.glob(pattern):
            if not source.is_file() or source.name.startswith(('bmi30_firmware','bmi30_split','bmi30_server')):continue
            target=DOCS/'host'/source.name
            copy_file(source,live(target))
            # Old failed restores may leave these user-service files root:0600.
            # Keep the verified contents, but give the runtime account access.
            user_ownership(target);copied+=1
    for relative in ('etc/bmi30/portal_config.json','var/lib/bmi30-access/settings.json'):
        if (root/relative).is_file():copy_file(root/relative,live(relative))
    for directory in ('etc/bmi30-agent','var/lib/bmi30-agent'):
        target=live(directory)
        if target.is_dir():shutil.rmtree(target)
        if agent_verified and (root/directory).is_dir():shutil.copytree(root/directory,target,symlinks=False)
    unmask('bmi30-core')
    if agent_verified:unmask('bmi30-agent')
    else:mask('bmi30-agent')
    configure_host(root,actual,own=True)
    return {'own_settings_files':copied,'agent_identity_imported':agent_verified}


def restore_own_wifi(root,profiles):
    """Append the target's station profiles; never replace the verified donor seed."""
    restored=0;skipped=0
    for source in sorted((root/'etc/NetworkManager/system-connections').glob('*.nmconnection')):
        try:
            text=source.read_text();original=configparser.ConfigParser(interpolation=None,strict=False);original.read_string(text)
            if original.get('wifi','mode',fallback='infrastructure')!='infrastructure':continue
            ssid=original.get('wifi','ssid',fallback='')
            if not ssid or ssid.upper().startswith('BMI30'):continue
            normalized=new_keyfile(text,'own')
            if not normalized:continue
            restored_profile=configparser.ConfigParser(interpolation=None,strict=False);restored_profile.read_string(normalized)
            # The freshly captured working router has priority 100. Own old
            # networks precede unspecified donor profiles and parent fallback.
            restored_profile['connection']['autoconnect-priority']='50'
            restored_profile['connection']['autoconnect']=original.get('connection','autoconnect',fallback='true')
            if original.get('ipv4','method',fallback='')=='manual':
                restored_profile['ipv4']=dict(original['ipv4'])
            identity=hashlib.sha256((source.name+'\0'+original.get('connection','uuid',fallback='')).encode()).hexdigest()[:20]
            destination=profiles/('bmi30-own-wifi-'+identity+'.nmconnection')
            if destination.exists():continue
            output=io.StringIO();restored_profile.write(output,space_around_delimiters=False)
            atomic(destination,output.getvalue().encode(),0o600);restored+=1
        except (OSError,UnicodeError,configparser.Error,ValueError):skipped+=1
    return {'own_wifi_profiles_restored':restored,'own_wifi_profiles_skipped':skipped}

def configure_network(root, actual, creator, fallback=None, preserve_address=True):
    cfg=read_json(root/'etc/bmi30-network/config.json')
    config_fallback=False
    if not isinstance(cfg,dict) and fallback:
        cfg=read_json(fallback[0]/'etc/bmi30-network/config.json');config_fallback=True
    if not isinstance(cfg,dict):raise Refused('Сетевая конфигурация USB-копии не читается.')
    cfg['id']=hostname(actual);cfg['automatic']=True
    seed=int(actual[-6:],16);address='10.'+str(64+(seed>>8)%128)+'.'+str(seed%250+1)+'.1/24'
    if preserve_address and actual==creator and not config_fallback:
        try:
            value=ipaddress.IPv4Interface(cfg.get('ap_address'))
            if value.ip.is_private:address=str(value)
        except (ValueError,TypeError):pass
    if not preserve_address:
        source_cfg=read_json((fallback[0] if fallback else root)/'etc/bmi30-network/config.json',{})
        try:source_network=ipaddress.IPv4Interface(source_cfg['ap_address']).network
        except (KeyError,ValueError,TypeError):source_network=ipaddress.IPv4Network('10.42.0.0/24')
        for offset in range(32000):
            candidate=ipaddress.IPv4Interface(address)
            if not candidate.network.overlaps(source_network):break
            address='10.'+str(64+((seed>>8)+offset+1)%128)+'.'+str((seed+offset+1)%250+1)+'.1/24'
        else:raise Refused('Сетевая конфигурация USB-копии не читается.')
    cfg['ap_address']=address;write_json(live('/etc/bmi30-network/config.json'),cfg)
    write_json(live('/var/lib/bmi30-access/retained-ap.json'),{'cidr':address})
    ap_fallback=False
    try:ap_text=own_hotspot(root,creator,address)
    except Refused:
        if not fallback:raise
        ap_text=own_hotspot(fallback[0],fallback[1],address);ap_fallback=True
    ap=new_keyfile(ap_text,hostname(actual),access_point=True,cidr=address)
    profiles=live('/etc/NetworkManager/system-connections');profiles.mkdir(parents=True,exist_ok=True)
    for path in profiles.iterdir():
        if not path.is_file():continue
        try:
            parser=configparser.ConfigParser(interpolation=None);parser.read_string(path.read_text())
            own_sta=parser.get('wifi','mode',fallback='')=='infrastructure' and parser.get('wifi','ssid',fallback='').upper() in (hostname(actual),'BMI30-'+actual.upper())
            if parser.get('wifi','mode',fallback='')=='ap' or own_sta:path.unlink()
        except (configparser.Error,UnicodeError):path.unlink()
    atomic(profiles/'bmi30-emergency-hotspot.nmconnection',ap.encode(),0o600)
    ip=address.split('/')[0]
    for name in ('/etc/NetworkManager/dnsmasq-shared.d/91-bmi30-captive-detection.conf','/etc/dnsmasq.d/bmi30-hotspot.conf'):
        path=live(name)
        if path.is_file():atomic(path,re.sub(r'(?<=/)10\.\d+\.\d+\.\d+',ip,path.read_text()).encode(),0o644)
    dns=('# BMI30 service network: ordinary DNS is forwarded upstream.\n'
         'address=/bmi30.local/'+ip+'\naddress=/'+hostname(actual).lower()+'.local/'+ip+'\n')
    atomic(live('/etc/NetworkManager/dnsmasq-shared.d/90-bmi30-captive-portal.conf'),dns.encode(),0o644)
    return {'hotspot_ssid':hostname(actual),'hotspot_ip':ip,'hotspot_key_source':'saved-usb-system',
            'network_settings_fallback':bool(config_fallback or ap_fallback)}

def personalize(image, actual, snapshot=SNAPSHOT, network_fallback=None):
    creator=serial(image['source_serial']);root=verified_snapshot(snapshot,creator)
    if network_fallback:
        network_fallback=(verified_snapshot(network_fallback[0],network_fallback[1]),serial(network_fallback[1]))
    own=actual==creator and read_json(root/'snapshot.json',{}).get('full_settings',False)
    if own:
        info=configure_identity(root,actual);mode='full';message='Работа с собственными настройками, сохранёнными на USB.'
    else:
        for directory in ('etc/bmi30-agent','var/lib/bmi30-agent'):
            if live(directory).is_dir():shutil.rmtree(live(directory))
        unmask('bmi30-core');mask('bmi30-agent')
        source=read_json(root/'home/techaid/Documents/host/bmi30_config.json',{})
        minimal={key:source[key] for key in ('portal_auth','engineer_auth') if key in source}
        for pattern in ('*.json','*.npz','*.npy'):
            for path in live(DOCS/'host').glob(pattern):
                if path.is_file() and not path.name.startswith(('bmi30_firmware','bmi30_split','bmi30_server')):path.unlink()
        minimal['recovery_mode']='factory-settings';write_json(live(DOCS/'host/bmi30_config.json'),minimal)
        user_ownership(DOCS/'host/bmi30_config.json')
        live(DOCS/'host/bmi30_sel.json').unlink(missing_ok=True)
        configure_host(root,actual,own=False)
        info={'own_settings_files':0,'agent_identity_imported':False};mode='factory-settings'
        message='Работа с USB и начальными настройками; собственные настройки eMMC недоступны.'
    original_creator=network_fallback[1] if network_fallback else creator
    info.update(configure_network(root,actual,creator,fallback=network_fallback,preserve_address=actual==original_creator))
    if own and network_fallback:
        info.update(restore_own_wifi(root,live('/etc/NetworkManager/system-connections')))
    return {'stage':'adopted','physical_serial':actual,'root_partuuid':image['root_partuuid'],'source_serial':creator,
      'mode':mode,'settings_source':'usb-snapshot' if own else 'new-configuration','emmc_read':False,'emmc_written':False,'message':message,**info}

def main():
    image=read_json(MANIFEST)
    if not isinstance(image,dict) or image.get('format')!=2 or image.get('mode')!='portable-emergency':raise Refused('Неверный манифест аварийной USB-системы.')
    actual=physical_serial();inv=inventory();root_disk=parent_disk(inv,inv['root_source']);boot_disk=parent_disk(inv,inv['boot_source'])
    disks={n['path']:n for n,_ in flat(inv['blockdevices']) if n.get('type')=='disk'}
    if root_disk!=boot_disk or disks[root_disk].get('tran')!='usb':raise Refused('Система не загружена целиком с USB.')
    nodes={n['path']:n for n,_ in flat(inv['blockdevices'])}
    if nodes[inv['root_source']].get('partuuid')!=image['root_partuuid'] or nodes[inv['boot_source']].get('partuuid')!=image['boot_partuuid']:raise Refused('Загружен другой USB-образ.')
    old=read_json(live('/var/lib/bmi30-usb-adoption.json'),{})
    if old.get('physical_serial')==actual and old.get('root_partuuid')==image['root_partuuid'] and old.get('stage')=='adopted' and old.get('revision')==2:return
    snapshot=SNAPSHOT;selected_image=image;emmc_read=False
    if actual!=serial(image['source_serial']):
        from usb_recovery_emmc_settings import try_settings
        snapshot=try_settings(actual,inv) or SNAPSHOT
        if snapshot!=SNAPSHOT:
            selected_image={**image,'source_serial':actual};emmc_read=True
    fallback=(SNAPSHOT,serial(image['source_serial'])) if emmc_read else None
    info=personalize(selected_image,actual,snapshot=snapshot,network_fallback=fallback)
    info['revision']=2
    if emmc_read:info.update(emmc_read=True,settings_source='own-emmc-snapshot',source_serial=image['source_serial'])
    for relative in ('host/bmi30_rollback.env','.bmi30_cloud_sync/rollback_state.env'):live(DOCS/relative).unlink(missing_ok=True)
    write_json(live('/var/lib/bmi30-usb-adoption.json'),info)
    write_json(live('/var/lib/bmi30-portable-runtime.json'),{'stage':'adopted','physical_serial':actual,'bundle_id':image.get('version','')})
    command('systemctl','daemon-reload')

if __name__=='__main__':
    try:main()
    except Exception as error:
        write_json(live('/var/lib/bmi30-usb-adoption.json'),{'stage':'failed','message':str(error) if isinstance(error,Refused) else 'Ошибка сохранённых USB-данных.','emmc_read':False})
        raise SystemExit(1)
