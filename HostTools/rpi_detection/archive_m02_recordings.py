"""Copy identified M02 recording folders to Windows with resumable SFTP and SHA256.

Remote deletion is a separate explicit cleanup action after complete verification.
Existing final local files must match their manifest; incomplete downloads use
.part suffixes until verified. Cleanup retains summaries, labels and helper code.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.tmp/new-device-20261009'))
from device_remote import ARGS, remote

INVENTORY=r'''
import hashlib,json,os,pathlib,socket,stat,subprocess,time,urllib.request
assert socket.gethostname()=='BMI30-E29805C7C'
assert pathlib.Path('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
os.nice(15)
subprocess.run(['ionice','-c','3','-p',str(os.getpid())],check=True,capture_output=True)
D=pathlib.Path('/home/techaid/Documents')
roots={'tag_statistics':D/'host/tag_statistics','player_recordings':D/'host/player_recordings'}
status=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=4))
assert not status['player_recorder']['recording'] and not status['player_recorder']['playback_active']
active=subprocess.check_output(['systemctl','list-units','--state=running','--no-legend','bmi30-tag-stats-*'],text=True)
assert not active.strip(),'Statistics job is still running'
files=[]
for label,root in roots.items():
 assert root.is_dir() and not root.is_symlink()
 for path in sorted(root.rglob('*')):
  assert not path.is_symlink(),'Unexpected symlink: '+str(path)
  if not path.is_file():continue
  assert path.resolve().is_relative_to(root.resolve())
  before=path.stat();digest=None;provenance='source_sha256_now'
  summary=path.with_name('summary.json')
  if path.name=='capture.jsonl.gz' and summary.is_file():
   data=json.loads(summary.read_text())
   if data.get('archive_bytes')==before.st_size and len(data.get('archive_sha256',''))==64 and summary.stat().st_mtime_ns>=before.st_mtime_ns:
    digest=data['archive_sha256'];provenance='recorder_sha256_at_close'
  if digest is None:
   h=hashlib.sha256()
   with path.open('rb') as stream:
    for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
   digest=h.hexdigest()
  after=path.stat();assert (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'Source file changed'
  files.append({'relative':label+'/'+path.relative_to(root).as_posix(),'remote':str(path),
   'size':before.st_size,'mtime_ns':before.st_mtime_ns,'sha256':digest,'hash_provenance':provenance})
server=next((str(p) for p in [pathlib.Path('/usr/lib/openssh/sftp-server'),pathlib.Path('/usr/lib/ssh/sftp-server')] if p.is_file()),None)
print(json.dumps({'hostname':socket.gethostname(),'serial':'490df72e29805c7c','created_epoch':time.time(),
 'root_device':subprocess.check_output(['findmnt','-no','SOURCE','/'],text=True).strip(),
 'roots':{k:str(v) for k,v in roots.items()},'files':files,'total_bytes':sum(f['size'] for f in files),
 'sftp_server':server,'core_pid':subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip(),
 'persistent_config_sha256':hashlib.sha256((D/'host/bmi30_config.json').read_bytes()).hexdigest()}))
'''

CHECK_SOURCE=r'''
import hashlib,json,pathlib,socket,subprocess,sys,urllib.request
assert socket.gethostname()=='BMI30-E29805C7C'
assert pathlib.Path('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
m=json.load(sys.stdin);changed=[]
for row in m['files']:
 path=pathlib.Path(row['remote'])
 assert any(path.is_relative_to(pathlib.Path(r)) for r in m['roots'].values())
 try:
  s=path.stat()
  if path.is_symlink() or s.st_size!=row['size'] or s.st_mtime_ns!=row['mtime_ns']:changed.append(row['relative'])
 except OSError:changed.append(row['relative'])
status=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=4))
print(json.dumps({'source_files_changed':changed,
 'core_pid_unchanged':subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()==m['core_pid'],
 'persistent_config_unchanged':hashlib.sha256(pathlib.Path('/home/techaid/Documents/host/bmi30_config.json').read_bytes()).hexdigest()==m['persistent_config_sha256'],
 'upper_confirm_count':status['detector']['channels']['upper']['confirm_count'],
 'detector_active':status['detector']['active'],'worker_alive':status['detector']['worker']['alive']}))
'''

DELETE_BULK=r'''
import hashlib,json,os,pathlib,socket,stat,subprocess,sys,time,urllib.request
assert socket.gethostname()=='BMI30-E29805C7C'
assert pathlib.Path('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
assert subprocess.check_output(['findmnt','-no','SOURCE','/'],text=True).strip()=='/dev/sda2'
os.nice(15);subprocess.run(['ionice','-c','3','-p',str(os.getpid())],check=True,capture_output=True)
p=json.load(sys.stdin)
roots={'tag_statistics':'/home/techaid/Documents/host/tag_statistics','player_recordings':'/home/techaid/Documents/host/player_recordings'}
assert p['roots']==roots
assert p['local_verification']['all_sha256_match'] is True
status=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=4))
assert not status['player_recorder']['recording'] and not status['player_recorder']['playback_active']
assert not subprocess.check_output(['systemctl','list-units','--state=running','--no-legend','bmi30-tag-stats-*'],text=True).strip()
selected=[]
for row in p['files']:
 path=pathlib.Path(row['remote'])
 assert any(path.is_relative_to(pathlib.Path(root)) for root in roots.values())
 wanted=(path.name=='capture.jsonl.gz' and path.is_relative_to(pathlib.Path(roots['tag_statistics']))) or ((path.suffix=='.npz' or path.name.endswith('.voice.wav')) and path.is_relative_to(pathlib.Path(roots['player_recordings'])))
 if not wanted:continue
 assert not path.is_symlink() and path.resolve()==path
 before=path.stat();assert stat.S_ISREG(before.st_mode)
 assert before.st_size==row['size'] and before.st_mtime_ns==row['mtime_ns'],'Source changed: '+row['relative']
 selected.append(row)
receipt_path=pathlib.Path(roots['tag_statistics'])/'offload-receipt-20261020.json'
receipt={'state':'verifying_source_before_cleanup','started_epoch':time.time(),'local_destination':p['local_destination'],
 'local_verification':p['local_verification'],'source_files_to_delete':len(selected),'deleted':[],'deleted_bytes':0,
 'retained':'All summaries, labels, metadata, source helpers and directories; persistent device configuration untouched'}
def save():
 temp=receipt_path.with_suffix('.tmp');temp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n');os.replace(temp,receipt_path)
save()
# Verify every selected source before deleting any of them.
for row in selected:
 path=pathlib.Path(row['remote']);h=hashlib.sha256()
 with path.open('rb') as stream:
  for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
 assert h.hexdigest()==row['sha256'],'Current source SHA256 differs: '+row['relative']
receipt['state']='cleanup';receipt['all_current_source_sha256_match']=True;save()
for row in selected:
 path=pathlib.Path(row['remote']);before=path.stat()
 assert before.st_size==row['size'] and before.st_mtime_ns==row['mtime_ns'] and not path.is_symlink()
 path.unlink()
 receipt['deleted'].append({'relative':row['relative'],'size':row['size'],'sha256':row['sha256']})
 receipt['deleted_bytes']+=row['size']
 if len(receipt['deleted'])%10==0:save()
receipt.update(state='complete',finished_epoch=time.time(),source_archives_deleted=len(receipt['deleted']))
disk=os.statvfs('/home/techaid/Documents');receipt['available_bytes_after']=disk.f_bavail*disk.f_frsize
remaining=[path for root in roots.values() for path in pathlib.Path(root).rglob('*') if path.is_file()]
receipt['remaining_recording_files']=len(remaining);receipt['remaining_recording_bytes']=sum(path.stat().st_size for path in remaining)
save();print(json.dumps(receipt))
'''


def save(path,data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temporary,path)


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def local_path(destination,name):
    pure=PurePosixPath(name)
    assert not pure.is_absolute() and '..' not in pure.parts
    for part in pure.parts:
        assert not any(c in part for c in '<>:"\\|?*') and not part.endswith((' ','.')),part
    path=destination.joinpath(*pure.parts)
    assert path.resolve().is_relative_to(destination.resolve())
    return path


def quoted(value):
    return '"'+str(value).replace('\\','/').replace('"','\\"')+'"'


def verify(destination,manifest):
    verified=[];done_bytes=0;reported=0
    for row in manifest['files']:
        final=local_path(destination,row['relative'])
        part=final.with_name(final.name+'.part')
        candidate=final if final.exists() else part
        assert candidate.is_file() and candidate.stat().st_size==row['size'],row['relative']
        assert digest(candidate)==row['sha256'],'SHA256 mismatch: '+row['relative']
        if candidate==part:
            assert not final.exists()
            part.rename(final)
        os.utime(final,ns=(time.time_ns(),row['mtime_ns']))
        verified.append(row['relative']);done_bytes+=row['size']
        if done_bytes-reported>1024**3:
            print(json.dumps({'phase':'sha256_verification','verified_bytes':done_bytes,'total_bytes':manifest['total_bytes']}),flush=True)
            reported=done_bytes
    source=remote(CHECK_SOURCE,payload=manifest,timeout=30)
    assert not source['source_files_changed'],source
    result=dict(verified_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        source_hostname=manifest['hostname'],all_sha256_match=True,files_verified=len(verified),
        bytes_verified=done_bytes,source_check=source,remote_sources_deleted=False)
    save(destination/'verification.json',result)
    save(destination/'transfer-progress.json',dict(phase='verified',percent=100,
        copied_bytes=done_bytes,total_bytes=manifest['total_bytes'],files_verified=len(verified),
        all_sha256_match=True,verified_utc=result['verified_utc']))
    print(json.dumps(result),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('inventory','copy','verify','cleanup'))
    parser.add_argument('--delete-verified-source-archives',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'HostTools/rpi_detection/recordings/2026-10-20/M02-archive')
    args=parser.parse_args();destination=args.output.resolve()
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'.gitignore').write_text('*\n!.gitignore\n',encoding='ascii')
    manifest_path=destination/'source-manifest.json'
    if args.action=='inventory':
        assert not manifest_path.exists(),'Use the existing immutable manifest or a new destination'
        manifest=remote(INVENTORY,timeout=180)
        assert manifest['root_device']=='/dev/sda2'
        save(manifest_path,manifest)
        print(json.dumps({'inventory_complete':True,'files':len(manifest['files']),
            'bytes':manifest['total_bytes'],'destination':str(destination)}),flush=True)
        return
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if args.action=='cleanup':
        if not args.delete_verified_source_archives:
            parser.error('Cleanup requires explicit --delete-verified-source-archives')
        report=json.loads((destination/'verification.json').read_text(encoding='utf-8'))
        assert report['all_sha256_match'] and report['files_verified']==len(manifest['files'])
        assert report['bytes_verified']==manifest['total_bytes']
        # Recheck the local backup immediately before the separate cleanup action.
        for row in manifest['files']:
            path=local_path(destination,row['relative'])
            assert path.is_file() and path.stat().st_size==row['size'] and digest(path)==row['sha256'],row['relative']
        payload={**manifest,'local_verification':report,'local_destination':str(destination)}
        result=remote(DELETE_BULK,payload=payload,timeout=600)
        save(destination/'source-cleanup-receipt.json',result)
        report.update(remote_sources_deleted=True,deleted_source_archives=result['source_archives_deleted'],
            source_archive_bytes_removed=result['deleted_bytes'],cleanup_receipt='source-cleanup-receipt.json')
        save(destination/'verification.json',report)
        print(json.dumps({k:v for k,v in result.items() if k not in ('deleted','local_verification')}),flush=True)
        return
    if args.action=='verify':
        verify(destination,manifest);return
    assert shutil.disk_usage(destination).free>manifest['total_bytes']+1024**3,'Insufficient local disk space'
    lines=[]
    for row in manifest['files']:
        final=local_path(destination,row['relative']);final.parent.mkdir(parents=True,exist_ok=True)
        if final.exists():
            assert final.stat().st_size==row['size'] and digest(final)==row['sha256'],'Existing destination differs'
            continue
        part=final.with_name(final.name+'.part')
        assert not part.exists() or part.stat().st_size<=row['size'],'Invalid partial size'
        lines.append('get -a '+quoted(row['remote'])+' '+quoted(part))
    batch=destination/'sftp-batch.txt';batch.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    command=[str(Path(ARGS[0]).with_name('sftp.exe')),'-q','-b',str(batch)]
    if manifest.get('sftp_server'):
        command+=['-s','/usr/bin/nice -n 15 /usr/bin/ionice -c 3 '+manifest['sftp_server']]
    command+=ARGS[1:-1]+[ARGS[-1]]
    started=time.monotonic()
    with (destination/'sftp-stdout.txt').open('wb') as stdout,(destination/'sftp-stderr.txt').open('wb') as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr)
        while process.poll() is None:
            copied=0
            for row in manifest['files']:
                final=local_path(destination,row['relative']);part=final.with_name(final.name+'.part')
                candidate=final if final.exists() else part
                if candidate.exists():copied+=min(row['size'],candidate.stat().st_size)
            progress=dict(phase='copying',copied_bytes=copied,total_bytes=manifest['total_bytes'],
                percent=round(copied*100/manifest['total_bytes'],2),elapsed_s=time.monotonic()-started)
            save(destination/'transfer-progress.json',progress)
            print(json.dumps(progress),flush=True)
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:pass
    if process.returncode:
        raise RuntimeError('SFTP failed: '+(destination/'sftp-stderr.txt').read_text(errors='replace')[-2500:])
    verify(destination,manifest)


if __name__=='__main__':main()
