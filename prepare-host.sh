#!/usr/bin/env bash
# Explicit dependency setup only; run.sh never invokes sudo or downloads packages.
set -Eeuo pipefail
root=$(cd "$(dirname "$(realpath "$0")")" && pwd)
case "${1:---help}" in
--system)
    elevate=()
    if [[ $(id -u) != 0 ]]; then elevate=(sudo); fi
    if command -v apt-get >/dev/null; then
        "${elevate[@]}" apt-get update
        "${elevate[@]}" apt-get install -y build-essential binutils time util-linux coreutils tar numactl sysstat default-jre-headless python3-venv linux-tools-common
        # A custom kernel may not have a repository package. Give a specific remedy.
        "${elevate[@]}" apt-get install -y "linux-tools-$(uname -r)" || {
            echo "No repository tools package for this kernel. Ask administrator for matching perf; set NGSPICE_PERF=/path/to/perf."
        }
    elif command -v dnf >/dev/null || command -v yum >/dev/null; then
        manager=$(command -v dnf || command -v yum)
        "${elevate[@]}" "$manager" install -y gcc gcc-c++ make binutils time util-linux coreutils tar numactl sysstat java-11-openjdk-headless perf
        echo 'Python 3.11+ may need installation separately. Set NGSPICE_PYTHON to its path.'
    else
        echo 'Unsupported package manager. Install Python 3.11+, gcc/g++, make, binutils, time, numactl, sysstat, Java and kernel-compatible perf manually.' >&2
        exit 1
    fi
    ;;
--z3)
    task_python=${NGSPICE_PYTHON:-python3}
    "$task_python" -c 'import sys; assert sys.version_info >= (3,11)'
    "$task_python" -m venv "$root/.venv"
    "$root/.venv/bin/python" -m pip install 'z3-solver==4.12.2.0'
    echo "Set NGSPICE_PYTHON=$root/.venv/bin/python or let run.sh select this venv automatically."
    ;;
*)
    echo 'bash prepare-host.sh --system : explicitly install system dependencies (administrator rights and package access)'
    echo 'bash prepare-host.sh --z3     : create local .venv and install Z3 (package access; no sudo)'
    ;;
esac
