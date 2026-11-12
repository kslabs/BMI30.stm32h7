"""Own-board eMMC -> its pinned USB. No adoption, network rewriting or EEPROM writes.

Run only as a detached root job: python3 -B clone.py /var/lib/bmi30-usb-owncopy-JOB/plan.json
The same command with --cleanup before the plan is suitable for ExecStopPost.
Private plans, source fingerprints and command logs stay on this device.
"""
from __future__ import annotations
import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
import urllib.request

DOCS = Path('/home/techaid/Documents')
SOURCE_ROOT = '/dev/mmcblk0p2'
SOURCE_BOOT = '/dev/mmcblk0p1'
ACCESS_SHA = 'ec373f37a9231196ff77aad61a7be312b8f8f9b5ee46ca1f411e6cb58fca53fe'
BOARDS = {
    'BMI30-C9775FBAE': ('abdd2dbc9775fbae', '25072933730021'),
    'BMI30-E29805C7C': ('490df72e29805c7c', '25072913740186'),
}
PAUSE_UNITS = ('bmi30-hotspot-info.service', 'bmi30-agent.service', 'bmi30-core.service')
KEEP_UNITS = ('NetworkManager.service', 'ssh.service', 'bmi30-network.service', 'bmi30-access.service')
BASE_EXCLUDES = ['/boot/firmware/***', '/dev/***', '/proc/***', '/sys/***', '/run/***',
                 '/tmp/***', '/mnt/***', '/media/***', '/swapfile', '/var/swap']


class Refused(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Refused(reason)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as stream:
        os.chmod(temporary, 0o600)
        stream.write((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def command(*args, timeout=30, input=None, pass_fds=()):
    result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=timeout,
                            pass_fds=pass_fds, env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        raise Refused('Command failed: ' + Path(args[0]).name)
    return result.stdout.strip()


def flat(nodes):
    for node in nodes:
        yield node
        yield from flat(node.get('children') or ())


def disk_inventory():
    return json.loads(command('lsblk', '-b', '-J', '-p', '-o',
        'NAME,PATH,TYPE,TRAN,SIZE,MODEL,SERIAL,WWN,RO,MAJ:MIN,MOUNTPOINTS'))['blockdevices']


def disk_pin(disk):
    return {'path': disk['path'], 'tran': disk.get('tran'), 'model': str(disk.get('model') or '').strip(),
            'serial': str(disk.get('serial') or '').strip(), 'size': int(disk['size']),
            'wwn': disk.get('wwn'), 'maj_min': disk['maj:min'],
            'diskseq': int(Path('/sys/class/block', Path(disk['path']).name, 'diskseq').read_text().strip())}


def validate_target(plan, nodes, actual_pin, allowed_mounts=()):
    require(plan.get('usb_erase_authorized') is True, 'USB erase is not authorized')
    expected = plan['target']
    require(expected['path'] == '/dev/sda' and expected['tran'] == 'usb', 'Unsupported target')
    require(actual_pin == expected, 'USB identity or current attachment changed')
    require(actual_pin['size'] == 31457280000, 'Unexpected USB capacity')
    require(actual_pin['serial'] == BOARDS[plan['host']][1], 'USB belongs to another board plan')
    require(bool(actual_pin['diskseq']) and bool(actual_pin['maj_min']), 'Missing USB attachment pin')
    usb = [n for n in nodes if n.get('type') == 'disk' and n.get('tran') == 'usb']
    require(len(usb) == 1 and usb[0]['path'] == expected['path'], 'Unexpected additional USB disk')
    target = next(n for n in nodes if n.get('path') == expected['path'])
    for node in flat([target]):
        require(not node.get('ro'), 'Target is read-only')
        for mount in node.get('mountpoints') or ():
            require(not mount or mount in allowed_mounts, 'Target is mounted outside this copy job')


def identity(plan):
    require(not sys.flags.optimize, 'Optimized Python is refused')
    require(os.geteuid() == 0, 'Root required')
    require(plan['host'] in BOARDS, 'Unknown board')
    require((plan['cm5'], plan['target']['serial']) == BOARDS[plan['host']], 'Board/USB pair mismatch')
    require(socket.gethostname() == plan['host'], 'Hostname changed')
    require(Path('/proc/device-tree/serial-number').read_text().strip('\0\n ') == plan['cm5'], 'CM5 changed')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == plan['boot_id'], 'Source rebooted')
    for mount, device, key in (('/', SOURCE_ROOT, 'root_partuuid'), ('/boot/firmware', SOURCE_BOOT, 'boot_partuuid')):
        require(command('findmnt', '-n', '-o', 'SOURCE', mount) == device, 'Source media changed')
        require(command('findmnt', '-n', '-o', 'PARTUUID', mount) == plan[key], 'Source partition changed')
    require(plan['root'] == SOURCE_ROOT and plan['boot'] == SOURCE_BOOT, 'Only own native eMMC source is allowed')


def guard(plan, allowed_mounts=(), descriptor=None):
    identity(plan)
    nodes = disk_inventory()
    disk = next((n for n in nodes if n.get('path') == '/dev/sda'), None)
    require(disk is not None, 'Pinned USB absent')
    validate_target(plan, nodes, disk_pin(disk), allowed_mounts)
    if descriptor is not None:
        st = os.fstat(descriptor)
        require(stat.S_ISBLK(st.st_mode) and st.st_rdev == os.stat('/dev/sda').st_rdev, 'Open USB changed')
    swaps = command('swapon', '--show=NAME', '--noheadings').splitlines()
    require(not any(v.strip().startswith('/dev/sda') for v in swaps), 'USB swap is active')
    return nodes


def rooted(root, relative, follow_final=True):
    """Resolve absolute symlinks against the copied root, never the live host."""
    root = Path(root)
    pending = list(PurePosixPath(str(relative).lstrip('/')).parts)
    current = root
    links = 0
    while pending:
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            require(current != root, 'Symlink escapes copied root')
            current = current.parent
            continue
        candidate = current / part
        if candidate.is_symlink() and (pending or follow_final):
            links += 1
            require(links <= 40, 'Symlink loop')
            link = PurePosixPath(os.readlink(candidate))
            if link.is_absolute():
                current = root
            pending = list(link.parts[1:] if link.is_absolute() else link.parts) + pending
        else:
            current = candidate
    return current


def fingerprint(root, names):
    result = {}
    for name in sorted(set(names)):
        path = rooted(root, name, follow_final=False)
        st = path.lstat()
        info = {'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid}
        if stat.S_ISLNK(st.st_mode):
            info.update(kind='symlink', target=os.readlink(path))
        elif stat.S_ISREG(st.st_mode):
            info.update(kind='file', bytes=st.st_size, sha256=sha(path))
        else:
            raise Refused('Unexpected critical file type')
        result[name] = info
    return result


def critical_names(plan):
    verify_active_selection(plan)
    names = ['etc/hostname', 'etc/hosts', 'etc/machine-id', 'var/lib/dbus/machine-id',
             'home/techaid/Documents/host/bmi30_split_active_version.env',
             'home/techaid/Documents/host/bmi30_config.json']
    for key, item in plan['sources'].items():
        require(Path(item['path']).is_relative_to(DOCS), 'Active source path outside own workspace')
        require(sha(item['path']) == item['sha256'], 'Active source drift')
        names.append(item['path'].lstrip('/'))
    for folder in ('etc/ssh', 'etc/bmi30-agent', 'var/lib/bmi30-agent', 'etc/bmi30-network',
                   'etc/NetworkManager/system-connections', 'home/techaid/.ssh', 'root/.ssh'):
        path = Path('/') / folder
        if path.is_dir():
            names.extend(str(p).lstrip('/') for p in path.rglob('*') if p.is_symlink() or p.is_file())
    for name in ('var/lib/bmi30-network/credentials.json', 'var/lib/bmi30-access/settings.json',
                 'etc/bmi30/portal_config.json'):
        if (Path('/') / name).is_file():
            names.append(name)
    for pattern in ('*.json', '*.env', '*.npz', '*.npy'):
        names.extend(str(p).lstrip('/') for p in (DOCS / 'host').glob(pattern) if p.is_file() or p.is_symlink())
    names.extend(str(p).lstrip('/') for p in Path('/opt/bmi30-network').glob('*.py') if p.is_file())
    return sorted(set(names))


def verify_active_selection(plan):
    selected = {}
    for line in (DOCS / 'host/bmi30_split_active_version.env').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        require(bool(sep) and key not in selected, 'Invalid source selection env')
        values = shlex.split(value)
        require(len(values) == 1, 'Invalid source selection value')
        selected[key] = values[0]
    require(selected.get('BMI30_SPLIT_VERSION') == plan['expected_source_version'], 'Active source version changed')
    for key in ('BMI30_CORE_PATH', 'BMI30_ENGINE_SOURCE'):
        path = Path(selected[key])
        if not path.is_absolute():
            path = DOCS / path
        require(path.resolve() == Path(plan['sources'][key]['path']).resolve(), 'Active source selection changed')
    for item in plan['sources'].values():
        require(sha(item['path']) == item['sha256'], 'Active source hash changed')


def api_status():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8765/api/status', timeout=5) as stream:
        return json.load(stream)


def process(unit):
    pid = int(command('systemctl', 'show', unit, '-p', 'MainPID', '--value'))
    require(pid > 0, 'Required service is not running: ' + unit)
    ticks = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]
    return {'pid': pid, 'start_ticks': ticks}


def access_helpers():
    source = Path('/opt/bmi30-network/access.py').read_bytes()
    require(hashlib.sha256(source).hexdigest() == ACCESS_SHA, 'Maintenance helper source drift')
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in ('claim_busy', 'release_busy')]
    require(len(nodes) == 2, 'Maintenance helpers missing')
    ns = {'Path': Path, 'os': os, 'uuid': uuid, 'json': json, 'time': time}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'verified_maintenance_helpers', 'exec'), ns)
    return ns['claim_busy'], ns['release_busy']


class Lease:
    def __init__(self):
        self.path = Path('/run/bmi30-access/busy')
        self.claim, self.release = access_helpers()
        self.owner = self.claim(self.path)
        require(self.owner is not None, 'Another maintenance operation is active')
        self.stop = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.loop, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.release(self.path, self.owner)
            raise

    def refresh(self):
        refresh_owner(self.path, self.owner)

    def loop(self):
        while not self.stop.wait(2):
            try:
                self.refresh()
            except BaseException as error:
                self.error = error
                return

    def check(self):
        if self.error is not None:
            raise Refused('Maintenance lease lost')
        self.refresh()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
        self.release(self.path, self.owner)


def refresh_owner(path, owner):
    with path.open() as stream:
        value = json.load(stream)
        st = os.fstat(stream.fileno())
    require(value.get('token') == owner[0] and (st.st_dev, st.st_ino) == tuple(owner[1:]), 'Maintenance owner changed')
    current = path.stat()
    require((current.st_dev, current.st_ino) == tuple(owner[1:]), 'Maintenance inode changed')
    os.utime(path, None)


class Copy:
    def __init__(self, plan, job):
        self.plan, self.job = plan, job
        self.mount = job / 'mount'
        self.boot = self.mount / 'boot/firmware'
        self.stage_name = 'preparing'
        self.started = time.time()
        self.lease = None
        self.last_owner = None
        self.paused = []
        self.command_number = 0
        self.excludes = BASE_EXCLUDES + ['/var/lib/bmi30-usb-owncopy-*/***']
        self.root_uuid = self.boot_uuid = None
        self.runtime_at_pause = None
        for name in command('swapon', '--show=NAME', '--noheadings').splitlines():
            if name.strip().startswith('/') and not name.strip().startswith('/dev/'):
                self.excludes.append(name.strip())

    def stage(self, name, **extra):
        self.stage_name = name
        atomic_json(self.job / 'status.json', {'job_id': self.plan['job_id'], 'host': self.plan['host'],
            'stage': name, 'source': SOURCE_ROOT, 'target': '/dev/sda', 'usb_serial': self.plan['target']['serial'],
            'started_at': self.started, 'updated_at': time.time(), 'elapsed_s': round(time.time() - self.started, 1),
            'source_pid': os.getpid(), 'boot_tested': False, 'safe_to_remove': False, **extra})

    def run(self, label, args, timeout=7200, input=None, pass_fds=(), allowed_exit=(0,), check_lease=True):
        self.command_number += 1
        path = self.job / ('%03d-%s.log' % (self.command_number, label))
        with path.open('x') as log:
            os.chmod(path, 0o600)
            proc = subprocess.Popen(args, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, text=True, pass_fds=pass_fds,
                start_new_session=True, env={**os.environ, 'LC_ALL': 'C'})
            if input is not None:
                proc.stdin.write(input)
                proc.stdin.close()
            started = time.monotonic()
            heartbeat = started
            try:
                while proc.poll() is None:
                    require(time.monotonic() - started < timeout, 'Step timed out: ' + label)
                    if self.lease and check_lease:
                        self.lease.check()
                    if time.monotonic() - heartbeat >= 2:
                        self.stage(self.stage_name, step=label, step_elapsed_s=round(time.monotonic() - started, 1))
                        heartbeat = time.monotonic()
                    time.sleep(.3)
                require(proc.returncode in allowed_exit, 'Step failed: ' + label)
            finally:
                if proc.poll() is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=5)
        return path

    def root_sync(self, label, allow_vanished=False):
        self.check_mounts()
        args = ['ionice', '-c', '3', 'nice', '-n', '15', 'rsync', '-aHAXx', '--numeric-ids',
                '--delete-delay', '--no-inc-recursive', '--info=progress2', '--stats', '--outbuf=L']
        args += ['--exclude=' + value for value in self.excludes] + ['/', str(self.mount) + '/']
        # A vanished live file is a failed bulk pass, not a silently verified copy.
        return self.run(label, args, allowed_exit=(0, 24) if allow_vanished else (0,))

    def check_mounts(self):
        guard(self.plan, (str(self.mount), str(self.boot)))
        for path, value in ((self.mount, self.root_uuid), (self.boot, self.boot_uuid)):
            require(os.path.ismount(path), 'USB mount disappeared')
            require(command('findmnt', '-n', '-o', 'UUID', '-M', str(path)) == value, 'Wrong mounted target filesystem')

    def pause(self):
        require(self.lease is not None, 'Maintenance lease missing')
        self.lease.check()
        for unit in PAUSE_UNITS:
            if subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0:
                if unit == 'bmi30-core.service':
                    status = api_status()
                    require(status.get('ok') and not status.get('stale') and status['connection']['connected'], 'Core not healthy before final pause')
                    require(status['detector']['worker']['alive'], 'Worker not alive before final pause')
                    require(status['service']['engine_source'] == self.plan['sources']['BMI30_ENGINE_SOURCE']['path'], 'Running engine differs from plan')
                    self.runtime_at_pause = {'mode': status['mode'], 'settings': status['detector']['settings'],
                        'sync': status.get('sync'),
                        'channel_policy': {ch: {key: status['detector']['channels'][ch][key] for key in
                            ('enabled', 'confirm_count', 'threshold', 'threshold_high', 'auto_threshold', 'burst_gate', 'burst_blank_s', 'burst_max_ratio')}
                            for ch in ('upper', 'lower')}}
                self.paused.append(unit)
                self.save_pause_state()
                command('systemctl', 'stop', unit, timeout=40)
                require(command('systemctl', 'show', unit, '-p', 'MainPID', '--value') == '0', 'Writer failed to stop')
        require(self.runtime_at_pause is not None, 'Core must be active before final pause')

    def save_pause_state(self):
        atomic_json(self.job / 'pause-state.json', {'job_id': self.plan['job_id'], 'units': list(self.paused),
            'owner': self.lease.owner if self.lease else self.last_owner if self.paused else None,
            'worker': process_start(os.getpid()),
            'resumed': not self.paused})

    def resume(self):
        if self.paused and self.lease is None:
            # Keep proof for ExecStopPost; never bypass a lost maintenance owner.
            self.save_pause_state()
            return list(self.paused)
        errors = []
        for unit in reversed(self.paused):
            try:
                self.lease.refresh()
                command('systemctl', 'start', unit, timeout=40)
            except Exception:
                errors.append(unit)
        self.paused = [unit for unit in self.paused if unit in errors]
        self.save_pause_state()
        return errors

    def ensure_directories(self):
        for name in ('dev', 'proc', 'sys', 'run', 'mnt', 'media', 'tmp'):
            path = self.mount / name
            require(not path.is_symlink(), 'Virtual mountpoint became symlink')
            path.mkdir(exist_ok=True)
            path.chmod(0o1777 if name == 'tmp' else 0o755)
            os.chown(path, 0, 0)

    def rewrite_fstab(self, disk_id):
        self.check_mounts()
        path = self.mount / 'etc/fstab'
        require(not path.is_symlink(), 'Unexpected fstab symlink')
        lines, found = path.read_text().splitlines(), set()
        for index, line in enumerate(lines):
            fields = line.split()
            if fields and not line.lstrip().startswith('#') and len(fields) > 1 and fields[1] in ('/', '/boot/firmware'):
                require(fields[1] not in found, 'Duplicate boot mount entry')
                found.add(fields[1])
                fields[0] = 'PARTUUID=' + disk_id + ('-02' if fields[1] == '/' else '-01')
                lines[index] = '\t'.join(fields)
        require(found == {'/', '/boot/firmware'}, 'Missing root/boot fstab entries')
        path.write_text('\n'.join(lines) + '\n')

    def activate_boot(self, disk_id):
        """Keep fresh FAT unbootable until verified root and complete staged boot."""
        self.check_mounts()
        require(not any(self.boot.iterdir()), 'Fresh FAT unexpectedly contains boot files')
        stage = self.boot / ('.bmi30-copy-stage-' + self.plan['job_id'])
        stage.mkdir()
        self.run('boot-stage', ['rsync', '-rt', '--delay-updates', '--modify-window=1', '/boot/firmware/', str(stage) + '/'], timeout=600)
        log = self.run('boot-stage-checksum', ['rsync', '-rcn', '--delete', '--out-format=%i|%n', '/boot/firmware/', str(stage) + '/'], timeout=600)
        require(not log.read_text().strip(), 'Staged boot checksum differs')
        cmdline = stage / 'cmdline.txt'
        require(cmdline.is_file() and not cmdline.is_symlink(), 'Invalid staged cmdline')
        text, count = re.subn(r'(?<!\S)root=\S+', 'root=PARTUUID=' + disk_id + '-02', cmdline.read_text())
        require(count == 1, 'Ambiguous root boot reference')
        cmdline.write_text(text)
        files = [p for p in stage.rglob('*') if p.is_file()]
        holds = {p.relative_to(stage).as_posix() for p in files if
                 re.fullmatch(r'(kernel.*\.img(?:\.gz)?|vmlinuz.*|start.*\.elf)', p.name)}
        for config in (p for p in files if p.suffix.lower() in ('.txt', '.cfg')):
            for name in re.findall(r'^\s*(?:kernel|start_file)\s*=\s*([^#\s]+)', config.read_text(errors='replace'), re.M):
                rel = PurePosixPath(name)
                require(not rel.is_absolute() and '..' not in rel.parts and (stage / name).is_file(), 'Unsupported boot executable path')
                holds.add(rel.as_posix())
        require(any(name.endswith(('.img', '.img.gz')) or Path(name).name.startswith('vmlinuz') for name in holds), 'No known Linux kernel activation point')
        for path in files:
            rel = path.relative_to(stage)
            if rel.as_posix() in holds:
                continue
            target = self.boot / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
        self.run('sync-boot-before-kernels', ['sync', '-f', str(self.boot)], timeout=300)
        self.check_mounts()
        # Firmware helpers first, actual kernel image(s) last. Root is already verified.
        for name in sorted(holds, key=lambda v: (not v.endswith('.elf'), v)):
            target = self.boot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / name, target)
        for path in sorted((p for p in stage.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            path.rmdir()
        stage.rmdir()
        log = self.run('boot-active-checksum', ['rsync', '-rcn', '--delete', '--exclude=/cmdline.txt', '--out-format=%i|%n', '/boot/firmware/', str(self.boot) + '/'], timeout=600)
        require(not log.read_text().strip(), 'Activated boot checksum differs')
        require(re.findall(r'(?<!\S)root=(\S+)', (self.boot / 'cmdline.txt').read_text()) == ['PARTUUID=' + disk_id + '-02'], 'USB root boot reference differs')

    def verify_tree(self, relative, excludes=()):
        source = rooted(Path('/'), relative)
        target = rooted(self.mount, relative)
        args = ['ionice', '-c', '3', 'nice', '-n', '15', 'rsync', '-aHAXxnci', '--numeric-ids',
                '--delete', '--out-format=%i|%n%L']
        args += ['--exclude=' + name for name in excludes] + [str(source) + '/', str(target) + '/']
        log = self.run('verify-' + relative.replace('/', '-'), args, timeout=1800)
        changes = [line for line in log.read_text(errors='replace').splitlines() if '|' in line]
        bad = [line for line in changes if not (line.startswith('.f..t......|') or line.startswith('.d..t......|'))]
        require(not bad, 'Content/ownership/link verification differs: ' + relative)

    def unmount(self):
        errors = []
        for path in (self.boot, self.mount):
            if os.path.ismount(path):
                try:
                    self.run('unmount-' + path.name, ['umount', str(path)], timeout=120, check_lease=False)
                except BaseException:
                    errors.append(str(path))
        return errors

    def execute(self):
        guard(self.plan)
        verify_active_selection(self.plan)
        for tool in ('rsync', 'sfdisk', 'mkfs.vfat', 'mkfs.ext4', 'e2fsck', 'fsck.fat', 'ionice', 'nice'):
            require(shutil.which(tool) is not None, 'Required tool absent: ' + tool)
        self.stage('preparing')
        keep = {unit: process(unit) for unit in KEEP_UNITS}
        size = os.statvfs('/')
        required = int((size.f_blocks - size.f_bfree) * size.f_frsize * 1.05) + 1024 ** 3
        require(required + 513 * 1024 ** 2 < self.plan['target']['size'], 'Insufficient USB capacity')
        with contextlib.ExitStack() as locks:
            for path, kind in ((Path('/var/lib/bmi30-usb-owncopy.lock'), 'flock'),
                (DOCS / '.bmi30_cloud_sync/server_operation.lock', 'flock'),
                (Path('/var/lib/dpkg/lock-frontend'), 'lockf'), (Path('/var/lib/dpkg/lock'), 'lockf')):
                path.parent.mkdir(parents=True, exist_ok=True)
                stream = locks.enter_context(path.open('a'))
                (fcntl.flock if kind == 'flock' else fcntl.lockf)(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lease = Lease()
            self.last_owner = self.lease.owner
            self.save_pause_state()
            existing = command('lsblk', '-nr', '-o', 'PARTUUID,UUID').lower().split()
            disk_id = uuid.uuid4().hex[:8]
            while disk_id == '00000000' or any(v.startswith(disk_id) for v in existing):
                disk_id = uuid.uuid4().hex[:8]
            root_uuid, fat_id = str(uuid.uuid4()), uuid.uuid4().hex[:8]
            while root_uuid.lower() in existing:
                root_uuid = str(uuid.uuid4())
            while (fat_id[:4] + '-' + fat_id[4:]).lower() in existing:
                fat_id = uuid.uuid4().hex[:8]
            self.root_uuid = root_uuid
            self.boot_uuid = (fat_id[:4] + '-' + fat_id[4:]).upper()
            descriptor = os.open('/dev/sda', os.O_RDWR | os.O_CLOEXEC)
            locks.callback(os.close, descriptor)
            guard(self.plan, descriptor=descriptor)
            self.stage('formatting')
            self.run('partition', ['sfdisk', '--lock=yes', '--wipe', 'always', '/proc/self/fd/' + str(descriptor)],
                timeout=60, input='label: dos\nlabel-id: 0x' + disk_id + '\n\nstart=1MiB,size=512MiB,type=c,bootable\nstart=513MiB,type=83\n', pass_fds=(descriptor,))
            command('udevadm', 'settle', timeout=30)
            for index, tool in ((1, 'mkfs.vfat'), (2, 'mkfs.ext4')):
                guard(self.plan, descriptor=descriptor)
                part = '/dev/sda' + str(index)
                require(command('lsblk', '-dn', '-o', 'PKNAME', part) == 'sda', 'Partition belongs to another disk')
                require(command('blkid', '-s', 'PARTUUID', '-o', 'value', part).lower() == disk_id + '-0' + str(index), 'Partition identity differs')
                fd = os.open(part, os.O_RDWR | os.O_CLOEXEC)
                locks.callback(os.close, fd)
                guard(self.plan, descriptor=descriptor)
                require(stat.S_ISBLK(os.fstat(fd).st_mode) and os.fstat(fd).st_rdev == os.stat(part).st_rdev, 'Open partition changed')
                args = [tool, '-F', '32', '-i', fat_id, '-n', 'BMI30BOOT'] if index == 1 else [tool, '-F', '-m', '1', '-U', root_uuid, '-L', 'BMI30ROOT', '-E', 'lazy_itable_init=0,lazy_journal_init=0']
                self.run('format-' + str(index), args + ['/proc/self/fd/' + str(fd)], timeout=180, pass_fds=(fd,))
            guard(self.plan, descriptor=descriptor)
            require(command('blkid', '-s', 'UUID', '-o', 'value', '/dev/sda2') == root_uuid, 'Root UUID mismatch')
            require(command('blkid', '-s', 'UUID', '-o', 'value', '/dev/sda1') == self.boot_uuid, 'Boot UUID mismatch')
            atomic_json(self.job / 'target-filesystems.json', {'root_uuid': self.root_uuid, 'boot_uuid': self.boot_uuid})
            self.mount.mkdir(mode=0o700)
            command('mount', '-U', root_uuid, str(self.mount))
            self.boot.mkdir(parents=True)
            command('mount', '-U', self.boot_uuid, str(self.boot))
            allowed = (str(self.mount), str(self.boot))
            guard(self.plan, allowed, descriptor)
            self.stage('copying_live')
            self.root_sync('bulk-root', allow_vanished=True)
            self.stage('final_copy_paused')
            self.pause()
            try:
                guard(self.plan, allowed, descriptor)
                self.root_sync('final-root')
                self.ensure_directories()
                names = critical_names(self.plan)
                frozen = fingerprint(Path('/'), names)
                atomic_json(self.job / 'critical-fingerprints.json', frozen)
                self.stage('verifying_paused', critical_file_count=len(names))
                for folder in ('etc', 'opt', 'usr/local', 'var/lib/dpkg', 'home/techaid/Documents/host/bmi30_active_runtime/project'):
                    self.verify_tree(folder, ('/fstab', '/fake-hwclock.data', '/resolv.conf') if folder == 'etc' else ())
                require(fingerprint(self.mount, names) == frozen, 'Own settings/identity/active code differs on USB')
                require(fingerprint(Path('/'), names) == frozen, 'Source settings changed during final verification')
                self.rewrite_fstab(disk_id)
                self.run('sync-root-before-boot', ['sync', '-f', str(self.mount)], timeout=900)
                self.stage('activating_boot_paused')
                self.activate_boot(disk_id)
                guard(self.plan, allowed, descriptor)
                self.stage('flushing_paused')
                self.run('sync-boot', ['sync', '-f', str(self.boot)], timeout=300)
                self.run('sync-root', ['sync', '-f', str(self.mount)], timeout=900)
                require(not self.unmount(), 'USB unmount failed')
                self.run('fsck-root-readonly', ['e2fsck', '-fn', '/dev/sda2'], timeout=300)
                self.run('fsck-boot-readonly', ['fsck.fat', '-n', '/dev/sda1'], timeout=120)
                require(fingerprint(Path('/'), names) == frozen, 'Source changed before writer restart')
            finally:
                # Normal failures never restore old source config; restart only units we paused.
                unmount_errors = self.unmount()
                resume_errors = self.resume()
                if self.lease:
                    self.lease.close()
                    self.lease = None
                    self.save_pause_state()
                require(not unmount_errors and not resume_errors, 'Cleanup requires operator attention')
            require({unit: process(unit) for unit in KEEP_UNITS} == keep, 'Network/SSH/access process changed')
            result = {'ok': True, 'host': self.plan['host'], 'job_id': self.plan['job_id'],
                'cm5': self.plan['cm5'], 'source_root_partuuid': self.plan['root_partuuid'],
                'source_boot_partuuid': self.plan['boot_partuuid'], 'source_version': self.plan['expected_source_version'],
                'source': SOURCE_ROOT, 'target': '/dev/sda', 'usb_serial': self.plan['target']['serial'],
                'root_partuuid': disk_id + '-02', 'boot_partuuid': disk_id + '-01', 'root_uuid': root_uuid,
                'boot_uuid': self.boot_uuid, 'runtime_at_final_pause': self.runtime_at_pause,
                'final_runtime': self.runtime_at_pause,
                'copied': True, 'critical_file_count': len(names), 'own_identity_settings_verified': True,
                'network_profiles_personalized': False, 'source_config_restored_from_backup': False,
                'source_active_hashes': {key: item['sha256'] for key, item in self.plan['sources'].items()},
                'source_config_sha256_at_final_sync': frozen['home/techaid/Documents/host/bmi30_config.json']['sha256'],
                'source_env_sha256_at_final_sync': frozen['home/techaid/Documents/host/bmi30_split_active_version.env']['sha256'],
                'network_source_hashes_at_final_sync': {name: item['sha256'] for name, item in frozen.items()
                    if name.startswith('opt/bmi30-network/') and item['kind'] == 'file'},
                'network_ssh_access_processes_unchanged': True, 'verified_copy': True, 'safe_to_remove': True,
                'source_runtime_reverified': False, 'source_runtime_postcheck_required': True,
                'boot_tested': False, 'finished_at': time.time()}
            atomic_json(self.job / 'result.json', result)
            self.stage('complete', result=result, safe_to_remove=True)
            return result


def process_start(pid):
    return {'pid': pid, 'start_ticks': Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]}


def cleanup(plan, job):
    """ExecStopPost safety net: resume only this dead worker's recorded units."""
    identity(plan)
    path = job / 'pause-state.json'
    if not path.is_file():
        return
    saved = json.loads(path.read_text())
    require(saved.get('job_id') == plan['job_id'], 'Foreign pause state')
    worker = saved.get('worker')
    if worker:
        with contextlib.suppress(FileNotFoundError, ProcessLookupError):
            require(process_start(worker['pid']) != worker, 'Copy worker is still running')
    require(set(saved.get('units', ())) <= set(PAUSE_UNITS), 'Unexpected paused service')
    busy = Path('/run/bmi30-access/busy')
    claim, release = access_helpers()
    owner = tuple(saved['owner']) if saved.get('owner') else None
    if not owner:
        require(not saved.get('units'), 'Paused writers have no maintenance owner')
        return
    if not busy.exists():
        owner = claim(busy)
        require(owner is not None, 'Another maintenance owner appeared')
    refresh_owner(busy, owner)
    errors = []
    try:
        for unit in reversed(saved.get('units', ())):
            try:
                refresh_owner(busy, owner)
                command('systemctl', 'start', unit, timeout=40)
            except Exception:
                errors.append(unit)
        filesystems = job / 'target-filesystems.json'
        if filesystems.is_file():
            expected = json.loads(filesystems.read_text())
            for path, value in ((job / 'mount/boot/firmware', expected['boot_uuid']), (job / 'mount', expected['root_uuid'])):
                if os.path.ismount(path):
                    try:
                        require(command('findmnt', '-n', '-o', 'UUID', '-M', str(path)) == value, 'Foreign filesystem on cleanup mount')
                        command('umount', str(path), timeout=30)
                    except Exception:
                        errors.append('unmount:' + str(path))
    finally:
        release(busy, owner)
    require(not errors, 'Writer restart requires operator attention')
    atomic_json(path, {'job_id': plan['job_id'], 'units': [], 'resumed': True, 'exec_stop_post': True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cleanup', action='store_true')
    parser.add_argument('plan', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    plan = json.loads(args.plan.read_text())
    require(isinstance(plan['job_id'], str) and len(plan['job_id']) <= 64 and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', plan['job_id']), 'Invalid job ID')
    job = Path('/var/lib/bmi30-usb-owncopy-' + plan['job_id'])
    require(args.plan == job / 'plan.json' and not args.plan.is_symlink(), 'Unexpected plan location')
    require(job.is_dir() and not job.is_symlink() and job.stat().st_uid == 0 and job.stat().st_mode & 0o077 == 0, 'Untrusted job directory')
    require(args.plan.stat().st_uid == 0 and args.plan.stat().st_mode & 0o077 == 0, 'Untrusted plan permissions')
    if args.cleanup:
        cleanup(plan, job)
        return
    require(Path(__file__).resolve().parent == job, 'Worker must be staged inside its own job')
    copy = Copy(plan, job)
    atomic_json(job / 'source.pid', process_start(os.getpid()))
    def interrupted(signum, frame):
        raise Refused('Copy interrupted by signal')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        result = copy.execute()
        print(json.dumps(result), flush=True)
    except BaseException as error:
        # Cover failures before/inside the final pause and preserve private command logs.
        unmount_errors = copy.unmount()
        resume_errors = copy.resume()
        if copy.lease:
            with contextlib.suppress(Exception):
                copy.lease.close()
                copy.lease = None
                copy.save_pause_state()
        copy.stage('failed', error_type=type(error).__name__, reason=str(error) if isinstance(error, Refused) else 'See private device logs',
                   unmount_errors=unmount_errors, resume_errors=resume_errors)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
