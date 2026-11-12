"""Opt-in USB-to-eMMC restore. Default USB boot keeps the eMMC host disabled."""
import re,subprocess,uuid
from usb_recovery_common import *
BOOT=P('/boot/firmware')

def source_is_usb(inv):
    try:return copy_direction(inv)=='emmc-restore'
    except (Refused,KeyError):return False

def state(inv):
    visible=source_is_usb(inv)
    target=next((n for n,_ in flat(inv['blockdevices']) if n['path']=='/dev/mmcblk0'),None)
    enabled=False;reason=''
    if visible and target:
        try:validate(inv,'/dev/mmcblk0',config(),kind='emmc-restore');enabled=True
        except Refused as error:reason=str(error)
    elif visible:reason='Встроенная память отключена аварийной USB-загрузкой или недоступна.'
    try:disabled=P('/proc/device-tree/soc@107c000000/mmc@fff000/status').read_text().strip('\0\n')=='disabled'
    except OSError:disabled=False
    return {'visible':visible,'available':enabled,'target':'/dev/mmcblk0' if target else None,
            'reason':reason,'requires_reboot_to_enable':visible and disabled}

def remove_usb_emmc_block(text):
    text=re.sub(r'\n# BMI30_EMERGENCY_EMMC_BEGIN.*?# BMI30_EMERGENCY_EMMC_END\n?', '\n',text,flags=re.S)
    return re.sub(r'^\s*dtoverlay=bmi30-usb-no-emmc\s*\n?', '',text,flags=re.M)

def enable_controller(data):
    inv=inventory()
    if not source_is_usb(inv):raise Refused('Действие доступно только при загрузке с USB.')
    if data.get('confirm_reboot') is not True:raise Refused('Подтвердите перезапуск с USB для включения eMMC.')
    if read_json(STATE/'latest.json',{}):
        from usb_recovery_backend import latest
        if latest().get('stage') in ACTIVE_STAGES:raise Refused('Дождитесь окончания копирования.')
    path=BOOT/'config.txt';before=path.read_bytes()
    after=remove_usb_emmc_block(before.decode())
    if after==before.decode():raise Refused('Контроллер eMMC уже разрешён. Перезапуск не требуется.')
    atomic(STATE/'boot-config-before-emmc-enable.txt',before)
    atomic(path,after.encode(),0o644);command('sync','-f',str(BOOT))
    try:
        command('systemd-run','--quiet','--collect','--unit=bmi30-usb-enable-emmc-'+uuid.uuid4().hex[:8],
            '--on-active=4s','/usr/bin/systemctl','reboot',timeout=10)
    except Exception:
        atomic(path,before,0o644);raise
    return {'ok':True,'rebooting':True,'message':'Перезапуск с USB для включения eMMC. Запись памяти ещё не начинается.'}

def prepare_native_image(mount,boot,root_partuuid,boot_partuuid):
    from usb_recovery_worker import remove_copied_update_transactions
    remove_copied_update_transactions(mount)
    cmdline,fstab=rewrite_boot((boot/'cmdline.txt').read_text(),(mount/'etc/fstab').read_text(),root_partuuid,boot_partuuid)
    atomic(boot/'cmdline.txt',cmdline.encode(),0o644);atomic(mount/'etc/fstab',fstab.encode(),0o644)
    atomic(boot/'config.txt',remove_usb_emmc_block((boot/'config.txt').read_text()).encode(),0o644)
    adoption=read_json('/var/lib/bmi30-usb-adoption.json',{})
    if adoption.get('stage')=='adopted' and adoption.get('physical_serial')==physical_serial():
        write_json(mount/'var/lib/bmi30-portable-runtime.json',{'stage':'adopted','physical_serial':adoption['physical_serial'],'bundle_id':config()['version']})
    (mount/'etc/bmi30-usb-recovery-image.json').unlink(missing_ok=True)
    (mount/'var/lib/bmi30-usb-adoption.json').unlink(missing_ok=True)
    (mount/'etc/systemd/system/bmi30-usb-adopt.service').unlink(missing_ok=True)
    (mount/'etc/systemd/system/multi-user.target.wants/bmi30-usb-adopt.service').unlink(missing_ok=True)
    for path in (mount/'etc/systemd/system').glob('*.service.d/05-bmi30-usb-adopt.conf'):path.unlink()
    (boot/'BMI30-RECOVERY.txt').unlink(missing_ok=True)
