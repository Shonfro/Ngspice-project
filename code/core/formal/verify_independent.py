"""Verify a locally extracted copy on an independently authorized host."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess as sp
import sys

from edabench.ngspice import read_raw, _waveform_checks

root=Path(__file__).resolve().parent
os.chdir(root)
cfg=json.loads(Path('config.json').read_text())
host=socket.gethostname()
if host.split('.')[0]==cfg['main_host']:
    raise SystemExit('Use a host independent of the performance server')
out=Path('evidence').resolve()
out.mkdir()
state={'host':host}
def save():
    (out/'status.json').write_text(json.dumps(state,indent=2)+'\n')
def run(argv,log,cwd=root,env=None,timeout=1800):
    with (out/log).open('w') as stream:
        return sp.run(argv,cwd=cwd,stdout=stream,stderr=sp.STDOUT,env=env,timeout=timeout).returncode

if run(['sha256sum','-c','SHA256SUMS'],'checksums.log'):
    raise SystemExit('Bundle damaged; see checksums.log')
(out/'environment.json').write_text(json.dumps({'host':host,'uname':list(os.uname()),'python':sys.version,
    'free_bytes':shutil.disk_usage(root).free,'tools':{v:shutil.which(v) for v in ('java','z3','ldd','readelf')}},indent=2))
for path in ('/etc/os-release','/proc/meminfo'):
    shutil.copyfile(path,out/Path(path).name)
for tool,required in [('tlc','java'),('z3','z3')]:
    try:
        if not shutil.which(required):
            raise FileNotFoundError(required)
        if tool=='tlc':
            jar=os.environ.get('TLC_JAR')
            if not jar:
                raise FileNotFoundError('Set TLC_JAR to tla2tools.jar')
            run(['java','-version'],'java-version.log')
            rc=run(['java','-cp',str(Path(jar).resolve()),'tlc2.TLC','-workers','1','-config',
                    'ExperimentPipeline.cfg','ExperimentPipeline.tla'],'tlc.log',root/'tla')
            log=(out/'tlc.log').read_text()
            counts=re.search(r'(\d+) states generated,\s*(\d+) distinct states found',log)
            state[tool]={'status':'SUCCEEDED' if not rc and 'No error has been found' in log and counts else 'FAILED',
                         'exit_code':rc,'states':list(map(int,counts.groups())) if counts else None}
        else:
            run(['z3','-version'],'z3-version.log')
            rc=run(['z3','-smt2','evidence.smt2'],'z3.log')
            nc=run(['z3','-smt2','negative-control.smt2'],'z3-negative.log')
            a=(out/'z3.log').read_text().splitlines()[:1]
            b=(out/'z3-negative.log').read_text().splitlines()[:1]
            state[tool]={'status':'SUCCEEDED' if rc==nc==0 and a==['sat'] and b==['unsat'] else 'FAILED',
                         'observed':a,'negative_control':b}
    except (OSError,sp.TimeoutExpired) as error:
        state[tool]={'status':'BLOCKED','reason':str(error)}
    save()

def numerical():
    if os.uname().machine!='x86_64':
        raise RuntimeError('Exact archived ELF requires x86_64')
    names,ref=read_raw(root/'input/reference.raw')
    signals=['v(g7529_1)','v(g7509_0)','v(g7552_0)']
    assert _waveform_checks(names,ref,signals,cfg['stop_ns'])['passed']
    env={k:v for k,v in os.environ.items() if k not in ('LD_PRELOAD','LD_LIBRARY_PATH')
         and not k.startswith(('OMP_','GOMP_','KMP_','MKL_','EDABENCH_PIN_'))}
    env.update(OMP_NUM_THREADS=str(cfg['threads']),OMP_THREAD_LIMIT=str(cfg['threads']),
               OMP_PROC_BIND='FALSE',LC_ALL='C')
    records=[]
    for kind in ('original','optimized'):
        elf=root/'bin'/kind
        if run(['ldd',str(elf)],f'{kind}-ldd.log',env=env) or 'not found' in (out/f'{kind}-ldd.log').read_text():
            raise RuntimeError(f'{kind}: unresolved ELF dependency')
        run([str(elf),'--version'],f'{kind}-version.log',env=env)
        for i in range(1,4):
            label=f'{kind}-{i}'
            directory=out/label
            directory.mkdir()
            for name in ('circuit.sp','driver.cir','models'):
                (directory/name).symlink_to(root/'input'/name)
            code=run([str(elf),'-n','-b','driver.cir'],label+'.log',directory,env)
            console=(out/(label+'.log')).read_text(errors='replace')
            if code or 'BENCH_SIMULATION_COMPLETE' not in console:
                raise RuntimeError(f'{label} did not complete, exit {code}')
            if not re.search(r'^Using KLU as Direct Linear Solver\s*$',console,re.M):
                raise RuntimeError(f'{label}: KLU not observed')
            if any(f'{key}={cfg["threads"]}' not in console for key in ('BENCH_NUM_THREADS','BENCH_OMP_NUM_THREADS')):
                raise RuntimeError(f'{label}: thread setup not observed')
            actual,rows=read_raw(directory/'waveform.raw')
            wave=_waveform_checks(actual,rows,signals,cfg['stop_ns'])
            grid=actual==names and len(rows)==len(ref) and all(a[0]==b[0] for a,b in zip(ref,rows))
            values=[(a,b) for x,y in zip(ref,rows) for a,b in zip(x[1:],y[1:])] if grid else []
            absolute=max((abs(a-b) for a,b in values),default=None)
            relative=max((abs(a-b)/abs(a) if a else (0 if a==b else math.inf) for a,b in values),default=None)
            records.append(dict(run=label,passed=wave['passed'] and grid and absolute==0,
                exact_grid=grid,max_absolute_v=absolute,
                max_relative=relative if relative is not None and math.isfinite(relative) else None,checks=wave))
            (out/'waveforms.json').write_text(json.dumps(records,indent=2))
            if not records[-1]['passed']:
                raise RuntimeError(f'{label}: waveform comparison failed')
    return {'status':'SUCCEEDED','replicates':len(records)}
try:
    state['numerical']=numerical()
except (OSError,ValueError,RuntimeError,AssertionError,sp.TimeoutExpired) as error:
    state['numerical']={'status':'FAILED','reason':str(error)}
save()
raise SystemExit(0 if all(state[key]['status']=='SUCCEEDED' for key in ('tlc','z3','numerical')) else 1)
