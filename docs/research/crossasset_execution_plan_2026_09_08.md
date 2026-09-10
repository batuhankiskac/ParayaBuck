# Cross-asset completion execution plan — 2026-09-08

Status: internal coordination plan. This document authorizes research-only
acquisition, normalization, schema preflight, and bounded diagnostics for
X01–X06. It does not authorize production, configuration, registry,
PAPER/LIVE, trading, new subscriptions, purchases, secrets, or automatic
promotion.

## Checkout ruling

Continue in the current checkout. The ongoing authorized research, acquired
files, and uncommitted frozen artifacts live here, and the user explicitly
requested continuation. A fresh worktree would omit that state. The cost of
lower isolation is mitigated by disjoint cross-asset paths, new task-specific
hash pins, protocol-before-result manifests, and no edits to production,
configuration, V3, equity-owned, or crypto-owned implementation files.

Previous artifacts remain immutable inputs. H14 is already a completed
18-cell retrospective diagnostic with no current-V3 contribution proof and is
not rerun. Corrected P364 is already audited and remains research-only; it is
read for methodological constraints and is not rerun.

## Acceptance boundary

Each implementable step must have a short brief/protocol, an explicit input
manifest with hashes, focused tests, targeted validation, and a separate
review before any canonical economic claim. This checkpoint only delivers
preflight evidence and blockers. It does not claim an alpha result,
complementarity, or product readiness.

The frozen V3 baseline remains `equity_residual_momentum_v3` with a 0.55
ceiling. The crypto incumbent remains `crypto_low_variance_hysteresis_v1`
with its existing 0.05 ceiling. Shared gross and cash limits remain 0.60 and
0.40. A later V3 baseline run must use native no-persist paths and the frozen
inputs, and must be independently reviewed before launch; this plan does not
launch it.

## Workstreams

1. **X01 curve and ETF carry inputs.** Verify the 2016-01-01..2026-07-31
   official Treasury/FRED curve panel and quarantine all later observations.
   Record that the existing packet has no historical SHY/IEF/TLT bars,
   distributions, or PIT duration. Do not use current IEF duration or current
   holdings as historical data. Freeze an exact ETF rule only after an
   evidence-supported historical ETF source arrives; otherwise retain the
   maturity-bucket approximation as explicitly unavailable for economics.
2. **X02 commodity curve gate.** Preserve CME methodology/spec evidence and
   exhaust lawful public/authorized historical settlement, roll, and fund
   replication probes. Keep the precise DataMine/license blocker when no
   usable dated curve rows are available. Do not purchase, sign up, or use
   Stooq challenge/Yahoo 429 bodies.
3. **X03 country ETF accounting/PIT preflight.** Normalize existing
   MCHI/EWJ/EWZ bars, corporate actions, the locally parsed SEC MCHI N-PORT
   sample, two identity-bound EWJ/EWZ N-PORT samples, and FRED
   USD/CNY/USD/HKD observations. The MCHI XML parse and the two bounded
   samples remain metadata-only schema evidence; no holding rows are admitted.
   Do not infer historical valuation or membership vintages from these samples.
4. **X04 pairs data gate.** Normalize the local SPY/QQQ price subset and FINRA
   one-day short-volume sample. Keep borrow/locate/recall/financing and
   synchronized two-leg execution as blockers. No long-only substitute is
   labeled X04 and no economic return is produced.
5. **X05/X06 derivative schema preflight.** Reuse the existing Binance files;
   do not duplicate downloads owned by the crypto queue. Validate timestamps,
   symbols/contracts, joins, expiry proximity, funding intervals, index/mark
   fields, and bounded coverage. Require a future two-leg collateral,
   margin, liquidation, settlement, and cost ledger before economic claims.
   Product integration remains outside scope.
6. **Shared ledger and review.** Maintain one internal ledger with separate
   statuses: acquired, causal-normalized, protocol-frozen, tested,
   data-access-blocked, and outside-product-capability. Root reviews the
   generated artifacts independently before any canonical economics.

## Deliverables and stop conditions

The task-specific deliverables are:

- this plan and `crossasset_internal_execution_ledger_2026_09_08.md`;
- `data/research_manifests/crossasset_preflight_2026_09_08.json`, frozen
  before results;
- `scripts/research/crossasset_preflight_2026_09_08.py`, which only reads
  pinned research inputs and writes generated diagnostics;
- `tests/research/test_crossasset_preflight_2026_09_08.py`;
- generated X01–X06 normalization, schema, and blocker receipts under
  `data/research/generated/crossasset_preflight_2026_09_08/`;
- a result receipt containing source hashes, row counts, cutoff checks,
  schema checks, and explicit blockers.

The first executable protocol is the bounded cross-asset preflight. It may
normalize existing real files and produce data-quality evidence, but it must
stop before candidate return/economic execution whenever PIT, historical
ETF, borrow, derivative-risk, or product-capability gates are unresolved.
