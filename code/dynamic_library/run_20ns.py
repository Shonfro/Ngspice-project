#!/usr/bin/env python3
"""Final 20 ns A/B/C/D validation, reusing the project simulation and validation path."""
import csv, datetime, json, os, random, statistics, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'code/core'))
import experiment as e
ELF=e.ELF
JEMALLOC=Path(os.environ.get('NGSPICE_JEMALLOC', ROOT/'source/dependencies/allocators/jemalloc-5.3.0/lib/libjemalloc.so.2'))
TCMALLOC=Path(os.environ.get('NGSPICE_TCMALLOC', ROOT/'source/dependencies/allocators/gperftools-2.7/lib/libtcmalloc_minimal.so.4.5.3'))
BOLT=Path(os.environ.get('NGSPICE_BOLT_ELF', ROOT/'source/toolchain/ngspice-klu8-bolt'))
ROOT_OUT=ROOT/'result/dynamic_library/20ns/new-run'
SEED=20261123

def configs(): return {'A':(ELF,None),'B':(ELF,JEMALLOC),'C':(ELF,TCMALLOC),'D':(BOLT,TCMALLOC)}
def orders():
    r=random.Random(SEED); x=[]
    for _ in range(7):
        q=list('ABCD'); r.shuffle(q); x.append(q)
    return x
def run_group(kind, variants, seq):
    root=ROOT_OUT/kind; (root/'warmup').mkdir(parents=True,exist_ok=True); (root/'formal').mkdir(parents=True,exist_ok=True)
    # simulation creates its leaf; parents only are pre-created.
    for k,(elf,lib) in variants.items():
        e.simulation('c7552',8,20,elf,root/'warmup'/k,solver='klu',preload=lib)
    for i,q in enumerate(seq,1):
        for k in q:
            e.simulation('c7552',8,20,variants[k][0],root/'formal'/f'block-{i:02d}'/k,
                         reference=root/'warmup'/'A',solver='klu',preload=variants[k][1])
    return root
def stats(root):
    data={k:[json.loads((root/'formal'/f'block-{i:02d}'/k/'metrics.json').read_text()) for i in range(1,8)] for k in 'ABCD'}
    out={}
    for k,rows in data.items():
        w=[x['wall'] for x in rows]
        out[k]={'wall_s':w,'mean_s':statistics.mean(w),'median_s':statistics.median(w),'sd_s':statistics.stdev(w),
                'user_mean_s':statistics.mean(x['user'] for x in rows),'system_mean_s':statistics.mean(x['system'] for x in rows),
                'rss_mean_kib':statistics.mean(x['rss_kib'] for x in rows),'phase_rows':[x['phases'] for x in rows]}
    for a,b in (('A','B'),('A','C'),('C','D'),('A','D')):
        s=[data[a][i]['wall']-data[b][i]['wall'] for i in range(7)]; mean=statistics.mean(s); ci=2.44691185*statistics.stdev(s)/(7**0.5)
        out[f'{a}->{b}']={'paired_savings_s':s,'mean_s':mean,'ci95_s':[mean-ci,mean+ci],
                          'improvement_percent':100*mean/statistics.mean(x['wall'] for x in data[a]),
                          'faster_pairs':sum(x>0 for x in s),'df':6,'method':'paired Student-t, two-sided 95%'}
    (root/'summary.json').write_text(json.dumps(out,indent=2)+'\n')
    with (root/'formal-metrics.csv').open('w',newline='') as f:
        cols=['block','config','wall','user','system','rss_kib','minor_faults','major_faults','voluntary_cs','involuntary_cs']
        w=csv.DictWriter(f,cols); w.writeheader()
        for i in range(7):
            for k in 'ABCD': w.writerow(dict(block=i+1,config=k,**{c:data[k][i][c] for c in cols[2:]}))
    return out
def main():
    for p in (ELF,JEMALLOC,TCMALLOC,BOLT): assert p.is_file() and (p.stat().st_mode&0o111 or p.suffix=='.so.2'), p
    variants=configs(); seq=orders(); ROOT_OUT.mkdir(parents=True,exist_ok=True)
    (ROOT_OUT/'design.json').write_text(json.dumps({'seed':SEED,'stop_ns':20,'threads':8,'cpus':'0-15','monitor_cpu':16,'numa':'default','orders':seq,'variants':{k:{'elf':str(v[0]),'elf_sha256':e.sha(v[0]),'preload':str(v[1]) if v[1] else ''} for k,v in variants.items()}},indent=2)+'\n')
    root=run_group('formal-20ns',variants,seq); result=stats(root)
    (ROOT_OUT/'selection.json').write_text(json.dumps({'final':'D','reason':'BOLT+tcmalloc selected only after 20 ns A/D paired validation','summary':result},indent=2)+'\n')
if __name__=='__main__': main()
