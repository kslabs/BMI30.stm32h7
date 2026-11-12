"""Default emergency-USB feature for subsequent BMI30 application bundles."""
import importlib.util,json,pathlib
HERE=pathlib.Path(__file__).resolve().parent
FEATURE_FILES=('usb_recovery_common.py','usb_recovery_backend.py','usb_recovery_worker.py',
               'usb_recovery_progress.py','usb_recovery_finish.py',
               'wifi_profiles.py','wifi_profiles.html','patch_wifi_profiles.py',
               'usb_recovery_adopt.py','usb_recovery_stm32.py','usb_recovery_emmc_settings.py','usb_recovery_wifi_seed.py','recovery.html',
               'patch_portal.py','patch_firmware_check.py','patch_portal_launcher.py','patch_hotspot_identity.py','firmware_check.js','integrate_bundle.py','bundle_feature_cli.py','patch_bundle_builder.py',
               'usb_recovery_emmc.py','bmi30-usb-no-emmc.dtbo','bmi30-usb-no-emmc-overlay.dts',
               'patch_group_status.py','group_ui.js','bundle_ui.py','ui_language.js',
               'recovery_catalogs.tsv','recovery_aliases.json',
               *('translations.'+code+'.json' for code in ('uk','es','de','cs','ja','fr','pt','zh-Hans','it','ru')))
MODULE_PREFIX='project/host/bmi30_usb_recovery/'

def feature_source(name):
    if (HERE/name).is_file():return HERE/name
    directory='bmi30_network' if name in ('patch_group_status.py','group_ui.js') else 'rpi_i18n'
    return HERE.parent/directory/name

def shared_module(name):
    spec=importlib.util.spec_from_file_location('usb_shared_'+name,feature_source(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def portal(source):
    spec=importlib.util.spec_from_file_location('bmi30_emergency_usb_portal_patch',HERE/'patch_portal.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from patch_firmware_check import patch as manual_check
    from patch_portal_launcher import patch as versioned_launcher
    from patch_hotspot_identity import portal as hotspot_identity
    source=manual_check(module.patch(source))
    source=versioned_launcher(source)
    source=hotspot_identity(source)
    source=shared_module('patch_group_status').patch(source)
    from patch_wifi_profiles import patch as saved_wifi
    source=saved_wifi(source)
    return shared_module('bundle_ui').portal(source)

def add_to_bundle(files,manifest,version):
    name=manifest['BMI30_BUNDLE_PORTAL_REL']
    files[name]=portal(files[name].decode('utf-8')).encode('utf-8')
    i18n=shared_module('bundle_ui')
    core=manifest.get('BMI30_BUNDLE_CORE_REL')
    if core and core in files:files[core]=i18n.core(files[core].decode('utf-8')).encode('utf-8')
    for item in FEATURE_FILES:files[MODULE_PREFIX+item]=feature_source(item).read_bytes()
    files[MODULE_PREFIX+'recovery.html']=i18n.html(files[MODULE_PREFIX+'recovery.html'].decode('utf-8')).encode('utf-8')
    files[MODULE_PREFIX+'wifi_profiles.html']=i18n.html(files[MODULE_PREFIX+'wifi_profiles.html'].decode('utf-8')).encode('utf-8')
    files[MODULE_PREFIX+'config.json']=(json.dumps({'format':2,'mode':'portable-emergency',
       'feature_version':'0.4.1','version':version,'internal_storage_on_usb_boot':'read-own-settings-if-available'},indent=2)+'\n').encode()
    manifest['BMI30_BUNDLE_USB_EMERGENCY']='2'
    return files[name]

def validate_included(files,manifest):
    if manifest.get('BMI30_BUNDLE_USB_EMERGENCY')!='2':raise ValueError('Emergency USB feature is missing from release')
    for item in (*FEATURE_FILES,'config.json'):
        if MODULE_PREFIX+item not in files:raise ValueError('Emergency USB module missing: '+item)
    cfg=json.loads(files[MODULE_PREFIX+'config.json'])
    if cfg.get('mode')!='portable-emergency' or any(key in cfg for key in ('source_serial','target_serial','target_role')):
        raise ValueError('Emergency USB feature must remain portable')
