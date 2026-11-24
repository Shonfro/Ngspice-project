#!/usr/bin/env python3
"""2 ns allocator comparison using the existing experiment.simulation path."""
import csv, datetime, json, os, statistics, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'code/core'))
import experiment as e

ELF=e.ELF
JEMALLOC=Path(os.environ.get('NGSPICE_JEMALLOC', ROOT/'source/dependencies/allocators/jemalloc-5.3.0/lib/libjemalloc.so.2'))
TCMALLOC=Path(os.environ.get('NGSPICE_TCMALLOC', ROOT/'source/dependencies/allocators/gperftools-2.7/lib/libtcmalloc_minimal.so.4.5.3'))
BOLT=Path(os.environ.get('NGSPICE_BOLT_ELF', ROOT/'source/toolchain/ngspice-klu8-bolt'))
ORDERS=[['A','B','C'],['B','C','A'],['C','A','B']]
LIBS={'A':None,'B':JEMALLOC,'C':TCMALLOC}

def run_matrix(root, variants, tag):
    warm=root/'warmup'; formal=root/'formal'; warm.mkdir(parents=True); formal.mkdir(parents=True)
    refs={}
    rows=[]
    for key in 'ABC':
        folder=warm/key
        rows.append(e.simulation('c7552',8,2,variants[key][0],folder,solver='klu',preload=variants[key][1]))
        refs[key]=folder
    for block,order in enumerate(ORDERS,1):
        for key in order:
            folder=formal/f'block-{block:02d}'/key
            row=e.simulation('c7552',8,2,variants[key][0],folder,reference=refs['A'],solver='klu',preload=variants[key][1])
            rows.append(row)
    e.write(root/'manifest.json',{'tag':tag,'stop_ns':2,'warmup_runs':3,'formal_runs':9,'orders':ORDERS,
                                  'variants':{k:{'elf':str(v[0]),'elf_sha256':e.sha(v[0]),'preload':str(v[1]) if v[1] else ''}
                                              for k,v in variants.items()}})
    return rows

def summarize(root, variants):
    data={k:[] for k in variants}
    for block in range(1,4):
        for k in variants:
            p=root/'formal'/f'block-{block:02d}'/k/'metrics.json'
            data[k].append(json.loads(p.read_text()))
    out={}
    for k,vals in data.items():
        walls=[x['wall'] for x in vals]
        out[k]={'wall':walls,'mean_s':statistics.mean(walls),'median_s':statistics.median(walls),'sd_s':statistics.stdev(walls),
                'user_mean_s':statistics.mean(x['user'] for x in vals),'system_mean_s':statistics.mean(x['system'] for x in vals),
                'rss_mean_kib':statistics.mean(x['rss_kib'] for x in vals),'minor_faults': [x['minor_faults'] for x in vals],
                'major_faults':[x['major_faults'] for x in vals], 'voluntary_cs':[x['voluntary_cs'] for x in vals],
                'involuntary_cs':[x['involuntary_cs'] for x in vals]}
    for candidate in ('B','C'):
        savings=[data['A'][i]['wall']-data[candidate][i]['wall'] for i in range(3)]
        mean=statistics.mean(savings); ci=4.30265273*statistics.stdev(savings)/(3**0.5)
        out[f'A->{candidate}']={'paired_savings_s':savings,'mean_s':mean,'ci95_s':[mean-ci,mean+ci],
                                'improvement_percent':100*mean/statistics.mean([x['wall'] for x in data['A']]),
                                'faster_pairs':sum(x>0 for x in savings)}
    (root/'summary.json').write_text(json.dumps(out,indent=2)+'\n')

def main():
    assert ELF.is_file() and ELF.stat().st_mode & 0o111
    assert JEMALLOC.is_file() and TCMALLOC.is_file()
    batch='allocator-2ns-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
    root=ROOT/'result/dynamic_library/2ns-exploratory'
    (root/'log').mkdir(parents=True,exist_ok=True)
    variants={'A':(ELF,None),'B':(ELF,JEMALLOC),'C':(ELF,TCMALLOC)}
    run_matrix(root/'formal-allocator',variants,'original-elf')
    summarize(root/'formal-allocator',variants)
    best=max(('B','C'),key=lambda k: json.loads((root/'formal-allocator/summary.json').read_text())[f'A->{k}']['mean_s'])
    bestlib=variants[best][1]
    bolt_variants={'A':(ELF,None),'B':(ELF,bestlib),'C':(BOLT,bestlib)}
    if BOLT.is_file() and BOLT.stat().st_mode & 0o111:
        run_matrix(root/'formal-bolt',bolt_variants,'bolt-combination')
        summarize(root/'formal-bolt',bolt_variants)
    else:
        (root/'formal-bolt/status.json').write_text(json.dumps({'status':'skipped','reason':'BOLT ELF unavailable'},indent=2)+'\n')
    (root/'selection.json').write_text(json.dumps({'best_candidate_by_mean_s':best,'batch':batch,'stop_ns':2},indent=2)+'\n')

if __name__=='__main__': main()
