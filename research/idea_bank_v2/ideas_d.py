"""idea_bank_v2 -- ideas D: model-free statistical filters (IC50-IC51), execution and the real LIVE
account (IC52-IC58), tax structure (IC59-IC60).  See ideas_a.py for the schema."""

ALL_OOS = ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"]
NO_M2 = ["GSPC_1957_1999", "M1", "M3", "M4", "M5"]
MODERN_ONLY = ["M3", "M4", "M5"]

IDEAS = [
    # ------------------------------------------------------------------ statistical filters
    dict(
        id="IC50",
        title="Hit-rate (sign-persistence) sizing",
        category="statistical-filter",
        strength_rank=None,
        mechanism=(
            "The *frequency* of up days over the last 60 sessions is a different trend statistic from "
            "the return: two stretches with the same 60-session return can have hit rates of 45% (a few "
            "large up days carrying the whole move) or 60% (broad participation), and the broad version "
            "is the one that persists because it reflects many independent marginal buyers. Scaling the "
            "target by (hit60 - 0.45)/0.10 clipped is a parameter-light, non-parametric participation "
            "tilt that no tested arm computes - breadth_vote_v1 counted *instruments* above their SMAs, "
            "not days within one instrument."
        ),
        literature=[
            dict(ref="Moskowitz, Ooi & Pedersen, Time Series Momentum, JFE 2012 (participation)",
                 url="https://doi.org/10.1016/j.jfineco.2011.11.003"),
            dict(ref="Bollen, Kashyap & Sokolov / sign-persistence evidence in the momentum literature, 2021",
                 url="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3874517"),
        ],
        closest_tested=dict(
            study="breadth_vote_v1 (a count of indices above their own SMAs) and deriv_vote_v1 (price-derivative votes)",
            index_row="docs/research/breadth_vote_v1.md",
            difference=(
                "breadth_vote_v1's statistic counts *instruments* and is a vote; this counts up-days "
                "within the traded instrument - a within-series frequency, which is invariant to the "
                "size of the moves and therefore to the volatility the sizing already uses."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/ewma_band_v1_index.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="closes only")),
        plugin=dict(model="targets_from_data", detail="a rolling hit-rate multiplier inside wants()"),
        arms=[
            dict(id="A", rule="weight *= clip((hit60 - 0.45)/0.10, 0.75, 1.10)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="the same multiplier applied only while the vote is on (early path unchanged)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="early weight *= clip((hit20 - 0.45)/0.10, 0.50, 1.00)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low: a new but weak statistic; it is cheap and orthogonal, which is what a bank entry needs to be.",
    ),
    dict(
        id="IC51",
        title="Cross-sectional dispersion regime",
        category="statistical-filter",
        strength_rank=None,
        mechanism=(
            "Return dispersion across the nine sector ETFs is the other half of IC09's correlation "
            "statistic: high dispersion with low correlation means the market is rotating (an "
            "idiosyncratic regime in which index exposure is less rewarded and drawdowns are shallower), "
            "low dispersion means macro-driven beta (2018, 2020, 2022) in which index risk is "
            "undiversifiable. Sizing inversely with normalised dispersion is a breadth-of-outcomes risk "
            "model - the dispersion of the cross-section's returns, not of the index's own returns."
        ),
        literature=[
            dict(ref="Stivers & Sun, Cross-sectional return dispersion and time variation in the value premium, 2010",
                 url="https://doi.org/10.1007/s11156-009-0138-7"),
            dict(ref="Pollet & Wilson, Average correlation and stock market returns, JFE 2010",
                 url="https://doi.org/10.1016/j.jfineco.2010.02.004"),
        ],
        closest_tested=dict(
            study="vol_window_v1 (own-series volatility) and breadth_vote_v1 (a cross-instrument count)",
            index_row="docs/research/vol_window_v1.md",
            difference=(
                "A cross-sectional dispersion statistic is neither a volatility of the arm's own series "
                "nor a vote about levels; it measures how differently the market's constituents behaved, "
                "which the record never uses as a risk input."
            ),
        ),
        signal_inputs=["the nine sector SPDR daily closes (extra keys sec_*)"],
        data=dict(in_repo=["data/historical/batch_screen_v1_xl*.csv (1998-12-22 ->)"], new_downloads=[],
                  main_window="full",
                  oos=dict(evaluable=MODERN_ONLY,
                           note="SPDRs absent on GSPC_1957_1999/M1/M2 -> 3 applicable windows; reduced 3-window G7")),
        plugin=dict(model="targets_from_data", detail="data.extra('sec_xl*') inside wants()"),
        arms=[
            dict(id="A", rule="weight *= clip(disp_t/median252(disp), 0.70, 1.00), disp = 20-session mean of the cross-sectional stdev of the nine SPDRs' daily returns",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= clip(median252(corr_t)/corr_t, 0.70, 1.00) (the correlation complement, IC09's statistic)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="both multipliers of A and B multiplied together",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low: dispersion and correlation are close relatives; arm C is the combination that would have to carry the idea.",
    ),

    # ------------------------------------------------------------------ execution / LIVE frictions
    dict(
        id="IC52",
        title="Minimum-notional order filter",
        category="execution",
        strength_rank=11,
        mechanism=(
            "K's A1u band suppresses small downward resizes by *drift*; a dollar floor suppresses the "
            "remaining ones by *size*: a permitted downsize whose notional is below a threshold is "
            "skipped and its drift accumulates toward the next band crossing. Every skipped sell is a "
            "taxable event avoided and every skipped buy is free (buying is untaxed, only the spread "
            "costs), so the filter is monotone in the tax direction. It is also a real LIVE friction: a "
            "sub-$250 order in a $100k account pays spread and, at tax time, a currency conversion for "
            "proceeds that are economically irrelevant; the tracking error it creates is bounded by the "
            "next band crossing."
        ),
        literature=[
            dict(ref="gain_band_v3 / band_h2_combo_v1 (the drift bands this filter complements)",
                 url="docs/research/band_h2_combo_v1.md"),
            dict(ref="Alpaca order minimums and fractional trading (the account-level constraint)",
                 url="https://docs.alpaca.markets/docs/orders-at-alpaca"),
        ],
        closest_tested=dict(
            study="gain_band_v1-v3 (drift bands in percentage points) and ewma_band_v1 (2.5/5/10 pp bands)",
            index_row="docs/research/gain_band_v3.md",
            difference=(
                "Every tested band is a *weight-drift* threshold; this one is a *dollar-notional* "
                "threshold, so it bites in proportion to account size rather than to the size of the "
                "position change, and it is the only filter in the record that models the broker's "
                "minimum order economics."
            ),
        ),
        signal_inputs=["the permitted fill's notional (target weight x NAV), the arm's own instrument close"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv"], new_downloads=[],
                  main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="no data requirement; the notional scales with the window's own NAV convention")),
        plugin=dict(model="kernel_option",
                    detail="the filter changes which permitted fills happen, inside the fill decision. NeedsKernel: a fill-notional filter (all stages); the 21-ETF and after-tax stages at $100k are where it bites"),
        arms=[
            dict(id="A", rule="skip a permitted downsize when its notional < $250 (re-evaluate at the next decision)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="skip a permitted downsize when its notional < $1,000",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="skip when its notional < max($250, 0.05% of NAV)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="medium: a direct attack on the trim channel with an account-level justification, and the drift it leaves is bounded by the band.",
    ),
    dict(
        id="IC53",
        title="Order slicing over N sessions",
        category="execution",
        strength_rank=None,
        mechanism=(
            "A state flip moves the whole target, so the resulting order is the largest the strategy "
            "ever places. Slicing it into N equal daily parts reduces market impact and - more "
            "quantifiably - exploits the close-to-open drift the strategy already banks: a sell whose "
            "fills are spread over three opens sells some of the position at later, on average higher "
            "openings, while a buy is penalised symmetrically. In the last week of December slicing also "
            "physically moves part of a realisation past the year boundary, which interacts with H2."
        ),
        literature=[
            dict(ref="Almgren & Chriss, Optimal execution of portfolio transactions, Journal of Risk 2000",
                 url="https://doi.org/10.21314/JOR.2000.038"),
            dict(ref="exec_delay_v1 (day-level fill shifts: next close / t+2 / split fills all failed the real-open OOS windows)",
                 url="docs/research/exec_delay_v1.md"),
        ],
        closest_tested=dict(
            study="exec_delay_v1 (fill-timing family including a split-fill arm) and IB36/IB37 (fill filters/prices, in the bank)",
            index_row="docs/research/exec_delay_v1.md",
            difference=(
                "exec_delay_v1's split arm was one of its day-level variants and it failed on the real-open "
                "OOS windows - which is why this must be pre-registered with those windows as the decisive "
                "gate. The distinct point here is that the schedule is tied to the *drift* the account "
                "earns (sells later, buys earlier), not to a generic delay."
            ),
        ),
        signal_inputs=["the size of the pending resize and the subsequent sessions' opens"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv (OHLC)",
                           "data/historical/ewma_band_v1_index.csv (OHLC)"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=NO_M2, note="opens are needed for the later slices; GSPC_1957-1999 is close-only in the loader -> dropped, reduced 5-window G7")),
        plugin=dict(model="kernel_option",
                    detail="the fill schedule (how much fills on each of the next N opens) is kernel. NeedsKernel: multi-session fill schedule (all stages)"),
        arms=[
            dict(id="A", rule="a resize larger than 25 pp of NAV fills in 3 equal parts on the next 3 opens",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="a resize larger than 50 pp fills in 5 equal parts on the next 5 opens",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="every resize fills in 2 equal parts on the next 2 opens",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low-medium: the tested delay family failed on exactly the windows that matter, so this enters as a test of the drift argument rather than of slicing itself.",
    ),
    dict(
        id="IC54",
        title="Delayed intraday fill (auction vs continuous)",
        category="execution",
        strength_rank=None,
        mechanism=(
            "Production fills at the opening auction print - the most crowded and most gap-contaminated "
            "minute of the session for a leveraged ETF. A fill at a delayed intraday reference (the "
            "close of the first 30 minutes, or the first 30 minutes' VWAP) trades a small timing risk "
            "for a better expected price and, unlike the tested day-level delays, does not hold the "
            "decision one or two days late. exec_delay_v1 tested next-close, t+2 and split fills (all "
            "day-level); IB37's typical-price proxy replaced the fill *price* with (h+l+c)/3 for all "
            "fills and is a symmetric proxy, not an intraday delay."
        ),
        literature=[
            dict(ref="Bogousslavsky, The Cross-Section of Intraday and Overnight Returns, JFE 2021 (open-auction and intraday timing)",
                 url="https://doi.org/10.1016/j.jfineco.2020.07.011"),
            dict(ref="exec_delay_v1 (the tested day-level fill variants)",
                 url="docs/research/exec_delay_v1.md"),
        ],
        closest_tested=dict(
            study="exec_delay_v1 (next close, t+2 open, split fills) and moc_execution_v1 (same-close fills)",
            index_row="docs/research/exec_delay_v1.md",
            difference=(
                "Every tested alternative moves the fill by whole days or to the decision close; a "
                "30-minute delay is a different object (the position still exists for the same session's "
                "drift) and needs intraday data the harness does not hold - arm C is the version that "
                "can be run on the current daily panel."
            ),
        ),
        signal_inputs=["the fill session's intraday bars (new data) or, for arm C, its daily open/close"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (for the arm-C proxy)"],
            new_downloads=[dict(series="QQQ and QLD 30-minute bars (2000->) for arms A and B",
                                needed_by=["IC54"], source="Yahoo chart API (interval=30m is only available for the last 730 days; a vendor or the Alpaca bars API is needed for the full window)"),
                          dict(series="Alpaca SIP 1-minute/30-minute bars for the fill price (the LIVE source of truth)",
                               needed_by=["IC54"], source="Alpaca data API (the same SIP feed the LIVE path consumes)")],
            main_window="arm C: full; arms A and B: only as far back as the intraday series can be sourced (Alpaca SIP from 2016, which covers the 2017+ window but not 2000)",
            oos=dict(evaluable=NO_M2,
                     note="no intraday series exists for the OOS index windows, so G7 is replaced by the 2017+ window and the 21-ETF/after-tax stages - pre-register that reduced gate explicitly"),
        ),
        plugin=dict(model="kernel_option",
                    detail="the fill price is chosen by the kernel. NeedsKernel: an intraday fill reference (all stages)"),
        arms=[
            dict(id="A", rule="fills at the close of the first 30 minutes of the fill session",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="fills at the first-30-minute VWAP of the fill session",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="fills at the midpoint of the fill session's open-to-close range (a daily-data proxy, runnable now)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low-medium: the mechanism is concrete, but the record's execution family has failed repeatedly and the intraday data requirement limits the evaluable windows.",
    ),
    dict(
        id="IC55",
        title="FX conversion cost and the tax-payment schedule",
        category="execution",
        strength_rank=12,
        mechanism=(
            "The after-tax engine converts the TRY tax base at CBRT rates with no spread and pays the "
            "liability out of the USD account at the same rate. In the real account the liability is "
            "paid in TRY, funded by selling USD, and the bank's spread plus the transfer is 0.2-1% of "
            "the payment. Since the tax costs the strategy about 5 pp/yr, a 0.5% conversion cost is "
            "roughly 2.5 bp/yr - small, systematic, and completely unmodelled. Arms A and C price it; "
            "arm B asks the only decision question it creates: pay on the due date, or pre-fund the "
            "liability by selling in a TRY window chosen to be favourable (the engine *times "
            "realisations* everywhere, but never the payment)."
        ),
        literature=[
            dict(ref="after_tax_tr_v1 and vt_level_tax_v1 (the tax engine whose TRY conversion is free at CBRT rates)",
                 url="docs/research/after_tax_tr_v1.md"),
            dict(ref="TCMB daily indicative USD/TRY rates (the published series the engine uses)",
                 url="https://www.tcmb.gov.tr/kurlar/kurlar_tr.html"),
        ],
        closest_tested=dict(
            study="after_tax_tr_v1 / r2_tbill_combo_v1 (the tax engine and the only other account-level friction, the cash sweep)",
            index_row="docs/research/r2_tbill_combo_v1.md",
            difference=(
                "cash_yield_v1 added a *yield* to idle cash; no study adds a *cost* to the tax step. The "
                "payment schedule is also untouched: the realization-timing studies (tax_harvest_v1/v2, "
                "monthly_defer_v1, exit_timing_v2, state_defer_v1, tax_budget_v2) all shape when gains "
                "are realised, never when the liability is converted and paid."
            ),
        ),
        signal_inputs=["USD/TRY CBRT daily, Yi-UFE monthly, the engine's annual liability; a frozen conversion-cost assumption"],
        data=dict(in_repo=["data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv"],
                  new_downloads=[], main_window="full (after-tax stage)",
                  oos=dict(evaluable=ALL_OOS,
                           note="the pre-tax stages are unaffected (there is no tax); the arm is an after-tax-stage measurement plus a payment-timing rule - pre-register non-inferiority for the pre-tax stages")),
        plugin=dict(model="kernel_option",
                    detail="it changes fe_tax's payment/conversion step (and arm B its timing). NeedsKernel: tax-payment accounting (after-tax stage only)"),
        arms=[
            dict(id="A", rule="charge 0.50% on every TRY conversion when the liability is paid (a measurement arm: the same rule as K, priced honestly)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="after_tax_stage_only", params_frozen=True),
            dict(id="B", rule="pre-fund the liability: convert and set aside the due amount three months before the due date (a payment-timing rule)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="after_tax_stage_only", params_frozen=True),
            dict(id="C", rule="charge 1.00% (the cost stress)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="after_tax_stage_only", params_frozen=True),
        ],
        expected_evidence="medium: it is small in pp terms but it is real, systematic and currently invisible in every after-tax number the record publishes.",
    ),
    dict(
        id="IC56",
        title="Dividend-withholding cost on the traded leg",
        category="execution",
        strength_rank=None,
        mechanism=(
            "The traded leg is a synthetic total-return 2x built from the index's price and dividends, "
            "which implicitly assumes dividends are received gross. The real account holds a "
            "US-domiciled ETF, and a Turkish resident's US-source dividends are withheld at source (20% "
            "under the treaty's default, less with a reduced-rate claim) with a foreign tax credit "
            "possible against the Turkish liability. QLD's yield is small, so the drag is around 0.1 pp/yr "
            "- but it is nevertheless a flow the engine does not model anywhere, and it is exactly the "
            "kind of item that has to be right for a LIVE account whose whole edge is a few tenths of a "
            "percentage point after tax."
        ),
        literature=[
            dict(ref="IRS United States-Turkey income tax treaty, dividend article (withholding and credit)",
                 url="https://www.irs.gov/pub/irs-trty/turkey.pdf"),
            dict(ref="instrument_cost_v1 (fund-level costs: expense ratios and swap spreads are modelled, withholding is not)",
                 url="docs/research/instrument_cost_v1.md"),
        ],
        closest_tested=dict(
            study="instrument_cost_v1 (fund-level costs and reset decay) and after_tax_tr_v1 (the tax model's dividend treatment)",
            index_row="docs/research/instrument_cost_v1.md",
            difference=(
                "instrument_cost_v1 compared expense ratios and financing; the after-tax engine applies "
                "dividends to the price series. Neither models a *withholding* flow, and no study "
                "distinguishes a treaty-reduced rate from the default."
            ),
        ),
        signal_inputs=["the leg's dividend series (in repo) and a frozen withholding rate"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv (the QQQ dividend column the synthetic 2x leg is built from)",
                           "data/historical/riskoff_rotation_v4_etfs.csv (ETF dividends)"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS,
                           note="the OOS indices are price-only, so the arm is identical to the control there; pre-register the reduced statement (the main window, the 21 ETFs and the after-tax stage are where the drag exists)")),
        plugin=dict(model="kernel_option",
                    detail="it changes the leg's total-return recurrence (netting the dividend before the price series is built). NeedsKernel: leg construction (all stages)"),
        arms=[
            dict(id="A", rule="leg dividends are net of 20% withholding",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="leg dividends are net of 15% withholding (a reduced-rate claim)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="20% withholding on the on-state and early legs, with the withheld amount claimed as a credit against the Turkish liability (report-only in the after-tax model)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low-medium: a real, currently unmodelled friction whose size (about 0.1 pp/yr) is at the resolution limit of the gates.",
    ),
    dict(
        id="IC57",
        title="Early-close session handling",
        category="execution",
        strength_rank=None,
        mechanism=(
            "NYSE early closes (13:00) and half-days are a few percent of sessions and cluster around "
            "holidays, i.e. inside the December window that H2 already treats specially. The LIVE path "
            "has an early-close flush path, but the research engine treats those sessions as normal. Two "
            "untested behaviours: skip the decision on an early-close session (the printed close is the "
            "early close, and liquidity is thin) and defer the fill to the next full session; or decide "
            "on the previous full session's close so the order is placed into the thin session "
            "deliberately. The cost either way is one session of decision lag a few times a year."
        ),
        literature=[
            dict(ref="execution_model_audit_v1 §4 (the LIVE early-close flush path the research engine does not mirror)",
                 url="docs/research/execution_model_audit_v1.md"),
            dict(ref="NYSE trading calendar, early closes (13:00) and the holiday schedule",
                 url="https://www.nyse.com/markets/hours-calendars"),
        ],
        closest_tested=dict(
            study="exec_delay_v1 (fill-day shifts) and execution_model_audit_v1 (the live/research divergence audit)",
            index_row="docs/research/execution_model_audit_v1.md",
            difference=(
                "exec_delay_v1 shifted *all* fills by a fixed number of days and failed on the OOS "
                "windows; this shifts only the 2-4 early-close sessions a year and is motivated by the "
                "already-shipped live path rather than by a return forecast, so it is a correctness "
                "question as much as a performance one."
            ),
        ),
        signal_inputs=["the NYSE early-close calendar 2000-2026 (public)"],
        data=dict(in_repo=[],
                  new_downloads=[dict(series="NYSE early-close (13:00) session dates 2000-2026",
                                      needed_by=["IC57"], source="nyse.com public calendar (hand-registered, not market data)")],
                  main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="dates only; the OOS indices' own holiday calendars are not modelled, so the arm's effect there is nil - the main window and the after-tax stages carry it")),
        plugin=dict(model="kernel_option",
                    detail="it changes the decision/fill calendar. NeedsKernel: session-type handling (all stages)"),
        arms=[
            dict(id="A", rule="skip the decision on an early-close session (carry it to the next full session)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="decide on the previous full session's close and fill in the early-close session",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="skip only downsizes on early-close sessions (entries/exits/up-resizes keep production)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low: tiny and probably inside the noise, but it closes a documented live/research divergence.",
    ),
    dict(
        id="IC58",
        title="T+1 settlement funding constraint (cash account)",
        category="execution",
        strength_rank=None,
        mechanism=(
            "US securities have settled T+1 since 2024-05-28, but a *cash* account cannot use sale "
            "proceeds until settlement, whereas the engine's cash routing funds a same-session buy from "
            "a same-session sale instantly (this is how the early-path and resize logic is simulated). "
            "If the funded LIVE account is a cash account, a downsize-and-upresize pair in one decision "
            "would be infeasible and would cost a session of exposure. The arm models the constraint "
            "honestly (defer the buy half by one session) and includes the margin-account control, which "
            "is the account the owner must choose."
        ),
        literature=[
            dict(ref="SEC final rule, Shortening the Securities Transaction Settlement Cycle (T+1, effective 2024-05-28)",
                 url="https://www.sec.gov/rules/final/2023/34-96930.pdf"),
            dict(ref="execution_model_audit_v1 (live vs research divergences: cash routing is one of the unmodelled ones)",
                 url="docs/research/execution_model_audit_v1.md"),
        ],
        closest_tested=dict(
            study="execution_model_audit_v1 (the divergence audit) and the engine's own cash routing (never varied)",
            index_row="docs/research/execution_model_audit_v1.md",
            difference=(
                "No study varies the funding assumption: every simulation lets proceeds fund same-session "
                "purchases. This is an account-type question (cash vs margin) with a measurable cost in "
                "the transition sessions, not a return forecast."
            ),
        ),
        signal_inputs=["the session's direction changes (a downsize and an up-resize in the same decision)"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv"], new_downloads=[],
                  main_window="full", oos=dict(evaluable=ALL_OOS, note="no data requirement")),
        plugin=dict(model="kernel_option",
                    detail="it changes the cash routing inside the engine. NeedsKernel: settlement-aware funding (all stages)"),
        arms=[
            dict(id="A", rule="if a decision contains both a downsize and an up-resize, defer the buy half by one session (cash-account convention)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="defer only when the cash balance would otherwise go negative by more than 1% of NAV",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="defer every buy funded by same-session sale proceeds by one session (the strict version)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low: probably a handful of sessions a year, but it decides which account type the LIVE deployment needs.",
    ),

    # ------------------------------------------------------------------ tax structure
    dict(
        id="IC59",
        title="Asymmetric band: immediate up-resizes, K's gate on downsizes",
        category="tax-structure",
        strength_rank=10,
        mechanism=(
            "Buying is tax-free and selling is taxed, so the no-trade band should be asymmetric by "
            "*direction*: keep K's A1u gain-scaled gate on downsizes (0.5/3/8 pp by the USD FIFO gain "
            "fraction) and set the up-resize threshold to zero so any shortfall to the target is bought "
            "immediately. Production uses 0.5 pp in both directions because the band is one knob; the "
            "whole gain_band family varied the down-size side (and ewma_band varied a symmetric band), "
            "so the up-side threshold has never been tested. Removing it makes the strategy track a "
            "rising vol-target channel exactly - less underexposure after calm periods - at the price of "
            "more, smaller buys, which cost spread but no tax."
        ),
        literature=[
            dict(ref="gain_band_v3 (A1u: the shipped gain-scaled downsize band, up-resizes fixed at 0.5 pp)",
                 url="docs/research/gain_band_v3.md"),
            dict(ref="ewma_band_v1 (symmetric 2.5/5/10 pp bands) and IB35-C (a plain 3 pp band control, in the bank)",
                 url="docs/research/ewma_band_v1.md"),
        ],
        closest_tested=dict(
            study="gain_band_v1-v3 / band_h2_combo_v1 (K) and ewma_band_v1 (symmetrical bands)",
            index_row="docs/research/band_h2_combo_v1.md",
            difference=(
                "Every tested band is symmetric or down-side only; the up-resize threshold has been "
                "held at 0.5 pp in every arm ever run. This is a single-parameter change in the one "
                "direction where the tax argument is unambiguous."
            ),
        ),
        signal_inputs=["the target weight and the two books' FIFO records (for the down-size score)"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv (for arm C)"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="the up-band change is a pre-tax fill-schedule change too, so all stages are evaluable and G9 (turnover) applies")),
        plugin=dict(model="hybrid",
                    detail="a RULE with separate up/down band widths registered through fe_sim.RULE (the engine's documented extension point; the existing rule takes one band plus the A1u down-size rule). If the RULE table cannot express an asymmetric band, it becomes a kernel option - check fe_sim.RULE first"),
        arms=[
            dict(id="A", rule="up-resize band 0.0 pp; down-resize band = K's A1u (0.5/3/8 pp by the USD FIFO gain fraction)",
                 targets_from_data=False, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="up-resize band 0.25 pp; the same A1u down band",
                 targets_from_data=False, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="up band 0.0 pp and the down band scored on the indexed-TRY gain fraction instead of the USD one",
                 targets_from_data=False, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium: a one-knob change in the tax-free direction, cheap to test, and its failure mode (more small buys) is bounded by the spread.",
    ),
    dict(
        id="IC60",
        title="December up-resize acceleration (lot ageing)",
        category="tax-structure",
        strength_rank=None,
        mechanism=(
            "Yi-UFE indexation relief accrues with the *age* of the lot, monthly, so a lot bought in "
            "early January enjoys a full year of indexation by the following December while one bought "
            "in late December does not. H2 (production) postpones *downsizes* out of December; the "
            "mirror, never tested, is to pull the next year's purchases forward: allow the target to "
            "exceed production by a fixed 2 pp (capped at 0.98) during the last 10 December sessions so "
            "the extra lots start ageing through the December-to-January indexation step. The cost is "
            "one month of extra exposure in the seasonally strongest month, and the risk is a January "
            "gap against a position that is 2 pp above target - the mirror of the risk H2 accepted."
        ),
        literature=[
            dict(ref="tax_harvest_v2 / H2 (December *downsize* deferral: +0.14/+0.13/+0.27 pp after tax; worst cases are January gaps)",
                 url="docs/research/tax_harvest_v2.md"),
            dict(ref="after_tax_tr_v1 (the monthly Yi-UFE indexation mechanics the ageing argument rests on)",
                 url="docs/research/after_tax_tr_v1.md"),
        ],
        closest_tested=dict(
            study="tax_harvest_v1/v2 (H2: December downsize deferral) and monthly_defer_v1 (month-boundary deferral of downsizes)",
            index_row="docs/research/tax_harvest_v2.md",
            difference=(
                "Both shipped/tested December rules act on the *sell* side. Nothing in the record "
                "changes the *buy* side for tax reasons, so the mechanism (indexation ageing) has never "
                "been tested in the direction where it is free of tax."
            ),
        ),
        signal_inputs=["the session's position in the last 10 NYSE December sessions; the Yi-UFE calendar (after-tax stage)"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/after_tax_tr_v1_yiufe.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS,
                           note="the pre-tax effect is a December exposure tilt, so all six windows are evaluable; the tax content is after-tax only")),
        plugin=dict(model="targets_from_data",
                    detail="a December target add-on inside wants(); the fill rule is unchanged (no kernel needed)"),
        arms=[
            dict(id="A", rule="target = min(0.98, production target + 0.02) in the last 10 NYSE December sessions",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="target = min(0.98, production target x 1.05) in the last 10 NYSE December sessions",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="target = min(0.98, production target + 0.02) in the last 20 NYSE December sessions",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low: it adds exposure (the failure mode the exposure family recorded) for a tax effect that is a fraction of a basis point per lot.",
    ),
]
