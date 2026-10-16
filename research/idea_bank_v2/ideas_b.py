"""idea_bank_v2 -- ideas B: calendar (IC23-IC26), cross-asset (IC27-IC30), off-state assets
(IC31-IC38).  See ideas_a.py for the schema."""

ALL_OOS = ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"]
NO_M2 = ["GSPC_1957_1999", "M1", "M3", "M4", "M5"]
MODERN_ONLY = ["M3", "M4", "M5"]

IDEAS = [
    # ------------------------------------------------------------------ calendar
    dict(
        id="IC23",
        title="Day-of-week (Monday) exposure tilt",
        category="calendar",
        strength_rank=None,
        mechanism=(
            "The Monday effect - negative average returns concentrated in the first session of the "
            "trading week - is one of the oldest documented calendar regularities (French 1980; the "
            "'weekend effect' literature links it to the accumulation of information while the market "
            "is closed). The arm holds 0.75x the production target on Mondays only. The tested calendar "
            "family covers months and turn-of-month (seasonal_overlay_v1, later a placebo); the weekday "
            "is a different, much finer partition of the same exposure decision."
        ),
        literature=[
            dict(ref="French, Stock returns and the weekend effect, Journal of Financial Economics 1980",
                 url="https://doi.org/10.1016/0304-405X(80)90021-5"),
            dict(ref="Cross, The Behavior of Stock Prices on Fridays and Mondays, FAJ 1973",
                 url="https://doi.org/10.2469/faj.v29.6.67"),
        ],
        closest_tested=dict(
            study="seasonal_overlay_v1 / seasonal_placebo_v1 (May-October half exposure, cash in September, turn-of-month)",
            index_row="docs/research/seasonal_overlay_v1.md",
            difference=(
                "seasonal_overlay_v1 partitioned the year into months and the month into a turn-of-month "
                "window; a weekday partition is orthogonal to both. seasonal_placebo_v1's lesson - that "
                "the September effect was one episode - applies with equal force here, and the arm is "
                "therefore a *test* of the family's credibility, not a promotion candidate."
            ),
        ),
        signal_inputs=["the session's weekday (derived from the panel's dates)"],
        data=dict(in_repo=["the panel's own date array"], new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="dates only")),
        plugin=dict(model="targets_from_data", detail="a weekday mask inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="target *= 0.75 on Mondays (production target otherwise)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="target *= 0.90 on Mondays",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="mirror: target *= 0.75 on Fridays",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low: the effect has decayed since the 1980s and the mirror arm is the honest control.",
    ),
    dict(
        id="IC24",
        title="Turn-of-quarter exposure tilt",
        category="calendar",
        strength_rank=None,
        mechanism=(
            "Quarter ends are a heavier structural date than month ends: index rebalancing, quarterly "
            "window dressing and the institutional flow calendar concentrate in the last and first few "
            "sessions of a quarter, and the documented quarter-turn pattern in both equity and bond "
            "markets is separate from the month-turn pattern that was tested. The arm scales exposure "
            "in the three sessions spanning each quarter boundary and in the three sessions ending it, "
            "with both signs specified."
        ),
        literature=[
            dict(ref="Etula, Rinne, Suominen & Vaittinen, Dash for Cash: Monthly Market Impact of Institutional Liquidity Needs, RFS 2020",
                 url="https://doi.org/10.1093/rfs/hhz106"),
            dict(ref="Kandel & Marx, Nasdaq Trading Halts and the Advantages of a Specialist System / quarter-end flow evidence, 1999",
                 url="https://www.tandfonline.com/doi/abs/10.1080/1351847X.2011.579748"),
        ],
        closest_tested=dict(
            study="monthly_defer_v1 (YI-UFE month boundaries) and seasonal_overlay_v1 (turn-of-month)",
            index_row="docs/research/monthly_defer_v1.md",
            difference=(
                "Both tested boundaries are *monthly* (12 dates/yr, and monthly_defer_v1 changed when "
                "downsizes fill, not the target). The turn of the quarter is four dates a year with a "
                "different flow mechanism (quarterly rebalancing and reporting), and this arm changes "
                "the target weight."
            ),
        ),
        signal_inputs=["the session's date"],
        data=dict(in_repo=["the panel's own date array"], new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="dates only")),
        plugin=dict(model="targets_from_data", detail="a quarter-boundary mask inside wants()"),
        arms=[
            dict(id="A", rule="target *= 0.75 in the 3 sessions ending the last session of Feb/May/Aug/Nov",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="target = min(0.98, target * 1.10) in the 3 sessions spanning the quarter turn (last session of the quarter and the two after)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="mirror: target *= 0.75 in the 3 sessions after the quarter turn",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low: a small flow effect, and the tested seasonal family's placebo result is the base rate.",
    ),
    dict(
        id="IC25",
        title="Pre-FOMC announcement drift overlay",
        category="calendar",
        strength_rank=2,
        mechanism=(
            "Lucca & Moench document that the S&P 500 earns about 49 bp in the 24 hours *before* "
            "scheduled FOMC announcements - roughly 80% of the annual equity premium compressed into "
            "eight nights a year - while the announcement session itself carries no premium. The arm "
            "holds the full target (0.98, regardless of the vol target) on the pre-announcement "
            "session, held from the open of that session to the close of the announcement eve, and "
            "production's target otherwise. It is a single-window timing overlay, not a cycle model: "
            "IB17's five-week Cieslak-Morse-Vissing-Jorgensen cycle is in the bank and is a different "
            "empirical claim over different sessions."
        ),
        literature=[
            dict(ref="Lucca & Moench, The Pre-FOMC Announcement Drift, Journal of Finance 2015",
                 url="https://doi.org/10.1111/jofi.12196"),
            dict(ref="Federal Reserve FOMC historical meeting calendars (scheduled dates, 1994-2026)",
                 url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),
        ],
        closest_tested=dict(
            study="seasonal_overlay_v1 (calendar overlays on E2) and exec_delay_v1 (fill-day shifts)",
            index_row="docs/research/seasonal_overlay_v1.md",
            difference=(
                "The tested calendar overlays are month-level (May-Oct, September) or month-turn windows; "
                "this is a one-session window around a scheduled event with a large, replicated "
                "literature effect. IB17 (a 5-week FOMC-cycle tilt) is in the bank, not tested, and it "
                "is a different claim (the cycle, not the pre-announcement 24h)."
            ),
        ),
        signal_inputs=["the scheduled FOMC announcement dates 1994-2026 (public calendar, not market data)"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (dates)"],
            new_downloads=[dict(series="scheduled FOMC meeting/announcement dates, 1994-2026",
                                needed_by=["IC25"], source="federalreserve.gov public calendar (hand-registered, not market data)")],
            main_window="full from 2000 (all announcements covered; 1994-1999 is beyond the main window)",
            oos=dict(evaluable=NO_M2,
                     note="the scheduled FOMC calendar starts 1994, so GSPC_1957_1999 and M1 are live only for 1994-1999 (the pre-1994 portion is neutral); M2 (1935-1956) is un-evaluable -> the reduced 5-window G7 is pre-registered, with the neutral-fill convention stated before the run"),
        ),
        plugin=dict(model="targets_from_data",
                    detail="an event-date mask inside wants(); no engine change (the fill stays next-open as production)"),
        arms=[
            dict(id="A", rule="target = min(0.98, 1.0) on the session before each scheduled announcement; production target otherwise",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="target = min(0.98, 2.0 * production vt) on the pre-announcement session",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="mirror: target = 0.5 * production vt on the announcement session itself",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium-high: the largest documented calendar effect in equities, 8 sessions/yr, and the mirror arm distinguishes the drift from a pure exposure effect.",
    ),
    dict(
        id="IC26",
        title="Seasonal daylight (SAD) exposure tilt",
        category="calendar",
        strength_rank=None,
        mechanism=(
            "Kamstra, Kramer & Levi show that the seasonal variation in the length of the night - a "
            "fall-weighted measure of daylight change - explains a large and replicated part of the "
            "autumn/winter return pattern through risk aversion, and the effect is identifiable "
            "separately from the months because it varies continuously with date and latitude. The arm "
            "scales the target by (1 - c * night_factor), where night_factor is the normalised "
            "night-length change for 40.7N peaking at the autumn equinox. Unlike the tested month "
            "buckets, the variable is continuous and externally parameterised, and the southern-"
            "hemisphere mirror gives a built-in placebo."
        ),
        literature=[
            dict(ref="Kamstra, Kramer & Levi, Winter Blues: A SAD Stock Market Cycle, American Economic Review 2003",
                 url="https://doi.org/10.1257/000282803321455133"),
            dict(ref="Dolvin & Pyles, Seasonal affective disorder and the pricing of IPOs, 2007",
                 url="https://doi.org/10.1007/s11156-007-0024-0"),
        ],
        closest_tested=dict(
            study="seasonal_overlay_v1 / seasonal_placebo_v1 (May-October half exposure, September cash)",
            index_row="docs/research/seasonal_placebo_v1.md",
            difference=(
                "The tested seasonals are month buckets with no external variable; the SAD measure is "
                "continuous in the calendar, has a stated calibration (latitude, night length) and a "
                "falsifiable mirror - the same computation with the sign of the seasonal term inverted."
            ),
        ),
        signal_inputs=["the session's date and latitude 40.7N (computed, no download)"],
        data=dict(in_repo=["the panel's own date array"], new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="dates only; the measure is defined every session")),
        plugin=dict(model="targets_from_data", detail="a computed seasonal factor inside wants()"),
        arms=[
            dict(id="A", rule="target *= (1 - 0.15 * night_factor_t), night_factor in [0,1] peaking at the autumn equinox",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="same with coefficient 0.30",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="mirror sign: target *= (1 + 0.15 * night_factor_t) capped at the production target * 1.0 (an exposure control)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low: the tested seasonal family failed its placebo, so this enters as a test with the same prior.",
    ),

    # ------------------------------------------------------------------ cross-asset
    dict(
        id="IC27",
        title="Credit-equity relative strength (HYG vs QQQ)",
        category="cross-asset",
        strength_rank=None,
        mechanism=(
            "High-yield credit leads equity at turning points: credit investors are first-loss and "
            "better informed about the marginal issuer, and the high-yield market prices default risk "
            "continuously. When the equity vote is on while r_HYG(126) - r_QQQ(126) is negative, the "
            "equity market is priced for growth the credit market does not confirm - the classic "
            "internal divergence before 2007 and 2020. Both inputs are tradable prices, so the signal "
            "is implementable in the same account that trades the strategy."
        ),
        literature=[
            dict(ref="Gilchrist & Zakrajsek, Credit Spreads and Business Cycle Fluctuations, AER 2012",
                 url="https://doi.org/10.1257/aer.102.4.1692"),
            dict(ref="Bhattacharya, Berliner & Rosen, The Lead-Lag Relationship Between Credit and Equity Markets, 2018",
                 url="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3149576"),
        ],
        closest_tested=dict(
            study="breadth_vote_v1 (QQQ/SPY/IWM votes) and riskoff_rotation_v1-v4 (defensive rotation)",
            index_row="docs/research/breadth_vote_v1.md",
            difference=(
                "breadth_vote_v1's panel is other *equity indices* and its vote is each index's own trend; "
                "the closest is IB21 (IEF-vs-QQQ momentum spread) which is in the bank, not tested. Here "
                "the second instrument is a credit vehicle, whose price embeds spread plus rates plus "
                "default experience - a different asset class measuring the same event from the other side."
            ),
        ),
        signal_inputs=["HYG daily total-return close (new download)", "the arm's own instrument close (QQQ / local index)"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ)"],
            new_downloads=[dict(series="HYG (iShares iBoxx $ High Yield Corporate Bond ETF, 2007-04-11 ->)",
                                needed_by=["IC27"], source="Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/HYG)")],
            main_window="partial: HYG starts 2007-04, so the relative-strength state is live only from 2007 (fixed convention: before that the state is treated as neutral and weight is unchanged)",
            oos=dict(evaluable=MODERN_ONLY,
                     note="HYG 2007+ -> only M3/M4/M5 -> 3 applicable windows; reduced 3-window G7 and a holdout_insufficient risk"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('hyg') inside wants()"),
        arms=[
            dict(id="A", rule="weight *= 0.75 while r_HYG(126) - r_QQQ(126) < -3 pp (12-month price returns)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= 0.50 while r_HYG(126) - r_QQQ(126) < -6 pp",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="early-path entry additionally requires r_HYG(126) - r_QQQ(126) >= 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: the mechanism is documented, but the arm is blind for the two worst episodes (2000-02) and the OOS coverage is 3 windows.",
    ),
    dict(
        id="IC28",
        title="Cyclical-vs-defensive sector ratio",
        category="cross-asset",
        strength_rank=None,
        mechanism=(
            "The ratio of a cyclicals basket to a defensives basket (XLY+XLI+XLB vs XLP+XLU+XLV, "
            "equal-weighted, daily) is the market's own risk-appetite thermometer at the sector level: "
            "leadership rotates before the index level does, and the ratio's 126-session return "
            "measures whether money is moving *within* the market toward risk. Because it is a ratio of "
            "two internal baskets, it removes the level of the index and the direction of rates; what "
            "remains is composition."
        ),
        literature=[
            dict(ref="Hong, Torous & Valkanov, Do industries lead stock markets?, JFE 2007",
                 url="https://doi.org/10.1016/j.jfineco.2005.09.010"),
            dict(ref="Stoval, Sector rotation and the business cycle, 2011",
                 url="https://www.cmegroup.com/education/files/sector-rotation-and-the-business-cycle.pdf"),
        ],
        closest_tested=dict(
            study="breadth_vote_v1 (three equity indices) and riskoff_rotation_v1-v4 (sector ETFs as risk-off *holdings*)",
            index_row="docs/research/breadth_vote_v1.md",
            difference=(
                "breadth_vote_v1 counted indices above their own SMAs (a vote, and SPY agreed with QQQ "
                "91% of sessions); riskoff_rotation used sector ETFs as the *asset to hold*. This uses "
                "the cross-sector relative-strength *ratio* as a state with no holding decision, and "
                "IB20 (SMH-vs-QQQ RS) in the bank is a single-pair variant of the same idea."
            ),
        ),
        signal_inputs=["the nine sector SPDR total-return closes (registered extra keys sec_*)"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_xl*.csv (1998-12-22 ->; extra keys sec_xlb..sec_xly)"],
            new_downloads=[],
            main_window="full (the SPDRs start 1998-12-22, before the main window)",
            oos=dict(evaluable=MODERN_ONLY,
                     note="sector SPDRs are absent on GSPC_1957_1999, M1 and M2 -> 3 applicable windows; reduced 3-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('sec_xl*') inside wants()"),
        arms=[
            dict(id="A", rule="3-of-4 vote {SMA200, TSM252, DON126, r_cyclicals/defensives(126) > 0}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= 0.75 while r_cyclicals/defensives(126) < 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="early-path entry additionally requires r_cyclicals/defensives(126) > 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium-low: a genuinely different (composition) input; the sector baskets are highly correlated with the index so the marginal information may be small.",
    ),
    dict(
        id="IC29",
        title="Emerging-market relative strength (EEM vs SPY)",
        category="cross-asset",
        strength_rank=None,
        mechanism=(
            "EM equities outperform the US only when global liquidity is ample and the dollar is "
            "benign, so the EEM-vs-SPY relative-strength state is a direct read of the global "
            "risk-appetite cycle that does not depend on a US-only series; it is the equity-market "
            "twin of IC22's dollar state and, unlike the copper/gold ratio (IB23, in the bank), it is "
            "directly tradable and priced continuously. The arm uses it as a veto and as a fourth vote."
        ),
        literature=[
            dict(ref="Rey, Dilemma not Trilemma: The Global Financial Cycle and Monetary Policy Independence, 2015",
                 url="https://doi.org/10.3386/w21162"),
            dict(ref="Miranda-Agrippino & Rey, US Monetary Policy and the Global Financial Cycle, RFS 2020",
                 url="https://doi.org/10.1093/rfs/hhaa040"),
        ],
        closest_tested=dict(
            study="riskoff_rotation_v1-v4 (country/region ETFs as holdings) and relstrength_v1 (QLD vs SSO rotation)",
            index_row="docs/research/riskoff_rotation_v4.md",
            difference=(
                "relstrength_v1 rotated between two *US* leveraged funds; riskoff_rotation_v4 used "
                "country ETFs as the defensive asset selected by momentum, after tax it was not run. "
                "Using an EM index's relative strength as an on/off *state* is a global-cycle signal, "
                "not a holding choice."
            ),
        ),
        signal_inputs=["EEM and SPY daily total-return closes (EEM is a new download; SPY is in the repo)"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (SPY)"],
            new_downloads=[dict(series="EEM (iShares MSCI Emerging Markets ETF, 2003-04-14 ->)",
                                needed_by=["IC29"], source="Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/EEM)")],
            main_window="full from 2003-04 (neutral before, a fixed convention)",
            oos=dict(evaluable=MODERN_ONLY,
                     note="EEM 2003+ -> only M3/M4/M5 -> 3 applicable windows; reduced 3-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('eem') inside wants()"),
        arms=[
            dict(id="A", rule="3-of-4 vote {SMA200, TSM252, DON126, r_EEM(126) - r_SPY(126) > 0}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= 0.75 while r_EEM(126) - r_SPY(126) < -5 pp",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="early-path entry additionally requires r_EEM(126) - r_SPY(126) > 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: a clean global-cycle input, but the state is dominated by a handful of regimes and coverage is 3 windows.",
    ),
    dict(
        id="IC30",
        title="Stock-bond correlation regime",
        category="cross-asset",
        strength_rank=None,
        mechanism=(
            "The sign of the stock-bond correlation decides whether duration diversifies equity "
            "(negative, 2000-2020) or compounds it (positive, 2022-2023), and the sign flips are "
            "persistent enough to trade. A rolling 60-session correlation of QQQ and IEF returns above "
            "+0.30 marks a regime in which the strategy's risk is undiversifiable inside the portfolio "
            "and the 2x equity position is the whole of the risk budget; the arm scales the target down "
            "there. The stock-bond correlation is a *second-moment cross-statistic* - no tested arm "
            "computes one."
        ),
        literature=[
            dict(ref="Ilmanen, How Do Bonds Perform in Different Macro Environments?, 2011; Campbell, Sunderam & Viceira, Inflation Bets or Deflation Hedges?, 2017",
                 url="https://doi.org/10.3386/w21318"),
            dict(ref="Baele, Bekaert, Inghelbrecht & Wei, Flights to Safety, RFS 2020",
                 url="https://doi.org/10.1093/rfs/hhz055"),
        ],
        closest_tested=dict(
            study="leverage_frontier_v1 (BOND10 as a risk-off asset) and riskoff_rotation_v1-v4 (bond-leg momentum)",
            index_row="docs/research/riskoff_rotation_v1.md",
            difference=(
                "The tested bond legs answer 'should the off-state be bonds' (a holding question, using "
                "bond *momentum*). The correlation regime answers 'is the equity risk diversifiable at "
                "all' - a joint second-moment state, and IB21 (the IEF/QQQ momentum spread) in the bank "
                "is again a first-moment variant."
            ),
        ),
        signal_inputs=["IEF daily total-return close (extra key ief), the arm's own instrument close"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_ief.csv (2002-07-30 ->)"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=MODERN_ONLY,
                     note="IEF 2002+ -> only M3/M4/M5 -> 3 applicable windows; reduced 3-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('ief') inside wants()"),
        arms=[
            dict(id="A", rule="weight *= 0.75 while corr60(QQQ, IEF) > +0.30",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= 0.50 while corr60(QQQ, IEF) > +0.50",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, corr60 <= +0.30}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: the regime is real but rare (2022 and 2004 briefly) and the correlation is estimated from 60 sessions.",
    ),

    # ------------------------------------------------------------------ off-state assets
    dict(
        id="IC31",
        title="Off-state in investment-grade credit (LQD)",
        category="off-state",
        strength_rank=None,
        mechanism=(
            "While the trend vote is off the tested holdings are bills (S1/S2_10, cash_yield), "
            "Treasuries (IEF/BOND10) and gold - each of which pays either a taxable interest flow (the "
            "bill sweep, taxed annually at the progressive rate with no Yi-UFE indexation) or no "
            "income at all. Investment-grade credit (LQD) pays a spread plus a coupon that accrues "
            "*inside the price*: the return is a capital gain in the Turkish system, indexation-eligible "
            "and realised only when the sleeve is sold, which is the exact asymmetry the bill sweep "
            "lacks. The mechanism is the credit risk premium plus the tax character of the return."
        ),
        literature=[
            dict(ref="Asvanunt & Richardson, The Credit Risk Premium, Journal of Fixed Income 2017",
                 url="https://doi.org/10.3905/jfi.2017.27.3.006"),
            dict(ref="r2_tbill_combo_v1 (the tested bill-sleeve precedent this arm differs from)",
                 url="docs/research/r2_tbill_combo_v1.md"),
        ],
        closest_tested=dict(
            study="tbill_sweep_v1 (S1/S2_10 BIL sleeve) and r2_tbill_combo_v1 (the sleeve after tax: it lost)",
            index_row="docs/research/r2_tbill_combo_v1.md",
            difference=(
                "r2_tbill_combo_v1's sleeve (BIL) carries essentially no price risk and its return is "
                "taxed as interest; it lost after tax because the sleeve's gains are realised early. LQD "
                "is a *priced* asset with credit risk and a coupon in the price - different asset, "
                "different tax treatment of the same off-state slot. IB25/IB26 (SHY/IEF off-state) are "
                "in the bank and are Treasury analogues, not credit."
            ),
        ),
        signal_inputs=["LQD daily total-return close (new download)", "the arm's own instrument close (for the vote)"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QLD*/QQQ for the vote)"],
            new_downloads=[dict(series="LQD (iShares iBoxx $ Investment Grade Corporate Bond ETF, 2002-07-30 ->)",
                                needed_by=["IC31"], source="Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/LQD)")],
            main_window="partial: LQD starts 2002-07, so the 2000-2002 bear market - the most important off-state stretch in the window - is not covered by the arm (cash is held there by the fixed convention)",
            oos=dict(evaluable=MODERN_ONLY,
                     note="LQD 2002+ -> only M3/M4/M5 -> 3 applicable windows; reduced 3-window G7"),
        ),
        plugin=dict(model="kernel_option",
                    detail="a priced off-state asset is a second leg (like IB25's SHY sleeve), which the single-instrument account cannot hold; pre-tax/OOS/ETF stages can run as a blended synthetic instrument, the after-tax stage needs the kernel. NeedsKernel: state-dependent leg"),
        arms=[
            dict(id="A", rule="while the vote is off, hold 0.98 in LQD; production target otherwise",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="while the vote is off, hold the residual (0.98 - w) in LQD (paired with production's cash on the rest)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="hold 0.98 LQD while off AND LQD > its own SMA200, else cash",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low-medium: the tax-character argument is the novel part; the missing 2000-02 coverage is a real handicap.",
    ),
    dict(
        id="IC32",
        title="Off-state in high-yield credit (HYG)",
        category="off-state",
        strength_rank=None,
        mechanism=(
            "The same off-state slot at the low-rated end: HYG carries roughly 0.5-0.6 equity beta and "
            "a coupon that dominates its return in recoveries, so holding it while the vote is off "
            "converts the off-state into a partial risk-on position while realising the return as a "
            "*price* gain (indexation-eligible) rather than as interest. The counter-argument is "
            "precisely that it fires after equity has fallen, when credit is at its most fragile; the "
            "arm-B gate (HYG above its own SMA200) and the arm-C gate (the momentum leg still positive) "
            "are the two specified mitigations."
        ),
        literature=[
            dict(ref="Asvanunt & Richardson, The Credit Risk Premium, Journal of Fixed Income 2017",
                 url="https://doi.org/10.3905/jfi.2017.27.3.006"),
            dict(ref="FRED BAMLH0A0HYM2 (the spread the high-yield premium is paid for)",
                 url="https://fred.stlouisfed.org/series/BAMLH0A0HYM2"),
        ],
        closest_tested=dict(
            study="tbill_sweep_v1 / r2_tbill_combo_v1 (bill sleeve, after tax it lost) and leverage_frontier_v1 (GOLD as risk-off, +2.3 pp CAGR but +4.3 pp DD)",
            index_row="docs/research/leverage_frontier_v1.md",
            difference=(
                "Gold was the only tested off-state asset with market risk and it added drawdown; the "
                "tested bill sleeve had none. HYG is a *credit* asset with an explicit premium and a "
                "coupon, and neither gold nor bills share its payoff shape."
            ),
        ),
        signal_inputs=["HYG daily total-return close (new download)", "the arm's own instrument close"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv"],
            new_downloads=[dict(series="HYG (iShares iBoxx $ High Yield Corporate Bond ETF, 2007-04-11 ->)",
                                needed_by=["IC32"], source="Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/HYG)")],
            main_window="partial: HYG starts 2007-04 (cash before, fixed convention)",
            oos=dict(evaluable=MODERN_ONLY, note="2007+ -> M3/M4/M5 only; reduced 3-window G7"),
        ),
        plugin=dict(model="kernel_option",
                    detail="priced off-state asset = a second leg with its own FIFO book; NeedsKernel: state-dependent leg"),
        arms=[
            dict(id="A", rule="while off, hold 0.98 HYG",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="hold 0.98 HYG while off AND HYG > its own SMA200, else cash",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="hold 0.98 HYG while off AND the momentum leg (TSM252 of the arm's own close) > 0, else cash",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low: high-yield beta during the off-state is exactly the state in which credit suffers; the gates are what make it a test rather than a bet.",
    ),
    dict(
        id="IC33",
        title="Off-state in TIPS (TIP)",
        category="off-state",
        strength_rank=None,
        mechanism=(
            "The bill sleeve's interest is taxed annually at the progressive rate with no Yi-UFE "
            "indexation, while an inflation-linked bond fund accumulates the inflation compensation "
            "*into price*, which is indexation-eligible and realised only on sale. For a Turkish "
            "resident whose relief is measured by Yi-UFE, holding inflation-linked paper in the "
            "off-state hedges the tax base itself (the lira's realised inflation) rather than only "
            "choosing a return; the asset also has negative equity beta in disinflationary bear markets "
            "(2008, 2020) and positive beta to exactly the risk that inflates the tax bill."
        ),
        literature=[
            dict(ref="Campbell, Shiller & Viceira, Understanding Inflation-Indexed Bond Markets, Brookings 2009",
                 url="https://doi.org/10.1353/eca.0.0043"),
            dict(ref="after_tax_tr_v1 (the Yi-UFE indexation mechanics this arm exploits)",
                 url="docs/research/after_tax_tr_v1.md"),
        ],
        closest_tested=dict(
            study="IB26 (curve-conditioned IEF off-state, in the bank) and leverage_frontier_v1 (BOND10/gold risk-off)",
            index_row="docs/research/leverage_frontier_v1.md",
            difference=(
                "Every tested off-state asset is nominal (bills, BOND10/IEF, gold). TIPS are the only "
                "instrument whose return *is* the inflation that the Turkish indexation relief measures, "
                "so the choice interacts with the tax base rather than only with the return."
            ),
        ),
        signal_inputs=["TIP daily total-return close (new download)", "the arm's own instrument close"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv"],
            new_downloads=[dict(series="TIP (iShares TIPS Bond ETF, 2003-12-05 ->)",
                                needed_by=["IC33"], source="Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/TIP)")],
            main_window="partial: TIP starts 2003-12 (cash before, fixed convention)",
            oos=dict(evaluable=MODERN_ONLY, note="2003+ -> M3/M4/M5 only; reduced 3-window G7"),
        ),
        plugin=dict(model="kernel_option",
                    detail="priced off-state asset = a second leg; NeedsKernel: state-dependent leg"),
        arms=[
            dict(id="A", rule="while off, hold 0.98 TIP",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="while off, hold the residual (0.98 - w) in TIP",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="hold 0.98 TIP while off AND the 126-session change of the breakeven (T10YIE) > 0, else cash",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low: the tax-character mechanism is the strongest part; TIPS' real return over 2004-2021 was near zero.",
    ),
    dict(
        id="IC34",
        title="Off-state in defensive sectors (XLU / XLP)",
        category="off-state",
        strength_rank=None,
        mechanism=(
            "Utilities and staples are the equity-side substitutes for bonds: regulated or inelastic "
            "cash flows, high payout, low beta, and the dividend accrues in price (indexation-eligible) "
            "instead of as taxable interest. The distinction from riskoff_rotation_v1-v4 - which "
            "*rotated among* sector and country ETFs by trailing return and failed its drawdown gate on "
            "5 of 21 instruments - is that this arm holds one fixed defensive sector with no selection "
            "rule and no rotation turnover: the riskoff_rotation failure was a selection failure."
        ),
        literature=[
            dict(ref="Blitz, Huij & Martens, Residual momentum / defensive equity premium, 2011; Frazzini & Pedersen, Betting Against Beta, JFE 2014",
                 url="https://doi.org/10.1016/j.jfineco.2013.10.005"),
            dict(ref="riskoff_rotation_v4 (the tested sector-rotation precedent)",
                 url="docs/research/riskoff_rotation_v4.md"),
        ],
        closest_tested=dict(
            study="riskoff_rotation_v1-v4 (sector/country ETFs as the defensive holding, selected by momentum)",
            index_row="docs/research/riskoff_rotation_v4.md",
            difference=(
                "The tested family *selects* among defensives by trailing return every month (13.81% vs "
                "14.13% under doubled cost in v2; DD violations in v4). This arm fixes the asset in "
                "advance and holds it whenever the vote is off - no selection, no rotation."
            ),
        ),
        signal_inputs=["XLU / XLP daily total-return closes (registered extra keys sec_xlu, sec_xlp; the 1x ETF is the holding)"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_xlu.csv, _xlp.csv (1998-12-22 ->)"],
            new_downloads=[],
            main_window="full (SPDRs start 1998-12-22)",
            oos=dict(evaluable=MODERN_ONLY, note="SPDRs absent on GSPC_1957_1999/M1/M2 -> 3 applicable windows; reduced 3-window G7"),
        ),
        plugin=dict(model="kernel_option",
                    detail="a priced off-state asset needs a state-dependent leg/second book; NeedsKernel: state-dependent leg"),
        arms=[
            dict(id="A", rule="while off, hold 0.98 XLU",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="while off, hold 0.98 XLP",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="while off, hold 0.49 XLU + 0.49 XLP (a fixed equal split, no selection)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low: defensive equity in a bear market still falls; the tax character is the only edge.",
    ),
    dict(
        id="IC35",
        title="Off-state in gold miners (GDX)",
        category="off-state",
        strength_rank=None,
        mechanism=(
            "Gold itself was tested as the risk-off asset and lost its out-of-sample test "
            "(gold_riskoff_oos_v1). Gold *miners* are a different instrument: operating leverage to the "
            "gold price (beta to bullion around 1.5-2) plus equity-market participation, so holding GDX "
            "while off is a leveraged gold position with an equity kicker in the recovery - a payoff "
            "shape no tested off-state asset has, and one whose return is again a price gain rather "
            "than a taxable coupon."
        ),
        literature=[
            dict(ref="Baur & Lucey, Is Gold a Hedge or a Safe Haven?, Financial Review 2010",
                 url="https://doi.org/10.1111/j.1540-6288.2010.00254.x"),
            dict(ref="gold_riskoff_oos_v1 (the tested bullion risk-off arm and its OOS failure)",
                 url="docs/research/gold_riskoff_oos_v1.md"),
        ],
        closest_tested=dict(
            study="gold_riskoff_oos_v1 (gold as risk-off, failed every OOS gate) and leverage_frontier_v1 (GOLD risk-off, +2.3 pp CAGR / +4.3 pp DD)",
            index_row="docs/research/gold_riskoff_oos_v1.md",
            difference=(
                "Bullion and miners share a driver but not a beta: the tested arms were unconditional "
                "switches into a non-yielding metal; GDX adds equity-market beta and operating leverage, "
                "so its behaviour in a 2008-style crash is materially different (it fell harder and "
                "recovered faster)."
            ),
        ),
        signal_inputs=["GDX daily total-return close (new download)"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv"],
            new_downloads=[dict(series="GDX (VanEck Gold Miners ETF, 2006-05-22 ->)",
                                needed_by=["IC35"], source="Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/GDX)")],
            main_window="partial: GDX starts 2006-05 (cash before, fixed convention)",
            oos=dict(evaluable=MODERN_ONLY, note="2006+ -> M3/M4/M5 only; reduced 3-window G7"),
        ),
        plugin=dict(model="kernel_option",
                    detail="priced off-state asset = a state-dependent leg; NeedsKernel: state-dependent leg"),
        arms=[
            dict(id="A", rule="while off, hold 0.98 GDX",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="while off, hold 0.49 GDX + 0.49 cash (half size)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="hold 0.98 GDX while off AND GDX > its own SMA200, else cash",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low: the closest tested analogue (gold) failed every OOS gate; miners' extra beta cuts both ways.",
    ),
    dict(
        id="IC36",
        title="Off-state risk-parity blend (GLD / IEF / SHY) with a monthly rescale",
        category="off-state",
        strength_rank=14,
        mechanism=(
            "Rather than *choosing* one defensive asset by trailing momentum (riskoff_rotation_v1-v4, "
            "whose drawdown gate failed on 5 of 21 ETFs and which lost after tax) or holding cash, hold "
            "a fixed inverse-volatility blend of the defensive assets already on file: weights "
            "proportional to 1/vol20 of GLD, IEF and SHY, normalised to 0.98, recomputed once a month. "
            "The rule is state-free, so it adds no selection risk; the monthly rescale keeps turnover "
            "at a fraction of the tested rotation's, and the return is the average defensive premium "
            "with the asset-level noise diversified away."
        ),
        literature=[
            dict(ref="Asness, Frazzini & Pedersen, Leverage Aversion and Risk Parity, FAJ 2012",
                 url="https://doi.org/10.2469/faj.v68.n1.1"),
            dict(ref="riskoff_rotation_v1-v4 (the tested momentum-selected alternative)",
                 url="docs/research/riskoff_rotation_v4.md"),
        ],
        closest_tested=dict(
            study="riskoff_rotation_v1-v4 (momentum selection among defensives; failed doubled cost in v2 and the DD gate in v4) and leverage_frontier_v1 (single-asset risk-off choices)",
            index_row="docs/research/riskoff_rotation_v3.md",
            difference=(
                "The tested family picks the best-looking defensive every month (12 selections/yr, "
                "11%+ turnover); this holds *all* of them at inverse-vol weights and touches the "
                "allocation four times a year at most. No tested arm uses inverse-volatility weights."
            ),
        ),
        signal_inputs=["GLD, IEF, SHY daily total-return closes (registered extra keys)"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_gld.csv (2004-11-18 ->), _ief.csv, _shy.csv (2002-07-30 ->)"],
            new_downloads=[],
            main_window="partial: the blend is live from 2004-11 (before that the arm holds cash while off, a fixed convention)",
            oos=dict(evaluable=MODERN_ONLY, note="GLD/IEF/SHY 2002-2004+ -> M3/M4/M5 only; reduced 3-window G7"),
        ),
        plugin=dict(model="kernel_option",
                    detail="a priced off-state blend is a second leg (three instruments, monthly rescale); NeedsKernel: state-dependent multi-leg holding"),
        arms=[
            dict(id="A", rule="while off, hold 0.98 * (1/vol20_i) / sum(1/vol20_j) in each of {GLD, IEF, SHY}, recomputed on the first session of each month",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="the same over {GLD, IEF, SHY, XLU}",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="the same as A but the whole blend scaled by production vt (0.98 -> vt)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="medium: state-free, low turnover, diversified; the defensive premium itself was tested only through selection rules.",
    ),
    dict(
        id="IC37",
        title="Real-yield-gated Treasury off-state (IEF)",
        category="off-state",
        strength_rank=None,
        mechanism=(
            "The tested Treasury off-state (BOND10/IEF, leverage_frontier_v1) is unconditional; the "
            "*reason* to hold duration rather than bills is being paid a positive real yield. Holding "
            "7-10y Treasuries only while the 10-year real yield (DGS10 minus the 10-year breakeven) is "
            "above +0.25% means the off-state asset is compensated; below that (2011-2021) the sleeve "
            "is bills. The conditioning variable is the *level of the real yield* - an asset-valuation "
            "condition - not the curve slope that IB26 (in the bank) uses, a macro-cycle condition."
        ),
        literature=[
            dict(ref="Campbell, Sunderam & Viceira, Inflation Bets or Deflation Hedges? The Changing Risks of Nominal Bonds, 2017",
                 url="https://doi.org/10.3386/w21318"),
            dict(ref="leverage_frontier_v1 (the tested unconditional BOND10 risk-off sleeve)",
                 url="docs/research/leverage_frontier_v1.md"),
        ],
        closest_tested=dict(
            study="leverage_frontier_v1 (BOND10 risk-off, failed the bootstrap tail gate) and r2_tbill_combo_v1 (the bill sleeve after tax)",
            index_row="docs/research/leverage_frontier_v1.md",
            difference=(
                "The tested sleeves are unconditional; the only conditional Treasury arm in the record is "
                "in the *idea bank* (IB26, curve-conditioned) and has never been screened. This "
                "conditions on the real yield level, a different state variable."
            ),
        ),
        signal_inputs=["IEF (extra key ief), DGS10 (extra key dgs10), T10YIE (extra key t10yie)"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_ief.csv, _dgs10.csv, _t10yie.csv"],
            new_downloads=[],
            main_window="partial: live from 2003-01 (breakeven start); cash while off before that",
            oos=dict(evaluable=MODERN_ONLY, note="2003+ -> M3/M4/M5; reduced 3-window G7"),
        ),
        plugin=dict(model="kernel_option",
                    detail="priced off-state asset = a state-dependent leg; NeedsKernel: state-dependent leg"),
        arms=[
            dict(id="A", rule="while off, hold 0.98 IEF if real10y = DGS10 - T10YIE > +0.25%, else cash",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="the same with the threshold at +1.00%",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="while off, hold the residual (0.98 - w) in IEF under the +0.25% condition",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low-medium: the condition is well motivated, but the unconditional version already failed the tail gate.",
    ),
    dict(
        id="IC38",
        title="Off-state in 1x QQQ (never fully out)",
        category="off-state",
        strength_rank=None,
        mechanism=(
            "The off-state's cost is the entire equity risk premium of the flat stretches; a 1x index "
            "position keeps the price exposure that is the strategy's own tax base (a capital gain, "
            "indexation-eligible) instead of a taxable interest flow, and it removes the need for the "
            "E2/off distinction to be sharp. It is the unconditional limit of the early path: hold a "
            "vol-targeted 1x index position whenever the vote is off, rather than the conditional half "
            "weight R2 holds. early_qqq_v1 tested moving the *early leg* from half-QLD to QQQ and lost - "
            "this holds an *additional* 1x position in the states where the early leg does not fire at all."
        ),
        literature=[
            dict(ref="Frazzini & Pedersen, Betting Against Beta (the cost of being flat), JFE 2014",
                 url="https://doi.org/10.1016/j.jfineco.2013.10.005"),
            dict(ref="early_qqq_v1 (moving the early leg to QQQ: -0.11/-0.05/-0.01 pp after tax)",
                 url="docs/research/early_qqq_v1.md"),
        ],
        closest_tested=dict(
            study="early_qqq_v1 (the early leg in QQQ/SPY/SSO) and fast_reentry_v1/v2 (R2's conditional half-weight early path)",
            index_row="docs/research/early_qqq_v1.md",
            difference=(
                "Both tested arms keep the early *leg* conditional (three AND-gates) and at half weight. "
                "This holds an unconditional 1x position whenever the vote is off, including the deep-bear "
                "states where the early gates fail - the states where R2 is fully flat."
            ),
        ),
        signal_inputs=["the arm's own instrument close; the local index as the 1x leg"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ)",
                     "data/historical/ewma_band_v1_index.csv (OOS local indices)"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=ALL_OOS,
                     note="the 1x leg is the window's own index (fe_signals.synthetic_2x at gearing 1 / the local index), so all six windows are evaluable - the best OOS coverage of the six off-state ideas"),
        ),
        plugin=dict(model="kernel_option",
                    detail="on-state 2x and off-state 1x is a per-session leg switch; NeedsKernel: state-dependent leg (if the two-leg kernel's second leg can be sized to zero, it is expressible as two_leg with second_symbol QQQ)"),
        arms=[
            dict(id="A", rule="while off, hold 0.98 in the 1x index (unlevered); production target otherwise",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="B", rule="while off, hold 0.49 in the 1x index",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
            dict(id="C", rule="while off, hold min(1, 0.35/vol20_of_the_1x_leg) in the 1x index (a vol-targeted off-state)",
                 targets_from_data=False, needs_kernel_option=True, kernel_scope="all_stages", params_frozen=True),
        ],
        expected_evidence="low: it is extra exposure in the worst states (the failure mode exposure_k_v1 and leverage_frontier_v1 kept recording), but the tax character and the 6-window OOS coverage make it worth one screen.",
    ),
]
