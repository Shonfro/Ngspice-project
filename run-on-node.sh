#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$(realpath "$0")")" && pwd)
node=${1:?Usage: bash run-on-node.sh NODE smoke\|full\|check OUTPUT}
mode=${2:?Provide smoke, full, or check}
work=${3:?Provide a new output directory}
exec bash "$root/run.sh" "$mode" "$work" --node "$node" --skip-numa
