# Cross-asset strategy briefs and frozen gates — 2026-09-08

These briefs are research protocols only. They freeze the intended mechanism
before any economics and therefore do not authorize a result from incomplete
inputs.

## X01 — bond ETF curve carry / roll-down

Candidate universe: SHY, IEF, and TLT, with historical ETF identity and
split-adjusted bars plus explicit cash distributions required. Decision
frequency: monthly, after the completed month-end bar; execution: next
tradable session open. A future exact rule must use only point-in-time curve
observations and a frozen ETF mapping, then rank carry plus roll-down across
the three ETFs. The currently available official 2016-01-04..2026-07-31
Treasury par-yield panel can support maturity-bucket diagnostics, but current
IEF duration/holdings cannot be projected backward. Until historical ETF
bars, distributions, duration, and available-at vintages arrive, X01 remains
`DATA_ACCESS_BLOCKED` and no approximation result is canonical.

## X02 — commodity futures-curve carry

Candidate universe and roll schedule must be frozen before results. Required
inputs are dated futures settlements, contract specifications and multipliers,
volume/open interest, roll calendar, collateral yield, and an ETF/fund receipt
ledger if an ETF implementation is selected. Decision frequency is monthly
with next-session-open execution and explicit distributions/expense treatment.
CME methodology/spec evidence is retained, but no usable dated curve rows
are present. DataMine/licensed access is a precise blocker; no purchase or
signup is permitted. X02 remains `DATA_ACCESS_BLOCKED`.

## X03 — country ETF point-in-time value

Candidate universe: MCHI, EWJ, and EWZ. Decision frequency: monthly after a
completed valuation observation; execution: next tradable session open. Rank
and allocation formula, FX conversion, membership identity, distributions,
and accounting fields must be fixed before results. The local price and cash
action rows, FRED FX series, and SEC N-PORT samples are normalized as
accounting evidence. The MCHI XML parses 565 investment tags but has no
independently bound filing accession/retrieval receipt. Fresh official samples
bind EWJ to CIK `0000930667`/series `S000004249`/class `C000011953` (report
`2026-02-28`, 185 tags) and EWZ to the same CIK/series `S000004264`/class
`C000011968` (report `2025-02-28`, 54 tags). All 804 tags remain
metadata-only schema evidence (`admitted_holding_rows=0`). No historical P/B,
P/E, valuation, or membership vintage is inferred from these bounded samples.
X03 remains `DATA_ACCESS_BLOCKED` for economic value testing.

## X04 — equity pairs / statistical arbitrage

The mechanism requires a long cheap leg and a short expensive leg from a
frozen pair formation universe, point-in-time membership, synchronized fills,
borrow/locate/recall/financing costs, and pair margin. The FINRA sample is
short volume only and is not borrow availability or cost. The product's
long-only contract cannot represent this mechanism. X04 remains
`DATA_ACCESS_BLOCKED` and `OUTSIDE_PRODUCT_CAPABILITY`; no one-leg substitute
is labeled X04.

## X05 — crypto dated-futures cash-and-carry

The intended position is long spot and short a dated futures contract. A valid
protocol requires point-in-time spot and futures marks/settlements, expiry and
roll rules, contract multipliers, collateral/financing, margin and liquidation
rules, synchronized two-leg fills, and venue outage handling. The bounded
Binance files prove schema/timestamp joins and include a dated contract near
expiry, but do not supply a complete historical curve or risk ledger. X05
remains `DATA_ACCESS_BLOCKED` and `OUTSIDE_PRODUCT_CAPABILITY`.

## X06 — spot/perpetual funding carry

The intended position is long spot and short perpetual, with funding cash
flows at the venue's observed funding timestamps and mark/index risk. A valid
protocol requires historical funding, mark/index, open interest,
liquidation, collateral, margin, fees, and synchronized spot/perpetual fills.
The bounded sample has 745 aligned hourly spot/perpetual rows and 94 funding
observations with mark prices. The historical open-interest request was
rejected with Binance code `-1130`; no OI series is fabricated. X06 remains
`DATA_ACCESS_BLOCKED` and `OUTSIDE_PRODUCT_CAPABILITY`.

## Common result rule

No candidate is tuned after results. Any economics requires a new manifest
with protocol and source hashes, focused tests, an independently reviewed
result receipt, and a research-only shared two-leg or ETF accounting seam.
The V3 baseline is frozen and must not be changed to fit any candidate.
