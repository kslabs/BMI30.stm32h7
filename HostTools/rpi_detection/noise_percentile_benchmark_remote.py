"""Measure old/new accumulator cost in isolation on M02 without live mutations."""
import ast
from collections import deque
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from types import MethodType,SimpleNamespace

import numpy as np

assert subprocess.check_output(['hostname'],text=True).strip()=='BMI30-E29805C7C'
C=Path('/home/techaid/Documents/.tmp/noise-percentile-20261015/check')
result=[]
for size in (125,250):
    row={'history_samples':size}
    for label,name in [('old_mean','base_engine.py'),('mean_p95','engine.py')]:
        tree=ast.parse((C/name).read_text())
        names={'_rt_reference_noise_statistics','_det_accumulated_reference_noise'}
        nodes=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in names]
        ns=dict(np=np,time=time,deque=deque)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),name,'exec'),ns)
        s=SimpleNamespace(_det_noise_window_seconds=lambda:5.)
        s._det_noise_hist0=deque([(100+j*5/size,float(100+(j*17)%1000)) for j in range(size)])
        f=MethodType(ns['_det_accumulated_reference_noise'],s)
        times=[]
        for j in range(500):
            before=time.perf_counter_ns();f(0,100+(j*23)%2000,now=105+j*5/size);times.append((time.perf_counter_ns()-before)/1000)
        row[label]={'median_us':statistics.median(times),'p95_us':float(np.percentile(times,95))}
    result.append(row)
print(json.dumps({'benchmarks':result,'numpy_version':np.__version__}))
