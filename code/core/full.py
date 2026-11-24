"""Build-to-report experiment driver for the delivered offline inputs."""
import argparse
import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import statistics as st
import sys

import experiment as e
from solver_followup import compare_solvers

PACKAGE=Path(__file__).resolve().parents[2]
INPUT=PACKAGE/'environment/inputs'
SEED=20261115
THREADS=8
fix=json.loads((PACKAGE/'environment/config.json').read_text())
e.BOLT=Path(os.environ.get('NGSPICE_BOLT_DIR',str(PACKAGE/'source/toolchain'))).resolve()
e.PERF=Path(os.environ.get('NGSPICE_PERF',str(e.BOLT/'perf'))).resolve()
e.STOP=fix['windows_ns']

EXECUTION_NODE=int(os.environ.get('NGSPICE_EXECUTION_NODE','0'))
SKIP_NUMA=os.environ.get('NGSPICE_SKIP_NUMA','1')=='1'
TIMEOUT=int(os.environ.get('NGSPICE_TIMEOUT_SECONDS','7200'))
REMOTE_NODE=EXECUTION_NODE if SKIP_NUMA else int(os.environ['NGSPICE_REMOTE_NODE'])
node_cpus=Path(f'/sys/devices/system/node/node{EXECUTION_NODE}/cpulist').read_text().strip()
def expand(spec):
    result=set()
    for part in spec.split(','):
        lo,_,hi=part.partition('-')
        result.update(range(int(lo),int(hi or lo)+1))
    return result
# Use physical cores before SMT siblings on this server; record the actual set.
available=sorted(expand(node_cpus) & os.sched_getaffinity(0))
if len(available)<16:
    raise RuntimeError('Need 16 allowed CPUs on the execution node')
e.CPUS=expand(os.environ.get('NGSPICE_CPUS',','.join(map(str,available[:16]))))
e.MONITOR=int(os.environ.get('NGSPICE_MONITOR_CPU',str(available[16] if len(available)>16 else available[-1])))
if len(e.CPUS)!=16 or not e.CPUS <= set(available) or e.MONITOR not in available:
    raise RuntimeError('Invalid preflight CPU selection for this node')
fix.update(execution_node=EXECUTION_NODE,skip_numa=SKIP_NUMA,cpus=','.join(map(str,sorted(e.CPUS))),
           controller_cpu=e.MONITOR,timeout_seconds=TIMEOUT,remote_node=None if SKIP_NUMA else REMOTE_NODE,
           memory_modes=[EXECUTION_NODE] if SKIP_NUMA else ['default',EXECUTION_NODE,REMOTE_NODE])

def prepare(run,config,profile,stop):
    name=config['name']
    source=INPUT/name/('short' if stop<1 else 'full')
    contract=json.loads((source/'ngspice-contract.json').read_text())
    if contract['stop_ns']!=stop:
        raise ValueError('Requested window does not match the staged input')
    for filename in ('inputs.json','circuit.sp'):
        shutil.copy2(source/filename,run/filename)
    # Keep relative includes and directory hierarchy unchanged.
    (run/'models').symlink_to((source/'models').resolve(),target_is_directory=True)
    driver=(source/'driver.cir').read_text()
    driver,threads=re.subn(r'(?m)^set num_threads=\d+$',f"set num_threads={profile['threads']}",driver)
    driver,solvers=re.subn(r'(?m)^option klu(?:=0)?$','option klu' if profile['solver']=='klu' else 'option klu=0',driver)
    if threads!=1 or solvers!=1:
        raise ValueError(f'Unexpected stored driver structure: {source}')
    (run/'driver.cir').write_text(driver)
    contract.update(solver=profile['solver'],threads=profile['threads'])
    e.write(run/'ngspice-contract.json',contract)
    return {'command':['ngspice','-n','-b','driver.cir']}

e.adapter.prepare=prepare

def settings():
    cpu_ok=e.CPUS | {e.MONITOR} <= os.sched_getaffinity(0)
    if not cpu_ok:
        raise RuntimeError('Execution CPUs are not allowed')
    node=Path(f'/sys/devices/system/node/node{EXECUTION_NODE}/cpulist')
    if not e.CPUS <= e.spice_cpu_set(node.read_text().strip()):
        raise RuntimeError('Execution CPUs must belong to the selected node')
    if not SKIP_NUMA and not e.sp.check_output(['numactl','--show'],text=True).startswith('policy: default\n'):
        raise RuntimeError('Run from an unbound Linux Default memory policy')
    for command in ('numactl','numastat','pidstat','mpstat','taskset','readelf','make','gcc','g++'):
        if not shutil.which(command):
            raise RuntimeError(f'Missing system prerequisite: {command}')
    if not (e.BOLT/'llvm-bolt').is_file() or not e.PERF.is_file():
        raise FileNotFoundError('Bundled toolchain missing')
    if not e.CPUS <= e.spice_cpu_set(Path('/sys/devices/system/cpu/online').read_text().strip()):
        raise RuntimeError('The workload CPU set includes offline CPUs')

def cpu_set(spec):
    values=set()
    for item in spec.split(','):
        start,_,end=item.partition('-')
        values.update(range(int(start),int(end or start)+1))
    return values
e.spice_cpu_set=cpu_set

def memory_available(node):
    path=Path(f'/sys/devices/system/node/node{node}/meminfo')
    fields={name:int(size) for name,size in re.findall(r'Node \d+ (\S+):\s+(\d+) kB',path.read_text())}
    available=fields['MemFree']+max(0,fields['Inactive(file)']-fields.get('Dirty',0)-fields.get('Writeback',0))
    if available<2*1024**2:
        raise RuntimeError(f'Insufficient free plus reclaimable memory on node {node}: {available} KiB')

def simulate(name,stop,elf,out,solver='klu',policy=None,reference=None,record=False):
    if policy is None:
        policy=EXECUTION_NODE
    if type(policy) is int:
        memory_available(policy)
    return e.simulation(name,THREADS,stop,elf,out,reference=reference,record=record,
                        solver=solver,memory_policy=policy)

def orders(n,a='A',b='B'):
    return [a+b,*[(a+b if i%2==0 else b+a) for i in range(n)]]

def run_blocks(path,name,configs,schedule,cross=False):
    e.write(path/'design.json',{'orders':schedule,'configs':configs,'seed':SEED,'warmup_block':0})
    complete=[]
    refs={}
    for i,order in enumerate(schedule):
        block={}
        blockpath=path/f'block-{i:02d}'
        for key in order:
            elf,solver,policy=configs[key]
            current=blockpath/key
            block[key]=simulate(name,e.STOP[name],elf,current,solver,policy,refs.get(solver))
            refs.setdefault(solver,current)
        if cross:
            verdict=compare_solvers(blockpath/'A',blockpath/'B')
            e.write(blockpath/'cross-solver.json',verdict)
            if not verdict['passed']:
                raise RuntimeError(f'Cross-solver waveform comparison failed: {blockpath}')
        if i:
            complete.append(block)
        e.write(path/'blocks.json',complete)
    return complete

def stats(blocks,a,b):
    summary=e.differences(blocks,a,b)
    ratios=[x[a]['wall']/x[b]['wall'] for x in blocks]
    summary.update(baseline_sd_s=st.stdev(x[a]['wall'] for x in blocks),
                   trial_sd_s=st.stdev(x[b]['wall'] for x in blocks),
                   geometric_speedup=math.exp(st.mean(map(math.log,ratios))),
                   paired_relative_savings_pct=[100*(1-1/r) for r in ratios])
    return summary

def shares(blocks,variant,node):
    portions=[]
    for b in blocks:
        path=Path(b[variant]['path'])
        records=[json.loads(s) for s in (path/'telemetry.jsonl').read_text().splitlines()]
        for rec in records[len(records)//2:]:
            pages=rec['anonymous_mapping_pages']
            if sum(pages.values()):
                portions.append(pages.get(str(node),0)/sum(pages.values()))
    if not portions:
        raise RuntimeError(f'No live NUMA page telemetry for {variant}')
    return st.median(portions)

def gate(summary,local_share):
    return local_share>=.9 and summary['faster_pairs']>=6 and summary['paired_95ci_s'][1]<0

def main(work,mode):
    e.ROOT=work
    e.ELF=work/'install/bin/ngspice'
    for path in ('log','config','test','result','profile','models','netlist','report'):
        (work/path).mkdir(exist_ok=True)
    for key in e.STOP:
        e.write(work/f'config/ngspice-{key}.json',{'name':key,'timeout_seconds':TIMEOUT})
    e.write(work/'config/experiment.json',fix)
    if mode=='install':
        e.sp.run(['bash',str(PACKAGE/'code/core/install.sh'),str(PACKAGE),str(work)],check=True)
        return
    settings()
    for label,cmd in [('numa',['numactl','--hardware']),('policy',['numactl','--show']),('cpu',['lscpu']),
                      ('kernel',['uname','-a']),('bolt',[e.BOLT/'llvm-bolt','--version']),
                      ('perf',[e.PERF,'stat','-e',e.EVENT,'--','true'])]:
        e.command(cmd,work/f'log/{label}.txt')
    if '22.1.8' not in (work/'log/bolt.txt').read_text():
        raise RuntimeError('Expected LLVM BOLT 22.1.8')
    e.write(work/'log/environment.json',e.system_state())
    if mode=='check':
        return
    if not e.ELF.exists():
        e.sp.run(['bash',str(PACKAGE/'code/core/install.sh'),str(PACKAGE),str(work)],check=True)
    os.sched_setaffinity(0,{e.MONITOR})
    for name in (('c7552',) if mode=='smoke' else e.STOP):
        simulate(name,.1,e.ELF,work/'test'/name/'klu')
    simulate('c7552',.1,e.ELF,work/'test/c7552/sparse',solver='sparse')
    short=compare_solvers(work/'test/c7552/klu',work/'test/c7552/sparse')
    e.write(work/'test/c7552/cross-solver.json',short)
    if not short['passed']:
        raise RuntimeError('Short cross-solver check failed')
    if mode=='smoke':
        try:
            e.STOP={**e.STOP,'c7552':.1}
            optimized=e.profile('c7552',THREADS,work/'profile/c7552/klu8-short',memory_policy=EXECUTION_NODE)
            simulate('c7552',.1,optimized,work/'test/c7552/klu-bolt',reference=work/'test/c7552/klu')
            sparse_optimized=e.profile('c7552',THREADS,work/'profile/c7552/sparse8-short',solver='sparse',memory_policy=EXECUTION_NODE)
            simulate('c7552',.1,sparse_optimized,work/'test/c7552/sparse-bolt',solver='sparse',reference=work/'test/c7552/sparse')
        finally:
            e.STOP=fix['windows_ns']
        return

    prof={}
    for name,solver in (('c7552','klu'),('c7552','sparse')):
        memory_available(EXECUTION_NODE)
        prof[name,solver]=e.profile(name,THREADS,work/'profile'/name/f'{solver}8',solver=solver,memory_policy=EXECUTION_NODE)
    name='c7552'
    original=(e.ELF,'klu',EXECUTION_NODE)
    sparse=(e.ELF,'sparse',EXECUTION_NODE)
    bolt=(prof[name,'klu'],'klu',EXECUTION_NODE)
    sparse_bolt=(prof[name,'sparse'],'sparse',EXECUTION_NODE)
    report={}
    def execute(label,name,configs,schedule,cross=False):
        out=work/'result'/label
        blocks=run_blocks(out,name,configs,schedule,cross)
        if len(configs)==2:
            report[label]=stats(blocks,'A','B')
            e.write(out/'summary.json',report[label])
        return blocks
    execute('solver-originals',name,{'A':original,'B':sparse},orders(7),True)
    execute('bolt-klu8',name,{'A':original,'B':bolt},orders(7))
    execute('bolt-sparse8',name,{'A':sparse,'B':sparse_bolt},orders(7))
    if SKIP_NUMA:
        for label in ('numa','numa-remote','factorial'):
            e.write(work/f'result/{label}/status.json',{'status':'SKIPPED','reason':'Requested fixed-node execution without NUMA comparisons'})
        report['numa-pages']={'status':'SKIPPED','execution_node':EXECUTION_NODE}
        e.write(work/'result/final/selection.json',{'best_memory_policy':EXECUTION_NODE,
            'reason':'Fixed execution placement, not selected by a NUMA experiment',
            'optimized_sha256':e.sha(bolt[0])})
        execute('final',name,{'A':original,'B':bolt},orders(7))
        e.write(work/'result/summary.json',report)
        e.sp.run([sys.executable,'-B',str(PACKAGE/'code/core/analyze.py'),str(work)],check=True)
        return
    default=(e.ELF,'klu','default')
    local=original
    rng=random.Random(SEED)
    alternating=orders(7)
    formal=alternating[1:]
    rng.shuffle(formal)
    numa=execute('numa',name,{'A':default,'B':local},[alternating[0],*formal])
    local_share=shares(numa,'B',EXECUTION_NODE)
    default_share=shares(numa,'A',EXECUTION_NODE)
    remote=run_blocks(work/'result/numa-remote',name,{'R':(e.ELF,'klu',REMOTE_NODE)},['R']*4)
    report['numa-pages']={'local_node':EXECUTION_NODE,'remote_node':REMOTE_NODE,
        'default_local_share':default_share,'bound_local_share':local_share,'bound_remote_share':shares(remote,'R',REMOTE_NODE),
        'remote_seconds':[b['R']['wall'] for b in remote]}
    selected='default'
    if gate(report['numa'],local_share):
        sequence=['ABDC','BCAD','CDBA','DACB','ABDC','BCAD','CDBA']
        rng.shuffle(sequence)
        c=execute('factorial',name,{'A':default,'B':local,'C':(bolt[0],'klu','default'),'D':bolt},['ABDC',*sequence])
        effects={a+b:stats(c,a,b) for a,b in ('AB','AC','BD','CD','AD')}
        interaction=[b['D']['wall']-b['C']['wall']-b['B']['wall']+b['A']['wall'] for b in c]
        mean=st.mean(interaction)
        width=2.44691185*st.stdev(interaction)/len(interaction)**.5
        e.write(work/'result/factorial/summary.json',{'effects':effects,'interaction_s':interaction,
                                                      'interaction_95ci_s':[mean-width,mean+width]})
        if gate(effects['CD'],shares(c,'D',EXECUTION_NODE)):
            selected=EXECUTION_NODE
    else:
        e.write(work/'result/factorial/status.json',{'status':'SKIPPED','reason':'Local NUMA did not pass paired benefit and page-placement gates'})
    e.write(work/'result/final/selection.json',{'best_memory_policy':selected,'optimized_sha256':e.sha(bolt[0])})
    execute('final',name,{'A':default,'B':(bolt[0],'klu',selected)},orders(7))
    e.write(work/'result/summary.json',report)
    e.sp.run([sys.executable,'-B',str(PACKAGE/'code/core/analyze.py'),str(work)],check=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('check','install','smoke','full'))
    parser.add_argument('work',type=Path)
    args=parser.parse_args()
    work=args.work.resolve()
    if args.mode=='full' and work.exists():
        raise SystemExit('Use a new output directory for a new complete run')
    work.mkdir(parents=True,exist_ok=True)
    status=work/'status.json'
    lock_path=Path.home()/f'.cache/ngspice-reproduction/node{EXECUTION_NODE}.performance.lock'
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    host_lock=Path.home()/'workplace/eda/bolt/ngspice/v2/results/performance.lock'
    if not SKIP_NUMA and host_lock.is_file():
        lock_path=host_lock
    with lock_path.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.mode!='check':
            e.write(status,{'status':'RUNNING','mode':args.mode,'pid':os.getpid(),'at':datetime.datetime.now().isoformat()})
        try:
            main(work,args.mode)
        except BaseException as error:
            e.write(status,{'status':'FAILED','mode':args.mode,'error':str(error)})
            raise
        e.write(status,{'status':'SUCCEEDED','mode':args.mode})
