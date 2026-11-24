"""C7552 paired KLU8 replication and Sparse 1.3 comparison."""
from bisect import bisect_left
import datetime
import fcntl
import json
import os
import re
import traceback

import experiment as e

NAME = 'c7552'
THREADS = 8
HISTORY = '20261114-055440-34321'
STOP = e.STOP[NAME]

def compare_solvers(klu, sparse):
    """Compare saved voltages on both native grids over their common interval."""
    names, a = e.adapter.read_raw(klu/'waveform.raw')
    other, b = e.adapter.read_raw(sparse/'waveform.raw')
    assert names == other
    max_error = 0
    max_scaled_error = 0
    count = 0
    for reference, trial in ((a,b),(b,a)):
        grid = [row[0] for row in reference]
        for t,*voltages in trial:
            if t < grid[0] or t > grid[-1]:
                continue
            i = bisect_left(grid,t)
            lo = max(0,i-1)
            weight = (t-grid[lo])/(grid[i]-grid[lo]) if lo != i else 0
            for j,v in enumerate(voltages,1):
                expected = reference[lo][j] + weight*(reference[i][j]-reference[lo][j])
                error = abs(v-expected)
                max_error = max(max_error,error)
                max_scaled_error = max(max_scaled_error,error/(0.002+0.01*max(abs(v),abs(expected))))
            count += 1
    return {'samples_compared':count,'overlap_s':[max(a[0][0],b[0][0]),min(a[-1][0],b[-1][0])],
            'max_error_v':max_error,'absolute_tolerance_v':0.002,'relative_tolerance':0.01,
            'max_scaled_error':max_scaled_error,'passed':count>0 and max_scaled_error<=1}

