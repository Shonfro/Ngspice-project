"""Encode independently keyed archive facts as named SMT-LIB equalities."""
import hashlib
import json
from pathlib import Path
import sys

def fingerprint(value):
    payload=json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()
    return int(hashlib.sha256(payload).hexdigest(),16)

facts=json.loads(Path(sys.argv[1]).read_text())
target=Path(sys.argv[2])
lines=['(set-logic QF_LIA)','(set-option :produce-unsat-cores true)']
for i,fact in enumerate(facts):
    lines += [f'; {ascii(fact["label"])}',f'(declare-const fact_{i} Int)',
              f'(assert (! (= fact_{i} {fingerprint(fact["actual"])}) :named archived_{i}))',
              f'(assert (! (= fact_{i} {fingerprint(fact["expected"])}) :named expected_{i}))']
target.write_text('\n'.join(lines+['(check-sat)'])+'\n')
target.with_name('negative-control.smt2').write_text(
    '\n'.join(lines+['(assert (! (not (= fact_0 '+str(fingerprint(facts[0]['expected']))+')) :named injected_mismatch))',
                     '(check-sat)','(get-unsat-core)'])+'\n')
