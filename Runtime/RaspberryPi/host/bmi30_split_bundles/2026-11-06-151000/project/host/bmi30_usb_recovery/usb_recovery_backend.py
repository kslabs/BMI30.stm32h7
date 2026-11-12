"""Authenticated portal dispatch uses this module; jobs outlive page reloads."""
import fcntl, json, pathlib, secrets, subprocess, time
from usb_recovery_common import *
from usb_recovery_progress import fields as progress_fields

def latest(inv=None,cfg=None):
    job=read_json(STATE/'latest.json',{})
    if not job:return {'stage':'idle','progress':0,'message':'Запись не запущена.'}
    value=read_json(STATE/'jobs'/job['id']/'status.json',{'stage':'queued','progress':0})
    value['id']=job['id']
    if value.get('stage') in ACTIVE_STAGES:
        active=subprocess.run(['systemctl','is-active',job['unit']],capture_output=True,text=True,timeout=5).stdout.strip()
        if active not in ('active','activating'):
            value.update(stage='failed',message='Запись прервана. USB не готов к загрузке; повторите запись.',safe_to_remove=False)
        elif 'started_mono' in value:
            value.update(progress_fields(value))
    if value.get('stage')=='complete' and inv is not None:
        plan=read_json(STATE/'jobs'/job['id']/'plan.json',{})
        try:
            disk=next(n for n,_ in flat(inv['blockdevices']) if n.get('path')==plan.get('target_path'))
            matches=(plan.get('source_firmware_version')==cfg.get('version')
                and plan.get('source_serial')==inv['physical_serial']
                and plan.get('source_boot_id')==inv['boot_id']
                and plan.get('root_source')==inv['root_source']
                and plan.get('boot_source')==inv['boot_source']
                and plan.get('binding')==binding(inv,disk))
        except (StopIteration,KeyError,Refused):matches=False
        if not matches:
            return {'stage':'idle','progress':0,'safe_to_remove':False,
                'message':'Сохранён результат предыдущего копирования. Выберите носитель для новой записи.',
                'previous_copy':{'id':job['id'],'version':plan.get('source_firmware_version'),'stage':'complete'}}
    return value

def state():
    cfg=config();inv=inventory();devices=[];reason='';direction=None
    try:
        serial(inv['physical_serial'])
        if cfg.get('mode')!='portable-emergency':raise Refused('Функция требует обновления.')
        direction=copy_direction(inv)
        require_copy_ready(inv,cfg)
    except (Refused,KeyError) as error:reason=str(error)
    for disk,parent in flat(inv['blockdevices']):
        if disk.get('type')!='disk' or disk.get('tran')!='usb':continue
        disabled=reason
        if not disabled:
            try:validate(inv,disk['path'],cfg,mounted_ok=True)
            except Refused as error:disabled=str(error)
        devices.append({'path':disk['path'],'model':str(disk.get('model') or 'USB').strip(),
            'serial':disk.get('serial') or '', 'size':int(disk['size']), 'eligible':not disabled,'reason':disabled})
    image=read_json(MANIFEST,{})
    from usb_recovery_emmc import state as emmc_state
    emmc=emmc_state(inv)
    return {'ok':True,'version':cfg.get('version'),'source':hostname(inv['physical_serial']),
            'purpose':'portable-emergency',
            'disks':devices,'can_write':not reason,'blocked_reason':reason,'operation':latest(inv,cfg),'emmc':emmc,
            'copy':{'kind':direction,'target':emmc['target'] if direction=='emmc-restore' else None},
            'boot':{'root':inv['root_source'],'boot':inv['boot_source'],
                    'from_recovery_usb':bool(image),'adoption':read_json('/var/lib/bmi30-usb-adoption.json',{})}}

def plan(target,kind='usb'):
    if latest().get('stage') in ACTIVE_STAGES:raise Refused('Запись уже выполняется.')
    # A deliberately conservative bound includes live data and filesystem overhead.
    st=os.statvfs('/');used=(st.f_blocks-st.f_bfree)*st.f_frsize
    required=max(4*1024**3,int(used*1.08)+1024**3)
    value=make_plan(inventory(),str(target),config(),required,kind=kind)
    value['confirmation']=secrets.token_urlsafe(24)
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700);(STATE/'plans').mkdir(exist_ok=True,mode=0o700)
    write_json(STATE/'plans'/(value['id']+'.json'),value)
    return {'ok':True,'plan':value}

def start(data):
    identity=str(data.get('id',''))
    if not re.fullmatch('[0-9a-f]{32}',identity):raise Refused('Повторите выбор носителя.')
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (STATE/'start.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if latest().get('stage') in ACTIVE_STAGES:raise Refused('Запись уже выполняется.')
        value=read_json(STATE/'plans'/(identity+'.json'),{})
        if not value or not secrets.compare_digest(str(data.get('confirmation','')),value.get('confirmation','')):
            raise Refused('Подтвердите запись выбранного USB.')
        if data.get('erase_confirmed') is not True:raise Refused('Нужно подтвердить удаление данных на выбранном USB.')
        revalidate(value,inventory(),config(),mounted_ok=True)
        value['authorized']=True
        directory=STATE/'jobs'/identity;directory.mkdir(parents=True,mode=0o700)
        write_json(directory/'plan.json',value)
        write_json(directory/'status.json',{'stage':'queued','kind':value.get('kind','usb'),'progress':0,'message':'Запись поставлена в очередь.', 'safe_to_remove':False,**progress_fields({})})
        unit='bmi30-usb-write-'+identity
        command('systemd-run','--quiet','--collect','--unit='+unit,'--property=RuntimeMaxSec=14400',
                '--property=Nice=15','--property=IOSchedulingClass=idle',
                '/usr/bin/python3',str(MODULE/'usb_recovery_worker.py'),str(directory),timeout=12)
        write_json(STATE/'latest.json',{'id':identity,'unit':unit})
        (STATE/'plans'/(identity+'.json')).unlink(missing_ok=True)
    return {'ok':True,'started':True,'operation':latest()}

def dispatch(method,data=None):
    if method=='status':return state()
    if method=='plan':return plan((data or {}).get('target',''),(data or {}).get('kind','usb'))
    if method=='start':return start(data or {})
    if method=='read-stm32':
        from usb_recovery_stm32 import read_parameters
        return {'ok':True,'stm32':read_parameters()}
    if method=='enable-emmc':
        from usb_recovery_emmc import enable_controller
        return enable_controller(data or {})
    raise Refused('Неизвестное действие восстановления.')
