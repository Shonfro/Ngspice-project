#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = --help ] || [ "$#" -eq 0 ]; then
  cat <<'EOF'
Usage: run_dynamic_library_experiment.sh --ngspice ELF --input DRIVER --out DIR [--preload LIB]
Per configuration: one warmup then seven measured runs. This script never changes system libraries.
EOF
  exit 0
fi
ngspice=; input=; out=; preload=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --ngspice) ngspice=$2; shift 2;;
    --input) input=$2; shift 2;;
    --out) out=$2; shift 2;;
    --preload) preload=$2; shift 2;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done
test -x "$ngspice"; test -r "$input"; test -n "$out"; mkdir -p "$out"
printf 'ngspice=%s\ninput=%s\npreload=%s\n' "$(readlink -f "$ngspice")" "$(readlink -f "$input")" "$preload" > "$out/config.txt"
for phase in warmup run1 run2 run3 run4 run5 run6 run7; do
  /usr/bin/time -f '%e %U %S %M %F %R %w %c' -o "$out/$phase.time" env LD_PRELOAD="$preload" "$ngspice" -b "$input" > "$out/$phase.console.log" 2>&1
done
