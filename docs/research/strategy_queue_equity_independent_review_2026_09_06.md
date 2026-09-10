# ARCHIVAL_SUPERSEDED_DO_NOT_USE_AS_CURRENT

This 2026-09-06 intake snapshot is historical context only. Read current status, counts, NEXT state and hashes from the frozen equity-secondary gate report, source matrix, manifest and bound normalized packages.

# Independent equity candidate review — 2026-09-06

Status: independent source, local-data, and implementation-contract review of
all H01–H16 cards. This report does not run a backtest, acquire data, change
configuration or registries, or promote a candidate to PAPER/LIVE. `PASS`
means that the cited primary source supports the stated research mechanism;
it does not mean that the ParayaBuck long-only adaptation is investable.
`STOP` means that the candidate's economic test must wait for the listed
point-in-time, execution, or lifecycle gate. The decision objective is
complementarity with the existing equity V3 portfolio, not universal
standalone or all-period dominance.

## Common audit result

The owner report has coverage for all 16 attachment cards and correctly treats
the local candidate inputs as absent or unproven. The source evidence is mostly
accurate as mechanism evidence, but the source portfolios are often
long-short, market-neutral, or historical event studies. None is evidence of
the repository's long-only implementation or complementarity. All candidates
therefore remain `STOP` for economics pending a frozen manifest, causal
availability boundary, actual local input coverage, cost/shared-ledger test,
and untouched holdout.

The local evidence is precise for the gate: the canonical equity file is
`data/historical/equity_pit_symmetric_2016_2026_v5.csv`, 1d OHLCV, 2,658 NYSE
sessions from 2016-01-04 through 2026-07-30, with split-adjusted lineage. The
entity-lifetime and corporate-action files do not supply PIT fundamentals,
forecasts, Form 4, filing text, ownership/flow, index membership, merger, or
spin-off observations. OHLCV cannot proxy those fields. The configured stock
stream is IEX while the historical feed is SIP (`config/settings.yaml`), so
intraday/session claims need a feed-parity gate.

For every card, store separate `event_time`, `publication_time`, and
`available_at`; admit an observation only when `available_at <= decision_time`.
Freeze formula, direction, universe, lag, rebalance/holding horizon, missing
handling, costs, and event-collision policy before viewing results. Preserve
source revisions and identity history; current labels or restated facts are
not historical PIT evidence.

## H01–H16 decision table

| ID and exact card | Primary-source audit | Independent verdict | Concrete correction or next gate | Queue |
|---|---|---|---|---|
| **H01 — Değer seçimi (value)** | Asness, Moskowitz & Pedersen, [Value and Momentum Everywhere](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2174501), supports value and momentum premia across markets. | **PASS source / STOP data** | The source is not proof for a long-only book-value/market-value rank. Acquire PIT statements, market-cap alignment, identifiers, delistings, one frozen ratio and lag. Measure overlap with V3, size, sector and distress. | A after PIT foundation |
| **H02 — Brüt kârlılık / kalite** | Novy-Marx, [The Other Side of Value](https://www.nber.org/papers/w15940), supports gross profitability as a cross-sectional characteristic and discusses its value interaction. | **PASS source / STOP data** | Freeze gross profit/assets concept mapping, fiscal period and release lag. Do not add it as a V3 filter. Test whether it is complementary after costs and common-ledger controls. | A |
| **H03 — Düşük tahakkuk (accruals)** | Sloan, [Do Stock Prices Fully Reflect Information in Accruals and Cash Flows about Future Earnings?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2598), supports differential persistence and price underreaction. | **PASS source / STOP data** | The attachment formula `(net income − operating cash flow)/average assets` is an adaptation, not Sloan's full empirical specification. Freeze that exact formula, sign, scale, average-assets construction, statement mapping and restatement policy before results. | A |
| **H04 — Muhafazakâr yatırım / düşük varlık büyümesi** | Cooper, Gulen & Schill, [Asset Growth and the Cross-Section of Stock Returns](https://doi.org/10.1111/j.1540-6261.2008.01370.x), supports asset-growth predictability in the cited sample. | **PASS source / STOP data** | Published high-minus-low evidence is not long-only proof. Acquire PIT balance sheets and freeze annual growth, fiscal lag, merger/acquisition/spinoff treatment, entity continuity and sector/size controls. | B |
| **H05 — Net ortak getirisi (net payout yield)** | Boudoukh et al., [On the Importance of Measuring Payout Yield](https://www.nber.org/papers/w10651), explicitly defines payout as dividends plus repurchases and net payout as that amount less issuances. | **PASS source / STOP data** | The formula is supported, but cash-flow and Treasury-stock issuance measures differ. Select one measure and denominator, corporate-action policy, observation lag and long-only direction ex ante. | A |
| **H06 — Kamuya açıklanmış yönetici/ortak alımları** | Cohen, Malloy & Pomorski, [Decoding Inside Information](https://doi.org/10.1111/j.1540-6261.2012.01740.x) / [NBER w16454](https://www.nber.org/papers/w16454), supports separating routine from opportunistic insider trades. SEC [Form 4](https://www.sec.gov/files/form4.pdf) states the filing deadline and receipt-based filing time. | **PASS source / STOP data** | Replace the owner report's `form4data.pdf` link with the official `form4.pdf`. Use EDGAR acceptance/publication as the decision boundary; freeze transaction codes, amendments, insider role, open-market taxonomy and routine history. | A |
| **H07 — Analist tahmin revizyonları** | Chan, Jegadeesh & Lakonishok, [Momentum Strategies](https://www.nber.org/papers/w5375), supports sluggish forecast response. Low & Tan, [The Role of Analyst Forecasts in the Momentum Effect](https://doi.org/10.1016/j.irfa.2016.09.007), is the missing card-specific primary citation and reports forecast revisions in the momentum context. | **PASS source / STOP data** | Add Low & Tan to the owner source register. Their evidence does not establish a standalone long-only revision portfolio; acquire historical vintages, horizon, analyst/date identity, publication timestamps and stale-estimate policy. | B |
| **H08 — Bilanço sonrası sürüklenme (PEAD)** | Bernard & Thomas, [Post-Earnings-Announcement Drift](https://doi.org/10.2307/2491062), is the classic mechanism source. Add the attachment's current counterevidence, [Christensen, Timmermann & Veliyev (2026)](https://arxiv.org/abs/2601.08962), which reports post-announcement strategy returns consistent with efficient price formation after 2016. | **PASS historical source / STOP data and current validity** | Add the 2026 paper as a failure/weakening gate, not as a replacement. Acquire release timestamps, actuals, pre-release consensus vintages, surprise definition, revisions and event collisions; require a post-2016 holdout. | B |
| **H09 — Müşteri–tedarikçi bilgi yayılımı** | Cohen & Frazzini, [Economic Links and Predictable Returns](https://doi.org/10.1111/j.1540-6261.2008.01379.x), supports predictability across economically linked firms and explicitly reports a long-short strategy. | **PASS source / STOP data** | Freeze relationship vintage and direction (customer to supplier), event lag, termination, identifiers and link source. A current customer label is not a historical PIT relationship; long-only direction remains unproven. | B |
| **H10 — Finansal rapor metni değişimi (Lazy Prices)** | Cohen, Malloy & Nguyen, [Lazy Prices](https://www.nber.org/papers/w25084), studies 10-K/10-Q language changes and reports a short-changers/buy-non-changers portfolio. | **PASS source / STOP data** | The owner correctly notes that a long-only direction must be chosen. Acquire original filing versions and EDGAR acceptance times; freeze text normalization, section vocabulary, pair/version rule and direction. Do not claim the published long-short alpha for the adaptation. | B |
| **H11 — Fon çıkışından doğan zorunlu satış baskısı** | Coval & Stafford, [Asset Fire Sales (and Purchases) in Equity Markets](https://doi.org/10.1016/j.jfineco.2006.09.007), supports flow-driven institutional price pressure and predictable forced trades. | **PASS source / STOP data** | Their liquidity-provider framing does not determine the product's long-only direction. Acquire flow and holdings vintages, ownership weights, fund identity, publication lag and capacity; freeze buy-after-pressure timing and holding period. | A after PIT foundation |
| **H12 — Endeks değişiminden doğan fiyat baskısı** | Greenwood & Sammon, [The Disappearing Index Effect](https://doi.org/10.1111/jofi.13410), is the relevant source and specifically documents a diminished effect. [NYSE index methodology](https://www.nyse.com/publicdocs/nyse/indices/NYSE_Index_Series_Methodology.pdf) only supports the need to distinguish announcement and implementation. | **PASS weakened source / STOP data** | Preserve “effect may have weakened” in the card. Acquire PIT membership, named index/methodology vintages, announcement and effective timestamps; freeze direction and event-window execution. Do not use a legacy inclusion/exclusion result as current evidence. | C |
| **H13 — Gece–gündüz getirisi ayrıştırması** | Lou, Polk & Skouras, [A Tug of War: Overnight versus Intraday Expected Returns](https://doi.org/10.1016/j.jfineco.2019.03.011), supports separate overnight/intraday components and offsetting effects. | **PASS source / STOP data/execution** | The paper's implementation uses intraday transaction data and first-half-hour VWAP-style robustness, not a daily OHLC open/close proxy. Historical SIP versus live IEX also remains unresolved. Require raw/adjusted bars, session/auction semantics and feed parity before economics. | B |
| **H14 — Ay dönümü takvim stratejisi** | McConnell & Xu, [Equity Returns at the Turn of the Month](https://doi.org/10.2469/faj.v64.n2.11), defines the four-day interval as the last trading day of the month through the third trading day after and reports a historical pattern. | **PASS retrospective preflight/accounting / STOP integration** | The frozen candidate is **SPY only**; IWM is irrelevant. Use the v2 `full_2022` and `recent_2024` cells, exact calendar tuples, split-adjusted bars plus separate dividends exactly once, and next-session-open fills. No dividend forecast/filter or unsettled-cash reliance; no live/PIT creation-time claim. | B |
| **H15 — Nakit ödemeli birleşme farkı** | Mitchell & Pulvino, [Characteristics of Risk and Return in Risk Arbitrage](https://doi.org/10.1111/0022-1082.00401), supports event risk and nonlinear market exposure in risk arbitrage. | **PASS source / STOP lifecycle data** | Cash-target ownership is a new long-only adaptation; “borrowless” does not remove break, gap, termination or concentration risk. Acquire terms/amendments, announcement/close/cancel times, consideration, delistings and event cash ledger. | C |
| **H16 — Şirket bölünmesi sonrası fiyatlama** | The owner citation is wrong. The primary published paper is Amihud, Huh & Subrahmanyam, [Liquidity Spillovers: Evidence from Two-Step Spinoffs](https://www.sciencedirect.com/science/article/pii/S1386418125000400); the [SSRN working paper](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3762175) is also available. It studies two-step spinoffs, free-float/liquidity and related-firm valuation spillovers. | **PASS narrowed source / STOP data** | Replace `10.1016/j.finmar.2025.101000`. Narrow the hypothesis to the studied two-step, anticipated effective-date mechanism; the source does not establish generic temporary post-spin pressure or a trade direction. Acquire event/distribution/ownership-conversion/successor identity and lifecycle timestamps. | C |

## Concrete corrections to the owner report

1. Add Low & Tan (2016) to H07, add Christensen, Timmermann & Veliyev (2026)
   to H08 as counterevidence, and make the latter a current-validity gate.
2. Replace the H06 SEC link with the official [Form 4 PDF](https://www.sec.gov/files/form4.pdf),
   and preserve the receipt-time rule.
3. Keep H03's SSRN source, but label the attachment's cash-flow/average-assets
   equation as a frozen adaptation rather than Sloan's exact portfolio measure.
4. Correct H16's DOI/link and narrow its mechanism to two-step spinoffs and
   liquidity spillovers.
5. For H13, state that the source's intraday/VWAP construction is not replicated
   by daily OHLC; IEX/SIP parity remains a stop condition.
6. For H14, retain `PORTFOLIO_UNTESTED` only as a preflight status and add an
   explicit economic `STOP` until the SPY-only provenance, dividend treatment
   and next-open fill contract are recorded. IWM is not a requirement. The
   four days are the last trading day plus the first three trading days of the
   month.
7. Treat H01–H16 as acquisition priorities only. No source-backed item is an
   accepted strategy or a candidate for automatic promotion.

## Source-backed acquisition order

The highest-value shared data work is PIT fundamentals for H01/H02/H03/H05,
then the timestamped Form 4 and fund-flow/ownership feeds for H06/H11. H14 can
receive a low-cost isolated SPY-only retrospective preflight after its native
execution contract is verified; a raw source receipt is required for any
live/PIT availability claim. H07/H08/H09/H10 need forecast, earnings, relationship,
and filing vintages; H04 needs careful corporate-event treatment; H12/H15/H16
need event lifecycle feeds; H13 needs intraday and historical/live feed parity.
This is a data-acquisition queue, not a performance ranking. Every candidate
must still pass causal availability, frozen-specification, cost stress,
shared-ledger complementarity, and holdout gates before any product decision.

## H14 v2 independent implementation audit

The v2 protocol and manifest materially improve the candidate contract and
freeze `h14_spy_turn_of_month_lastday_first3_v2` as SPY only. Independently
recomputed hashes match the manifest: bars
`bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`, bars
manifest `d4266d7331e3bb1ea6303772ad14a4ea4ff01cd7845d507a138836216f81011f`,
corporate actions `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`,
corporate-action manifest
`514cc2f608b043da2608fe87753aa45cdfbe71a99ef970aee82c1d3361955953`,
`pyproject.toml` `1d4d89edf8622f229b9e847ee6818741a6f860c0e9666bc6a4095a488e12a3f6`,
and `requirements.lock`
`f33d2ba645350a8d8daa8df317025744c83a3cb9499f27e335aa6fdbcf842f2b`.

**PASS:** SPY-only signal recipe, `full_2022` (2022-01-03 through
2026-07-30), `recent_2024` (2024-01-02 through 2026-07-30), optional separate
2016 context, no July 31 row, 5% isolated sleeve, 95% cash, fixed 1x/2x/3x
costs, passive control, and exact next-open `L0`/`F4` mapping are coherent.

**PASS with assertions:** Alpaca's primary documentation defines ex-date
entitlement as a purchase on the ex-date not receiving the action and a sale
on or after ex-date retaining entitlement; the engine applies actions before
the same-session open fill. The SPY v5 audit has 40 cash-dividend rows and zero
SPY split rows. Map `ex_date -> 00:00 UTC` and `rate -> cash per share`, while
preserving raw fields and IDs. Missing payable/record dates remain missing;
payable date is not the ownership boundary. See [corporate actions](https://docs.alpaca.markets/us/reference/corporateactions-1)
and [date semantics](https://docs.alpaca.markets/us/reference/getcorporateannouncements).

**LIMITATION:** Alpaca warns that it gives no guarantee on corporate-action
creation time and that provider/processing delays can make actions unavailable
immediately after announcement. The captured CSV has a content hash but no
per-action `available_at` or raw-page receipt proving when each amount became
available. This does not block an explicitly labeled retrospective total-return
diagnostic because H14's calendar entry/exit signal never reads a dividend as a
forecast or filter, and the isolated 95% cash sleeve does not rely on unsettled
funding. It does block any live/PIT availability claim or product integration.
Preserve a raw request/response receipt or conservative availability lag before
making such a claim.

**Required preflight checks:**

1. Verify every v2 input hash and the repository commit; reject July 31 and
   later rows.
2. Assert no window rebasing: each bar's timestamp and OHLCV are identical
   whether loaded from `long_2016`, `full_2022`, or `recent_2024`.
3. Enumerate NYSE month/year and holiday boundaries and assert each event has
   exactly `P0,L0,F1,F2,F3,F4`; exclude an event if any tuple member is outside
   the endpoint.
4. Require the exposure-matched control's full `M0..M4` tuple inside the same
   endpoint; never truncate it or consume post-end prices.
5. Admit only SPY `cash_dividends` rows with valid ex-date and positive rate;
   assert pre-ex-date ownership receives cash, an ex-date-open purchase does
   not, and an ex-date sale retains the dividend. Never apply splits again to
   `adjustment=split` bars. The generic fetch script has fallbacks from
   `ex_date` to process/effective/payable dates; the H14 mapper must verify the
   original SPY `ex_date` field and reject a fallback-derived entitlement date.
6. Record source session label, next-open price, dividend cash, fees, duration,
   and NAV reconciliation event by event, using the same 5% sleeve, costs and
   endpoint for candidate and controls. Report `NOT_COMPARABLE` for any V3
   grid mismatch.

Until the native checks below pass, H14 remains `PREFLIGHT_PENDING`. Once they
pass, a retrospective cash-accrual diagnostic may proceed under the frozen
contract, with live/PIT creation-time provenance clearly marked unproven and
with no dividend-based signal or sizing decision. No product or promotion
verdict follows from that diagnostic.
