"""Read supported STM32 parameters. No OUT, reset, claim, or flash commands."""
import struct,subprocess,urllib.request,json
from usb_recovery_common import Refused

def read_device(device):
    result={'read_only':True,'applied':False};available=0
    for name,request,length in (('identity',0x40,40),('dc',0x3A,40)):
        try:data=bytes(device.ctrl_transfer(0xC0,request,0xFF if request==0x40 else 0,0,length,timeout=700))
        except Exception:continue
        if name=='identity' and len(data)>=32 and data[:4] in (b'RID1',b'RID2'):
            flags=int.from_bytes(data[6:8],'little');node=data[5]
            if node>31 or not flags&0x07FF:continue
            result['role']='master' if flags&0x80 else 'slave';result['node_id']=node
            result['device_code']=('M' if flags&0x80 else 'S')+f'{node:02d}'
            result['short_id']=data[8:18].split(b'\0',1)[0].decode('ascii',errors='ignore');available+=1
        elif name=='dc' and len(data)>=40 and data[:4]==b'DCCF':
            values=struct.unpack('<4sBBHIII5I',data[:40])
            result['dc']={'enabled':bool(values[2]),'flags':values[3],'settle_ms':values[4],
                          'set_at_ms':values[5],'adapt_updates':values[6]};available+=1
    if not available:raise Refused('Эта версия STM32 не ответила на поддерживаемые команды чтения.')
    return result

def read_parameters():
    active=subprocess.run(['systemctl','is-active','bmi30-core'],capture_output=True,text=True,timeout=5).stdout.strip()
    if active=='active':
        # The running owner already polls the MCU. Do not compete for its USB.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1/api/status',timeout=6) as response:status=json.load(response)
        return {'read_only':True,'applied':False,'via':'running-core',
          'firmware':status.get('stm32_firmware_version'),'device_code':status.get('sync_mode',{}).get('code'),
          'uid96':status.get('group_state',{}).get('stm32_uid96')}
    import usb.core,usb.util
    devices=list(usb.core.find(find_all=True,idVendor=0xCAFE,idProduct=0x4001))
    if len(devices)!=1:raise Refused('Не найден единственный STM32 BMI30 по USB.')
    try:return read_device(devices[0])
    finally:usb.util.dispose_resources(devices[0])
