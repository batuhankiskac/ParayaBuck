"""idea_bank_v2 -- ideas C: on-state instrument (IC39-IC40), two-leg allocations (IC41-IC46),
drawdown/recovery sizing (IC47-IC49).  See ideas_a.py for the schema."""

ALL_OOS = ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"]
NO_M2 = ["GSPC_1957_1999", "M1", "M3", "M4", "M5"]
MODERN_ONLY = ["M3", "M4", "M5"]
BANKED = "in the bank (idea_bank_v1) and never screened"

IDEAS = [
    # ------------------------------------------------------------------ on-state instrument
    dict(
        id="IC39",
        title="Margin-financed QQQ instead of QLD (broker leverage)",
        category="on-state-instrument",
        strength_rank=8,
        mechanism=(
            "QLD's 2x exposure is embedded leverage: the fund pays an expense ratio (about 0.95%) on the "
            "whole position plus a swap spread over the bill rate on the borrowed half, continuously "
            "and in all states. Financing the same 2x exposure at the broker (a Reg-T margin loan on "
            "QQQ) costs the broker's margin rate on the borrowed half only while it is borrowed, has no "
            "expense ratio, and receives the index's dividends on the *whole* position instead of on the "
            "unlevered half. instrument_cost_v1 tested TQQQ - another fund - and found its expense "
            "saving smaller than its financing cost; the broker-margin route, where the cost is a "
            "published rate rather than a fund's embedded swap, was never tested. At a 2x target the "
            "candidate saving is the expense ratio on the full position (about 0.9 pp/yr) plus the "
            "swap-to-margin spread on the borrowed half; the cost is margin-call risk (tested by arm B's "
            "exposure cap) and that the loan is repriced daily."
        ),
        literature=[
            dict(ref="instrument_cost_v1 (TQQQ at 2/3 weight vs QLD: the tested fund-level alternative)",
                 url="docs/research/instrument_cost_v1.md"),
            dict(ref="Frazzini & Pedersen, Betting Against Beta (the cost of the leverage constraint), JFE 2014",
                 url="https://doi.org/10.1016/j.jfineco.2013.10.005"),
        ],
        closest_tested=dict(
            study="instrument_cost_v1 (TQQQ vs QLD) and early_qqq_v1 (unlevered legs)",
            index_row="docs/research/instrument_cost_v1.md",
            difference=(
                "instrument_cost_v1 compared two *funds* (each with an expense ratio and a swap) and "
                "finished by rejecting the alternative; this compares a fund with a *broker loan*. The "
                "sign of the difference is not known in advance because the margin rate is an "
                "account-level published number, not a fund's embedded cost - and the arm is a "
                "leveraged QQQ position, so it also changes the dividend and reset mechanics."
            ),
        ),
        signal_inputs=["QQQ daily close (the vote and the traded instrument)", "the frozen broker margin rate"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ, SPY, QLD, VOO)",
                     "data/historical/stress_robustness_v1_dtb3.csv (financing)",
                     "the live broker's published margin rate, frozen at the current value and stressed +/-2 pp in arm C"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=ALL_OOS,
                     note="the OOS windows use the index itself at gearing 1; a margin loan at the broker rate over TB3MS is available there too, so all six windows remain evaluable"),
        ),
        plugin=dict(model="kernel_option",
                    detail="the account must carry a loan balance and accrue daily interest on it; fe_sim has no margin modelling, so this is a kernel option (all stages). NeedsKernel: a cash loan inside the account"),
        arms=[
            dict(id="A", rule="hold 2x exposure as wt/2 in QQQ plus a cash loan of wt/2 of NAV at the frozen margin rate (no expense ratio, dividends on the full position)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="as A but the loan is capped so gross exposure never exceeds 1.6x (the margin-call mitigation)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="as A with the margin rate stressed +2 pp (the cost-sensitivity arm)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="medium: a deterministic cost arbitrage between two ways of buying the same exposure (the mechanism IB27 was ranked #1 for), with a published input and an explicit cost stress.",
    ),
    dict(
        id="IC40",
        title="S&P 500 2x on-state instrument (SSO / synthetic 2x SPY)",
        category="on-state-instrument",
        strength_rank=None,
        mechanism=(
            "The signal is Nasdaq-based and the instrument is Nasdaq 2x, so the entire concentration - "
            "including the index that fell 83% in 2000-02 - sits in one market. A 2x S&P position (the "
            "real SSO, or a synthetic daily-reset 2x built from SPY with the engine's own recurrence) "
            "keeps the leverage and the fill convention but changes the underlying to the broader index, "
            "whose sector weights are less concentrated in long-duration technology. relstrength_v1 "
            "*rotated* between QLD and SSO by relative strength and lost; trend_robustness_v2 *split* the "
            "position by two index votes and also lost. The untested question is simpler: is the "
            "on-state instrument the right underlying at all, given that the vote already runs on QQQ?"
        ),
        literature=[
            dict(ref="relstrength_v1 (QLD vs SSO rotation: loses 1.2-4.1 pp CAGR) and trend_robustness_v2 (QLD+SSO split)",
                 url="docs/research/relstrength_v1.md"),
            dict(ref="Goyal & Jegadeesh, Comovement, Journal of Finance 2018 (index membership and concentration)",
                 url="https://doi.org/10.1111/jofi.12595"),
        ],
        closest_tested=dict(
            study="relstrength_v1 (rotation) and trend_robustness_v2 (index-vote split) and early_qqq_v1 (SSO as the early leg)",
            index_row="docs/research/relstrength_v1.md",
            difference=(
                "The tested arms change the *allocation rule* between two leveraged funds or move only "
                "the early leg's instrument; none replaces the on-state instrument outright while holding "
                "the signal fixed. The measured failure of the rotation does not answer this question, "
                "since a rotation must also get the timing right."
            ),
        ),
        signal_inputs=["QQQ (or the local index) daily close for the vote; SPY daily close for the synthetic 2x leg"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ, SPY)",
                     "the synthetic 2x recurrence of fe_signals.synthetic_2x applied to SPY"],
            new_downloads=[dict(series="SSO (ProShares Ultra S&P500, 2006-06-21 ->) for the real-fund cross-check",
                                needed_by=["IC40"], source="Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/SSO)")],
            main_window="full via the synthetic 2x SPY leg",
            oos=dict(evaluable=ALL_OOS,
                     note="in the OOS windows the on-state leg is already 2x of that window's own index, so this arm degenerates to the control there; the informative stages are the main window, the 21-ETF stage and the real-fund cross-check - state that in the pre-registration as a reduced OOS gate"),
        ),
        plugin=dict(model="kernel_option",
                    detail="the panel offers only the window's own underlying at 2x/1x; an SPY-based 2x leg is a new leg construction (data plumbing + kernel). NeedsKernel: a second leg family"),
        arms=[
            dict(id="A", rule="on-state instrument = synthetic daily-reset 2x of SPY; the vote stays QQQ's 2-of-3",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="as A and the early leg is also 2x SPY (unconditional early instrument change)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="as A but the vote is the 2-of-3 computed on the S&P index itself (signal and instrument aligned)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="medium-low: a real concentration question with a clean test, but the tested rotation's failure is an unfavourable base rate.",
    ),

    # ------------------------------------------------------------------ two-leg
    dict(
        id="IC41",
        title="Dual-core: QLD + SSO, one FIFO book per index",
        category="two-leg",
        strength_rank=9,
        mechanism=(
            "Split the target exposure between a Nasdaq-2x book and an S&P-2x book. Two equally "
            "leveraged but 0.9-correlated books dilute the single-index concentration (the 2000-02 "
            "problem) while keeping the leverage, and - the reason a two-leg kernel is being built - "
            "every downsize can be routed to the book whose FIFO head carries the smaller gain, so the "
            "tax on the trims that carry about 91% of the realised taxable gain falls further than K's "
            "band alone can achieve. The routing is bounded by a per-session route cap, so the split "
            "cannot drift far from its target."
        ),
        literature=[
            dict(ref="relstrength_v1 (the tested QLD-vs-SSO rotation) and trend_robustness_v2 (the tested QLD+SSO index split)",
                 url="docs/research/relstrength_v1.md"),
            dict(ref="Markowitz, Portfolio Selection, Journal of Finance 1952 (the diversification the two books buy)",
                 url="https://doi.org/10.1111/j.1540-6261.1952.tb01525.x"),
        ],
        closest_tested=dict(
            study="relstrength_v1 (rotation between QLD and SSO by relative strength) and trend_robustness_v2 (the QLD+SSO split by index votes)",
            index_row="docs/research/trend_robustness_v2.md",
            difference=(
                "The tested split sized the *second index* by its own trend vote and gave up 1.2-2.7 pp "
                "CAGR; the tested rotation switched instruments by relative strength. This holds both "
                "books simultaneously at a fixed 50/50 of the target and uses the second book for *tax "
                "routing* (IB28's mechanism, defined in the bank only for QQQ+QLD, never for a second "
                "index)."
            ),
        ),
        signal_inputs=["QQQ and SPY daily closes (the vote and the second leg's own bar); the two books' FIFO lot records"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ, SPY)"],
            new_downloads=[dict(series="SSO (ProShares Ultra S&P500, 2006-06-21 ->) for the real-fund cross-check",
                                needed_by=["IC41"], source="Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/SSO)")],
            main_window="full (the S&P leg is synthetic 2x from SPY before 2006-06 and the real SSO afterwards in the cross-check)",
            oos=dict(evaluable=ALL_OOS,
                     note="in the OOS windows the second index is not available, so the arm runs as the local index's 2x in one book - the OOS stage then measures only the routing effect; pre-register that as a reduced G7/G8 statement"),
        ),
        plugin=dict(model="two_leg",
                    detail="second_symbol=SSO; two books with separate FIFO queues and one cash account; pre-tax/OOS stages can run as a blended synthetic instrument (IB27's convention). NeedsKernel: two-leg account"),
        arms=[
            dict(id="A", rule="each leg carries 0.5 of the target; downsizes fill in the leg with the lower USD FIFO gain fraction (route cap 10 pp), up-resizes in the leg further below its share",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="leg weights proportional to 1/vol20 of each index, same routing as A",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="as A, but the early path (half weight) is held only in the Nasdaq leg",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="medium: the diversification is real, the routing is IB28's mechanism (ranked #6 there), and the cost is a second book to maintain.",
    ),
    dict(
        id="IC42",
        title="Two-leg routing on the TRY-indexed (taxable) gain, not the USD gain",
        category="two-leg",
        strength_rank=5,
        mechanism=(
            "Turkish tax is levied on the TRY gain *after* Yi-UFE indexation, not on the USD gain: a lot "
            "bought when the lira was weak has an indexed gain far below its USD gain, and the gap grows "
            "with the inflation accumulated between the two legs' purchase months. K's band and IB28's "
            "routing both score lots by the USD FIFO gain fraction, which is a proxy for the taxable "
            "quantity that is wrong by exactly that accumulated indexation. With two legs the kernel can "
            "route a band-permitted downsize to the leg with the smaller *indexed-TRY* gain fraction, "
            "using each leg's FIFO head, the purchase month's Yi-UFE and the CBRT rate on the sale date."
        ),
        literature=[
            dict(ref="after_tax_tr_v1 (the Yi-UFE indexation and FIFO mechanics the routing scores)",
                 url="docs/research/after_tax_tr_v1.md"),
            dict(ref="IB28 (USD-gain two-leg routing, in the bank) and gain_band_v1-v3 (the USD gain fraction defines the band widths)",
                 url="docs/research/gain_band_v3.md"),
        ],
        closest_tested=dict(
            study="gain_band_v1-v3 / band_h2_combo_v1 (the USD gain fraction driving the band widths) and gain_band_v2's A1/A2 variants",
            index_row="docs/research/gain_band_v3.md",
            difference=(
                "Every shipped realisation rule scores lots in USD; the tax is computed in TRY with "
                "indexation. The routing score is therefore a *different, tax-correct* quantity - and "
                "IB28's USD version, the closest idea in the bank, is not the same rule whenever TRY "
                "inflation moved between the legs' purchase dates (which is most of 2000-2026)."
            ),
        ),
        signal_inputs=["the two legs' FIFO lot records (purchase dates), USD/TRY CBRT daily, Yi-UFE monthly"],
        data=dict(
            in_repo=["data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv (after-tax stage inputs)",
                     "data/historical/stress_robustness_v1_daily.csv (the two legs' prices)"],
            new_downloads=[],
            main_window="full (the routing is inert before 2000 only in the sense that the after-tax calendar starts there)",
            oos=dict(evaluable=ALL_OOS,
                     note="pre-tax, the routing is a pure lot-selection rule among two identical legs and changes nothing; its content is the after-tax stage - state that in the pre-registration (a reduced pre-tax gate: non-inferiority only)"),
        ),
        plugin=dict(model="two_leg",
                    detail="second_symbol=QQQ; the routing score reads both books' FIFO heads plus the TRY/Yi-UFE calendar. NeedsKernel: two-leg account + tax-aware fill decision"),
        arms=[
            dict(id="A", rule="route downsizes to the leg with the smaller TRY-indexed FIFO gain fraction (route cap 10 pp); band widths = K's A1u on the USD fraction",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="after_tax_stage_only", params_frozen=True),
            dict(id="B", rule="as A with a route cap of 5 pp",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="after_tax_stage_only", params_frozen=True),
            dict(id="C", rule="as A, and the band widths are themselves keyed to the indexed-TRY fraction (0.5/3/8 pp at <=1%/<=5%/>5%)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="after_tax_stage_only", params_frozen=True),
        ],
        expected_evidence="medium-high: it scores the lot by the quantity the tax is actually levied on, which no shipped rule or banked idea does.",
    ),
    dict(
        id="IC43",
        title="Two-leg loss-netting route",
        category="two-leg",
        strength_rank=None,
        mechanism=(
            "Turkish tax is assessed per calendar year on net TRY gains with no loss carry-forward, so a "
            "realised loss is only valuable if a gain is realised in the same year. With two books the "
            "kernel can realise a small loss in the cheaper-gain leg *in the same session* as a "
            "band-permitted downsize in the other, netting within the year - which tax_budget_v2's "
            "single-leg year-end pool fill could only do by selling the same instrument, and which "
            "tax_harvest_v1's year-end harvesting could not do at all because indexed lots are rarely at "
            "a loss by December. The route is bounded by a fraction of the downsize's TRY gain and by the "
            "requirement that a loss lot exists."
        ),
        literature=[
            dict(ref="tax_budget_v2 (the single-leg year-end loss-pool fill: fired 4 times in 26.5 years)",
                 url="docs/research/tax_budget_v2.md"),
            dict(ref="tax_harvest_v1 H1 (year-end harvesting: indexed TRY lots are rarely at a loss)",
                 url="docs/research/tax_harvest_v1.md"),
        ],
        closest_tested=dict(
            study="tax_budget_v2 (deliberate year-end loss realisation in one leg) and tax_harvest_v1 H1",
            index_row="docs/research/tax_budget_v2.md",
            difference=(
                "The tested arms realise losses at year-end in the same instrument, when nothing is being "
                "sold; this pairs the loss with a *concurrent* gain realisation in a different book, "
                "which is the only pairing that makes the loss usable under a no-carry-forward rule."
            ),
        ),
        signal_inputs=["the two legs' FIFO lot records", "USD/TRY CBRT, Yi-UFE"],
        data=dict(
            in_repo=["data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv",
                     "data/historical/stress_robustness_v1_daily.csv"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=ALL_OOS,
                     note="inert pre-tax (it only changes which lot is sold); the content is the after-tax stage - pre-register non-inferiority for the pre-tax stages"),
        ),
        plugin=dict(model="two_leg",
                    detail="second_symbol=QQQ; NeedsKernel: two-leg account + tax-aware fill decision"),
        arms=[
            dict(id="A", rule="on a band-permitted downsize, additionally sell the loss leg for up to 25% of the downsize's realised TRY gain (only if a loss lot exists)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="after_tax_stage_only", params_frozen=True),
            dict(id="B", rule="the same with a 50% cap",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="after_tax_stage_only", params_frozen=True),
            dict(id="C", rule="the same with a 25% cap, but only while the year-to-date realised TRY gain exceeds 100,000 TRY",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="after_tax_stage_only", params_frozen=True),
        ],
        expected_evidence="medium-low: the no-carry-forward rule makes the pairing valuable, but loss legs must exist at exactly the moment a downsize is permitted.",
    ),
    dict(
        id="IC44",
        title="Two-leg band widths keyed to the indexed (taxable) gain fraction",
        category="two-leg",
        strength_rank=None,
        mechanism=(
            "K's band widens with the USD gain fraction of the units a sell would consume (0.5/3/8 pp at "
            "<=1%/<=5%/>5%). The taxable quantity is the indexed TRY fraction, and for a lot held "
            "through high-inflation months the indexed fraction can be far below the USD one - so the "
            "shipped rule trims when the actual tax cost is small, and it is too tight for the lots "
            "whose indexed gain is large. With two books the widths can also be evaluated per leg, and "
            "the extra freedom is that each leg's own FIFO head sets its own band."
        ),
        literature=[
            dict(ref="gain_band_v3 (the USD-fraction band widths shipped as A1u)",
                 url="docs/research/gain_band_v3.md"),
            dict(ref="IB39 (band widths by FX regime, in the bank): the closest variant, keyed to a market state rather than to the tax base",
                 url="docs/research/band_h2_combo_v1.md"),
        ],
        closest_tested=dict(
            study="gain_band_v1-v3 / band_h2_combo_v1 (K) and ewma_band_v1 (fixed bands 2.5/5/10 pp)",
            index_row="docs/research/band_h2_combo_v1.md",
            difference=(
                "K's score is the USD gain fraction; ewma_band's is the weight drift. Scoring the "
                "*indexed-TRY* fraction - the tax base after Yi-UFE relief - is a different state "
                "variable, and the per-leg evaluation is only possible with two books."
            ),
        ),
        signal_inputs=["each leg's FIFO lot record", "USD/TRY CBRT, Yi-UFE"],
        data=dict(
            in_repo=["data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv",
                     "data/historical/stress_robustness_v1_daily.csv"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=ALL_OOS,
                     note="the widths change the pre-tax fill schedule too (a different number of trims), so the pre-tax stages are evaluable - and must be, since G9 (turnover) applies"),
        ),
        plugin=dict(model="two_leg",
                    detail="second_symbol=QQQ; NeedsKernel: two-leg account + a per-leg band rule"),
        arms=[
            dict(id="A", rule="band widths 0.5/3/8 pp at an indexed-TRY FIFO gain fraction of <=1%/<=5%/>5% (K with the score swapped), evaluated per leg",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="as A plus a fourth width step: 12 pp above a 15% indexed fraction",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="widths 0.5/3/8 pp on max(USD fraction, indexed fraction) per leg (the conservative combination)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="medium: a one-variable change to the shipped rule using a better score for the same decision; the indexed fraction is small more often, so turnover may rise.",
    ),
    dict(
        id="IC45",
        title="Permanent small gold sleeve funded by a lower equity target (two-leg)",
        category="two-leg",
        strength_rank=None,
        mechanism=(
            "Gold as a *state-dependent* risk-off asset was tested and its OOS test failed "
            "(gold_riskoff_oos_v1); a permanent, small, band-rebalanced gold sleeve funded by a "
            "proportionally lower equity target is a different object with no timing content: it is a "
            "portfolio allocation between a leveraged equity core and a diversifying real asset, and "
            "the two-leg kernel makes its tax treatment explicit (its own FIFO queue, and rebalances "
            "routed to the cheaper book). The question it answers is whether the *average* cost of "
            "holding gold is repaid by its diversification, without any state variable to get wrong."
        ),
        literature=[
            dict(ref="Baur & Lucey, Is Gold a Hedge or a Safe Haven?, Financial Review 2010",
                 url="https://doi.org/10.1111/j.1540-6288.2010.00254.x"),
            dict(ref="gold_riskoff_oos_v1 (the tested state-dependent arm) and leverage_frontier_v1 (GOLD risk-off)",
                 url="docs/research/gold_riskoff_oos_v1.md"),
        ],
        closest_tested=dict(
            study="gold_riskoff_oos_v1 / leverage_frontier_v1 / riskoff_rotation_v4 (gold as a state-dependent holding)",
            index_row="docs/research/gold_riskoff_oos_v1.md",
            difference=(
                "All tested gold arms switch the whole account into gold when a state fires; none holds "
                "a *fixed small* sleeve alongside the equity core. The risk profile is completely "
                "different (a permanent 8-10% sleeve cannot cause the DD increase the switches caused)."
            ),
        ),
        signal_inputs=["GLD and QLD daily closes; the two books' FIFO lot records"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_gld.csv (2004-11-18 ->, extra key gld)",
                     "data/historical/stress_robustness_v1_daily.csv"],
            new_downloads=[],
            main_window="partial: GLD starts 2004-11; before that the sleeve is cash (a fixed convention)",
            oos=dict(evaluable=MODERN_ONLY, note="GLD 2004+ -> M3/M4/M5 only; reduced 3-window G7"),
        ),
        plugin=dict(model="two_leg",
                    detail="second_symbol=GLD; NeedsKernel: two-leg account (the sleeve is a priced second book)"),
        arms=[
            dict(id="A", rule="0.90 x production target in the QLD book + 0.08 in GLD, rebalanced only outside a 2 pp band",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="0.80 x production target + 0.18 GLD, same band",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="0.88 x production target + a gold sleeve sized 0.10 x clip(vol20(QLD)/vol20(GLD), 0.5, 2.0)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low-medium: no timing content to get wrong; the tested gold arms' failures came from the switch, not from gold.",
    ),
    dict(
        id="IC46",
        title="Permanent duration sleeve (QLD + IEF, risk-parity-lite) as a two-leg arm",
        category="two-leg",
        strength_rank=None,
        mechanism=(
            "A permanent small duration sleeve alongside the leveraged equity core is the standard "
            "leveraged risk-parity construction. As an *after-tax* object its point is the realisation "
            "schedule: r2_tbill_combo_v1's bill sleeve lost after tax because its gains were realised "
            "whenever the trend state flipped; a permanent sleeve is touched only by a 2 pp rebalance "
            "band, so its realisations are rare and can be routed to the cheaper book. The question is "
            "whether the sleeve's diversification pays for the drag of holding it through the equity "
            "bull markets, at fixed weights, with no state variable."
        ),
        literature=[
            dict(ref="Asness, Frazzini & Pedersen, Leverage Aversion and Risk Parity, FAJ 2012",
                 url="https://doi.org/10.2469/faj.v68.n1.1"),
            dict(ref="r2_tbill_combo_v1 (the tested bill sleeve after tax: -1.08 pp median bootstrap)",
                 url="docs/research/r2_tbill_combo_v1.md"),
        ],
        closest_tested=dict(
            study="r2_tbill_combo_v1 (S2_10 bill sleeve, lost after tax) and tbill_sweep_v1 (the sweep rules)",
            index_row="docs/research/r2_tbill_combo_v1.md",
            difference=(
                "The tested sleeves are state-keyed (bills only while the vote is off), so their gains "
                "are realised at every state flip - the diagnosed cause of the after-tax loss. A "
                "permanent sleeve with a 2 pp rebalance band realises on a completely different "
                "schedule; the two-leg kernel is what makes the routing explicit."
            ),
        ),
        signal_inputs=["IEF and QLD daily closes; the two books' FIFO lot records"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_ief.csv (2002-07-30 ->, extra key ief)",
                     "data/historical/stress_robustness_v1_daily.csv"],
            new_downloads=[],
            main_window="partial: IEF starts 2002-07 (cash before, a fixed convention)",
            oos=dict(evaluable=MODERN_ONLY, note="IEF 2002+ -> M3/M4/M5 only; reduced 3-window G7"),
        ),
        plugin=dict(model="two_leg",
                    detail="second_symbol=IEF; NeedsKernel: two-leg account"),
        arms=[
            dict(id="A", rule="0.88 x production target in the QLD book + 0.10 in IEF, rebalanced only outside a 2 pp band",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="0.78 x production target + 0.20 IEF, same band",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="0.90 x production target + an IEF sleeve sized 0.08 x clip(vol20(QLD)/vol20(IEF), 0.5, 3.0)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low-medium: the after-tax precedent for a duration sleeve is negative, but that sleeve was state-keyed and this one is not.",
    ),

    # ------------------------------------------------------------------ drawdown / recovery
    dict(
        id="IC47",
        title="Recovery-ramp sizing of the early position",
        category="drawdown-recovery",
        strength_rank=None,
        mechanism=(
            "Exposure is cheapest exactly when price has started recovering but the vote is still off - "
            "the early path's territory - yet production sizes that position at a fixed half weight. "
            "Scaling the early weight by how much of the 252-session drawdown has been recovered "
            "(recovered = (close - trough126)/(max252 - trough126), 0 at the trough, 1 at the old high) "
            "makes re-entry a ramp instead of a step and, unlike ladder_v1 (which keyed the early size "
            "on a vol-normalised recovery metric and won all three after-tax windows but missed the B1 "
            "share at 53.1%), the variable is parameter-free and monotone in the price itself."
        ),
        literature=[
            dict(ref="ladder_v1 (early-weight ladder keyed on a vol-normalised recovery metric: +0.30/+0.21/+0.23 pp after tax, B1 share 53.1%)",
                 url="docs/research/ladder_v1.md"),
            dict(ref="crash_entry_v1 (the tested crash-recovery entry *trigger*; all arms lost)",
                 url="docs/research/crash_entry_v1.md"),
        ],
        closest_tested=dict(
            study="ladder_v1 (early-leg conviction ladder) and crash_entry_v1 (drawdown-recovery entry path)",
            index_row="docs/research/ladder_v1.md",
            difference=(
                "ladder_v1 already changed the early weight, so this must be judged as a *different "
                "ladder variable*: the recovered fraction of the price drawdown, which is bounded, "
                "parameter-free and available from the close alone, instead of the vol-normalised "
                "recovery metric whose ceiling had to be chosen. crash_entry_v1 changed the trigger - "
                "this keeps production's early conditions untouched."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/ewma_band_v1_index.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="closes only; 252-session warm-up")),
        plugin=dict(model="targets_from_data", detail="early-leg weight as a function of the recovered fraction inside wants()"),
        arms=[
            dict(id="A", rule="while in the early state, early weight = production early weight x clip(recovered, 0.5, 1.0)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="the same with clip(recovered, 0.25, 1.0)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="early weight = 0.5 x production target x (1 + recovered), capped at the full production target",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: the closest tested ladder arm was close (B1 53.1% vs the 55% gate), and this variable is simpler and needs no calibration.",
    ),
    dict(
        id="IC48",
        title="Drawdown-velocity sizing",
        category="drawdown-recovery",
        strength_rank=None,
        mechanism=(
            "The *speed* of a decline, not its depth, predicts continuation: a 10% fall in 10 sessions "
            "is a de-leveraging event, a 10% fall over a year is a grind, and only the former is "
            "followed by more of itself. Scaling exposure by the 20-session decline from the running "
            "20-session high (dd20) is a fast risk control that acts inside the vote's 200-session lag, "
            "and it is a different statistic from IB29's depth variables (252-session and all-time-high "
            "drawdowns) and from dip_overlay_v1's fixed -5% triggers."
        ),
        literature=[
            dict(ref="dip_overlay_v1 (5-day -5% and RSI(2) triggers: all arms added drawdown or lost)",
                 url="docs/research/dip_overlay_v1.md"),
            dict(ref="Daniel & Moskowitz, Momentum Crashes, JFE 2016 (drawdown dynamics and trend fragility)",
                 url="https://doi.org/10.1016/j.jfineco.2015.12.002"),
        ],
        closest_tested=dict(
            study="IB29 (drawdown-depth scaling, in the bank) and dip_overlay_v1 (short-term drop triggers)",
            index_row="docs/research/dip_overlay_v1.md",
            difference=(
                "The tested overlays use *depth* below a prior close and *fixed* percentage thresholds. "
                "The 20-session drawdown from a running high is a rate-of-change variable with a "
                "continuous (not trigger) response, which is what distinguishes it from both."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/ewma_band_v1_index.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="closes only")),
        plugin=dict(model="targets_from_data", detail="dd20 scaling inside wants()"),
        arms=[
            dict(id="A", rule="weight *= clip(1 - dd20/0.10, 0.50, 1.00), dd20 = 1 - close/max(close over the last 20 sessions)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= clip(1 - 0.5*(dd20/dd252), 0.50, 1.00) (speed relative to depth)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="mirror: weight *= clip(1 + dd20/0.10, 1.00, 1.25) (buy the fast dip; the dip_overlay sign)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low: the family (fast risk overlays) has lost every time; the mirror arm is the informative control.",
    ),
    dict(
        id="IC49",
        title="Drawdown-budget governor (feedback target)",
        category="drawdown-recovery",
        strength_rank=15,
        mechanism=(
            "The vol target is a variance control, not a drawdown control, yet the owner's acceptance "
            "test is stated as a drawdown comparison against buy-and-hold. A governor that scales "
            "exposure so the *forecast* drawdown of the position stays inside a budget makes the "
            "drawdown the objective and volatility the instrument: "
            "sigma_budget_t = 0.35 x clip((0.35 - |NAV_dd_t|)/(0.35 - 0.20), 0.25, 1.0) and "
            "vt = min(1, 0.35/vol20, sigma_budget_t/vol20). It is *inactive* while the NAV drawdown is "
            "under 20% (sigma_budget = 0.35, exactly production) and de-risks progressively as the "
            "budget is consumed, which is a state the fixed vol target cannot see. Unlike IB29/IB30 it "
            "feeds back on the strategy's own NAV, so it is a kernel option under this API."
        ),
        literature=[
            dict(ref="Cloud, The Evolution of Drawdown-Based Risk Management / Grossman-Zhou (drawdown control), 1988",
                 url="https://doi.org/10.1093/rfs/1.4.361"),
            dict(ref="exposure_k_v1 and vol_shape_v1 (the closed vol-target variants this arm is *not*: the target stays 0.35 until the NAV budget is consumed)",
                 url="docs/research/exposure_k_v1.md"),
        ],
        closest_tested=dict(
            study="exposure_k_v1 / vt_level_tax_v1 / vol_shape_v1 (vol-target level and shape changes at matched average weight) and IB29/IB30 (price-based drawdown scaling, in the bank)",
            index_row="docs/research/exposure_k_v1.md",
            difference=(
                "The closed exposure family changes the *constant* (or the shape) of the vol target, and "
                "the banked drawdown ideas scale on the *price* drawdown. This one is a feedback law on "
                "the realised NAV drawdown with a dead zone below 20% - inactive on most sessions by "
                "construction, so it cannot be a disguised lower vol target."
            ),
        ),
        signal_inputs=["the arm's own instrument close and the engine's NAV path"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS,
                           note="the NAV path exists in every window; the pooled OOS bootstrap needs the governor to be reproduced inside the bootstrap chunk (a kernel requirement, not a data one)")),
        plugin=dict(model="kernel_option",
                    detail="the plugin API exposes market data only; the realised NAV drawdown lives in fe_sim's state, so the governor must be a kernel option (all stages). NeedsKernel: NAV-state feedback in the sizing step"),
        arms=[
            dict(id="A", rule="sigma_budget = 0.35 x clip((0.35 - |NAV_dd|)/(0.35 - 0.20), 0.25, 1.0); vt = min(1, 0.35/vol20, sigma_budget/vol20)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="the same with the decline starting at a 15% NAV drawdown and a 0.15 floor",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="the same as A applied only to upsizes (downsizes keep K's band rule)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="medium: it targets the quantity the acceptance test is stated in, with a dead zone that keeps it inactive in normal states; the kernel cost is the price.",
    ),
]
