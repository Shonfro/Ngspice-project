#!/usr/bin/env python3
"""Independent 2 ns profiles; profile runs are never included in formal timing."""
import os, sys
from pathlib import Path

if any(arg in ('-h', '--help') for arg in sys.argv[1:]):
    print('usage: run_profiles.py')
    print('Run independent 2 ns A/B/C profile diagnostics; output is excluded from formal timing.')
    raise SystemExit(0)

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'code/core'))
import experiment as e
ELF=e.ELF
LIBS={'A':None,'B':Path(os.environ.get('NGSPICE_JEMALLOC', ROOT/'source/dependencies/allocators/jemalloc-5.3.0/lib/libjemalloc.so.2')),
      'C':Path(os.environ.get('NGSPICE_TCMALLOC', ROOT/'source/dependencies/allocators/gperftools-2.7/lib/libtcmalloc_minimal.so.4.5.3'))}
out=ROOT/'result/dynamic_library/profile'
out.mkdir(parents=True,exist_ok=True)
os.sched_setaffinity(0,{16})
for key,lib in LIBS.items():
    folder=out/key
    e.simulation('c7552',8,2,ELF,folder,record=True,solver='klu',preload=lib)
    perfdata=folder/'perf.data'
    env=dict(os.environ,PATH=str(e.PERF.parent)+':'+os.environ['PATH'])
    e.command([e.PERF,'report','--stdio','-i',perfdata,'--sort','dso,symbol'],folder/'perf-report.txt',env)
    e.command([e.PERF,'script','-i',perfdata],folder/'perf-script.txt',env)
