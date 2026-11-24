"""Generate reported performance exclusively from completed recorded blocks."""
import csv
import hashlib
import json
from pathlib import Path
import sys

work=Path(sys.argv[1]).resolve()
result=work/'result'
report=work/'report'
report.mkdir(exist_ok=True)
processed=work/'intermediate'
processed.mkdir(exist_ok=True)
summary=json.loads((result/'summary.json').read_text())
rows=[]
pairs=[]
lines=['# Reproduced ngspice experiment','',
       '| Comparison | Original mean (s) | Trial mean (s) | Reduction | Speedup | Paired saving 95% CI (s) | Wins |',
       '|---|---:|---:|---:|---:|---:|---:|']
def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()
for label,stats in summary.items():
    if 'paired_delta_s' not in stats:
        continue
    lo,hi=stats['paired_95ci_s']
    a,b=stats['baseline_mean_s'],stats['trial_mean_s']
    n=len(stats['paired_delta_s'])
    lines.append(f'| {label} | {a:.2f} | {b:.2f} | {stats["reduction_percent"]:+.2f}% | {a/b:.4f} | [{-hi:+.2f}, {-lo:+.2f}] | {stats["faster_pairs"]}/{n} |')
    for i,d in enumerate(stats['paired_delta_s'],1):
        pairs.append(dict(stage=label,block=i,saving_s=-d,relative_saving_pct=stats['paired_relative_savings_pct'][i-1]))
for blocks_path in sorted(result.rglob('blocks.json')):
    for i,block in enumerate(json.loads(blocks_path.read_text()),1):
        for variant,record in block.items():
            folder=Path(record['path'])
            execution=json.loads((folder/'execution.json').read_text())
            verified=json.loads((folder/'validation.json').read_text())
            if not verified['passed']:
                raise RuntimeError(f'Invalid formal waveform in {folder}')
            phases=record['phases']
            rows.append(dict(stage=str(blocks_path.parent.relative_to(result)),block=i,variant=variant,
                solver=execution['profile']['solver'],threads=execution['profile']['threads'],
                stop_ns=execution['stop_ns'],memory_policy=execution['memory_policy'],sha256=execution['sha256'],
                wall_s=record['wall'],user_s=record['user'],system_s=record['system'],peak_rss_kib=record['rss_kib'],
                involuntary_context_switches=record['involuntary_cs'],voluntary_context_switches=record['voluntary_cs'],
                major_faults=record['major_faults'],minor_faults=record['minor_faults'],
                assembly_s=phases.get('Matrix load time'),factorization_s=phases.get('Matrix factor time'),
                solve_s=phases.get('Matrix solve time'),iterations=phases.get('Total iterations'),
                waveform_pass=True,path=str(folder.relative_to(work)),console_sha256=digest(folder/'console.log')))
for name,data in [('measurements',rows),('pairs',pairs)]:
    with (processed/f'{name}.csv').open('w',newline='') as f:
        out=csv.DictWriter(f,list(data[0]))
        out.writeheader()
        out.writerows(data)
(processed/'numa-pages.json').write_text(json.dumps(summary['numa-pages'],indent=2)+'\n')
factorial=result/'factorial/status.json'
lines+=['','Final deployment: '+(result/'final/selection.json').read_text().strip(),'',
        f'Formal waveform validations: {len(rows)}/{len(rows)}.',
        f'Geometric mean final paired speedup: {summary["final"]["geometric_speedup"]:.6f}×.',
        'Memory placement: '+json.dumps(summary['numa-pages']),
        'Factorial: '+(factorial.read_text().strip() if factorial.exists() else 'see result/factorial/summary.json'),
        'Profile samples are user CPU-clock samples at 99 Hz. Stage counters can overlap.',
        '', '[Normalized runs](../intermediate/measurements.csv) · [Paired results](../intermediate/pairs.csv)']
(report/'experiment.md').write_text('\n'.join(lines)+'\n')
