#!/bin/bash
# tbill_sweep_v1 Stage B: E2 control and the sweep arms, 1x/2x, run serially (each ~1-2 s, < 1 GB).
# Setup: see the docstring of adapt_harness.py. Usage: research/tbill_sweep_v1/run_stage_b.sh [python]
set -euo pipefail
PY=${1:-python3}
D="$(cd "$(dirname "$0")" && pwd)/stage_b"
cd "$D"
run() {  # name sweep cost config
  printf '%s %s: ' "$1" "$3"
  "$PY" research/paper_config_v2/run_stage_b.py --config "$D/configs/$4" --cost "$3" --sweep "$2" \
    --out "$D/runs/$1_$3.json.gz" 2>&1 | tail -1
}
for cost in 1.0 2.0; do
  run E2 none "$cost" E2.yaml
  run S1 s1 "$cost" E2_BIL.yaml
  run S2_5 s2_5 "$cost" E2_BIL.yaml
  run S2_10 s2_10 "$cost" E2_BIL.yaml
done
