#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$(realpath "$0")")" && pwd)
export PYTHONUTF8=1
if [[ -n "${NGSPICE_PYTHON:-}" ]]; then
    task_python=$NGSPICE_PYTHON
    "$task_python" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
else
    task_python=''
    for candidate in "$root/.venv/bin/python" python3.12 python3.11 python3 /opt/vendor/openlane/2.3.10/python/bin/python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,11))' 2>/dev/null; then
            task_python=$candidate
            break
        fi
    done
    [[ -n "$task_python" ]] || { echo 'Python 3.11+ required. Install it or set NGSPICE_PYTHON=/absolute/path/to/python.' >&2; exit 1; }
fi
export LC_ALL=C
exec "$task_python" -B -u "$root/code/core/launch.py" "$@"
