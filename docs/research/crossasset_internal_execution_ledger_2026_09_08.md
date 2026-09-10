# Cross-asset internal execution ledger — 2026-09-08

This is a new task-specific internal ledger. The prior
`strategy_queue_execution_ledger_2026_09_07.md` is preserved unchanged.
Statuses below are intentionally orthogonal: `ACQUIRED`,
`CAUSAL_NORMALIZED`, `PROTOCOL_FROZEN`, `TESTED`, `DATA_ACCESS_BLOCKED`, and
`OUTSIDE_PRODUCT_CAPABILITY` do not imply economic acceptance.

## Coordination ruling

`CONTINUE_CURRENT_CHECKOUT`: ongoing authorized research and uncommitted frozen
artifacts are present here, and the user explicitly requested continuation.
Fresh-worktree omission risk is mitigated by disjoint task paths, frozen hash
pins, and no production/config/V3/PAPER/LIVE edits.

## Frozen common boundary

- Cutoff: `2026-07-31T23:59:59Z`.
- Main Treasury curve history: `2016-01-01..2026-07-31`; later rows are
  quarantined and excluded.
- Equity incumbent: `equity_residual_momentum_v3`, ceiling `0.55`.
- Crypto incumbent: `crypto_low_variance_hysteresis_v1`, ceiling `0.05`.
- Shared gross/cash ceilings: `0.60` / `0.40`.
- Native timing: completed-bar decision, next tradable open; no quote-level
  opening claim.
- No economic result is canonical until an independent review of the frozen
  protocol, inputs, tests, and output receipt.
- H14 r4 and corrected P364 are read-only references and are not rerun.

## All 34 candidate inventory at checkpoint

| ID | Current state | Evidence or blocker |
|---|---|---|
| H01 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | SEC/PIT acquisition flow; field-level PIT gate remains |
| H02 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | SEC/PIT acquisition flow; accounting concepts remain |
| H03 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | SEC/PIT acquisition flow; accrual/cash-flow gate remains |
| H04 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | SEC/PIT acquisition flow; annual/event treatment remains |
| H05 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | SEC/PIT acquisition flow; payout history remains |
| H06 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | EDGAR flow; acceptance-time and transaction taxonomy remain |
| H07 | DATA_ACCESS_BLOCKED / TESTED pending | No forecast-vintage archive |
| H08 | DATA_ACCESS_BLOCKED / OUTSIDE_PRODUCT_CAPABILITY / TESTED pending | No release/consensus PIT feed or event adapter |
| H09 | DATA_ACCESS_BLOCKED / TESTED pending | No PIT relationship/event feed |
| H10 | DATA_ACCESS_BLOCKED / TESTED pending | No original filing-vintage text pipeline |
| H11 | DATA_ACCESS_BLOCKED / TESTED pending | No holdings/flow/rebalance archive |
| H12 | DATA_ACCESS_BLOCKED / TESTED pending | No announcement/application PIT archive |
| H13 | DATA_ACCESS_BLOCKED / OUTSIDE_PRODUCT_CAPABILITY / TESTED pending | No intraday/auction truth |
| H14 | PROTOCOL_FROZEN / TESTED | r4 retrospective diagnostic complete; portfolio contribution untested |
| H15 | DATA_ACCESS_BLOCKED / OUTSIDE_PRODUCT_CAPABILITY / TESTED pending | No deal lifecycle archive |
| H16 | DATA_ACCESS_BLOCKED / OUTSIDE_PRODUCT_CAPABILITY / TESTED pending | No spin-off lifecycle archive |
| K01 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | Coin Metrics acquisition; immutable vintage gate remains |
| K02 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | Transfer methodology and PIT mapping remain |
| K03 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | Address/fee semantics and Sybil policy remain |
| K04 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | Supply/mint/burn PIT policy remains |
| K05 | DATA_ACCESS_BLOCKED / TESTED pending | No immutable unlock announcement/actual event archive |
| K06 | DATA_ACCESS_BLOCKED / TESTED pending | No issuer flow publication archive |
| K07 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | Current/mutable labels require immutable PIT snapshot |
| K08 | DATA_ACCESS_BLOCKED / TESTED pending | No contract/bridge/venue ledger |
| K09 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | Binance funding/perp sample; causal lag/PIT gate remains |
| K10 | ACQUIRED / CAUSAL_NORMALIZED pending / TESTED pending | Derivatives probes; completeness/aggregation gate remains |
| K11 | DATA_ACCESS_BLOCKED / OUTSIDE_PRODUCT_CAPABILITY / TESTED pending | No microstructure/cost archive |
| K12 | DATA_ACCESS_BLOCKED / TESTED pending | No UTXO/entity-cluster PIT archive |
| X01 | ACQUIRED / CAUSAL_NORMALIZED / PROTOCOL_FROZEN; DATA_ACCESS_BLOCKED for ETF economics | Treasury/FRED cutoff panel passes; SHY/IEF/TLT history absent; FRED/Treasury PIT vintages absent |
| X02 | ACQUIRED / PROTOCOL_FROZEN; DATA_ACCESS_BLOCKED | CME methodology/spec only; dated settlement/roll rows unavailable without entitlement |
| X03 | ACQUIRED / CAUSAL_NORMALIZED / PROTOCOL_FROZEN; DATA_ACCESS_BLOCKED for value economics | Bars/CA/FX pass; local N-PORT parse finds 565 tags as metadata-only evidence, accession/receipt binding absent, admitted holdings 0 |
| X04 | ACQUIRED / CAUSAL_NORMALIZED / PROTOCOL_FROZEN; DATA_ACCESS_BLOCKED / OUTSIDE_PRODUCT_CAPABILITY | Prices + FINRA short volume only; borrow and two-leg engine absent |
| X05 | ACQUIRED / CAUSAL_NORMALIZED / PROTOCOL_FROZEN; DATA_ACCESS_BLOCKED / OUTSIDE_PRODUCT_CAPABILITY | Bounded Binance spot/perp/dated/index files; full curve, collateral, margin, settlement absent |
| X06 | ACQUIRED / CAUSAL_NORMALIZED / PROTOCOL_FROZEN; DATA_ACCESS_BLOCKED / OUTSIDE_PRODUCT_CAPABILITY | Bounded spot/perp/funding/index files; historical OI rejected and risk ledger absent |

## First protocol run

The frozen preflight manifest is
`data/research_manifests/crossasset_preflight_2026_09_08.json`. It is a
read-only, no-persist candidate-data gate. Its output is the generated result
receipt and per-card normalized samples. It does not run V3, H14, P364, a
short leg, a derivative leg, or any combined portfolio.

At this checkpoint:

- X01: official curve history is acquired and normalized for the cutoff;
  historical ETF bars/distributions/duration are blocked.
- X02: exact commodity carry is blocked by missing entitled historical
  settlements/curve rows; methodology/spec evidence is retained.
- X03: accounting/PIT schema sample can be inspected; the local 565-tag
  N-PORT parse is metadata-only because accession/receipt binding is absent.
  Value ranking is blocked by historical valuation vintages and
  membership/FX available-at mapping.
- X04: descriptive price/short-volume gate can be tested; borrow and
  synchronized short execution remain blocked.
- X05/X06: timestamp/schema/join preflight can be tested; economic claims are
  blocked by incomplete derivative risk and two-leg accounting.

## Review handoff

Root must independently inspect the frozen manifest, script, tests, output
hashes, and this ledger before treating any preflight as canonical economics.
No post-result parameter tuning is allowed. Any future economic run requires
a new protocol hash and a new ledger entry.

## X01 acquisition and V3 baseline update — 2026-09-08

The X01 source protocol was frozen before any return inspection in
`docs/research/x01_alpaca_acquisition_protocol_2026_09_08.md`.

The existing authorized Alpaca/SIP path was exercised by
`scripts/research/acquire_crossasset_x01_alpaca_2026_09_08.py` for both raw
and split-adjusted bars, using the canonical three-symbol request and the
documented one-symbol fallback. The sanitized receipt is under
`data/research/external/crossasset_public_2026_09_08/x01_alpaca/receipt.json`.
An earlier local probe attempt recorded DNS failure in a superseded receipt.
The authoritative receipt now records HTTP 401 for the primary and every
one-symbol fallback, for both raw and split-adjusted requests. No HTTP 401
payload, credential, or header was stored. Coverage is therefore zero for
SHY/IEF/TLT, and distributions/duration remain `NOT_ACQUIRED`.

The official Treasury endpoint was attempted for every year 2016 through
2026 by `scripts/research/acquire_crossasset_x01_treasury_2026_09_08.py`.
The receipt is
`data/research/external/crossasset_public_2026_09_08/x01_treasury/official_treasury_year_fetch_receipt.json`.
The authoritative receipt records HTTP 200 and offline schema validation for
all 11 requested years, with `network_downloaded_years` equal to 2016 through
2026, `reused_years=[]`, and `missing_years=[]`. An earlier local probe
attempt recorded DNS failure before these task files were available; that
superseded chronology is retained only as an attempt record. The normalized
cutoff panel remains 2,645 rows from 2016-01-04 through 2026-07-31 with 11
finite common maturities, and the separate quarantine contains 25 rows from
2026-08-03 through 2026-09-04. The manifest-pinned FRED panel is separately
cut at 2026-07-31: 2,761 in-cutoff rows (2,645 nonempty), with 24 later
nonempty rows written to quarantine and excluded from X01 inputs.

The unchanged V3 baseline plan is frozen in
`docs/research/v3_baseline_no_persist_protocol_2026_09_08.md`. Its verifier
`scripts/research/v3_baseline_no_persist_verify_2026_09_08.py` passed with
the exact v5 bars, lifetime, corporate-action, and settings hashes; it
reports `READY_FOR_INDEPENDENT_REVIEW`, `launch=false`, `persist=false`,
`state_path=null`, and `equity_ceiling=0.55`. No baseline run was launched.

Reviewer-ready focused tests now pass `8 passed`, and Ruff passes for the new
X01/V3 scripts and tests. The next gate is independent review of the frozen
X01 acquisition and V3 protocols; no ETF economics may start before that
review and historical ETF/distribution/PIT duration evidence.

## Early checkpoint result receipt

The frozen protocol manifest is
`data/research_manifests/crossasset_preflight_2026_09_08.json` with SHA-256
`8b3de64d8fe6ddff66be33a40ccd4dbfdb1700cd22aefe23f49e9ac3bb27ff89`.
The read-only runner is
`scripts/research/crossasset_preflight_2026_09_08.py`; focused validation is
`tests/research/test_crossasset_preflight_2026_09_08.py` and passed `5 passed`
in the project virtual environment.

Generated evidence is under
`data/research/generated/crossasset_preflight_2026_09_08/` with a
`validation_receipt.json` containing hashes for `results.json` and each
normalized output. The run reported `economic_execution=false` and
`product_edits=false`.

Observed new preflight coverage:

- X01: 2,645 sorted unique Treasury sessions from 2016-01-04 through
  2026-07-31, all 11 common maturities finite; 25 rows from 2026-08-03 through
  2026-09-04 are strictly quarantined. Official iShares product pages were
  probed, while the as-of holdings routes returned HTML under a JSON content
  type and no structured historical payload. SHY/IEF/TLT PIT duration and
  historical holdings remain missing.
- X03: 7,977 normalized MCHI/EWJ/EWZ bars (2,659 per symbol), 67 target cash
  actions, a local SEC MCHI parse with 565 investment tags, and official
  identity-bound EWJ/EWZ N-PORT samples with 185/54 tags. All 804 parsed tags
  are metadata-only with zero admitted holding rows; MCHI accession/receipt
  binding and continuous valuation/membership vintages remain blocked. FRED
  FX rows are present through the cutoff.
- X04: two FINRA rows for SPY/QQQ. The output labels short volume as
  descriptive and not borrow/locate evidence.
- X05/X06: 745 spot rows and 745 perpetual rows with 745 common hourly
  timestamps, 94 funding observations with mark prices, 24 dated-contract
  hourly rows, one expiry-proximity daily row, and 24 index-price rows.
  Historical OI is recorded as Binance `-1130`; collateral, margin,
  liquidation, settlement, and two-leg capability remain blocked.

The preflight status is
`PREFLIGHT_COMPLETE_WITH_DATA_AND_CAPABILITY_BLOCKERS`. These are data and
schema gates only and must not be promoted to canonical economics.

The 2026-09-09 public-route exhaustion packet is
`data/research/external/crossasset_public_2026_09_09/public_route_exhaustion/route_exhaustion_report.json`
(SHA-256 `023bdf1becde9ca6b59d48d159b1167cfdce5e772eba0cbd0b4778f8103fad09`).
It contains 18 exact HTTPS route receipts: 17 HTTP 200 and one HTTP 404 for
an optional SEC headers route. iShares as-of holdings requests returned HTML
under a JSON content type, so no historical holdings payload was admitted.
The SEC EWJ/EWZ XML samples are parsed and identity-bound in the matrix, but
their bounded reports do not establish continuous PIT valuation or membership.

## 2026-09-08 coordination checkpoint

Root coordination reports H01–H05 implementer work complete and pending
independent review. Crypto causal-adapter remediation remains ongoing. This
ledger records that coordination state without copying any private SEC
identity or contact data into cross-asset artifacts.

Protocol and receipt pins for this checkpoint:

- X01 acquisition protocol SHA-256:
  `a54c72f2c6eb940d6160cb91ec639ade76f36dfdf79e921ee027c5a2ea548cc8`.
- Alpaca sanitized receipt SHA-256:
  `c1de2b74bdabeec877f895e80e0de90333a84d6e4cb05c1ca4a98ca7a8b55fba`.
- Treasury year-fetch receipt SHA-256:
  `38b29df5c130a5dae4ae09c3a1249a9d2425fd7b6763cc76e831423c3b423e03`.
- V3 no-persist protocol SHA-256:
  `1c2663d8bf5b3a914199435260ddd8416d09a597a9dd3d7ec5c3d271f6a55a2d`.

## Review correction checkpoint — 2026-09-08

Status: `DONE_WITH_CONCERNS`, pending fresh independent review. No network
retry or economic execution was performed after the review failure.

- X01 FRED cutoff artifacts: `data/research/generated/crossasset_preflight_2026_09_08/x01_fred_curve_in_cutoff.csv` (2,761 rows; 2,645 nonempty; SHA-256 `6a0f54fe2bb1734693dd03cd2f16c706fb7c9b6834ea04f27412f4209e735f28`) and `x01_fred_curve_quarantine.csv` (24 later nonempty rows; SHA-256 `c256ac5d0d1bf48c830b404d9cb0eb30044d725d9fa0a9e6455fe6b8cf000059`). The main artifact ends at 2026-07-31; all later rows are excluded.
- X01 Alpaca receipt: `data/research/external/crossasset_public_2026_09_08/x01_alpaca/receipt.json` (SHA-256 `c1de2b74bdabeec877f895e80e0de90333a84d6e4cb05c1ca4a98ca7a8b55fba`). Raw and split requests are independently recorded for the primary and SHY/IEF/TLT fallbacks. All are HTTP 401 with zero admitted rows. Later-page failures are fail-closed and tested with a 200-then-500 fixture.
- X01 Treasury receipt: `data/research/external/crossasset_public_2026_09_08/x01_treasury/official_treasury_year_fetch_receipt.json` (SHA-256 `38b29df5c130a5dae4ae09c3a1249a9d2425fd7b6763cc76e831423c3b423e03`). Top-level `requested_years` enumerates 2016 through 2026; all 11 task files pass exact vintage header, onset-aware optional maturity, date/order, finite-value, and hash validation and are recorded as HTTP 200/offline-validated with `bundle_admitted=true`, `status=COMPLETE`.
- Preflight result: `data/research/generated/crossasset_preflight_2026_09_08/results.json` (SHA-256 `28378f5b4a206ba4a67a41de99e7ad537b4e6ab47282d51ed3f3ba80c7f7e`) and validation receipt `validation_receipt.json` (SHA-256 `b88aeb5d88087281d89e40a6664dc1b3fcafa02932cc8e668f7a056637cc4828`). X03/X05/X06 now reject bad width, extra/reordered columns, ordering, finite-value, symbol, and OHLC fixtures; crypto streams still lack full derivative risk inputs and product capability.
- Validation: the focused crossasset suite passed 44 tests across preflight, X01 acquisition/Treasury/Yahoo, receipt verification, and X02-X06 gates; Python compilation passed on all owned scripts/tests. The package remains research-only with no economic or product execution.

Residual blockers remain canonical Alpaca entitlement (HTTP 401), PIT
duration/holdings vintages and source reconciliation for the Yahoo fallback,
and X02/X03/X04/X05/X06 causal or execution capabilities. No credentials or
SEC contact data are present in these artifacts.

## Public X01 fallback acquisition — 2026-09-08

The corrected Yahoo fallback runner queried SHY, IEF, and TLT through the
frozen 2026-08-01 exclusive endpoint for both OHLCV and dividend/split events.
All three returned HTTP 200 JSON with 2,659 daily rows from 2016-01-04 through
2026-07-31 and 126 dividend events; no split events were reported. Raw
responses, normalized bars, and dividend ledgers are hash-pinned in
`data/research/external/crossasset_public_2026_09_08/x01_yahoo/receipt.json`
(SHA-256 `2ec4f01c7718ebfcd2f4b912af38ee7508c5d38218c2635b40cdeebb326e6418`).
The receipt records `admitted_rows=0`: Yahoo does not provide historical PIT
duration/holdings or available-at release vintages, and adjusted close is not
accepted as a substitute for separately reconciled split-adjusted OHLC and
distribution cash. The validator checked 2,658 adjacent close/dividend
intervals per symbol within 0.001 relative tolerance, plus exact row count,
ordering, identity, OHLC, finite-value, cutoff, and event-date semantics.

The unresolved duration semantics are frozen in
`docs/research/x01_maturity_bucket_approximation_protocol_2026_09_08.md`,
which maps SHY to 1–3Y, IEF to 7–10Y, and TLT to 20Y+ Treasury columns while
explicitly recording `PIT backing unavailable`. Its protocol SHA-256 is
`61de33659736a16ae51a51e3e1b9bfbba3e6c26a8d5d529046e5c86659cb9cb0`.

Fallback manifest SHA-256:
`3697cbfdac2357eb1190d78f16956c19c2d05ed454f81da2663334742a6ac15c`.
The all-symbol alternative probe receipt SHA-256 is
`fa30d09ca870df2f5ffad6a00d1f34711d326a926b4e2f0de578a6955b2f6c54`.

## X02-X06 acquisition and two-leg gates — 2026-09-08

The bounded source matrix is
`data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json`
(SHA-256 `fe1ad481c7d03130996652ffc50d607779ccdb45dec6a537dd0f8120856054c8`; canonical matrix pin `6f6060e1b04708118ea20372926162ce38d83f5e697eae1cb8523d7c3e7cf0cb`),
with gate report
`data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json`
(SHA-256 `2344673644597e652adf8c6e02e2d0a2d7bdf9bc3a2959228a88e540c2a2cc1c`).
The research-only two-leg ledger report is
`data/research_runs/crossasset_x02_x06_2026_09_08/two_leg_ledger_report.json`
(SHA-256 `ac19d23b641d9e826ae865c40c16fc24f56c850c78a529ae4529da74a5be5c76`).
The matrix contains 29 sanitized probe receipts, including official SEC
exchange-ticker, CME, and CFTC alternatives plus four Binance REST alternatives
for X05/X06 and identity-bound EWJ/EWZ N-PORT samples; canonical
source-matrix verification is provided by
`scripts/research/crossasset_receipt_verifier_2026_09_08.py`. The X01 manifest
also carries a canonical SHA-256 pin (`6ee938cfa1c945c4ef037218dccb552d2f3f691595b453d0b9ce686f7997dd8f`).
X02 is blocked on dated contract settlement/risk fields; X03 now has exact
EWJ/EWZ identity samples but remains blocked on continuous PIT valuation and
membership vintages (MCHI raw accession binding is also absent); X04 is blocked on
borrow/locate/recall history; X05 is blocked on continuous PIT OI; X06 is
blocked on PIT availability and exact-duplicate review. No rows are admitted.
No economic result is produced from this fallback.
