# Equity Universe Integrity Audit

Audit date: 2026-09-18
Repository: `/Users/batuhankiskac/Desktop/ParayaBuck`
Scope: production equity-universe semantics only. No alpha design, parameter
optimization, returns, P&L, or economic backtest was performed.

## Verdict

Preliminary structural verdict before any production edit:

`UNIVERSE_CONTRACT_AMBIGUOUS`

Final verdict:

`UNIVERSE_CONTRACT_AMBIGUOUS_OPERATOR_DECISION_REQUIRED`

`PRODUCTION_BASELINE_REVALIDATION_NOT_REQUIRED`

No production fix was made. The repository does not establish whether ordinary
ETFs/ETNs/ETPs are intended ordinary-alpha instruments, nor does it provide an
authoritative product-type/security-master field from which a complete
leveraged/inverse policy can be implemented safely. Choosing common stocks only,
all exchange-traded products, or a stricter product taxonomy would change the
investable universe and requires an operator decision plus a suitable
effective-dated security master.

The current documented contract is broad active/tradable Alpaca `US_EQUITY` on
supported exchanges. That is closest to option C in the audit brief, but the
“except explicitly blocked products” part is only partially implemented by a
finite ticker list and is not a complete product-policy contract.

## Starting main SHA

- Repository: `/Users/batuhankiskac/Desktop/ParayaBuck`
- Starting local `main`: `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`
- Starting `origin/main`: `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`
- Starting status: clean
- Maintenance branch: `maintenance/equity-universe-integrity-v1`, created from
  that exact `main`

PAPER/LIVE state recorded before the audit:

- `runtime.mode`: `paper`
- Equity PAPER identity: `equity_residual_momentum_v3`, 1d, weekly
- Equity parameters: formation 252, skip 21, beta window 273, residual-vol
  window 63, top_n 8, max single weight 0.10, IVOL reject quantile 0.70,
  `exclude_symbols=[SPY]`
- Crypto PAPER identity: `crypto_low_variance_hysteresis_v1`, 1d, 96h,
  top_n 2
- `live.enabled`: `false`
- `live.hard_dollar_cap`: `0`
- `PARAYABUCK_ENABLE_LIVE`: unset
- `PARAYABUCK_ALLOW_PAPER_SMOKE`: unset

The focused baseline suite passed before branch creation: **95 passed in
11.79s**.

## Intended equity-universe contract

Repository evidence is consistent on the pool/stream distinction:

1. `docs/strategy_architecture.md` defines the equity source as every active,
   tradable US equity on supported exchanges, followed by a Stage-A shortlist,
   scanner eligibility, and a bounded observation set.
2. `selected_symbols` is the eligible opportunity pool. It is intentionally not
   truncated by `maximum_stream_symbols`, sector, or issuer diversity.
3. The configured strategy ranks the pool and produces the observation set.
4. PAPER, LIVE, and backtest use the same strategy-ranking path where the
   required inputs exist.

The repository does **not** answer the narrower product question “does equity
mean operating-company common stock only?” It also does not define an
authoritative allow/deny taxonomy for ETF, ETN, ETP, ADR, preferred, closed-end
fund, warrant, unit, commodity-linked, or volatility-linked products.

Therefore the honest Q1 answer is: the code intends a broad broker
`US_EQUITY`-class universe, closest to C, but its ordinary-alpha product scope
is underspecified. The audit does not silently convert that contract to
common-stock-only.

## Current classification architecture

The current path is:

```text
Alpaca get_all_assets(US_EQUITY, ACTIVE)
  -> tradable + supported exchange + allow/deny filters
  -> 20-bar candidate metrics
  -> finite static leveraged/inverse flag
  -> scanner eligible pool
  -> strategy rank_candidates() over the full pool
  -> pinned symbols + alpha-ranked observation set
  -> PortfolioStrategy target
  -> sleeve/per-asset/count/gross/cash risk overlay
```

Important implementation facts:

- `preflight.eligible_equity_assets()` asks Alpaca for `AssetClass.US_EQUITY`
  and `AssetStatus.ACTIVE`, then checks symbol, tradability, supported exchange,
  allowlist, and denylist.
- The integration passes only `symbol`, `exchange`, `tradable`, `status`, and a
  best-effort `sector`/`economic_sector` lookup into `EquityCandidate`.
- The installed Alpaca `Asset` model exposes `name` and `attributes`, but the
  integration discards both. It does not carry a dedicated common-stock,
  ETF, ETN, ETP, leverage, or inverse field.
- `classify_instrument()` recognizes only exact static sets: 11 sector ETFs, 2
  broad-market ETFs, and 3 approved inverse ETFs. Every other equity symbol is
  returned as `instrument_category="equity"`.
- `sector_of()` is the same small exact map and returns `other` for everything
  else.
- `is_leveraged_or_inverse_equity()` is an exact finite ticker list, not a
  security-master lookup.
- `_filter_reason()` rejects only candidates whose finite-list flag is true,
  except for configured inverse symbols, which are force-kept as approved
  reserved instruments.

## Instrument-type audit

The table uses these definitions:

- “cached candidate” means the symbol exists in the current local candidate
  cache (`data/universe/equity_candidates.json`).
- “pool” means it exists in the on-disk August 31 active artifact’s
  `selected_symbols`. That artifact is stale today and is not treated as a
  current valid runtime universe.
- “alpha eligible” is a structural scanner result, not a return or P&L claim.

| Symbol | Actual product type | Current ParayaBuck category | Sector | Scanner result / alpha eligibility | Reserved? | Correct under the evidenced contract? |
|---|---|---|---|---|---|---|
| SPY | Broad-market ETF | `broad_market_etf` | `broad_market` | Force-kept; reference/history available; V3 excludes benchmark from alpha | Reference | Yes for reference-only streaming; not an ordinary alpha candidate |
| VXX | Volatility-linked ETN | `equity` | `other` | Cached; rejected here by `minimum_liquidity`; would be ordinary equity if it passed | No | Contract-dependent; product type is not established by the repo |
| BITO | Bitcoin-linked futures ETF | `equity` | `other` | Cached; passes current scanner metrics; in stale active pool | No | Contract-dependent; current code cannot prove its type |
| BND | Bond ETF | `equity` | `other` | Cached; rejected here by `atr_range` | No | Contract-dependent; current code cannot prove its type |
| GDX | Gold-miners ETF | `equity` | `other` | Cached; passes current scanner metrics; in stale active pool | No | Contract-dependent; current code cannot prove its type |
| ARKK | Active equity ETF | `equity` | `other` | Cached; passes current scanner metrics; in stale active pool | No | Contract-dependent; current code cannot prove its type |
| TQQQ | +3x daily Nasdaq-100 ETF | `equity` | `other` | Finite static flag true; rejected as `leveraged_or_inverse` | No | Correct only for the finite-list policy |
| SQQQ | -3x daily Nasdaq-100 ETF | `equity` | `other` | Finite static flag true; rejected as `leveraged_or_inverse` | No | Correctly blocked and not an approved configured inverse |
| BOIL | +2x daily natural-gas ETF | `equity` | `other` | Finite static flag false; currently rejected by `minimum_liquidity`; latent gap if metrics pass | No | Not safely certifiable under an all-leverage-excluded policy |
| TZA | -3x daily small-cap ETF | `equity` | `other` | Finite static flag false; currently rejected by `minimum_liquidity`; latent gap if metrics pass | No | Not safely certifiable under an all-leverage-excluded policy |
| TSLL | +2x daily Tesla ETF | `equity` | `other` | Finite static flag false; passes current metrics; in stale active pool | No | Concrete static-list gap if all leveraged products are intended to be blocked |
| SH | -1x daily S&P 500 ETF | `inverse_equity_etf` | `inverse_market` | Force-kept despite leverage flag; reserved observation symbol | Inverse hedge | Correct as an explicitly approved inverse instrument; role policy remains separate from ordinary alpha |
| PSQ | -1x daily Nasdaq-100 ETF | `inverse_equity_etf` | `inverse_market` | Force-kept despite leverage flag; reserved observation symbol | Inverse hedge | Correct as an explicitly approved inverse instrument; role policy remains separate from ordinary alpha |
| DOG | -1x daily Dow 30 ETF | `inverse_equity_etf` | `inverse_market` | Force-kept despite leverage flag; reserved observation symbol | Inverse hedge | Correct as an explicitly approved inverse instrument; role policy remains separate from ordinary alpha |

Product-type references used for the representative table include Cboe’s VXX
listing, ProShares’ BITO, SH, PSQ, DOG, BOIL and leveraged/inverse catalogue,
Vanguard’s BND, VanEck’s GDX, ARK’s ARKK, and Direxion’s TZA and TSLL pages.
They establish the examples’ product types; they do not turn the local
historical CSV into a complete security master.

## ETF / ETN / ETP findings

Current official Alpaca documentation describes `Asset` as a security model with
fields including `asset_class`, exchange, symbol, name, status, tradability and
attributes. The `AssetClass` enum contains broad classes such as `us_equity`,
not common-stock/ETF/ETN/ETP subtypes. `GetAssetsRequest` can filter the broad
asset class and status, but the documented request/model surface does not expose
a reliable product-type discriminator:

- [Alpaca Asset model](https://alpaca.markets/sdks/python/api_reference/trading/models.html)
- [Alpaca AssetClass enum](https://alpaca.markets/sdks/python/api_reference/trading/enums.html)
- [Alpaca GetAssetsRequest](https://alpaca.markets/sdks/python/api_reference/trading/requests.html)
- [Alpaca Assets API](https://alpaca.markets/sdks/python/api_reference/broker/assets.html)

The local SDK confirms the installed `Asset` fields are:
`id`, `asset_class`, `exchange`, `symbol`, `name`, `status`, `tradable`,
`marginable`, `shortable`, `easy_to_borrow`, `fractionable`, minimum-size
fields, and `attributes: Optional[List[str]]`. The repository currently uses
none of `name`, `attributes`, or any structured security-type field.

Consequences:

- Ordinary ETFs, ETNs, and other exchange-traded products can enter the broad
  eligible pool if their trading metrics pass.
- They are then labeled `equity` by the static classifier, which is a category
  fallback, not proof that they are common stocks.
- A hand-maintained ticker blacklist is not a complete security-master policy.
- A safe complete repair needs an operator-approved instrument policy and an
  authoritative, preferably effective-dated, product taxonomy. No such source
  is present in this repository, and Alpaca’s documented `US_EQUITY` field is
  too broad to substitute for one.

## Leveraged / inverse findings

The finite static list is exactly:

```text
SH PSQ DOG SQQQ SOXL SOXS SPXL SPXS SPXU TECL TECS TQQQ UPRO UVXY
```

The candidate cache contains 14 candidates with the finite
`leveraged_or_inverse` flag. The historical dataset contains the four static
recognized names `SH`, `PSQ`, `DOG`, and `SQQQ`, but also contains the externally
verified leveraged/inverse products `BOIL` and `TZA`, which the static list does
not recognize. The current candidate cache additionally contains `TSLL`, which
is a +2x ETF and currently passes scanner metrics into the stale active pool.

This is the failure mode when a new product lists after the code’s static list
was written: it is normalized as ordinary `equity`, gets `leveraged_or_inverse
== False`, and can pass the scanner. The present repository therefore cannot
certify an “all leveraged/inverse products excluded” contract.

The configuration separately and intentionally permits only the exact
configured inverse symbols `SH`, `PSQ`, and `DOG`. `EquityUniverseConfig`
requires configured inverse symbols to be in the fallback list and to satisfy
`is_inverse_equity()`, which is why `SQQQ` cannot be configured as an approved
inverse hedge.

## Reserved symbol semantics

### Reference-only: SPY

`SPY` is a configured reference and benchmark. The scanner force-keeps it so
history and streaming remain available. `trade_reference_symbols: false` keeps
it out of the normal market-state trade allowlist, and the PAPER V3 parameters
also explicitly exclude it from alpha scoring. This role is consistent.

### Explicit inverse hedges: SH, PSQ, DOG

These symbols are configured `inverse_trade_symbols`, force-kept by the scanner,
marked as `inverse_hedge`, and exempted from the general leverage rejection so
they remain observable and tradeable in their explicitly approved role.

`build_observation_set()` removes pinned symbols from the alpha-ranker’s
available pool, so they do not become ordinary names merely because they are
needed in the stream. However, the later V3 target path receives the pinned
symbols in its runtime observation context and excludes only `SPY`; it does not
carry an inverse-hedge role flag into target construction. The read-only PAPER
database contains a 2026-08-28 equity target with `SH` as the surviving symbol.
That is structural evidence that an approved inverse can be selected by the
ordinary V3 target path. Whether this is intended hedge availability or an
ordinary-alpha role leak is not resolved by the repository documentation.

### Managed-position pins

Managed positions and open managed orders are pinned into the observation set so
the system can observe, rebalance, and exit owned symbols. The runtime universe
adapter adds these to observation membership without widening the eligible
opportunity pool. This is the correct role separation for ownership safety.

## Sector-cap semantics

`maximum_per_sector` is `5` in production configuration. The code and docs make
its current scope precise:

| Layer | Current behavior | Assessment |
|---|---|---|
| Scanner eligible pool | No sector, issuer, or stream-cap truncation; every candidate passing eligibility enters `selected_symbols` | Correct under the documented pool/stream contract |
| Scanner fallback observation set | Known sectors are capped at `maximum_per_sector`; issuer duplicates are skipped; reserved symbols are preloaded; `unknown` uses stream capacity instead of 5 | Deliberate fallback behavior, but not a hard unknown-sector cap |
| Strategy-ranked observation set | `build_observation_set()` keeps pins and takes the alpha-ranked pool up to stream capacity; it does not apply sector or issuer caps | Explicitly replaces the fallback diversity ranking |
| Strategy target | V3 selects from the runtime observation context; no sector rule | No sector target constraint |
| Risk overlay / execution | Enforces sleeves, per-asset limits, counts, cash/gross and safety gates; no sector rule | No sector target constraint |

The `maximum_per_sector` value is therefore not a universal portfolio
diversification limit. It is a scanner fallback observation-set rule. The
repository does not establish a target-level sector contract, so no target-level
sector rule was added.

## Unknown-sector behavior

The live candidate integration currently supplies no sector metadata in the
candidate cache: all 13,098 cached candidates have `economic_sector: null`.
The scanner falls back to the small static map. In the five persisted equity
snapshots:

| Snapshot | Eligible pool | Static-unknown eligible | Metadata coverage | Observation set | Static-unknown observation |
|---|---:|---:|---:|---:|---:|
| 2026-08-10 | 30 | 3 | 14.0845% | 0 | 0 |
| 2026-08-13 | 290 | 247 | 13.9373% | 30 | 4 |
| 2026-08-16 | 290 | 247 | 13.9373% | 30 | 8 |
| 2026-08-29 | 291 | 248 | 14.1869% | 30 | 19 |
| 2026-08-31 | 291 | 248 | 14.1869% | 4 | 0 |

The August 29 19/30 observation result is on the strategy-alpha path. The
August 16 8/30 result shows the scanner fallback’s explicit `unknown` capacity
behavior can also exceed `maximum_per_sector=5`. A deterministic synthetic
scanner replay with six otherwise identical unknown-sector candidates and a
two-name sector cap selected all six; the strategy-ranked builder likewise
selected all six because it receives no sector policy.

This is best classified as intentional degradation at the scanner level and a
separate alpha-selection policy, not an accidental target-level cap. It is
unsafe to describe `maximum_per_sector` as protecting the final portfolio. If
the operator intends it as a hard invariant for the eligible pool, alpha
observation set, or target portfolio, the current implementation violates that
newly clarified contract and must be changed in a separate maintenance task.

The symbol-only V3 structural replay over the four non-empty persisted
observation snapshots produced a maximum of **0 unknown-sector target symbols**.
The target symbol sets were:

```text
2026-08-13: DOG KO QQQ SH XLI XLP XLV
2026-08-16: DOG QQQ SH XLE
2026-08-29: DOG JNJ KO SH XLE
2026-08-31: SH
```

This is not evidence of a universal sector guarantee; it is only the result of
the available persisted snapshots and local bars. No portfolio economics were
computed.

## Strategy-ranked observation-set behavior

The production V3 refresh path is explicit:

1. scan the eligible pool;
2. fetch strategy warmup history for that pool;
3. call `build_observation_set()` with V3;
4. replace the scanner fallback observation set with V3 alpha ranking;
5. persist the resulting bounded observation set.

The ranker shares the strategy score path with `decide()`, preserving the
documented BACKTEST/PAPER/LIVE alpha-ranking parity. It preserves only:

- reserved/pinned symbols;
- history readiness and latest-bar alignment;
- maximum stream capacity.

It does not preserve `maximum_per_sector`, fallback issuer diversity, or a
product-type role distinction. That is why fallback scanner diversity cannot be
treated as protection for PAPER/LIVE.

## Target/risk-layer behavior

The target/risk path has no sector metadata lookup and no
`maximum_per_sector`/sector-equivalent rule. The documented risk boundary covers
market sleeves, per-asset caps, position counts, cash buffer, gross exposure,
loss/drawdown latches, and execution safety. Adding a sector target constraint
would be a new portfolio policy, so it was not added.

The inverse symbols are available to the ordinary V3 target context after being
pinned. This is acceptable only if “explicit inverse hedge” means an approved
tradeable input to the current strategy; it is not acceptable if it means
hedge-only and never ordinary alpha-selected. The repository does not decide
between those policies.

## Backtest / PAPER / LIVE parity

### What is aligned

- Production refresh, PAPER, and LIVE load the same active-universe artifact
  semantics.
- The scanner, configured pins, static leveraged/inverse list, and
  `build_observation_set()` are shared.
- Strategy ranking uses the same strategy code as target construction.
- Runtime streaming and decision completeness use `observation_set`, while
  pool membership remains `selected_symbols`.
- Backtest PIT snapshots carry both the eligible pool and observation set, and
  the backtest engine uses the same observation builder when a strategy is
  supplied.

### What is not provably aligned

The PIT backtest builder explicitly documents that historical broad
sector/tradability metadata and historical spread are unavailable and degraded.
It reconstructs candidate eligibility from trailing bars, uses the same static
instrument list, and does not have an effective-dated security master. It cannot
historically reconstruct:

- whether a symbol was an ETF, ETN, ETP, ADR, preferred, warrant, unit, or
  common stock at the snapshot;
- whether a current product wrapper or leverage objective was in force at that
  historical date;
- historical broker active/tradable status from current metadata;
- historical sector classification after issuer/product changes.

Accordingly, the claim is **strategy/scanner-code parity**, not full
security-master parity. Current PAPER/LIVE product-type behavior can differ from
PIT behavior because neither side has a complete time-aware product taxonomy,
and the PIT path explicitly degrades missing metadata.

The on-disk active equity artifact was generated at
`2026-08-31T12:43:13Z` and is 436.79 hours old at audit time versus the 36-hour
limit. `validate_active_universe()` returns `stale`; the runtime should fail
closed rather than treat it as the current active PAPER universe.

## PIT metadata safety

| Metadata field | Time invariant? | Safe to apply historically? | Evidence / treatment |
|---|---|---|---|
| Symbol identity | Usually, but ticker reuse and corporate actions can break it | Only with effective-dated identity/lifetime data | Local bar identity is not a complete security master |
| `AssetClass.US_EQUITY` | Broad class, not a product subtype | No product-type conclusion | Official Alpaca enum is broad `us_equity` |
| Product wrapper: common/ETF/ETN/ETP/ADR/preferred/etc. | Not guaranteed | No | Absent from local CSV and not a dedicated Alpaca `Asset` field |
| Leverage/inverse objective | Often stable for a fund identity, but can change through wrapper/ticker/product events | Only with effective-dated product metadata | Current finite ticker list is not PIT-complete |
| Active/tradable status | Time-varying | No | Current active asset status cannot be projected backward; backtest uses bar availability as an approximation |
| Exchange/listing | Time-varying | No without dated listings | Current exchange is not historical truth |
| Economic sector | Can change through issuer/holding/classification changes | No without dated taxonomy | Current candidate metadata is absent; static map is not PIT metadata |
| Price/volume and derived ATR/liquidity | Observed at the bar timestamp | Yes, subject to feed/adjustment basis | Backtest uses strictly prior local bars |
| Spread | Time-varying and absent historically here | No | Backtest explicitly degrades historical spread |
| Configured reference/inverse role | Current policy is configuration-time invariant | Yes as a policy replay, not as market truth | `settings.yaml` pins the role |

## Historical data structural audit

Canonical local dataset:
`data/historical/equity_pit_symmetric_2016_2026_v5.csv`

- Rows: 11,686,832
- Distinct symbols: 9,010
- Date range: 2016-01-04 through 2026-07-31
- Full-dataset common-stock count: **not provable** from the CSV/manifest
- Full-dataset ETF count: **not provable** from the CSV/manifest
- Full-dataset ETN/ETP count: **not provable** from the CSV/manifest
- Product subtype labels produced by the current static classifier:
  3 inverse ETFs, 11 sector ETFs, 2 broad-market ETFs, and 8,994 default
  `equity` symbols
- Static `economic_sector="other"`: 8,973 of 9,010 symbols (99.59%)
- Full-dataset leveraged/inverse count: **not provable** from local metadata;
  at least the verified symbols `SH`, `PSQ`, `DOG`, `SQQQ`, `BOIL`, and `TZA`
  are present, while the static list recognizes only the first four
- Conservative current-code “no product subtype proof” count: **8,994**
  symbols in the default `equity` bucket. This is deliberately not called a
  common-stock count.
- The small repository sector map contains 21 ordinary-equity examples, but
  those hand-maintained examples do not establish the product type of the
  remaining 8,989 symbols.

Representative suspicious products present in the historical candidate pool
include `VXX`, `BITO`, `BND`, `GDX`, `ARKK`, `SQQQ`, `BOIL`, and `TZA`.
The current 13,098-symbol candidate cache also contains `TSLL` and all table
examples above; its broker-derived `economic_sector` field is null for every
candidate.

## Bugs found

### 1. Missing authoritative product taxonomy — unresolved contract gap

The current implementation cannot honestly claim that `equity` means common
stock. It also cannot honestly claim complete exclusion of all leveraged,
inverse, ETN, or volatility-linked products. The static fallback silently
masquerades as a product classification for 8,994 historical symbols.

This is a production correctness risk, but a safe repair requires two decisions
that the repository does not make:

1. the intended ordinary-alpha product policy; and
2. the authoritative current/PIT security-master source and effective-date
   semantics.

No policy was invented and no blacklist was expanded.

### 2. Finite leveraged/inverse detection — concrete gap

`TSLL` is a current cached candidate, passes scanner metrics, and is in the
stale selected pool while the static detector returns false. `BOIL` and `TZA`
are present in the historical dataset and also return false. If the intended
contract is “all leveraged/inverse products are excluded except explicitly
approved inverse hedges,” this is a correctness defect. The current repository
does not provide enough metadata to repair it completely without operator
direction.

### 3. Inverse role is not carried into target construction — conditional gap

The observation builder keeps `SH`/`PSQ`/`DOG` out of the ordinary alpha-ranked
available pool, but the V3 target context can still select them because only
`SPY` is excluded in the strategy parameters. The code is correct if approved
inverse symbols are intentionally tradeable by the equity strategy; it is a
role leak if they are hedge-only. The existing documentation does not resolve
that distinction.

### 4. `maximum_per_sector` is not a target-level invariant — not a bug under
the current documented contract

The rule is intentionally scoped to scanner fallback observation diversity, and
the strategy alpha intentionally replaces that fallback. Unknown sectors use a
capacity limit. This is a meaningful limitation, not a hidden portfolio
guarantee. No target-level change was made.

## Fixes made

**No production fix required or safely authorized in this audit.**

Only the audit report was added. No strategy, allocation, risk budget, scanner
threshold, PAPER identity, LIVE setting, crypto behavior, or production source
file was changed. No orders were placed and no research candidate was
registered.

## Structural before/after comparison

No source fix was applied, so PRE-FIX and POST-FIX are identical:

| Measure | Result |
|---|---:|
| Persisted snapshots changed | 0 / 5 |
| Snapshot-change percentage | 0% |
| Unique symbols removed | 0 |
| Unique symbols added | 0 |
| Current/live universe changed | No; the on-disk artifact remains stale and runtime rejects it |
| V3-ranked observation sets changed | No |
| Product-type distribution changed | No |
| Sector-cap violation count changed | No |

The structural replay was symbol/role/count-only. It did not calculate return,
Sharpe, drawdown, turnover economics, or P&L.

## V3 baseline revalidation requirement

`PRODUCTION_BASELINE_REVALIDATION_NOT_REQUIRED` for this audit because no
production universe rule or code was changed.

If the operator chooses common-stock-only, complete leveraged/inverse exclusion,
hedge-only inverse roles, or a hard sector cap, that future correction can
materially change V3 opportunity-set membership. It must then be a separate
frozen structural audit followed by `PRODUCTION_BASELINE_REVALIDATION_REQUIRED`
before treating historical V3 evidence as applicable to the corrected universe.

## Tests

Baseline and final source state used the same focused suite:

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_hardening.py tests/test_observation_set.py \
  tests/test_universe_snapshots.py tests/test_pit_universe_parity.py \
  tests/test_product_universe_guard.py tests/test_runtime_product_universe.py \
  tests/test_crypto_universe.py tests/test_scanner_cache_determinism.py -q
```

Result: **95 passed in 12.01s** on the final report-only branch state; the
verified starting-source baseline was **95 passed in 11.79s**. Additional
read-only structural checks covered:

- current SDK `Asset` fields and `AssetClass` enum;
- six-name unknown-sector scanner fallback versus `maximum_per_sector=2`;
- strategy-ranked unknown-sector observation behavior;
- reserved `SPY`/`SH`/`PSQ`/`DOG` force-keep behavior;
- five persisted active-universe snapshots;
- symbol-only V3 target replay over the four non-empty snapshots;
- stale-artifact validation (`stale` as expected).

No economics were run.

## Files changed

- Added [UNIVERSE_INTEGRITY_AUDIT.md](/Users/batuhankiskac/Desktop/ParayaBuck/UNIVERSE_INTEGRITY_AUDIT.md)
- No production source files changed.

## Branch cleanup actions

Research branches were not merged or deleted. They remain separate research
evidence and were not used as economic evidence for this audit:

```text
research/overnight-intraday-design-v1  7ec53efecc4d37cab17d0d96f194389b4513a69b
research/overnight-reversal-v1         41e1c9026bb34bbe81f98577844c9eca0523da8a
research/spy-defensive-overlay-v1      da9a99b6e23df880fb64afa78d1f38aa70edd5ec
research/v3-volatility-managed-v1      9dbe4bd78b3276159dd089e4edf4e15b9ed1cb01
```

The matching `origin/research/*` refs were preserved. No branch deletion was
needed for this audit.

## Final main SHA

`main` and `origin/main` remain at:

`5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`

## Final verdict

`UNIVERSE_CONTRACT_AMBIGUOUS_OPERATOR_DECISION_REQUIRED`

`PRODUCTION_BASELINE_REVALIDATION_NOT_REQUIRED`

The next authorized step is not an alpha change. It is an operator decision on
the ordinary-alpha product contract and the security-master source that can
enforce it both currently and point-in-time.
