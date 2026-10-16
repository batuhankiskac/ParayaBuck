"""Idea bank part B -- macro/regime filters (IB12-IB15), calendar/structural effects (IB16-IB19)
and cross-asset signals (IB20-IB23).  Data only; see ideas_a.py's docstring."""

MACRO = [
    {
        "id": "IB12",
        "title": "Real 10-year yield veto (nominal yield minus trailing CPI inflation)",
        "category": "macro-filter",
        "rank": 9,
        "mechanism": (
            "credit_vote_v1 tested *nominal* credit spreads and the nominal 10y-3m curve as risk-off "
            "triggers and lost 2.6-5.2 pp of CAGR, but the variable with a documented link to "
            "long-horizon equity returns is the *real* rate: the equity risk premium falls one-for-"
            "one in the real 10-year yield, and the 2022 de-rating is the clean example.  Arm A vetoes "
            "the on-state while DGS10 minus the trailing 12-month CPI change exceeds 2.0%; arm B "
            "halves the weight above 1.5%; arm C makes it a 4th vote (>= 3 of {SMA200, TSM252, "
            "DON126, real rate <= 2.0%}).  All three keep the trend votes as the primary signal and "
            "use the real rate only as a slow veto/tilt."
        ),
        "literature": [
            {"ref": "Asness, Fight the Fed Model, JPM 2003 (real-rate/valuation link)",
             "url": "https://www.aqr.com/Insights/Research/Journal-Article/Fight-the-Fed-Model"},
            {"ref": "Campbell & Shiller 1998; Fama & French, The Equity Premium, JF 2002",
             "url": "https://doi.org/10.1111/1540-6261.00437"},
        ],
        "closest_tested": {
            "study": "credit_vote_v1 K1 (BAA10Y <= its 200-session SMA) and K3 (curve inverted >= 20 sessions)",
            "index_row": "docs/research/credit_vote_v1.md",
            "difference": (
                "K1/K3 are nominal spreads compared to their own history (a self-referential trigger); "
                "the real rate is a level-vs-inflation quantity with an absolute threshold, and the "
                "scout inventory lists 'real-rate filters' as untested."
            ),
        },
        "signal_inputs": ["FRED DGS10 (daily)", "FRED CPIAUCSL (monthly, 1-month lag)"],
        "data": {
            "in_repo": ["data/historical/leverage_frontier_v1_dgs10.csv (1962-01-02 -> 2026-09-29)",
                        "data/historical/credit_vote_v1_DGS10.csv"],
            "new_downloads": ["FRED CPIAUCSL (monthly SA index, 1947+), saved as "
                              "data/historical/idea_bank_v1_cpi.csv with URL + SHA-256 in the study manifest"],
            "main_window": "full (DGS10 daily from 1962, CPI from 1947, 1-month publication lag honoured)",
            "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2"],
                    "note": ("US CPI and DGS10/MODIS exist over the three US windows; M3-M5 (Japan, "
                             "Germany, UK) have no US macro overlay -> a reduced 3-window G7/G8 is "
                             "pre-registered exactly as credit_vote_v1 did.")},
        },
        "plugin": {"model": "targets_from_data", "detail": "external series aligned by date (the engine's `rates`-style input pattern); no engine change"},
        "arms": [
            {"id": "A", "rule": "weight = 0 while real10y > 2.0%, else production; real10y = DGS10 - CPI YoY (1-month lag)"},
            {"id": "B", "rule": "weight *= 0.5 while real10y > 1.5%"},
            {"id": "C", "rule": "3-of-4 vote {SMA200, TSM252, DON126, real10y <= 2.0%}"},
        ],
        "prior": "medium-low for return, medium for drawdown: the veto family has lost every time, but the real rate is the untested, better-motivated variable.",
    },
    {
        "id": "IB13",
        "title": "10-year yield momentum veto (rising long rates)",
        "category": "macro-filter",
        "rank": None,
        "mechanism": (
            "Duration is the discount rate applied to long-duration equity cash flows, and QLD is a "
            "2x long-duration Nasdaq-100 position -- the most rate-sensitive equity expression "
            "available in the account.  The tested credit arms used *level* triggers; untested is the "
            "60-session *change* in DGS10 as a risk-off trigger: veto while the 10y has risen more "
            "than 0.50 pp over 60 sessions (arm A), halve above 0.25 pp (arm B), or make it a 4th "
            "vote (arm C).  The 2022 and 2018 rate shocks are where this should bind."
        ),
        "literature": [
            {"ref": "Fama & French 2002 (term-structure slope as a return predictor); Estrella & Mishkin 1998",
             "url": "https://doi.org/10.1111/1540-6261.00437"},
        ],
        "closest_tested": {
            "study": "credit_vote_v1 K3 (T10Y3M inverted for >= 20 consecutive sessions) and K1",
            "index_row": "docs/research/credit_vote_v1.md",
            "difference": "K3 keys on the *slope sign* (inversion), not on the speed of the long-rate move; no tested arm uses a yield change.",
        },
        "signal_inputs": ["FRED DGS10 (daily)",
                          "FRED T10Y3M / GS10-TB3MS for the pre-1982 US OOS windows"],
        "data": {"in_repo": ["leverage_frontier_v1_dgs10.csv (1962+)", "credit_vote_v1_T10Y3M.csv (1982+)",
                             "credit_vote_v1_TB3MS.csv (monthly 1934+)", "credit_vote_v1_GS10.csv (monthly 1953+)"],
                 "new_downloads": ["FRED TB3MS daily-ified is not needed: GS10/TB3MS monthly splice as in credit_vote_v1, "
                                   "OR FRED TB3MS monthly (already in repo)"],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2"],
                         "note": "monthly GS10/TB3MS with a 1-month lag for the OOS windows (credit_vote_v1's convention)"}},
        "plugin": {"model": "targets_from_data", "detail": "no engine change"},
        "arms": [
            {"id": "A", "rule": "weight = 0 while DGS10_t - DGS10_{t-60} > +0.50 pp"},
            {"id": "B", "rule": "weight *= 0.5 while the same change > +0.25 pp"},
            {"id": "C", "rule": "3-of-4 vote {SMA200, TSM252, DON126, 60-session DGS10 change <= +0.50 pp}"},
        ],
        "prior": "low-medium.",
    },
    {
        "id": "IB14",
        "title": "Credit-spread momentum (widening BAA-10y), not the spread level",
        "category": "macro-filter",
        "rank": None,
        "mechanism": (
            "The tested credit arm asked whether the BAA-10y spread was above its own 200-session "
            "mean -- a level test that is slow and self-referential.  Spread *momentum* is the "
            "variable credit desks actually trade: veto while the 60-session change exceeds +0.30 pp "
            "(arm A), halve above +0.15 pp (arm B), or a 4th vote (arm C).  Mechanically it fires "
            "earlier and unwinds earlier than the level test, which is where credit_vote_v1's 5 pp "
            "CAGR loss came from."
        ),
        "literature": [
            {"ref": "Gilchrist & Zakrajsek, Credit spreads and business cycle fluctuations, AER 2012",
             "url": "https://doi.org/10.1257/aer.102.4.1692"},
        ],
        "closest_tested": {
            "study": "credit_vote_v1 K1/K2/K4 (BAA10Y vs its 200-session SMA, 4-vote majority, replacement of TSM252)",
            "index_row": "docs/research/credit_vote_v1.md",
            "difference": "level-vs-SMA versus a fixed-horizon change; the BAA-10y series is the same input but the statistic is new.",
        },
        "signal_inputs": ["FRED BAA10Y (daily, 1986+)", "monthly BAA-AAA for the pre-1986 OOS windows"],
        "data": {"in_repo": ["data/historical/credit_vote_v1_BAA10Y.csv", "credit_vote_v1_BAA.csv (monthly 1919+)",
                             "credit_vote_v1_AAA.csv (monthly 1919+)"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2"],
                         "note": "monthly BAA-AAA (1919+) with a 1-month lag, the credit_vote_v1 OOS convention"}},
        "plugin": {"model": "targets_from_data", "detail": "no engine change"},
        "arms": [
            {"id": "A", "rule": "weight = 0 while BAA10Y_t - BAA10Y_{t-60} > +0.30 pp"},
            {"id": "B", "rule": "weight *= 0.5 while the same change > +0.15 pp"},
            {"id": "C", "rule": "3-of-4 vote {SMA200, TSM252, DON126, 60-session spread change <= +0.30 pp}"},
        ],
        "prior": "low-medium.",
    },
    {
        "id": "IB15",
        "title": "Variance-risk-premium proxy (implied minus realised variance)",
        "category": "macro-filter",
        "rank": None,
        "mechanism": (
            "vix_filter_v1 tested the VIX *level*, a VIX spike ratio and the VIX/VIX3M term structure "
            "as cash triggers and lost 0.7-3.2 pp.  Untested is the difference between implied and "
            "realised variance, VRP_t = (VIX_t/100)^2 - vol20^2, which is the priced compensation "
            "for variance risk rather than the level of fear.  Bollerslev-Tauchen-Zhou find the VRP "
            "predicts returns at the quarterly horizon; arms: halve the weight while VRP < 0 (arm A), "
            "a z-score tilt weight *= clip(1 + 0.5*z, 0.7, 1.1) with z on a 252-session window "
            "(arm B), and a 4th vote 'VRP > 0' (arm C)."
        ),
        "literature": [
            {"ref": "Bollerslev, Tauchen & Zhou, Expected stock returns and variance risk premia, RFS 2009",
             "url": "https://doi.org/10.1093/rfs/hhp008"},
        ],
        "closest_tested": {
            "study": "vix_filter_v1 X1 (VIX>30), X2 (VIX/SMA60>1.5), X3 (VIX>VIX3M)",
            "index_row": "docs/research/vix_filter_v1.md",
            "difference": ("all three tested arms use the VIX level or its own term structure; none "
                           "subtracts realised variance, i.e. none is a premium rather than a level."),
        },
        "signal_inputs": ["^VIX close (daily, 1990+)", "the arm's own instrument close (for vol20)"],
        "data": {"in_repo": ["data/historical/vix_filter_v1_vix.csv (1990-01-02 -> 2026-09-29)",
                             "data/historical/vix_filter_v1_fred.csv (VIXCLS)"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999(1990-1999 slice)"],
                         "note": ("^VIX starts 1990-01-02, so only the 1990-1999 part of the "
                                  "GSPC_1957_1999 window is evaluable and M1-M5 are not; exactly "
                                  "reproduce vix_filter_v1's G8 convention or pre-register a single-"
                                  "window gate.")}},
        "plugin": {"model": "targets_from_data", "detail": "no engine change"},
        "arms": [
            {"id": "A", "rule": "weight *= 0.5 while VRP_t = (VIX/100)^2 - vol20^2 < 0"},
            {"id": "B", "rule": "weight *= clip(1 + 0.5*z_t, 0.7, 1.1), z = (VRP - median252(VRP))/std252(VRP)"},
            {"id": "C", "rule": "3-of-4 vote {SMA200, TSM252, DON126, VRP > 0}"},
        ],
        "prior": "low-medium: the closest arm's family lost, and the OOS coverage is one window.",
    },
]

CALENDAR = [
    {
        "id": "IB16",
        "title": "Pre-holiday exposure top-up (session before a NYSE holiday)",
        "category": "calendar",
        "rank": None,
        "mechanism": (
            "The pre-holiday effect (the last session before an exchange holiday earns an abnormal "
            "positive return) is one of the oldest documented calendar anomalies (Ariel 1990, "
            "Lakonishok-Smidt 1988) and is completely untested here -- the inventory confirms "
            "'pre-holiday / holiday effect / day-of-week: NOT tested'.  The arms are deliberately "
            "written two-sided so the screen measures the sign rather than assuming it: A sets the "
            "target to the 0.98 cap on the session before a holiday (only while the trend vote is "
            "on); B adds 25% to the weight; C *cuts* to 75% (the mirror that a data-mining screen "
            "would need to be able to reject).  Holidays are detected from the session index itself "
            "-- a Monday-Friday date on which the market did not trade is a holiday or an "
            "unscheduled closure, and the two are treated identically -- so no new data is needed."
        ),
        "literature": [
            {"ref": "Ariel, High stock returns before holidays, JF 1990",
             "url": "https://doi.org/10.1111/j.1540-6261.1990.tb05116.x"},
            {"ref": "Lakonishok & Smidt, Are seasonal anomalies real? RFS 1988",
             "url": "https://doi.org/10.1093/rfs/1.4.403"},
        ],
        "closest_tested": {
            "study": "seasonal_overlay_v1 S4 (turn-of-month) and seasonal_placebo_v1",
            "index_row": "docs/research/seasonal_overlay_v1.md",
            "difference": ("S4 is a month-boundary effect and was killed by its placebo; the holiday "
                           "calendar is an orthogonal date set, and the scout inventory records "
                           "pre-holiday as untested."),
        },
        "signal_inputs": ["the session calendar (derivable from any of the repo's daily files)"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv (session index)",
                             "ewma_band_v1_index.csv (index session calendars)"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"],
                         "note": ("the holiday set must be the *local* market's calendar for M3-M5; "
                                  "each window's own session index provides it, and US holidays fall "
                                  "out of the ^GSPC/^IXIC calendars automatically.")}},
        "plugin": {"model": "targets_from_data", "detail": "a calendar multiplier on the target series; no engine change"},
        "arms": [
            {"id": "A", "rule": "target = 0.98 (cap) on the last session before a detected holiday, while the trend vote is on"},
            {"id": "B", "rule": "target *= 1.25 (capped at 0.98) on that session"},
            {"id": "C", "rule": "target *= 0.75 on that session (the mirror sign)"},
        ],
        "prior": "low-medium: the effect is small and decayed, but it costs nothing and tests a genuinely orthogonal date set.",
    },
    {
        "id": "IB17",
        "title": "FOMC cycle (the 5-week meeting cycle) exposure tilt",
        "category": "calendar",
        "rank": None,
        "mechanism": (
            "idea_survey_v2 rejected the *pre-FOMC announcement drift* (it disappeared after 2015 and "
            "adds ~8 round trips/yr), and no arm has ever used a policy calendar.  The 'FOMC cycle' "
            "of Cieslak, Morse & Vissing-Jorgensen is a different object: the equity premium "
            "concentrates in even weeks of the five-week cycle anchored on scheduled meetings.  Arms "
            "tilt the target within the cycle: A 1.0x in even weeks / 0.85x in odd weeks; B the same "
            "with 0.70x; C the mirror (0.85x in even weeks) so the screen can reject a sign-free "
            "artefact.  The tilt is a *multiplier on the existing target*, so no new trade type and "
            "no extra round trip is introduced beyond the ordinary resize band."
        ),
        "literature": [
            {"ref": "Cieslak, Morse & Vissing-Jorgensen, Stock returns over the FOMC cycle, JF 2019",
             "url": "https://doi.org/10.1111/jofi.12838"},
            {"ref": "Lucca & Moench, The pre-FOMC announcement drift, JF 2015 (the rejected variant)",
             "url": "https://doi.org/10.1111/jofi.12196"},
        ],
        "closest_tested": {
            "study": "seasonal_overlay_v1 (calendar overlays: May-Oct, September, turn-of-month)",
            "index_row": "docs/research/seasonal_overlay_v1.md",
            "difference": ("the tested overlays are month-of-year effects; the FOMC cycle is a "
                           "fortnightly policy calendar anchored on meeting dates -- not tested "
                           "(the pre-FOMC *day* drift was rejected in a survey, not measured)."),
        },
        "signal_inputs": ["scheduled FOMC meeting dates 1994-2026 (public Fed calendar, hard-coded constant table)"],
        "data": {"in_repo": [], "new_downloads": ["federalreserve.gov published FOMC calendar dates 1994-2026, "
                                                  "stored as data/historical/idea_bank_v1_fomc_dates.csv with source URL + SHA-256 "
                                                  "(not market data; a public meeting calendar)"],
                 "main_window": "reduced: 2000-03-09 -> 2026-09-29 is fully covered (FOMC dates exist from 1994)",
                 "oos": {"evaluable": [],
                         "note": "no OOS window has FOMC dates (1994+) -> G7/G8 are not evaluable; pre-register a reduced gate (main window + 21 ETFs + rolling starts + bootstraps, no OOS clause), disclosed before the run."}},
        "plugin": {"model": "targets_from_data", "detail": "a calendar multiplier; no engine change"},
        "arms": [
            {"id": "A", "rule": "target *= 1.00 when w is even and 0.85 when w is odd, w = floor((session date - the most recent scheduled FOMC meeting date)/7 calendar days) (week 0 = the meeting week)"},
            {"id": "B", "rule": "the same cycle definition with 1.00 / 0.70"},
            {"id": "C", "rule": "the mirror: 0.85 when w is even, 1.00 when w is odd"},
        ],
        "prior": "low: no OOS coverage at all and the nearest tested calendar family was placebo-rejected; included because the mechanism is untested and cheap.",
    },
    {
        "id": "IB18",
        "title": "Options-expiration week tilt",
        "category": "calendar",
        "rank": None,
        "mechanism": (
            "Expiration weeks concentrate hedging and dealer-gamma flow, and the documented effect is "
            "a *reversal* pattern around monthly expiration (Stivers & Sun): the week of the third "
            "Friday has negative and more volatile returns.  Untested here, and it needs no new data "
            "-- the third Friday is computable from the session index.  A: weight * 0.75 in every "
            "expiration week; B: the same only in the quarterly (Mar/Jun/Sep/Dec) expirations, where "
            "the flow is largest; C: the mirror (weight * 0.85 outside expiration weeks, i.e. a "
            "positive tilt on them)."
        ),
        "literature": [
            {"ref": "Stivers & Sun, Returns and option expiration (2013); Ni, Pearson & Poteshman (2005)",
             "url": "https://doi.org/10.1016/j.jbankfin.2012.09.008"},
        ],
        "closest_tested": {
            "study": "seasonal_overlay_v1 / seasonal_placebo_v1 (calendar overlays)",
            "index_row": "docs/research/seasonal_placebo_v1.md",
            "difference": "no tested arm uses an expiration calendar; the scout inventory lists 'quarter-end / options expiry / month-end rebalancing flow: NOT tested'.",
        },
        "signal_inputs": ["the session calendar (third-Friday computation)"],
        "data": {"in_repo": ["session indices in the daily files"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "calendar-only"}},
        "plugin": {"model": "targets_from_data", "detail": "calendar multiplier; no engine change"},
        "arms": [
            {"id": "A", "rule": "target *= 0.75 during the Mon-Fri week containing the third Friday of every month"},
            {"id": "B", "rule": "target *= 0.75 only in the quarterly expiration weeks (Mar/Jun/Sep/Dec)"},
            {"id": "C", "rule": "target *= 0.85 outside expiration weeks (mirror sign)"},
        ],
        "prior": "low.",
    },
    {
        "id": "IB19",
        "title": "US presidential-election cycle tilt",
        "category": "calendar",
        "rank": None,
        "mechanism": (
            "Santa-Clara & Valkanov document that the equity premium is earned in the second half of "
            "the presidential term (years 3-4), a large-sample calendar effect computable for every "
            "window from the year alone.  Like the other calendar arms it is written two-sided: A "
            "1.0x in years 3-4 / 0.85x in years 1-2; B 0.85x in the election year only; C the mirror "
            "(0.85x in years 3-4).  The screen's multiple-testing correction is what should decide "
            "whether a 90-year-old pattern survives on a 26-year sample."
        ),
        "literature": [
            {"ref": "Santa-Clara & Valkanov, The presidential puzzle, JF 2003",
             "url": "https://doi.org/10.1111/1540-6261.00588"},
        ],
        "closest_tested": {
            "study": "seasonal_overlay_v1 / seasonal_placebo_v1",
            "index_row": "docs/research/seasonal_placebo_v1.md",
            "difference": "the tested calendar set is intra-year (months, turn-of-month); no arm uses a multi-year political cycle.",
        },
        "signal_inputs": ["calendar year"],
        "data": {"in_repo": [], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2"], "note": "US windows only; the political cycle is US-specific"}},
        "plugin": {"model": "targets_from_data", "detail": "calendar multiplier; no engine change"},
        "arms": [
            {"id": "A", "rule": "target *= 1.00 in term years 3-4, *= 0.85 in years 1-2"},
            {"id": "B", "rule": "target *= 0.85 in the election year only"},
            {"id": "C", "rule": "target *= 0.85 in years 3-4, 1.00 in years 1-2 (mirror)"},
        ],
        "prior": "low.",
    },
]

CROSS_ASSET = [
    {
        "id": "IB20",
        "title": "Semiconductor relative strength as a cyclical risk-on vote",
        "category": "cross-asset",
        "rank": 8,
        "mechanism": (
            "The tested breadth panel (QQQ/SPY/IWM) is a poor discriminator because SPY and QQQ "
            "agree 91% of the time.  Semiconductors are the economically leading cyclical block "
            "inside the Nasdaq-100 itself: SMH's 126-session return minus QQQ's is a within-technology "
            "cyclical signal rather than a second index.  A: a 4th vote (>= 3 of {SMA200, TSM252, "
            "DON126, semis 126-return > QQQ's}); B: veto the on-state while SMH underperforms QQQ by "
            "more than 5 pp over 126 sessions; C: the early path's entry additionally requires it."
        ),
        "literature": [
            {"ref": "Semiconductor cycle as a global growth bellwether (industry/BIS literature)",
             "url": "https://www.bis.org/publ/qtrpdf/r_qt1809.htm"},
            {"ref": "Asness, Frazzini, Israel, Moskowitz & Pedersen, Size matters if you control your junk (sector momentum)",
             "url": "https://doi.org/10.1093/rfs/hhac008"},
        ],
        "closest_tested": {
            "study": "breadth_vote_v1 B4 (5-vote majority of {QQQ SMA/TSM/DON, SPY TSM, IWM TSM})",
            "index_row": "docs/research/breadth_vote_v1.md",
            "difference": ("B4 adds whole-index votes that agree with QQQ's 91% of the time; the "
                           "scout inventory records 'semis/tech sub-industry signals: NOT tested'. "
                           "The instrument is a sector ETF whose correlation with QQQ is high but "
                           "whose *relative* trend carries the cyclical information."),
        },
        "signal_inputs": ["SMH daily close (new)", "QQQ daily close (in repo)"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv (QQQ)"],
                 "new_downloads": ["Yahoo chart API SMH (2000-05-05 ->), saved as "
                                   "data/historical/idea_bank_v1_smh.csv with URL + SHA-256 in the study manifest"],
                 "main_window": "full window retained: sessions before the signal's first valid date (2000-05-05 + 126) take the 4th vote as *neutral* (2-of-3 behaviour), a convention fixed in advance",
                 "oos": {"evaluable": [],
                         "note": "SMH has no pre-2000 history -> G7/G8 are not evaluable for this arm; pre-register the reduced gate (main window + 21-ETF stage + rolling starts + bootstraps) as credit_vote_v1 did for its non-US windows."}},
        "plugin": {"model": "targets_from_data", "detail": "second series aligned by date; no engine change"},
        "arms": [
            {"id": "A", "rule": "3-of-4 vote {SMA200, TSM252, DON126, r_SMH126 > r_QQQ126}"},
            {"id": "B", "rule": "weight = 0 while (r_SMH126 - r_QQQ126) < -5 pp"},
            {"id": "C", "rule": "early-path entry additionally requires r_SMH126 - r_QQQ126 > 0"},
        ],
        "prior": "medium-low for return; the cleanest untested cyclical signal that needs only one small download.",
    },
    {
        "id": "IB21",
        "title": "Bond-equity momentum spread as a risk-regime modifier",
        "category": "cross-asset",
        "rank": None,
        "mechanism": (
            "riskoff_rotation_v1-v4 tested *holding* the better of bonds/gold while the trend vote is "
            "off (and the whole off-state family is closed).  Untested is using the bond-equity "
            "momentum spread as a *modifier of the on-state*: when intermediate Treasuries lead the "
            "Nasdaq over 252 sessions, the long-duration equity position is being paid less for its "
            "risk, so A halves the weight while IEF's 252-session return exceeds QQQ's, B cuts to "
            "0.75 only when the lead exceeds 10 pp, and C restricts the early path (a source of extra "
            "realisations) to sessions where bonds are not leading."
        ),
        "literature": [
            {"ref": "Asness, Moskowitz & Pedersen, Value and momentum everywhere, JF 2013 (cross-asset momentum)",
             "url": "https://doi.org/10.1111/jofi.12021"},
        ],
        "closest_tested": {
            "study": "riskoff_rotation_v3/v4 (defensive leg chosen monthly by 252-session return) and riskoff_after_tax_v1",
            "index_row": "docs/research/riskoff_rotation_v4.md",
            "difference": ("the tested arms allocate the *off-state* capital; this leaves the "
                           "allocation alone and uses the same spread to size the *on-state*."),
        },
        "signal_inputs": ["IEF daily close (in repo)", "QQQ daily close"],
        "data": {"in_repo": ["data/historical/riskoff_rotation_v4_etfs.csv (IEF 2002-07-30 -> 2026-10-13, adjclose)"],
                 "new_downloads": [],
                 "main_window": "reduced start: 2003-07 (IEF + 252 warm-up); sessions before that take the multiplier as 1.0 (fixed convention)",
                 "oos": {"evaluable": [],
                         "note": "IEF starts 2002-07-30 -> G7/G8 not evaluable; reduced gate pre-registered (main window, 21-ETF stage, bootstraps)."}},
        "plugin": {"model": "targets_from_data", "detail": "second series aligned by date; no engine change"},
        "arms": [
            {"id": "A", "rule": "weight *= 0.5 while r_IEF252 > r_QQQ252"},
            {"id": "B", "rule": "weight *= 0.75 while r_IEF252 - r_QQQ252 > +10 pp"},
            {"id": "C", "rule": "early-path entry permitted only while r_IEF252 - r_QQQ252 <= 0"},
        ],
        "prior": "low-medium.",
    },
    {
        "id": "IB22",
        "title": "International lead-lag: same-session European move as a causal intraday signal",
        "category": "cross-asset",
        "rank": 4,
        "mechanism": (
            "This is the only idea in the bank with a *timing* edge rather than a statistical one: at "
            "the US close (16:00 ET) that decides the trade, the European session of the *same* "
            "calendar day has already closed (^GDAXI/^FTSE close ~11:30 ET) and Japan's session "
            "closed before the US open.  So a same-session EU return is information the decision "
            "maker legally has, not a look-ahead.  A: the early path's entry additionally requires "
            "EU_t = 0.5*(r_GDAXI,t + r_FTSE,t) > 0; B: requires EU_t + r_N225,t-1 > 0; C: cuts the "
            "on-state weight to 0.75 on the session after EU_t < -2% (a 'Europe sold off hard' risk "
            "cut).  If the US-close decision is genuinely informed by the rest of the world's session, "
            "these are the only arms in the bank that can monetise it."
        ),
        "literature": [
            {"ref": "Rapach, Strauss & Zhou, International stock return predictability, JF 2013",
             "url": "https://doi.org/10.1111/jofi.12041"},
            {"ref": "Hou, Global momentum (international lead-lag)", "url": "https://doi.org/10.1016/j.jfineco.2006.07.004"},
        ],
        "closest_tested": {
            "study": "breadth_vote_v1 (same-close cross-index votes) and relstrength_v1 (Q1-Q4)",
            "index_row": "docs/research/breadth_vote_v1.md",
            "difference": ("those arms read another index's *same-close* value (no timing advantage: "
                           "the US and the other index close together) or a monthly rotation; this "
                           "exploits the time-zone ordering, which no arm in the record uses. "
                           "signal_cross_check_v1 is about data-source noise, not lead-lag."),
        },
        "signal_inputs": ["^GDAXI, ^FTSE, ^N225 daily close (all in repo) plus QQQ"],
        "data": {"in_repo": ["data/historical/ewma_band_v1_index.csv (^N225 1965+, ^GDAXI 1987+, ^FTSE 1984+)"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["M3", "M4", "M5"],
                         "note": ("the arm needs non-US data, which exists only from 1984/1987 and not "
                                  "at all in 1935-56; GSPC_1957_1999, M1 and M2 are therefore not "
                                  "evaluable -> pre-register a reduced 3-window G7 (as credit_vote_v1 "
                                  "did) plus the 21-ETF stage.")}},
        "plugin": {"model": "targets_from_data", "detail": "multiple series aligned by date in the target builder; no engine change"},
        "arms": [
            {"id": "A", "rule": "early entry additionally requires 0.5*(r_GDAXI,t + r_FTSE,t) > 0"},
            {"id": "B", "rule": "early entry additionally requires 0.5*(r_GDAXI,t + r_FTSE,t) + r_N225,t-1 > 0"},
            {"id": "C", "rule": "on-state weight *= 0.75 on the session after 0.5*(r_GDAXI + r_FTSE) < -2%"},
        ],
        "prior": "medium: causally clean, uses existing data, no new parameters beyond the thresholds.",
    },
    {
        "id": "IB23",
        "title": "Copper/gold ratio trend (growth-vs-safety) as a regime filter",
        "category": "cross-asset",
        "rank": None,
        "mechanism": (
            "The copper/gold ratio is the practitioner's growth-versus-safety indicator (Gundlach): "
            "copper prices industrial demand, gold prices real-rate fear, and their ratio leads "
            "cyclical equity risk appetite.  Only gold appears anywhere in the record (as an off-state "
            "holding) and no other commodity has ever been used.  A: a 4th vote on the ratio's "
            "252-session return > 0; B: veto the early path while the ratio's 126-session return < 0; "
            "C: weight *= 0.75 while the ratio is below its 252-session median."
        ),
        "literature": [
            {"ref": "Gundlach copper/gold ratio; Bhardwaj & Janardanan (commodity ratio as a growth proxy)",
             "url": "https://doi.org/10.1016/j.jcomm.2021.100216"},
        ],
        "closest_tested": {
            "study": "riskoff_rotation_v1-v4 / gold_riskoff_oos_v1 / leverage_frontier_v1 (gold as a holding)",
            "index_row": "docs/research/gold_riskoff_oos_v1.md",
            "difference": ("every tested use of gold is as a *holding* in the off state; the scout "
                           "inventory records 'commodities other than gold: NOT tested' and the "
                           "ratio-as-signal form is untested for gold too."),
        },
        "signal_inputs": ["HG=F (COMEX copper front-month continuation, new) and GC=F (COMEX gold front-month continuation, in repo) daily closes; both are signal inputs only (a futures continuation is not the instrument traded in any arm)"],
        "data": {"in_repo": ["data/historical/leverage_frontier_v1_daily.csv (GC=F 2000-08-30 -> 2026-09-29)"],
                 "new_downloads": ["Yahoo chart API HG=F (2000 ->); if unavailable, the ETF proxies CPER (2011+)/"
                                   "DBB (2007+) are not long enough, so HG=F is the requirement"],
                 "main_window": "reduced start: ~2001-09 (GC=F 2000-08-30 + 252 warm-up)",
                 "oos": {"evaluable": [],
                         "note": "no pre-2000 futures data in the repo -> reduced gate (main window, 21-ETF stage, bootstraps)."}},
        "plugin": {"model": "targets_from_data", "detail": "two external series; no engine change"},
        "arms": [
            {"id": "A", "rule": "3-of-4 vote {SMA200, TSM252, DON126, r_HG/GC(252) > 0}"},
            {"id": "B", "rule": "early-path entry permitted only while r_HG/GC(126) >= 0"},
            {"id": "C", "rule": "weight *= 0.75 while HG/GC is below its 252-session median"},
        ],
        "prior": "low-medium.",
    },
]
