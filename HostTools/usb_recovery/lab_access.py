"""One-shot restoration of the existing bench public key on an identified USB root.

This diagnostic helper is not part of a distributed firmware bundle. It never
mounts or writes eMMC, and refuses any different physical board or USB image.
"""
import base64,hashlib,json,os,pathlib,re,subprocess,uuid
P=pathlib.Path

def restore(root,config,actual_serial,source,partuuid):
    root=P(root)
    if (not re.fullmatch('[0-9a-f]{16}',actual_serial) or not int(actual_serial,16)
        or actual_serial!=config['physical_serial']):raise RuntimeError('Wrong physical board')
    if not re.fullmatch('/dev/sd[a-z]+2',source) or partuuid!=config['root_partuuid']:
        raise RuntimeError('Not the selected recovery USB')
    key=config['public_key'].strip();fields=key.split()
    if len(fields)<2 or fields[0]!='ssh-ed25519':raise RuntimeError('Unexpected public key type')
    wire=base64.b64decode(fields[1],validate=True)
    if len(wire)!=51 or wire[:19]!=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':raise RuntimeError('Invalid public key')
    record=next(line.split(':') for line in (root/'etc/passwd').read_text().splitlines() if line.startswith('techaid:'))
    uid,gid=int(record[2]),int(record[3]);home=root/'home/techaid'
    for path in (root/'home',home,home/'.ssh',home/'.ssh/authorized_keys'):
        if path.is_symlink():raise RuntimeError('Refusing a linked account path')
    if not home.is_dir():raise RuntimeError('Account home is unavailable')
    directory=home/'.ssh';directory.mkdir(mode=0o700,exist_ok=True)
    target=directory/'authorized_keys';before=target.read_text() if target.is_file() else ''
    already=any(fields[1] in line.split() for line in before.splitlines() if not line.lstrip().startswith('#'))
    if not already:
        after=before.rstrip()+'\n'+fields[0]+' '+fields[1]+' bmi30-bench-recovery\n'
        temp=target.with_name('authorized_keys.'+uuid.uuid4().hex)
        with temp.open('w') as stream:stream.write(after);stream.flush();os.fsync(stream.fileno())
        os.chown(temp,uid,gid);temp.chmod(0o600);temp.replace(target)
    os.chown(home,uid,gid);home.chmod((home.stat().st_mode&0o777|0o700)&~0o022)
    os.chown(directory,uid,gid);directory.chmod(0o700)
    os.chown(target,uid,gid);target.chmod(0o600)
    return {'ok':True,'physical_serial':actual_serial,'root_partuuid':partuuid,
            'public_key_present':True,'key_added':not already,'emmc_written':False,
            'fingerprint':'SHA256:'+base64.b64encode(hashlib.sha256(wire).digest()).decode().rstrip('=')}

def main():
    config=json.loads(P('/etc/bmi30-lab-access.json').read_text())
    serial=P('/proc/device-tree/serial-number').read_text().strip('\0\n').lower()
    source=subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip()
    partuuid=subprocess.check_output(['findmnt','-n','-o','PARTUUID','/'],text=True).strip()
    result=restore(P('/'),config,serial,source,partuuid)
    folder=P('/var/lib/bmi30-lab-access');folder.mkdir(mode=0o700,exist_ok=True)
    (folder/'result.json').write_text(json.dumps(result))
    # The key remains as the restored bench access; this helper is not run on
    # later boots or copied native systems.
    P('/etc/systemd/system/multi-user.target.wants/bmi30-lab-access.service').unlink(missing_ok=True)

if __name__=='__main__':main()
