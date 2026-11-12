"""Activate only the portal and release metadata, preserving acquisition."""
import hashlib,json,os,runpy,shlex,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
p=json.load(sys.stdin);i=p['before'];r=p['release']
D=Path('/home/techaid/Documents');A=D/'host/bmi30_active_runtime/project'
B=D/'host/bmi30_split_bundles'/r['version'];W=D/'.tmp/flag-20261016'
def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def api():return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=4))
def pid():return run('systemctl','show','bmi30-core','-p','MainPID','--value')
def atomic(path,data,reference=None):
    stat=(path if path.exists() else reference).stat();temp=path.with_name(path.name+'.brand-tmp')
    temp.write_bytes(data);os.chown(temp,stat.st_uid,stat.st_gid);os.chmod(temp,stat.st_mode&0o777);temp.replace(path)
assert run('hostname')==i['hostname']=='BMI30-E29805C7C'
assert Path('/proc/device-tree/serial-number').read_text().strip('\0\n')==i['serial']=='490df72e29805c7c'
assert run('findmnt','-n','-o','SOURCE','/')==i['root']=='/dev/sda2'
assert (D/'host/bmi30_split_active_version.env').read_text()==i['env']
assert all(sha(A/path)==i['hashes'][key] for key,path in i['paths'].items())
assert sha(Path('/usr/local/bin/bmi30-hotspot-info-server.py'))==i['installed_portal_sha256']
assert sha(B/'SHA256SUMS')==r['bundle_checksums_sha256']
validation=run('bash',str(D/'switch_bmi30_split_versions.sh'),'--validate',r['version'])
before=api();owner=pid();assert int(owner)>0
assert before['ok'] and not before['stale'] and before['connection']['connected']
assert before['detector']['worker']['alive']
assert not before['player_recorder']['recording'] and not before['player_recorder']['playback_active']
unchanged={f:sha(f) for f in (A/'host').iterdir() if f.is_file() and '.py' in f.name}
settings={f.resolve():f.read_bytes() for f in (D/'host').iterdir() if f.is_file() and f.suffix in ['.json','.npz','.npy']}
portal=(B/'project/hotspot_info_server.py').read_bytes()
assert hashlib.sha256(portal).hexdigest()==r['hashes']['portal']
updates={'BMI30_SPLIT_VERSION':r['version'],'BMI30_SPLIT_BUNDLE_ID':r['version'],
 'BMI30_SPLIT_LABEL':'Decorative Ukrainian flag in portal header',
 'BMI30_SPLIT_SELECTED_BY':'validated-M02-portal-flag-update',
 'BMI30_SPLIT_SELECTED_AT':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
 'BMI30_BUNDLE_ORIGIN':'Portal brand flag only, without a tooltip; acquisition modules unchanged'}
env=('\n'.join(line.split('=',1)[0]+'='+shlex.quote(updates[line.split('=',1)[0]])
 if line.split('=',1)[0] in updates else line for line in i['env'].splitlines())+'\n').encode()
manifest=(B/'project/host/bmi30_firmware_release.env').read_bytes()
targets={A/'hotspot_info_server.py':portal,D/'hotspot_info_server.py':portal,
 Path('/usr/local/bin/bmi30-hotspot-info-server.py'):portal,
 A/'host/bmi30_firmware_release.env':manifest,D/'host/bmi30_firmware_release.env':manifest,
 D/'host/bmi30_split_active_version.env':env,
 A/'host/bmi30_split_active_version.env':(B/'project/host/bmi30_split_active_version.env').read_bytes()}
backup=W/('backup-'+r['version']);backup.mkdir(mode=0o700,exist_ok=False)
saved={};committed=False
try:
    for path in [*targets,*settings]:
        dest=backup/path.relative_to('/');dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,dest)
    saved={path:path.read_bytes() for path in targets}
    for path,data in targets.items():
        assert not path.is_symlink();atomic(path,data)
    run('systemctl','restart','bmi30-hotspot-info')
    for attempt in range(30):
        try:
            response=json.load(urllib.request.urlopen('http://127.0.0.1/api/status',timeout=2))
            assert response['hostname']=='BMI30-E29805C7C'
            break
        except Exception:time.sleep(.25)
    else:raise RuntimeError('Portal did not recover')
    assert pid()==owner,'Core PID changed'
    os.environ['BMI30_CONFIG_JSON']=str(D/'host/bmi30_config.json')
    ns=runpy.run_path('/usr/local/bin/bmi30-hotspot-info-server.py')
    html=ns['render_portal_page']('BMI30-E29805C7C',session_username='verification',request_host='192.168.0.154').decode()
    assert 'BMI30 Management Portal<svg class="brand-origin-flag"' in html
    assert html.count('class="brand-origin-flag"')==1
    time.sleep(5)
    after=api()
    assert pid()==owner
    assert after['ok'] and not after['stale'] and after['connection']['connected'] and after['connection']['last_frame_age_s']<2
    assert after['detector']['worker']['alive']
    assert after['detector']['worker']['results']>before['detector']['worker']['results']
    assert after['detector']['worker']['overflows']==before['detector']['worker']['overflows']
    for key in ['selected','stream_mode','base_buf_len','freq_hz','avg_n','rpi_avg_n']:
        assert before['mode'][key]==after['mode'][key],key
    assert before['detector']['settings']==after['detector']['settings']
    for channel in ['upper','lower']:
        for key in ['enabled','confirm_count','threshold','threshold_high','auto_threshold','noise_up','noise_down','noise_unit']:
            assert before['detector']['channels'][channel][key]==after['detector']['channels'][channel][key]
    errors=['seq_gap_ch0','seq_gap_ch1','seq_dup_ch0','seq_dup_ch1','crc_bad','magic_bad','host_rx_ack_fail']
    deltas={key:after['stream_stats'][key]-before['stream_stats'][key] for key in errors}
    assert all(value==0 for value in deltas.values()),deltas
    assert all(sha(path)==value for path,value in unchanged.items())
    assert all(path.read_bytes()==value for path,value in settings.items())
    assert all(path.read_bytes()==value for path,value in targets.items())
    committed=True
    result={'version':r['version'],'firmware_version':r['firmware_version'],'backup':str(backup),
     'core_pid':owner,'core_pid_preserved':True,'acquisition_files_preserved':len(unchanged),
     'settings_preserved':True,'new_worker_results':after['detector']['worker']['results']-before['detector']['worker']['results'],
     'error_deltas':deltas,'portal_sha256':r['hashes']['portal'],'active_root':i['root'],'validation':validation}
    (backup/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
finally:
    if saved and not committed:
        for path,data in saved.items():atomic(path,data)
        run('systemctl','restart','bmi30-hotspot-info')
