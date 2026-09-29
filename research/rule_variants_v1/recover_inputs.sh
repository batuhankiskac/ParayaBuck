#!/bin/sh
# Recover the archived profit_max_v1 inputs (QQQ/QLD/SPY/VOO bars + restated dividends)
# into the git-ignored data/research/rule_variants_v1/ and verify their SHA-256.
set -eu
cd "$(dirname "$0")/../.."
TAG=archive/research/profit-max-v1-2026-09
OUT=data/research/rule_variants_v1
mkdir -p "$OUT"
for f in profit_max_v1_daily_split_sip.csv profit_max_v1_corporate_actions.csv; do
  git show "$TAG:data/historical/$f" > "$OUT/$f"
done
shasum -a 256 -c <<SUMS
87e20c9e32f0e42f67f6e9bd4c4e51b5e06003277bd9b3a875e25df3cbf24b0e  $OUT/profit_max_v1_daily_split_sip.csv
c0efd11fa4492d89a1ff997b8cc86ba600f98204197e177b63b82a9cc485fdcd  $OUT/profit_max_v1_corporate_actions.csv
SUMS
