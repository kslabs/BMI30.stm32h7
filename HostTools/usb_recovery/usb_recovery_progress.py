"""Elapsed time uses the current boot's monotonic clock, never NTP wall time."""
import math,time

def fields(previous, *, bytes_copied=None, finished=False, now_mono=None, now_wall=None):
    now=time.monotonic() if now_mono is None else now_mono
    wall=time.time() if now_wall is None else now_wall
    start=previous.get('started_mono',now)
    if not isinstance(start,(int,float)) or not math.isfinite(start) or start>now:start=now
    count=previous.get('bytes_copied',0) if bytes_copied is None else bytes_copied
    count=max(0,int(count))
    elapsed=max(0,now-start)
    result={'started_mono':start,'started_at':previous.get('started_at',wall),
            'elapsed_s':round(elapsed,3),'bytes_copied':count,
            'average_bytes_per_s':round(count/elapsed,3) if elapsed>0 and count else None}
    if finished:result['finished_at']=wall
    return result
