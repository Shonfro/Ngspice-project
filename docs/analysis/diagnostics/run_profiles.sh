#!/usr/bin/env bash
set -Eeuo pipefail

package=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
formal="$package/result/dynamic_library/20ns/measurements/formal-20ns/formal/block-01"
perf="$package/source/toolchain/perf"
original=${NGSPICE_ORIGINAL_ELF:-/home/lyy/workplace/eda/bolt/src/ngspice/upstream-20261107-231732/install/bin/ngspice}
bolt=${NGSPICE_BOLT_ELF:-/home/lyy/workplace/eda/bolt/optimized/v1/ngspice/ngspice-klu8-bolt}
jemalloc=${NGSPICE_JEMALLOC:-$package/source/dependencies/allocators/jemalloc-5.3.0/lib/libjemalloc.so.2}
tcmalloc=${NGSPICE_TCMALLOC:-$package/source/dependencies/allocators/gperftools-2.7/lib/libtcmalloc_minimal.so.4.5.3}
out="$package/docs/analysis/diagnostics/profiles-20ns"
mkdir -p "$out"

configs=${PROFILE_CONFIGS:-ABCD}
for config in $(printf '%s' "$configs" | fold -w1); do
    dir="$out/$config"
    mkdir -p "$dir"
    cp -a "$formal/$config/circuit.sp" "$formal/$config/driver.cir" "$formal/$config/models" "$dir/"
    elf="$original"; preload=""
    case "$config" in
        B) preload="$jemalloc" ;;
        C) preload="$tcmalloc" ;;
        D) elf="$bolt"; preload="$tcmalloc" ;;
    esac
    {
        printf 'config=%s\nelf=%s\npreload=%s\n' "$config" "$elf" "$preload"
        sha256sum "$elf"
        [[ -n "$preload" ]] && sha256sum "$preload"
        printf 'cpu_affinity=0-15\nmonitor_cpu=16\nnuma_policy=default\nthreads=8\nsolver=klu\n'
    } > "$dir/config.txt"
    printf 'perf record -e cpu-clock:u -F 99 -g -- %s -n -b driver.cir\n' "$elf" > "$dir/command.txt"
    if [[ -n "$preload" ]]; then
        (cd "$dir" && env LD_PRELOAD="$preload" taskset -c 0-15 "$perf" record -e cpu-clock:u -F 99 --call-graph "${CALL_GRAPH:-dwarf}" -o perf.data -- "$elf" -n -b driver.cir > perf.stdout 2> perf.stderr)
    else
        (cd "$dir" && taskset -c 0-15 "$perf" record -e cpu-clock:u -F 99 --call-graph "${CALL_GRAPH:-dwarf}" -o perf.data -- "$elf" -n -b driver.cir > perf.stdout 2> perf.stderr)
    fi
    (cd "$dir" && "$perf" report --stdio -i perf.data --sort dso,symbol > perf-report.txt 2> perf-report.stderr || true)
    (cd "$dir" && "$perf" script -i perf.data > perf-script.txt 2> perf-script.stderr || true)
    stat -c 'perf_data_bytes=%s' "$dir/perf.data" > "$dir/status.txt"
done
