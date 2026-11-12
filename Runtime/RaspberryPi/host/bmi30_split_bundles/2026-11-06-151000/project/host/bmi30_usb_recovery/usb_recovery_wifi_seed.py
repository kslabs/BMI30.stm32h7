"""Capture only the active NM connection; passwords stay in memory and on the selected USB."""
import configparser,io,uuid
from usb_recovery_common import Refused

def key_string(value):
    value=str(value).replace('\\','\\\\').replace('\n','\\n').replace('\r','\\r').replace('\t','\\t')
    return value.replace(' ','\\s')

def active_settings(interface):
    import dbus
    bus=dbus.SystemBus();service='org.freedesktop.NetworkManager';properties='org.freedesktop.DBus.Properties'
    try:
        manager=dbus.Interface(bus.get_object(service,'/org/freedesktop/NetworkManager'),service)
        device=manager.GetDeviceByIpIface(interface,timeout=5)
        active=dbus.Interface(bus.get_object(service,device),properties).Get(service+'.Device','ActiveConnection',timeout=5)
        if str(active)=='/':return None
        path=dbus.Interface(bus.get_object(service,active),properties).Get(service+'.Connection.Active','Connection',timeout=5)
        connection=dbus.Interface(bus.get_object(service,path),service+'.Settings.Connection')
        settings=connection.GetSettings(timeout=5)
        if '802-11-wireless' not in settings:return None
        secrets=connection.GetSecrets('802-11-wireless-security',timeout=5)
        return settings,secrets
    except dbus.DBusException as error:
        if error.get_dbus_name().endswith('UnknownDevice'):return None
        raise Refused('Cannot read the current Wi-Fi settings.') from None

def station_profile(settings,secrets,identity,priority):
    wifi=settings.get('802-11-wireless') or {};security=settings.get('802-11-wireless-security') or {}
    try:ssid=bytes(wifi['ssid']).decode('utf-8')
    except (KeyError,ValueError,UnicodeError):raise Refused('Cannot read the current Wi-Fi settings.') from None
    if not ssid or '\0' in ssid:raise Refused('Cannot read the current Wi-Fi settings.')
    method=str(security.get('key-mgmt') or '')
    if method not in ('wpa-psk','sae'):raise Refused('This Wi-Fi security mode cannot be copied.')
    password=str((secrets.get('802-11-wireless-security') or {}).get('psk') or '')
    if not 8<=len(password.encode('utf-8'))<=64 or '\0' in password:
        raise Refused('The active Wi-Fi connection needs a saved password.')
    cfg=configparser.ConfigParser(interpolation=None)
    cfg['connection']={'id':identity,'uuid':str(uuid.uuid4()),'type':'wifi','interface-name':'wlan0',
                       'autoconnect':'true','autoconnect-priority':str(priority),'autoconnect-retries':'2'}
    cfg['wifi']={'mode':'infrastructure','ssid':key_string(ssid)}
    cfg['wifi-security']={'key-mgmt':method,'psk':key_string(password),'psk-flags':'0'}
    cfg['ipv4']={'method':'auto'};cfg['ipv6']={'method':'auto'}
    out=io.StringIO();cfg.write(out,space_around_delimiters=False);return out.getvalue()

def capture_source_wifi(source_id):
    station=active_settings('wlan0');ap=active_settings('wlan0ap')
    profiles={};summary={'active_wifi_saved':False,'source_hotspot_fallback_saved':False}
    if station:
        if str(station[0].get('802-11-wireless',{}).get('mode','infrastructure'))!='infrastructure':
            raise Refused('Cannot read the current Wi-Fi settings.')
        profiles['bmi30-recovery-router.nmconnection']=station_profile(*station,'bmi30-recovery-router',100)
        summary['active_wifi_saved']=True
    if ap and str(ap[0].get('802-11-wireless',{}).get('mode'))=='ap' and str(ap[0].get('ipv4',{}).get('method'))=='shared':
        profiles['bmi30-recovery-via-source.nmconnection']=station_profile(*ap,'bmi30-recovery-via-'+source_id,-100)
        summary['source_hotspot_fallback_saved']=True
    return {'profiles':profiles,'summary':summary}
