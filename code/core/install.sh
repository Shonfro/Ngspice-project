#!/usr/bin/env bash
set -Eeuo pipefail
root=$1
work=$2
mkdir -p "$work"/{src,build,install,log}
source_directory="$work/src/ngspice-47"
if [[ ! -x "$source_directory/configure" ]]; then
    tar -xzf "$root/source/ngspice-47.tar.gz" -C "$work/src"
fi
cd "$work/build"
export LC_ALL=C
export CFLAGS='-O2 -g -fno-reorder-blocks-and-partition'
export CXXFLAGS='-O2 -g -fno-reorder-blocks-and-partition'
export LDFLAGS='-Wl,--emit-relocs -Wl,--build-id=sha1'
"$source_directory/configure" --prefix="$work/install" --without-x --with-readline=no \
    --enable-xspice --enable-cider --enable-klu --enable-openmp >"$work/log/configure.log" 2>&1
make -j"${BUILD_JOBS:-8}" >"$work/log/make.log" 2>&1
make install >"$work/log/make-install.log" 2>&1
readelf -SWn "$work/install/bin/ngspice" > "$work/log/elf.txt"
grep -q ' .rela.text ' "$work/log/elf.txt"
grep -q ' .symtab ' "$work/log/elf.txt"
"$work/install/bin/ngspice" --version >"$work/log/ngspice-version.log"
sha256sum "$work/install/bin/ngspice" >"$work/log/original.sha256"
