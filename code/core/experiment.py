#!/usr/bin/env python3
"""Sequential ngspice profiling, paired screening and blocked validation."""
import collections
import csv
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import statistics as stats
import subprocess as sp
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT.parent / 'eda/bolt'
SUITE = Path('/opt/eda_public/benchmark-suite/2026.10.28-1')
ELF = OLD / 'src/ngspice/upstream-20261107-231732/install/bin/ngspice'
BOLT = OLD / 'build-bolt-upstream-22.1.8/bin'
PERF = OLD.parent / 'toolchain/perf/3.10.0-1160/usr/bin/perf'
CPUS = set(range(16))
MONITOR = 16
EVENT = 'cpu-clock:u'
STOP = {'c7552': 20}
BOLT_FLAGS = ['--relocs', '--reorder-blocks=ext-tsp', '--reorder-functions=hfsort+',
              '--split-functions', '--split-all-cold', '--dyno-stats', '--print-profile-stats', '-v=1']
# Use the adapter shipped alongside this module.
from edabench import ngspice as adapter

def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, indent=2, default=str) + '\n')
    temp.replace(path)

def sha(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()

def command(args, path, env=None):
    with path.open('w') as handle:
        sp.run([str(x) for x in args], stdout=handle, stderr=sp.STDOUT, env=env, check=True)

def get(path):
    return path.read_text(errors='replace')

def _expand_cpus(spec):
    for part in spec.split(','):
        lo, _, hi = part.partition('-')
        yield from range(int(lo), int(hi or lo)+1)

def monitor(pid):
    proc = Path('/proc') / str(pid)
    if not proc.exists():
        return {}
    pages = collections.Counter()
    anonymous_pages = collections.Counter()
    for line in get(proc/'numa_maps').splitlines():
        for node, count in re.findall(r'\bN(\d+)=(\d+)',line):
            pages[node] += int(count)
            if re.search(r"\banon=\d+",line):
                anonymous_pages[node] += int(count)
    status = {}
    for thread in (proc/'task').iterdir():
        data = dict(line.split(':', 1) for line in get(thread/'status').splitlines() if ':' in line)
        status[thread.name] = data.get('Cpus_allowed_list','').strip()
    return {'time': time.time(), 'pid': pid, 'exe': str((proc/'exe').resolve()),
            'thread_affinity': status, 'numa_pages': pages, 'anonymous_mapping_pages': anonymous_pages,
            'frequency_khz': {i:get(p).strip() for i in sorted(CPUS)
                              if (p:=Path(f'/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq')).is_file()},
            'host_cpu': [l for l in get(Path('/proc/stat')).splitlines() if l.split()[0] in {f'cpu{i}' for i in CPUS}]}

def system_state():
    return {str(p):get(p) for p in (Path('/proc/loadavg'),Path('/proc/stat'),Path('/proc/meminfo'),
        *sorted(Path('/sys/devices/system/node').glob('node*/meminfo')))}

def simulation(name, threads, stop, elf, out, reference=None, record=False, solver="klu", memory_policy=None):
    print(f'{out} {name} {threads} threads {stop:g} ns', flush=True)
    config = json.loads(get(ROOT/f'config/ngspice-{name}.json'))
    out.mkdir(parents=True)
    profile = {'name':f'{solver}{threads}', 'threads':threads, 'solver':solver}
    prepared = adapter.prepare(out,config,profile,stop)
    input_path = out/'inputs.json'
    manifest = ROOT/'models'/f'{name}-{stop:g}ns.json'
    if not manifest.exists():
        shutil.copyfile(input_path,manifest)
        shutil.copyfile(out/'circuit.sp',ROOT/'netlist'/f'{name}-{stop:g}ns.sp')
    elif json.loads(get(manifest)) != json.loads(get(input_path)):
        raise RuntimeError(f'Input closure changed: {out}')
    env = {key:value for key,value in os.environ.items()
           if key != 'LD_PRELOAD' and not key.startswith(('OMP_','GOMP_','KMP_','MKL_','EDABENCH_PIN_'))}
    env.update(LC_ALL='C',OMP_NUM_THREADS=str(threads),OMP_THREAD_LIMIT=str(threads),
               OMP_PROC_BIND='FALSE',PATH=str(PERF.parent)+':'+os.environ['PATH'])
    policy_args = [] if memory_policy in (None, 'default') else ['numactl',f'--membind={memory_policy}']
    exec_args = ['taskset','-c',','.join(map(str,sorted(CPUS))),*policy_args,sys.executable, str(Path(__file__).with_name('selected.py')),str(elf),
                 *prepared['command'][1:]]
    run = ['/usr/bin/time','-o','time.tsv','-f','%e\t%U\t%S\t%M\t%c\t%w\t%F\t%R',*exec_args]
    if record:
        run = [str(PERF),'record','-e',EVENT,'-F','99','-o','perf.data','--',*exec_args]
    write(out/'execution.json', {'command':run,'elf':elf,'sha256':sha(elf),'profile':profile,
                                 'stop_ns':stop,'memory_policy':0 if memory_policy is None else memory_policy,'monitor_cpu':MONITOR})
    start = time.monotonic()
    audited = memory_policy is not None
    next_snapshot = start
    if audited:
        write(out/'system-before.json',system_state())
    observed = False
    collectors = []
    with (out/'console.log').open('w') as log, (out/'telemetry.jsonl').open('w') as tele:
        proc = sp.Popen(run,cwd=out,env=env,stdout=log,stderr=sp.STDOUT,start_new_session=True)
        try:
            while proc.poll() is None:
                if time.monotonic()-start > config.get('timeout_seconds',1800):
                    raise TimeoutError(str(out))
                try:
                    pid = int(get(out/'target.pid'))
                    data = monitor(pid)
                    if data.get('exe') == str(elf.resolve()):
                        if audited and not observed and stop >= 1:
                            for name,args in [('pidstat',['pidstat','-u','-r','-w','-p',str(pid),'10']),
                                              ('mpstat',['mpstat','-P','ALL','10'])]:
                                with (out/f'{name}.log').open('w') as metrics_log:
                                    collectors.append(sp.Popen(args,stdout=metrics_log,stderr=sp.STDOUT))
                        if audited and time.monotonic() >= next_snapshot:
                            elapsed = int(time.monotonic()-start)
                            base = Path('/proc')/str(pid)
                            snap = out/'numa-snapshots'
                            snap.mkdir(exist_ok=True)
                            (snap/f'{elapsed:04d}-maps.txt').write_text(get(base/'numa_maps'))
                            (snap/f'{elapsed:04d}-status.txt').write_text(get(base/'status'))
                            with (snap/f'{elapsed:04d}-numastat.txt').open('w') as handle:
                                sp.run(['numastat','-p',str(pid)],stdout=handle,stderr=sp.STDOUT)
                            next_snapshot = time.monotonic()+20
                        observed = True
                        tele.write(json.dumps(data)+'\n')
                        tele.flush()
                        if any(not set(_expand_cpus(mask)) <= CPUS for mask in data['thread_affinity'].values()):
                            raise RuntimeError('Observed wrong affinity')
                except (OSError, ValueError, IndexError):
                    pass
                time.sleep(1 if stop < 1 or not observed else 5)
            if proc.returncode:
                raise RuntimeError(f'Simulation exit {proc.returncode}: {out}')
        finally:
            for collector in collectors:
                if collector.poll() is None:
                    collector.terminate()
                try:
                    collector.wait(timeout=5)
                except sp.TimeoutExpired:
                    collector.kill()
                    collector.wait()
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except sp.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait()
    if audited:
        write(out/'system-after.json',system_state())
        if stop >= 1 and len(list((out/'numa-snapshots').glob('*-maps.txt'))) < 3:
            raise RuntimeError(f'Insufficient live NUMA snapshots: {out}')
    verdict = adapter.validate(out, config, stop, reference)
    verdict['live_elf_observed'] = observed
    write(out/'validation.json',verdict)
    if not verdict['passed'] or (stop >= 1 and not observed):
        raise RuntimeError(f'Validation failed: {out}')
    phases = {}
    for key,val in re.findall(r'^([^=\n\r]+?)\s*=\s*([0-9.]+)\s*$',get(out/'console.log'),re.M):
        key=key.strip()
        if 'time' in key.lower() or 'iteration' in key.lower() or key.startswith('Circuit '):
            phases[key]=float(val)
    metrics={'path':str(out),'threads':threads,'phases':phases}
    if not record:
        keys=['wall','user','system','rss_kib','involuntary_cs','voluntary_cs','major_faults','minor_faults']
        metrics.update(zip(keys,map(float,get(out/'time.tsv').split())))
    write(out/'metrics.json',metrics)
    return metrics

def profile(name, threads, out, solver="klu", memory_policy=None):
    out.mkdir(parents=True)
    sim=out/'simulation'
    simulation(name,threads,STOP[name],ELF,sim,record=True,solver=solver,memory_policy=memory_policy)
    perfdata=sim/'perf.data'
    env=dict(os.environ,PATH=str(PERF.parent)+':'+os.environ['PATH'])
    command([PERF,'buildid-list','-f','-i',perfdata],out/'buildids.txt',env)
    build_id=re.search(r'Build ID:\s*(\w+)',sp.check_output(['readelf','-n',str(ELF)],text=True))[1]
    if not re.search(r'^\s*'+build_id+r'\s',get(out/'buildids.txt'),re.M|re.I):
        raise RuntimeError('Target build ID missing')
    command([PERF,'report','--stdio','--percent-limit','0.2','--sort','dso,symbol','-i',perfdata],
            out/'hotspots.txt',env)
    fdata=out/'ngspice.fdata'
    command([BOLT/'perf2bolt','--basic-events','--show-density','--perfdata='+str(perfdata),
             '--relocs',ELF,'-o',fdata],out/'perf2bolt.log',env)
    assert fdata.stat().st_size
    opt=out/'ngspice.bolt'
    command([BOLT/'llvm-bolt',ELF,'--data='+str(fdata),'-o',opt,*BOLT_FLAGS],out/'llvm-bolt.log',env)
    write(out/'manifest.json',{'elf':ELF,'elf_sha256':sha(ELF),'build_id':build_id,'bolt_sha256':sha(BOLT/'llvm-bolt'),
                               'fdata_sha256':sha(fdata),'optimized_sha256':sha(opt),'flags':BOLT_FLAGS,
                               'solver':solver,'sampling_event':EVENT,'sampling_hz':99,'threads':threads,'stop_ns':STOP[name]})
    return opt

def differences(blocks, a, b):
    base=[block[a]['wall'] for block in blocks]
    trial=[block[b]['wall'] for block in blocks]
    delta=[x-y for x,y in zip(trial,base)]
    n=len(delta)
    critical={3:4.30265273,7:2.44691185}[n]
    mean=stats.mean(delta)
    width=critical*stats.stdev(delta)/n**0.5
    return {'baseline_mean_s':stats.mean(base),'trial_mean_s':stats.mean(trial),
            'baseline_sd_s':stats.stdev(base),'baseline_range_s':[min(base),max(base)],
            'trial_range_s':[min(trial),max(trial)],'baseline_median_s':stats.median(base),
            'trial_median_s':stats.median(trial),'paired_delta_s':delta,'mean_delta_s':mean,
            'paired_95ci_s':[mean-width,mean+width],'faster_pairs':sum(d<0 for d in delta),
            'reduction_percent':-100*mean/stats.mean(base)}

