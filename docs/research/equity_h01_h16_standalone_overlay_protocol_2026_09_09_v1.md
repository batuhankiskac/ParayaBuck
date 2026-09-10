# Equity H01–H16 standalone/overlay protocol — v1 (2026-09-09)

Status: `FROZEN_PRE_OUTCOME_DEFINITIONS`. This is a pre-outcome definition freeze; it records no economic result and cannot change V3, PAPER, LIVE, production, registry, or configuration.

## Common frozen contract

- Cutoff: `2026-07-31T23:59:59Z`.
- Universe: all US common equities with point-in-time security/CIK identity and eligible evidence at the decision boundary; a current ticker list is not a PIT universe.
- Decision: official UTC publication/acceptance time, then the next supported NYSE regular-session open.
- Windows: full `2022-01-01..2025-12-31`, recent `2024-01-01..2025-12-31`, true holdout `2026-01-01..2026-07-31`; holdout is never used for selection.
- Costs: current equity execution-cost model at `1x`, `2x`, and `3x`, fixed before any run.
- Shared overlay: relative to the 55% equity sleeve, `95% current V3 + 5% candidate`; account caps are equity `<=55%`, gross `<=60%`, cash `>=40%`, positions `<=6`.
- Missing fields, stale/revised values without vintage, identity failures, and unavailable timestamps exclude rows. OHLCV/current labels do not replace causal fields.
- Robustness plan is frozen at calendar/regime, drawdown/tail, turnover, exact-date correlation, 10,000 moving-block bootstrap (21-session blocks, seed 20260909), 10,000 within-window permutations (seed 20260910), and fixed placebo controls.

## Candidate definitions

| ID | Mechanism | Exact formula | Direction | Rebalance/holding | Required causal fields |
|---|---|---|---|---|---|
| H01 | Value | BM_t = PIT book_equity_t / market_cap_t; market_cap_t = PIT shares_outstanding_t * decision_price_t | long highest BM decile | monthly on the last supported NYSE session after all eligible information is available / 21 NYSE sessions | book_equity, shares_outstanding, decision_price, fiscal_period, accepted_at |
| H02 | Gross profitability | GP/TA_t = (revenue_t - COGS_t) / mean(total_assets_t, total_assets_t-1), using first accepted vintage | long highest GP/TA decile | monthly on the last supported NYSE session after all eligible information is available / 21 NYSE sessions | revenue, cost_of_goods_sold, total_assets, fiscal_period, accepted_at |
| H03 | Low accruals | ACCR_t = (net_income_t - operating_cash_flow_t) / mean(total_assets_t, total_assets_t-1) | long lowest accrual decile | monthly on the last supported NYSE session after all eligible information is available / 21 NYSE sessions | net_income, operating_cash_flow, total_assets, accepted_at |
| H04 | Low asset growth | AG_t = total_assets_t / total_assets_t-1 - 1; use the first accepted annual report for each fiscal year | long lowest asset-growth decile | monthly on the last supported NYSE session after all eligible information is available / 252 NYSE sessions or next annual rebalance | total_assets, fiscal_year, accepted_at, merger/spinoff identity |
| H05 | Net payout yield | NPY_t = (cash_dividends_t + issuer_buybacks_t - primary_issuance_t) / market_cap_t, trailing 12 months through decision_time | long highest NPY decile | monthly on the last supported NYSE session after all eligible information is available / 21 NYSE sessions | dividends, buybacks, issuance, market_cap, action_time, accepted_at |
| H06 | Opportunistic public insider purchases | score_i,t = sum(open-market non-derivative direct purchase value by eligible owners in prior 21 sessions) / market_cap_t; eligible only when frozen routine/10b5-1/exclusion taxonomy passes | long highest positive score decile | monthly on the last supported NYSE session after all eligible information is available / 63 NYSE sessions from next open after EDGAR accepted_at | Form4/4-A ownership, transaction code, owner/issuer CIK, accepted_at, 10b5-1/routine flags, price, shares |
| H07 | Analyst forecast revisions | REV_i,t = (new_consensus_eps_i,t - prior_consensus_eps_i,t) / abs(prior_consensus_eps_i,t); horizon is 1 fiscal year and prior vintage is the latest older vintage available at publication | long highest positive revision decile | monthly on the last supported NYSE session after all eligible information is available / 63 NYSE sessions from next open after forecast publication | consensus EPS, prior vintage, horizon, analyst/date identity, publication_at, issuer identity |
| H08 | Post-earnings-announcement drift | SURP_i,t = (actual_eps_i,t - pre_release_consensus_eps_i,t) / abs(pre_release_consensus_eps_i,t); one event per issuer per 63 sessions | long highest positive surprise decile | monthly on the last supported NYSE session after all eligible information is available / 21 NYSE sessions from next open after release_at | actual EPS, pre-release consensus, release_at, accepted_at, revision vintage, event identity |
| H09 | Customer-supplier information spillover | SPILL_supplier,t = customer_surprise_t * relationship_weight_supplier,customer,t; relationship must be PIT named and directional | long highest positive supplier spillover decile | monthly on the last supported NYSE session after all eligible information is available / 21 NYSE sessions from next open after linked customer event | named customer/supplier link, direction, relationship vintage, termination, customer event publication_at, security IDs |
| H10 | Filing language change / Lazy Prices adaptation | CHANGE_i,t = 1 - cosine(tfidf(section_text_latest), tfidf(section_text_prior)); sections are MD&A and risk factors, same form and issuer | long lowest CHANGE quintile (predeclared non-changer adaptation); no short leg | monthly on the last supported NYSE session after all eligible information is available / 63 NYSE sessions from next open after EDGAR accepted_at | original 10-K/10-Q pair, same-CIK/report-period linkage, accepted_at, MD&A/risk-factor text, version/amendment identity |
| H11 | Fund-flow forced selling pressure | PRESSURE_i,t = sum_f(-net_flow_f,t * PIT_ownership_weight_i,f,t / median_dollar_volume_20d_i,t); buy after positive pressure | long highest positive pressure decile | monthly on the last supported NYSE session after all eligible information is available / 21 NYSE sessions from next open after flow/holding publication lag | fund flow, holdings vintage, fund CIK, ownership weight, publication_at, ADV |
| H12 | Index-change price pressure | event_score_i,t = +1 for a PIT named-index addition announced before implementation; removals are recorded but not traded by this long-only contract | long additions only | monthly on the last supported NYSE session after all eligible information is available / 21 NYSE sessions from next open after effective_at | named index, methodology vintage, membership, announcement_at, effective_at, implementation notice, security ID |
| H13 | Overnight versus intraday decomposition | INTRA_i,t = close_i,t-1 / open_i,t-1 - 1; rank at t-1 close, enter at t open, exit at t close; previous overnight return is a control | long highest prior-session intraday-return decile | monthly on the last supported NYSE session after all eligible information is available / one regular session open-to-close | raw and adjusted open/close, session calendar, quote/trade timestamps, auction/fill semantics, feed identity |
| H14 | Turn-of-month ETF exposure | preserve frozen SPY rule: P0 decision, L0 open entry, F3 close decision, F4 open exit; explicit four-session tuple | long SPY during frozen turn-of-month tuple | monthly on the last supported NYSE session after all eligible information is available / exact frozen L0/F1/F2/F3 open-to-open intervals | SPY split-adjusted bars, NYSE calendar, cash dividends, P0/L0/F1/F2/F3/F4 tuple |
| H15 | Cash merger arbitrage adaptation | SPREAD_i,t = (cash_offer_per_share_t - target_price_t) / target_price_t; cash-only offers, no stock consideration | long target after public announcement | monthly on the last supported NYSE session after all eligible information is available / until close/cancel/break/delist terminal event; missing terminal event is non-admissible | cash offer, target ID, announcement_at, amendments, close/cancel/break/delist, consideration currency |
| H16 | Two-step spinoff ownership conversion | event_i,t = first effective date with explicit parent/recipient ownership conversion; signal is a binary event, no inferred return sign | long recipient security after effective date; parent/recipient identity must be explicit | monthly on the last supported NYSE session after all eligible information is available / 63 NYSE sessions from next supported open after effective_at | announcement_at, effective_at, distribution, conversion ratio, successor IDs, adjusted prices, lifecycle |

## H14b diagnostic variants

H14's existing frozen 18-cell result is preserved. H14b variants are separate definitions and have no result until their dated current-V3 capacity/rank inputs are available.

| Variant | Frozen rule | Status |
|---|---|---|
| H14b_spare_slot_only | admit the frozen H14 sleeve only on dates current V3 has fewer than 6 positive positions; otherwise hold candidate sleeve in cash | `NOT_RUN_MISSING_JOINED_CAPACITY_INPUT` |
| H14b_lowest_ranked_slot_substitution | when V3 has 6 positions and H14 is active, replace only the lowest-ranked V3 slot using a predeclared V3 rank snapshot; otherwise preserve V3 | `NOT_RUN_MISSING_DATED_V3_RANK_INPUT` |

## Data and decision boundary

Every candidate requires a receipt-bound source matrix, immutable publication/acceptance time, source vintage, security continuity, explicit missing-data disposition, and a complete full/recent/true-holdout inventory before any economic run. Standalone and shared-capital results must be reported separately; a valid literature mechanism is not local evidence.

## Primary references

- **H01** Value; Asness, Moskowitz, Pedersen: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2174501
- **H02** Gross profitability; Novy-Marx: https://www.nber.org/papers/w15940
- **H03** Accrual anomaly; Sloan: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2598
- **H04** Asset growth; Cooper, Gulen, Schill: https://doi.org/10.1111/j.1540-6261.2008.01370.x
- **H05** Total payout yield; Boudoukh et al.: https://www.nber.org/papers/w10651
- **H06** Routine versus opportunistic insider trades; Cohen, Malloy, Pomorski: https://www.nber.org/papers/w16454
- **H07** Analyst forecast revisions; Chan, Jegadeesh, Lakonishok: https://www.nber.org/papers/w5375
- **H08** Post-earnings-announcement drift; Bernard and Thomas: https://doi.org/10.2307/2491062
- **H09** Customer-supplier information links; Cohen and Frazzini: https://doi.org/10.1111/j.1540-6261.2008.01379.x
- **H10** Filing language changes; Cohen, Malloy, Nguyen: https://www.nber.org/papers/w25084
- **H11** Fund-flow price pressure; Coval and Stafford: https://doi.org/10.1016/j.jfineco.2006.09.007
- **H12** Index effects; Greenwood and Sammon: https://doi.org/10.1111/jofi.13410
- **H13** Overnight versus intraday returns; Lou, Polk, Skouras: https://doi.org/10.1016/j.jfineco.2019.03.011
- **H14** Turn of the month; McConnell and Xu: https://doi.org/10.2469/faj.v64.n2.11
- **H15** Risk arbitrage; Mitchell and Pulvino: https://doi.org/10.1111/0022-1082.00401
- **H16** Two-step spinoffs; Amihud, Huh, Subrahmanyam: https://www.sciencedirect.com/science/article/pii/S1386418125000400

No candidate is economically admitted or promoted by this protocol.
