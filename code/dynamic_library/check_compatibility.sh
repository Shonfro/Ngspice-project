#!/usr/bin/env bash
set -euo pipefail
out=${1:-result/dynamic_library/compatibility}
mkdir -p "$out"
uname -a > "$out/uname.txt"
ldd --version 2>&1 | head -1 > "$out/glibc.txt"
readlink -f /lib64/ld-linux-x86-64.so.2 > "$out/loader.txt"
lscpu > "$out/lscpu.txt"
ldconfig -p > "$out/ldconfig.txt" || true
for f in /lib64/libc.so.6 /lib64/libm.so.6 /lib64/libpthread.so.0 /lib64/libdl.so.2; do
  [ -e "$f" ] && { readelf -V "$f"; readelf -Ws "$f"; } > "$out/$(basename "$f").elf.txt" 2>&1 || true
done
printf 'compatibility evidence: %s\n' "$out"
