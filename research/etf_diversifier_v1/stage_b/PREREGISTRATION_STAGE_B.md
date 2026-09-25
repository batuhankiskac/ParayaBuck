# ETF diversifier v1 — Stage B pre-registration (shared ledger)

Status: frozen before any Stage B outcome was computed (2026-09-25).
Honest scope note: Stage A results (`../RESULTS.md`) were visible when this
was written. There is no fresh data; Stage B's purpose is to confirm the Stage
A screen **under one shared account** — shared cash, shared daily-loss latch
(0.5%, enforced in backtests), cross-market sell-before-buy, and the shipped
account-wide position cap — not to provide a new out-of-sample test.

## Harness

The accepted physical-clock shared-ledger replay
(`research/physical_availability_clock_v2`, restored byte-for-byte from tag
`archive/research/account-capacity-migration-8-v3-accepted-2026-09`, commit
`9c5e613`): XNYS close availability / next-open execution for equities, the
causal hourly crypto contract, one cash balance, one position book, one NAV.
Costs (slippage, spread, crypto taker fee, equity regulatory fees) are scaled
for **every** sleeve at 1x/2x/3x.

## Composite equity engine

The harness supports one engine per market, so V3 and the ETF sleeve run as
one equity engine with a research-only composite strategy:

- V3 part: the unchanged shipped V3 strategy, weekly cadence. Its target goes
  through the canonical risk overlay **alone**, with its own allocation and the
  shipped cap of 8 account slots (crypto slots count, ETF holdings do not).
- ETF part: frozen C1 or C2 logic, re-decided at the first V3 weekly decision
  of each calendar month (up to four sessions after the Stage A month-start
  boundary); on other weeks it holds current ETF weights (no drift trades).
  ETF weights are scaled to the ETF allocation, capped at 0.10 per name, never
  pushed above the account gross/cash limit, and — when the daily-loss latch is
  active — never increased (same rule the overlay applies).
- ETF holdings are **additive slots**: they do not consume the shipped cap of
  8, so V3 and crypto keep exactly their shipped slot behaviour. (Production
  would need an explicit ETF slot budget; this is recorded as a requirement,
  not tested here.)
- ETF symbols are pinned into the equity observation stream with the stream
  capacity raised by the same count, so V3's visible candidate set is
  unchanged. SPY/AGG/BIL bars and dividends come from the canonical v5 inputs;
  TLT/IEF/GLD/DBC/SHY from the ETF campaign inputs.

## Arms (each at 1x, 2x, 3x)

- **A** — shipped: plain V3 0.55 + crypto 0.05 (unmodified harness path).
- **B** — matched cash control: composite with V3 0.40, ETF part disabled
  (0.15 idle cash) + crypto 0.05.
- **C1 / C2** — composite with V3 0.40 + ETF 0.15 (C1 static / C2 trend) +
  crypto 0.05.
- **I1 invariance (1x)** — composite with V3 0.55 and ETF disabled; must
  reproduce arm A's decision and fill event streams exactly.

## Frozen gates

Structural (must hold for every arm, otherwise the campaign is INCONCLUSIVE):
S1 I1 decision and fill event hashes equal A's; S2 cash conserved, position
quantities reconciled, no duplicate or unowned fills; S3 non-ETF slot claims
never exceed 8; S4 every fill strictly after its order creation.

Economic (per candidate; combined account NAV sampled at each XNYS close,
daily returns, √252, zero risk-free rate; windows as in Stage A):
G1–G8 exactly as in Stage A against arm A, with G1 replaced by S1–S4; plus
- **G9a** full 1x Sharpe ≥ B + 0.05,
- **G9b** full 1x Calmar (CAGR / max DD) ≥ B,
- **G9c** validation 1x Sharpe ≥ B.

Verdict: a candidate is `STAGE_B_PASS` only if every structural and economic
gate holds. If both pass, the higher full 1x Sharpe is preferred. Passing
authorizes writing a production-integration design (ETF slot budget, sleeve
ownership), not a production change.
