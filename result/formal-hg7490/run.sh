#!/usr/bin/env bash
set -Eeuo pipefail
base=$(cd "$(dirname "$(realpath "$0")")" && pwd)
: "${TLC_JAR:?Set TLC_JAR to tla2tools.jar}"
jar=$(realpath "$TLC_JAR")
py="${PYTHON:-python3}"
out=$(mktemp -d "${FORMAL_OUTPUT_DIR:-$base}/local-verification.XXXXXX")
# Java's native filename encoding can be ASCII on this host. Execute in an ASCII
# scratch directory, then preserve every log even when a check fails.
stage=$(mktemp -d /tmp/ngspice-formal.XXXXXX)
finish() {
    code=$?
    cp -a "$stage/." "$out/" || code=1
    rm -rf "$stage"
    exit "$code"
}
trap finish EXIT
cp -p "$base"/tla/* "$stage/"
cp -p "$jar" "$stage/tla2tools.jar"
cd "$stage"
java -cp tla2tools.jar tlc2.TLC -workers 1 -config ExperimentPipeline.cfg ExperimentPipeline.tla >tlc.log 2>&1
grep -q 'No error has been found' tlc.log
set +e
java -cp tla2tools.jar tlc2.TLC -workers 1 -config NegativeControl.cfg ExperimentPipeline.tla >tlc-negative.log 2>&1
code=$?
set -e
[[ "$code" != 0 ]]
grep -q 'Invariant Canary is violated' tlc-negative.log
"$py" -B "$base/constraints.py" "$base/inputs/c7552-constraints.json" "$stage/evidence.smt2"
"$py" -B - "$stage" <<'PY' >z3.log
from pathlib import Path
import sys
import z3
directory=Path(sys.argv[1])
print('Z3',z3.get_version_string())
for name,expected in [('evidence.smt2',z3.sat),('negative-control.smt2',z3.unsat)]:
    solver=z3.Solver()
    solver.from_file(str(directory/name))
    observed=solver.check()
    print(name,observed,len(solver.assertions()))
    assert observed==expected
    if observed==z3.unsat:
        print('unsat core:',solver.unsat_core())
PY
printf 'PASS %s\n' "$out"
