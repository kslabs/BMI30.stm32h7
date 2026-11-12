"""Bounded, observable final filesystem flush and ordinary unmount."""
import os,subprocess,time
from usb_recovery_common import Refused

class FinishTimeout(Refused):
    def __init__(self,operation,seconds):
        super().__init__('Finishing the write timed out. The drive is not ready for removal.')
        self.operation=operation;self.timeout_s=seconds

def stop(process):
    if process.poll() is None:
        process.kill()
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:pass

def run_step(args,operation,timeout,heartbeat):
    started=time.monotonic();deadline=started+timeout
    process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             env={**os.environ,'LC_ALL':'C'})
    try:
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise FinishTimeout(operation,timeout)
            try:result=process.wait(timeout=min(2,remaining))
            except subprocess.TimeoutExpired:
                heartbeat(round(time.monotonic()-started,1));continue
            if result:raise Refused('The final filesystem operation failed. The drive is not ready for removal.')
            return
    finally:stop(process)

def finish(job,boot,root,report,*,sync_boot_timeout=300,sync_root_timeout=900,unmount_timeout=120):
    steps=(('sync-boot',['sync','-f',str(boot)],sync_boot_timeout,97,'Saving the boot partition…'),
           ('sync-root',['sync','-f',str(root)],sync_root_timeout,98,'Saving system data to the drive…'),
           ('unmount-boot',['umount',str(boot)],unmount_timeout,99,'Unmounting the boot partition…'),
           ('unmount-root',['umount',str(root)],unmount_timeout,99,'Unmounting the system partition…'))
    for operation,args,timeout,progress,message in steps:
        def heartbeat(elapsed):
            report(job,'finishing',progress,message,finishing_step=operation,
                   step_elapsed_s=elapsed,step_timeout_s=timeout)
        heartbeat(0)
        run_step(args,operation,timeout,heartbeat)
    if os.path.ismount(boot) or os.path.ismount(root):
        raise Refused('The drive is still mounted and is not ready for removal.')

def cleanup_unmount(path):
    """Never detach lazily, and never let a failed-device cleanup wait forever."""
    if not os.path.ismount(path):return
    try:run_step(['umount',str(path)],'cleanup-unmount',30,lambda elapsed:None)
    except (Refused,OSError):pass
