"""Portable, fail-closed environment checks and experiment orchestration."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

PACKAGE = Path(__file__).resolve().parents[2]

def expand(spec):
    cpus = set()
    for part in spec.strip().split(','):
        if part:
            lo, _, hi = part.partition('-')
            cpus.update(range(int(lo), int(hi or lo) + 1))
    return cpus

def available_memory(text):
    fields = {key: int(value) for key, value in re.findall(r'Node \d+ (\S+):\s+(\d+) kB', text)}
    return fields.get('MemFree', 0) + max(0, fields.get('Inactive(file)', 0) - fields.get('Dirty', 0) - fields.get('Writeback', 0))

def choose_cpus(cpus, read_topology):
    """Choose separate cores first, rather than assuming CPU IDs imply topology."""
    representatives, siblings = {}, []
    for cpu in sorted(cpus):
        key = read_topology(cpu)
        if key in representatives:
            siblings.append(cpu)
        else:
            representatives[key] = cpu
    ordered = list(representatives.values()) + siblings
    if len(ordered) < 16:
        raise ValueError('Need at least 16 allowed logical CPUs on one NUMA node')
    return ordered[:16], ordered[16] if len(ordered) > 16 else ordered[15]

def topology():
    status = Path('/proc/self/status').read_text()
    allowed_nodes = expand(re.search(r'^Mems_allowed_list:\s*(.*)$', status, re.M)[1])
    allowed = os.sched_getaffinity(0) & expand(Path('/sys/devices/system/cpu/online').read_text())
    nodes = []
    for path in sorted(Path('/sys/devices/system/node').glob('node[0-9]*')):
        node = int(path.name[4:])
        if node not in allowed_nodes:
            continue
        cpus = sorted(expand((path / 'cpulist').read_text()) & allowed)
        nodes.append(dict(node=node, allowed_cpus=cpus, available_kib=available_memory((path / 'meminfo').read_text())))
    return nodes

def select_node(nodes, requested):
    eligible = [n for n in nodes if len(n['allowed_cpus']) >= 16 and n['available_kib'] >= 2 * 1024**2]
    if requested != 'auto':
        selected = next((n for n in eligible if n['node'] == int(requested)), None)
        if selected is None:
            raise ValueError(f'Node {requested} is unavailable, has fewer than 16 allowed CPUs, or less than 2 GiB free/reclaimable memory. See preflight.json. Select another --node or adjust the scheduler allocation.')
        return selected
    if not eligible:
        raise ValueError('No allowed NUMA node has 16 CPUs and 2 GiB free/reclaimable memory. Request a larger scheduler allocation; thread count is not silently reduced.')
    # Memory ranking is deterministic; it does not claim the CPUs are idle or reserved.
    return max(eligible, key=lambda n: (n['available_kib'], -n['node']))

def core_key(cpu):
    base = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
    return tuple((base / name).read_text().strip() for name in ('physical_package_id', 'core_id'))

def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')

def probe(args, report, env=None):
    try:
        result = subprocess.run(list(map(str, args)), text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, timeout=60)
        item = dict(command=list(map(str, args)), returncode=result.returncode, output=result.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        item = dict(command=list(map(str, args)), returncode=-1, output=str(exc))
    report['probes'].append(item)
    return item

def require_probe(args, report, remedy, env=None):
    result = probe(args, report, env)
    if result['returncode']:
        raise RuntimeError(result['output'][-2500:] + '\n解决方法：' + remedy)
    return result['output']

def fingerprint(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def check_environment(args, out, report):
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise RuntimeError('This package requires Linux x86_64; the bundled BOLT is an x86_64 ELF.')
    required = ['numactl', 'numastat', 'taskset', 'pidstat', 'mpstat', 'lscpu', 'readelf',
                'gcc', 'g++', 'make', 'tar', 'java', 'bash']
    missing = [cmd for cmd in required if not shutil.which(cmd)]
    if not Path('/usr/bin/time').exists():
        missing.append('/usr/bin/time')
    if missing:
        raise RuntimeError('Missing system prerequisites: ' + ', '.join(missing) + '\nRun bash prepare-host.sh --system (requires administrator rights), or ask the administrator. No sudo is run by the experiment launcher.')
    report['numa_nodes'] = topology()
    selected = select_node(report['numa_nodes'], args.node)
    cpus, monitor = choose_cpus(selected['allowed_cpus'], core_key)
    node = selected['node']
    report.update(selected_node=node, cpus=cpus, monitor_cpu=monitor,
                  controller_shares_logical_cpu=monitor in cpus,
                  selection_rule='explicit node' if args.node != 'auto' else 'most free/reclaimable memory among eligible nodes; no CPU exclusivity guaranteed')
    os.sched_setaffinity(0, set(selected['allowed_cpus']))
    bind = ['numactl', '--physcpubind=' + ','.join(map(str, sorted(set(cpus) | {monitor}))), f'--membind={node}']
    require_probe([*bind, 'true'], report, 'Allow NUMA memory binding in the container/scheduler or run on the host; CPU binding alone does not replace memory binding.')
    if args.numa:
        remote_nodes = [n for n in report['numa_nodes'] if n['node'] != node and n['available_kib'] >= 2*1024**2]
        requested = args.remote_node
        remote = next((n for n in remote_nodes if str(n['node']) == requested), None) if requested != 'auto' else max(remote_nodes, key=lambda n:n['available_kib'], default=None)
        if remote is None:
            raise RuntimeError('NUMA comparison requires a different allowed memory node with 2 GiB free/reclaimable memory. Use --skip-numa on a single-node/restricted server, or select --remote-node N.')
        report['remote_node'] = remote['node']
        require_probe(['numactl', f'--membind={remote["node"]}', 'true'], report, 'Request access to the remote memory node or use --skip-numa.')
        policy = require_probe(['numactl', '--show'], report, 'Start from a shell with Default memory policy for the NUMA experiment.')
        if not policy.startswith('policy: default\n'):
            raise RuntimeError('NUMA comparison must start with Default memory policy; launch outside an inherited membind/interleave wrapper, or use --skip-numa.')
    if shutil.disk_usage(out).free < 8 * 1024**3:
        raise RuntimeError('Need at least 8 GiB free output disk space. Select another output directory; 20 GiB or more is recommended for full runs.')
    for relative in ('source/ngspice-47.tar.gz', 'source/toolchain/formal/tla2tools.jar',
                     'environment/inputs/c7552/full/driver.cir', 'environment/inputs/c7552/short/driver.cir'):
        if not (PACKAGE / relative).is_file():
            raise RuntimeError('Missing packaged input: ' + relative + '; extract the complete archive, not only the scripts.')
    bolt_dir = Path(os.environ.get('NGSPICE_BOLT_DIR', PACKAGE / 'source/toolchain')).expanduser().resolve()
    version = require_probe([*bind, bolt_dir / 'llvm-bolt', '--version'], report,
                            'Provide compatible LLVM BOLT 22.1.8 and perf2bolt in NGSPICE_BOLT_DIR. Do not silently substitute a different BOLT version.')
    if '22.1.8' not in version or not (bolt_dir / 'perf2bolt').is_file():
        raise RuntimeError('Need LLVM BOLT 22.1.8 and perf2bolt in NGSPICE_BOLT_DIR.')
    report['bolt_dir'] = str(bolt_dir)
    report['bolt_sha256'] = fingerprint(bolt_dir / 'llvm-bolt')
    with tempfile.TemporaryDirectory(prefix='ngspice-preflight-') as temporary:
        temporary = Path(temporary)
        cfile = temporary / 'probe.c'
        cfile.write_text('#include <omp.h>\nint main(void) { return omp_get_max_threads()<1; }\n')
        require_probe([*bind, 'gcc', '-fopenmp', '-Wl,--emit-relocs', '-Wl,--build-id=sha1', cfile, '-o', temporary/'probe'], report,
                      'Install build-essential/binutils (Ubuntu) or gcc/gcc-c++/make/binutils/libgomp (RHEL); see prepare-host.sh.')
        require_probe([*bind, temporary/'probe'], report, 'Install the compiler OpenMP runtime (libgomp).')
        perf_override = os.environ.get('NGSPICE_PERF')
        candidates = [perf_override] if perf_override else [str(PACKAGE/'source/toolchain/perf'), shutil.which('perf')]
        report['perf_candidates'] = []
        for candidate in dict.fromkeys(c for c in candidates if c):
            candidate = shutil.which(candidate) or str(Path(candidate).expanduser().resolve())
            data = temporary / 'probe.perf.data'
            attempt = probe([*bind, candidate, 'record', '-e', 'cpu-clock:u', '-F', '99', '-o', data,
                             '--', sys.executable, '-c', 'import time; end=time.monotonic()+0.25\nwhile time.monotonic()<end: pass'], report)
            report['perf_candidates'].append(dict(path=candidate, returncode=attempt['returncode']))
            if attempt['returncode'] == 0:
                reader = probe([*bind, candidate, 'report', '--stdio', '-i', data], report)
                if reader['returncode'] == 0:
                    report['perf'] = candidate
                    report['perf_sha256'] = fingerprint(candidate)
                    break
        if 'perf' not in report:
            raise RuntimeError('No usable perf could record/read cpu-clock:u samples. Bundled perf was tried, then host perf unless NGSPICE_PERF was explicit.\nInstall tools matching uname -r (prepare-host.sh --system), or set NGSPICE_PERF=/absolute/path/to/perf. Permission failures require the administrator to permit user-space perf sampling (perf_event_paranoid/container capability); the launcher does not change kernel security settings. See probes in preflight.json.')
    env = dict(os.environ, LC_ALL='C', PYTHONUTF8='1')
    env.pop('PYTHONPATH', None)
    z3_candidates = [None, str(PACKAGE/'source/toolchain/formal/python')]
    for path in z3_candidates:
        trial = dict(env)
        if path:
            trial['PYTHONPATH'] = path
        result = probe([*bind, sys.executable, '-B', '-c', 'import z3; assert z3.get_version_string()=="4.12.2"; s=z3.Solver(); s.add(z3.BoolVal(True)); assert s.check()==z3.sat; print(z3.get_version_string())'], report, trial)
        if result['returncode'] == 0:
            report['z3_pythonpath'] = path
            break
    else:
        raise RuntimeError('No usable z3-solver 4.12.2 for this Python/platform. Run bash prepare-host.sh --z3 and retry (creates a local venv; requires package access).')
    require_probe([*bind, 'java', '-version'], report, 'Install a compatible Java runtime; TLC will be actually executed before the experiment.')
    report.update(python=sys.executable, python_version=sys.version, platform=platform.platform(),
                  kernel=platform.release(), skip_numa=not args.numa, binding_command=bind,
                  disk_free_bytes=shutil.disk_usage(out).free)
    return bind

def main(argv=None):
    parser = argparse.ArgumentParser(description='Preflight, formal checks and portable C7552 execution. Defaults to fixed-node full; NUMA comparisons require --numa.')
    parser.add_argument('mode', choices=('check', 'smoke', 'full', 'install', 'archive-analysis'), nargs='?', default='full')
    parser.add_argument('output', nargs='?', type=Path)
    parser.add_argument('--node', default=os.environ.get('NGSPICE_EXECUTION_NODE', 'auto'))
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--numa', action='store_true', help='Enable Default/local/remote NUMA comparisons on detected nodes')
    group.add_argument('--skip-numa', action='store_true', help='Fixed-node run (default)')
    parser.add_argument('--remote-node', default='auto')
    args = parser.parse_args(argv)
    if args.node != 'auto' and not args.node.isdigit():
        parser.error('--node must be auto or a nonnegative node ID')
    if args.mode == 'archive-analysis':
        return subprocess.call([sys.executable, '-B', str(PACKAGE/'code/core/archive_analysis.py')])
    out = (args.output or Path.home()/'.cache/ngspice-runs'/f'{args.mode}-{datetime.datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}').expanduser().resolve()
    if args.mode in ('smoke','full','install') and (not str(out).isascii() or any(ch.isspace() for ch in str(out))):
        parser.error('Build output must use an ASCII path without whitespace for the upstream Autotools toolchain. The package directory can contain Chinese/spaces. Specify e.g. /tmp/ngspice-runs/run-001 or an ASCII data-disk path.')
    if out.exists():
        parser.error('Use a new output directory; no existing results are overwritten: '+str(out))
    try:
        out.mkdir(parents=True)
    except OSError as error:
        parser.error('Cannot create output directory: '+str(error)+'; choose a writable output path.')
    report = dict(package=str(PACKAGE), output=str(out), probes=[], status='CHECKING')
    try:
        bind = check_environment(args, out, report)
        report['status']='PASSED'
        dump(out/'preflight.json', report)
        print(f"Preflight passed: node={report['selected_node']} CPUs={report['cpus']} monitor={report['monitor_cpu']} perf={report['perf']}", flush=True)
        print('Output: '+str(out), flush=True)
        env=dict(os.environ, LC_ALL='C', PYTHONUTF8='1',
                 NGSPICE_EXECUTION_NODE=str(report['selected_node']), NGSPICE_SKIP_NUMA='0' if args.numa else '1',
                 NGSPICE_CPUS=','.join(map(str,report['cpus'])), NGSPICE_MONITOR_CPU=str(report['monitor_cpu']),
                 NGSPICE_PERF=report['perf'], NGSPICE_BOLT_DIR=report['bolt_dir'],
                 TLC_JAR=str(PACKAGE/'source/toolchain/formal/tla2tools.jar'), PYTHON=sys.executable,
                 FORMAL_OUTPUT_DIR=str(out))
        env.pop('PYTHONPATH',None)
        if report['z3_pythonpath']:
            env['PYTHONPATH']=report['z3_pythonpath']
        if args.numa:
            env['NGSPICE_REMOTE_NODE']=str(report['remote_node'])
        dump(out/'status.json', dict(status='RUNNING',mode=args.mode,phase='formal-check',pid=os.getpid()))
        with (out/'formal-check.log').open('w') as log:
            subprocess.run([*bind, 'bash', str(PACKAGE/'results/formal-hg7490/run.sh')], env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        dump(out/'status.json',dict(status='RUNNING',mode=args.mode,phase='experiment',pid=os.getpid()))
        runtime_bind = bind[:-1] if args.numa else bind
        subprocess.run([*runtime_bind, sys.executable, '-B', '-u', str(PACKAGE/'code/core/full.py'), args.mode, str(out/'experiment')],env=env,check=True)
        dump(out/'status.json',dict(status='SUCCEEDED',mode=args.mode,scope='environment/formal only' if args.mode=='check' else args.mode))
        return 0
    except Exception as error:
        report['error']=str(error)
        if report['status'] != 'PASSED':report['status']='FAILED'
        dump(out/'preflight.json',report)
        dump(out/'status.json',dict(status='FAILED',mode=args.mode,error=str(error)))
        print('FAILED: '+str(error)+'\nDiagnostics: '+str(out),file=sys.stderr,flush=True)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
