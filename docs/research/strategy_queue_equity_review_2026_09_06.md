# ARCHIVAL_SUPERSEDED_DO_NOT_USE_AS_CURRENT

This 2026-09-06 intake snapshot is historical context only. Read current status, counts, NEXT state and hashes from the frozen equity-secondary gate report, source matrix, manifest and bound normalized packages.

# Equity candidate queue review — 2026-09-06

Status: research-only intake and feasibility review. No backtest, P&L, ranking,
data acquisition, production, configuration, registry, PAPER, or LIVE change was
performed. The source report's 16 H cards are hypotheses, not validated
strategies. The review uses original papers or official source documentation and
separates their published evidence from a possible long-only ParayaBuck
adaptation.

## Local evidence and hard boundary

The current production equity identity is `equity_residual_momentum_v3`: weekly
cadence, positive 12-1 gate, and frozen 6-1/9-1/12-1 rank consensus. Its 0.55
allocation ceiling and all shared gross, cash, risk, position, minimum-order, and
execution rules remain unchanged. The current research data gate records
`M3_DATA_GATE_PASS` for its own residual/reference data, while explicitly stating
that candidate economics and the candidate OCP were not run.

The locally evidenced equity inputs are:

* `data/historical/equity_pit_symmetric_2016_2026_v5.csv`: 11,686,832 data rows,
  1d OHLCV, 2,658 NYSE sessions (`2016-01-04` through `2026-07-30`) per the
  accepted M3 record; split-adjusted canonical lineage.
* `data/historical/equity_entity_lifetimes_full_v5.csv`: 9,010 entity rows with
  PIT lifetime fields.
* `data/historical/equity_survivorship_full_corporate_actions_v5.csv`: 143,446
  data rows (header excluded), with action type, symbol, ex/record/payable dates,
  rates, source/target symbols, and raw IDs. This is not a proof that every
  candidate's event or accounting field is available at decision time.
* The canonical bars contain complete 2,658-session rows for SPY, QQQ, and XLB,
  XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY. Canonical v5 has zero IWM
  rows, while a separately normalized IWM sidecar exists for the M3 gate; that
  sidecar is irrelevant to the SPY-only H14 diagnostic. The repository pins
  `pandas-market-calendars` 5.4.0 and the NYSE calendar in the M3 manifest.
  `data/release_evidence_v2/calendar_year.json` is the only calendar-named data
  artifact found in the targeted inventory.

There is no locally evidenced PIT fundamentals, insider/Form 4, analyst forecast
vintage, earnings-event, customer/supplier, filing-text-vintage, fund-flow or
ownership, index-membership announcement, merger, or spin-off event dataset.
The canonical daily bars provide OHLCV, but do not by themselves prove a
publication-time event feed or an executable open/close fill model. SEC EDGAR
APIs expose submissions and XBRL facts, but no SEC acquisition is authorized by
this review: <https://www.sec.gov/search-filings/edgar-application-programming-interfaces>.

## Candidate cards

Each verdict is a research gate, not an economic conclusion. `DATA_BLOCKED`
means the required point-in-time input is not locally evidenced. `CAPABILITY_BLOCKED`
means the product contract also lacks the event, lifecycle, or execution ability.
`PORTFOLIO_UNTESTED` means the minimum price data is present but no shared-ledger
experiment has been run.

### H01 — Değer seçimi (value)

* Hypothesis: rank one preselected valuation ratio, such as book value/market
  value, and investigate the value leg as a separate portfolio.
* Original evidence: Asness, Moskowitz, and Pedersen study value and momentum
  across eight markets and report value premia; that evidence includes portfolio
  constructions and does not validate this repository's long-only adaptation.
  Source: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2174501>.
* Adaptation: long-only low valuation names, with no momentum filter unless a
  later manifest explicitly declares one. PIT financial statement vintages,
  identifiers, delistings, and market-cap alignment are required.
* Local status/verdict: `DATA_BLOCKED`; A priority, but the common PIT foundation
  must precede economics. Expected complementarity is a hypothesis: value may
  differ from residual momentum, while sector, size, and distress overlap must
  be measured.
* Frozen minimum test: predeclare one ratio, formation/reporting lag, eligible
  universe, rebalance cadence, long-only selection, costs, and shared-ledger
  controls; stop if PIT facts or identity joins fail.

### H02 — Brüt kârlılık / kalite

* Hypothesis: rank gross profit/total assets as a separate portfolio; do not add
  it as a V3 filter.
* Original evidence: Novy-Marx reports gross profitability's cross-section
  relation and its interaction with value. Source:
  <https://www.nber.org/papers/w15940>.
* Adaptation: long-only high gross-profitability names. PIT financials,
  accounting concept mapping, fiscal-period availability, and restatement rules
  are required. The paper's portfolio evidence is not executable long-only
  proof.
* Local status/verdict: `DATA_BLOCKED`; A priority with H01/H03/H05 shared-data
  work. Complementarity is plausible but quality/value and V3 universe overlap
  must be measured.
* Frozen minimum test: one gross-profit/assets definition, publication lag and
  fiscal alignment, fixed universe/portfolio construction, and cost/shared-ledger
  controls.

### H03 — Düşük tahakkuk (accruals)

* Hypothesis: `(net income − operating cash flow)/average assets`; investigate
  the low-accrual group.
* Original evidence: Sloan finds earnings persistence differs between accrual and
  cash-flow components and prices do not fully reflect that information. Source:
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2598>.
* Adaptation: long-only low accruals, with accounting mapping and sign/scale
  fixed before results. PIT financials, cash-flow statements, average-asset
  construction, restatements, and identifier history are required.
* Local status/verdict: `DATA_BLOCKED`; A priority, quality-adjacent. Expected
  complementarity is uncertain because accruals can correlate with profitability,
  distress, and value.
* Frozen minimum test: one accounting formula and fiscal lag, explicit missing/
  negative handling, fixed selection and turnover rules, then shared-ledger
  control and robustness.

### H04 — Muhafazakâr yatırım / düşük varlık büyümesi

* Hypothesis: research a separate portfolio of companies with low annual asset
  growth.
* Original evidence: Cooper, Gulen, and Schill report asset growth as a
  cross-sectional predictor after comparisons with known characteristics. Source:
  <https://doi.org/10.1111/j.1540-6261.2008.01370.x>.
* Adaptation: long-only low-growth names. PIT balance sheets, fiscal timing,
  mergers/acquisitions, spinoffs, and entity continuity are required; the paper's
  high-minus-low evidence is not a long-only result.
* Local status/verdict: `DATA_BLOCKED`; B priority after H01/H02/H03/H05 data
  feasibility. Complementarity is a hypothesis and may be exposed to investment,
  size, value, and sector tilts.
* Frozen minimum test: annual growth definition and merger treatment frozen in a
  manifest, then fixed universe, cadence, costs, and joint ledger.

### H05 — Net ortak getirisi (net payout yield)

* Hypothesis: `(dividends + buybacks − issuance)/market cap`.
* Original evidence: Boudoukh et al. find total payout yield has cross-sectional
  information beyond dividend yield. Source: <https://www.nber.org/papers/w10651>.
* Adaptation: long-only high net-payout names. PIT dividends, repurchases,
  issuance, split/corporate-action treatment, and market-cap denominators are
  required; published high-minus-low factor evidence is not an executable
  long-only acceptance.
* Local status/verdict: `DATA_BLOCKED`; A priority with the common PIT foundation.
  Complementarity is plausible versus price momentum but payout, value, and size
  overlap must be reported.
* Frozen minimum test: one cash-flow/action definition and observation lag,
  fixed long-only selection, corporate-action handling, costs, and shared ledger.

### H06 — Kamuya açıklanmış yönetici/ortak alımları

* Hypothesis: use unusual open-market insider purchases only after public
  notification.
* Original evidence: Cohen, Malloy, and Pomorski separate routine from
  opportunistic insider trades and find the latter contain the predictive signal.
  Source: <https://www.nber.org/papers/w16454>. SEC Form 4 is generally due by
  the end of the second business day after execution and is deemed filed when
  received: <https://www.sec.gov/files/form4.pdf>.
* Adaptation: long-only buys after EDGAR acceptance/publication time, excluding
  grants, exercises, gifts, and amendments according to a frozen transaction
  taxonomy. PIT CIK/ticker identity, acceptance timestamp, transaction code,
  price/quantity, insider role, and historical routine are required.
* Local status/verdict: `DATA_BLOCKED`; A priority after H01/H02/H03/H05 data
  foundation. Complementarity could be event-driven and distinct, but sparse
  signals, delays, liquidity, and sector clustering are unknown.
* Frozen minimum test: acceptance timestamp is the decision boundary; one
  unusual-trade rule and holding horizon; next executable bar only; no
  transaction-date lookahead.

### H07 — Analist tahmin revizyonları

* Hypothesis: rank published earnings-expectation revisions separately from
  price momentum.
* Original evidence: Chan, Jegadeesh, and Lakonishok document sluggish analyst
  forecast response in their momentum study. Low and Tan study the role of
  analyst forecast revisions in the momentum effect. Sources:
  <https://www.nber.org/papers/w5375> and
  <https://doi.org/10.1016/j.irfa.2016.09.007>.
* Adaptation: long-only positive revision names after the forecast publication;
  the original momentum/long-short evidence does not prove this adaptation.
  Historical forecast vintages, analyst/date identity, publication timestamps,
  estimate horizon, and company identity are required.
* Local status/verdict: `DATA_BLOCKED`; B priority, information-momentum-adjacent.
  Complementarity is uncertain and may duplicate earnings or momentum exposure.
* Frozen minimum test: one forecast horizon and revision aggregation, exact
  publication-time lag, fixed rank/selection/cadence, and shared-ledger controls.

### H08 — Bilanço sonrası sürüklenme (PEAD)

* Hypothesis: positive earnings surprise after the actual release, with a fixed
  event horizon.
* Original evidence: Bernard and Thomas study delayed price response after
  earnings announcements. Current counterevidence from Christensen, Timmermann,
  and Veliyev reports post-announcement strategy returns consistent with weaker
  post-2016 persistence. Sources: <https://doi.org/10.2307/2491062> and
  <https://arxiv.org/abs/2601.08962>.
* Adaptation: long-only positive-surprise names after release; long-short decile
  evidence and historical event windows do not establish this product contract.
  Release timestamp, actual EPS, pre-release consensus, surprise definition,
  revisions/restatements, and next executable session are required.
* Local status/verdict: `DATA_BLOCKED` + `CAPABILITY_BLOCKED`; B priority but the
  repository already records PEAD as blocked because these PIT fields and a typed
  event view are absent. Complementarity is a hypothesis and may overlap H07.
* Frozen minimum test: one surprise formula, release-time boundary, fixed hold
  horizon and event collision policy; fail closed on missing timestamps.

### H09 — Müşteri–tedarikçi bilgi yayılımı

* Hypothesis: information transfers from a known customer to its supplier.
* Original evidence: Cohen and Frazzini find predictable returns across
  economically linked firms using principal-customer relationships. Source:
  <https://doi.org/10.1111/j.1540-6261.2008.01379.x>.
* Adaptation: long-only supplier/customer response after the linked-firm event;
  the published long-short strategy is not a long-only proof. PIT relationship
  vintages, event/price timestamps, link direction, identifier history, and
  relationship termination are required.
* Local status/verdict: `DATA_BLOCKED`; B priority, information-momentum-adjacent.
  Complementarity is potentially high but link concentration and event overlap
  must be measured.
* Frozen minimum test: one link source and vintage rule, one event mapping and
  lag, fixed side/hold period, and shared-ledger event collision handling.

### H10 — Finansal rapor metni değişimi (Lazy Prices)

* Hypothesis: risk/text change versus the prior filing; direction is frozen before
  outcomes.
* Original evidence: Cohen, Malloy, and Nguyen find active filing-language
  changes predict later operations and returns. Source:
  <https://www.nber.org/papers/w25084>.
* Adaptation: a long-only rule must choose a direction (for example, hold
  non-changers or buy a predeclared favorable class); the paper's short-changer,
  buy-non-changer portfolio cannot be transplanted unchanged. Original 10-K/10-Q
  versions, filing acceptance timestamps, section/text normalization, and CIK
  identity are required.
* Local status/verdict: `DATA_BLOCKED`; B priority. Complementarity may be low
  correlation with price momentum but is unproven and text availability/latency
  dominate feasibility.
* Frozen minimum test: one filing pair/version rule, section vocabulary and
  direction fixed ex ante, decision at EDGAR acceptance, and no text generated
  after the cutoff.

### H11 — Fon çıkışından doğan zorunlu satış baskısı

* Hypothesis: fund flow multiplied by pre-known ownership estimates exogenous
  selling pressure.
* Original evidence: Coval and Stafford study predictable mutual-fund flow-driven
  transactions and price pressure. Source:
  <https://doi.org/10.1016/j.jfineco.2006.09.007>.
* Adaptation: a long-only implementation needs a fixed response direction and
  timing (for example, buy after pressure), while the paper's liquidity-provider
  framing is not executable proof. Timely flows, holdings vintages, fund
  identifiers, ownership weights, and publication dates are required.
* Local status/verdict: `DATA_BLOCKED`; A priority after the common PIT foundation.
  Complementarity is a hypothesis and may overlap reversal, liquidity, and
  value; delayed holdings disclosure is a central hazard.
* Frozen minimum test: one flow/ownership observation lag, pressure aggregation,
  fixed long-only response and hold period, costs/capacity, and event overlap.

### H12 — Endeks değişiminden doğan fiyat baskısı

* Hypothesis: investigate pressure after an announced index addition/removal.
* Original evidence: Greenwood and Sammon document a diminished index effect in
  their 2025 study. Source: <https://doi.org/10.1111/jofi.13410>. An official
  index methodology example states composition changes are announced before
  implementation: <https://www.nyse.com/publicdocs/nyse/indices/NYSE_Index_Series_Methodology.pdf>.
* Adaptation: long-only buy/sell handling must be fixed around announcement and
  effective dates; a legacy inclusion/exclusion spread is not validated here.
  PIT membership, announcement and implementation timestamps, index methodology
  versions, and corporate-action adjustments are required.
* Local status/verdict: `DATA_BLOCKED`; C priority because effect may have
  weakened and event execution is costly. Complementarity is uncertain and may
  concentrate in benchmark rebalances.
* Frozen minimum test: one named index and vintage, announcement/effective
  timestamp rule, fixed direction/hold, and no use of post-event membership.

### H13 — Gece–gündüz getirisi ayrıştırması

* Hypothesis: separate an overnight/intraday session signal.
* Original evidence: Lou, Polk, and Skouras decompose returns and report
  strategy-specific overnight/intraday continuation and offsetting effects. Source:
  <https://doi.org/10.1016/j.jfineco.2019.03.011>.
* Adaptation: long-only open/close execution requires explicit session timestamps;
  daily OHLC alone does not prove executable fills or remove bid/ask/gap effects.
  The paper's intraday transaction/VWAP-style construction is not replicated by
  a daily OHLC proxy. Raw and adjusted OHLC, exchange session calendar,
  opening/closing auction or next-bar semantics, and historical/live feed parity
  are required.
* Local status/verdict: `DATA_BLOCKED` + `CAPABILITY_BLOCKED`; B priority but
  already execution/data blocked in the supplied card. Historical canonical feed
  is SIP while configured live stock stream is IEX, so parity is unresolved.
  Complementarity is a hypothesis, not a daily-bar correlation claim.
* Frozen minimum test: one session split, exact order timestamp/fill model,
  adjusted/raw treatment, and SIP/IEX comparability gate before economics.

### H14 — Ay dönümü takvim stratejisi

* Hypothesis: liquid index exposure on pre-fixed turn-of-month trading days.
* Original evidence: McConnell and Xu examine CRSP daily returns and report a
  four-day turn-of-month pattern across countries. Source:
  <https://doi.org/10.2469/faj.v64.n2.11>.
* Adaptation: the frozen diagnostic is SPY only. The `P0` close decision is made
  before the turn-of-month tuple, with the first candidate fill inside it at the
  `L0` open; the `F3` close decision exits at the `F4` open. The paper's
  historical market result does not prove current ETF or long-only profitability.
* Local status/verdict: `RETROSPECTIVE_DIAGNOSTIC_COMPLETE /
  PORTFOLIO_CONTRIBUTION_UNTESTED`. The completed [r4 native receipt](../../data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_receipt.json)
  records exactly 18 `PASS` cells across candidate, passive, and fixed calendar
  control modes, the `full_2022` and `recent_2024` windows, and 1x/2x/3x costs;
  six are H14 candidate cells. Both primary windows end
  2026-07-30; optional 2016 context cannot replace them. Canonical bars cover
  SPY for 2,658 NYSE sessions and are split-adjusted; the source has 40 SPY
  cash-dividend rows and zero SPY split rows. Payment-date posting and live/PIT
  quote truth remain unproved. No current-V3 integration or promotion claim is
  supported.
  Complementarity is a hypothesis: mostly market/ETF timing may add little to
  V3 and must be tested on net time-aligned returns and common down days.
* Frozen minimum test: exact `P0,L0,F1,F2,F3,F4` calendar tuples, one native
  next-open fill convention, split-adjusted bars with ex-date cash accrual once,
  fixed 5% isolated sleeve, same-SPY passive/cash and one fixed four-session
  calendar control; no run until the shared protocol and independent strict
  data gate pass.

### H15 — Nakit ödemeli birleşme farkı

* Hypothesis: buy only cash-offer targets and investigate the completion spread.
* Original evidence: Mitchell and Pulvino characterize risk and return in risk
  arbitrage and its nonlinear market exposure. Source:
  <https://doi.org/10.1111/0022-1082.00401>.
* Adaptation: long-only target ownership has binary completion, break, borrowless
  but event-heavy downside; the historical arbitrage evidence does not validate
  this contract. Deal terms, amendments, closing/cancellation events, target
  identity, announcement timestamps, cash/share consideration, and delistings
  are required.
* Local status/verdict: `DATA_BLOCKED` + `CAPABILITY_BLOCKED`; C priority.
  Complementarity could differ from momentum but gap risk, concentration, and
  lifecycle handling dominate.
* Frozen minimum test: one event source and timestamp, cash-only eligibility,
  fixed entry/exit/cancellation rules, event cash ledger, and capacity limits.

### H16 — Şirket bölünmesi sonrası fiyatlama

* Hypothesis: post-spin-off ownership conversion and temporary price pressure.
* Original evidence: Amihud, Huh, and Subrahmanyam study two-step spinoffs and
  liquidity/valuation spillovers around the effective date. Sources:
  <https://www.sciencedirect.com/science/article/pii/S1386418125000400> and
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3762175>.
* Adaptation: narrow the hypothesis to the studied two-step, anticipated
  effective-date ownership-conversion mechanism. A generic post-spin pressure
  rule and its trade direction are not established. Spin-off announcements and
  effective dates, distributions, ownership conversion, successor identifiers,
  price adjustments, and lifecycle mapping are required.
* Local status/verdict: `DATA_BLOCKED` + `CAPABILITY_BLOCKED`; C priority.
  Complementarity is potentially event-driven but sparse, identity-sensitive,
  and exposed to scanner/execution gaps.
* Frozen minimum test: one event taxonomy, announcement/effective timestamp
  boundary, successor mapping, fixed long-only side/hold, and cancellation or
  missing-identity fail-closed rules.

## Queue and stop conditions

The report's proposed order remains: H01/H02/H03/H05 common PIT foundation,
then H06/H11; H14 as the early price/calendar feasibility screen; H07/H08/H09/H10
after event/forecast/filing data gates; H04 next; H12/H15/H16 after their event
feeds and lifecycle capability exist; H13 only after session execution/feed parity.
This is prioritization, not a scope reduction: all 16 cards remain research
hypotheses. No candidate may be called `PAPER_RESEARCH_CANDIDATE` until novelty,
PIT availability, frozen signal/execution, cost stress, shared-ledger controls,
complementarity, and holdout gates pass. No candidate changes the current V3
identity or its 0.55 ceiling.

## Source and local-evidence notes

Paper links were checked on 2026-09-06 against NBER, Wiley/DOI, JSTOR/DOI,
author-hosted or institutional copies, ScienceDirect/DOI, and official SEC/NYSE
documentation. Published return figures, where any source reports them, are not
reproduced here as repository performance claims. The local data statements are
file/header/count checks and the accepted M3 R5/R4 documentation; no external
dataset was downloaded or added.
