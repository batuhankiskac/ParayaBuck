# Hard blockers — NYSE Group Security Master entitlement — 2026-09-22

Predeclared. Do not weaken after seeing a quote. A low price never converts a FAIL to a PASS.

1. No usable history before the canonical start. Archive (full+delta) must begin no later than
   the first 2016 canonical research date. A later start is a HARD FAIL.
2. No issue-level stable identity. Without persistent non-reused Stock Number semantics
   (or a proven equivalent), ticker reuse cannot be resolved. HARD FAIL.
3. Product taxonomy insufficient. Ordinary/company securities must separate from ETFs, ETNs,
   preferred, warrants, rights, units, funds, structured products, ADR classes. HARD FAIL.
4. Research/backtest use prohibited. Internal quantitative research, historical backtesting, and
   derived internal classification must be expressly permitted. HARD FAIL.
5. Purely restated history. If historical files are only today's retrospectively corrected
   reconstruction with no way to reconstruct the original knowledge state (correction-arrival
   history), HARD FAIL — unless governance proves a conservative PIT-safe alternative in writing.
6. No legal internal storage/derived-use path. Raw files, checksums, backups, and derived
   effective-dated tables must have a legal storage path while subscribed. HARD FAIL.
   CONDITIONAL only: originals must be destroyed at termination but reproducible derived
   classifications (no raw vendor fields) may remain — record re-entitlement cost for re-audit.

Any single HARD FAIL → `ENTITLEMENT_REJECTED` for the NYSE path; trigger
`NASDAQ_CBOE_CONTINGENCY.md`. All HARD PASS (or retention-only CONDITIONAL) + in-scope quote →
`ENTITLEMENT_ACCEPTED_PROCEED_TO_HISTORICAL_SAMPLE`.
