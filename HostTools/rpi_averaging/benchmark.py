"""Measure averaging CPU time in an isolated process on the Raspberry."""
import json
import time
import numpy as np
from bmi30_averaging import RollingMean


def benchmark(iterations=10000):
    results = []
    for length in (200,600):
        data = np.arange(length,dtype=np.int32) + 32768
        for depth in (1,2,4,8,12):
            ring = RollingMean()
            for i in range(40):ring.push((i//2)&1,i&1,data,depth,'bench')
            start = time.process_time()
            for i in range(iterations):ring.push((i//2)&1,i&1,data,depth,'bench')
            elapsed = time.process_time()-start
            results.append({'samples':length,'depth':depth,'cpu_us_per_packet':elapsed*1e6/iterations,
                            'memory_bytes':ring.state(depth)['bytes'],
                            'one_core_percent_at_100_packets_s':elapsed/iterations*100*100})
    return results


if __name__ == '__main__':print(json.dumps(benchmark()))
