#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$(realpath "$0")")" && pwd)
case "${1:---help}" in
  --install|--system)
    elevate=(); [[ $(id -u) -eq 0 ]] || elevate=(sudo)
    if command -v apt-get >/dev/null 2>&1; then
      "${elevate[@]}" apt-get update
      "${elevate[@]}" apt-get install -y build-essential binutils make gcc g++ cmake time util-linux coreutils tar numactl sysstat default-jre-headless python3-venv
      "${elevate[@]}" apt-get install -y "linux-tools-$(uname -r)" || echo 'WARN: 当前内核没有匹配 linux-tools；请管理员提供同内核 perf。'
    elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
      pm=$(command -v dnf || command -v yum)
      "${elevate[@]}" "$pm" install -y gcc gcc-c++ make cmake binutils time util-linux coreutils tar numactl sysstat java-11-openjdk-headless perf
    else
      echo 'ERROR: 未识别 apt/dnf/yum；请手工安装 README 所列依赖。' >&2; exit 1
    fi
    ;;
  --user-python)
    py=${NGSPICE_PYTHON:-python3}; "$py" -m venv "$root/.venv"; "$root/.venv/bin/python" -m pip install 'z3-solver==4.12.2.0'
    ;;
  *)
    echo '用法：bash setup.sh --install（安装系统依赖，需要 sudo 或 root）'
    echo '      bash setup.sh --user-python（用户目录安装 Python/Z3，不修改系统）'
    ;;
esac
