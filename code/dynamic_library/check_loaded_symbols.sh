#!/usr/bin/env bash
set -euo pipefail
lib=${1:?library path required}
elf=${2:?ngspice ELF required}
test -r "$lib"; test -x "$elf"
printf 'library=%s\n' "$(readlink -f "$lib")"
sha256sum "$lib"
file "$lib"; readelf -d "$lib" | grep -E 'SONAME|NEEDED' || true
nm -D "$lib" | grep -E ' (malloc|free|calloc|realloc|posix_memalign|aligned_alloc|malloc_usable_size)$' || true
LD_PRELOAD="$lib" LD_DEBUG=libs,symbols "$elf" -v > /tmp/ngspice-dl-version.out 2> /tmp/ngspice-dl-debug.log || true
grep -E 'malloc|free|calloc|realloc|posix_memalign' /tmp/ngspice-dl-debug.log | head -200
