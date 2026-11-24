"""Cross-check the included historical final statistics without launching simulation."""
import csv
import json
from pathlib import Path
import statistics
import tarfile

home=Path(__file__).resolve().parents[2]
archive=next((home/'raw').glob('ngspice-c7552-full-*.tar.gz'))
ends='result/final-validation/klu8-20261115-051336-94122/'
with tarfile.open(archive,'r:gz') as contents:
    prefix='ngspice_workload_select/'+ends
    blocks=json.load(contents.extractfile(prefix+'blocks.json'))
    summary=json.load(contents.extractfile(prefix+'summary.json'))
a=[row['A']['wall'] for row in blocks]
b=[row['B']['wall'] for row in blocks]
d=[x-y for x,y in zip(a,b)]
mean=statistics.mean(d)
width=2.44691185*statistics.stdev(d)/7**.5
assert len(d)==7 and abs((1-statistics.mean(b)/statistics.mean(a))*100-summary['reduction_percent'])<1e-9
target=home/'output/archive-analysis'
target.mkdir(parents=True,exist_ok=True)
data=dict(formal_pairs=len(d),original_mean_s=statistics.mean(a),optimized_mean_s=statistics.mean(b),
          savings_s=d,average_saving_s=mean,ci95_s=[mean-width,mean+width],
          reduction_percent=100*mean/statistics.mean(a))
(target/'final-summary.json').write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps(data,indent=2))
