"""Idea bank part A -- trend-signal algebra (IB01-IB06) and volatility estimators (IB07-IB11).

Data-only module: every idea is a dict; render.py turns the concatenated list into
docs/research/idea_bank_v1.json and docs/research/idea_bank_v1.md.  No simulation, no data read.
"""

TREND = [
    {
        "id": "IB01",
        "title": "Efficiency-ratio (Kaufman) momentum vote",
        "category": "trend-signal",
        "rank": 3,
        "mechanism": (
            "The production momentum vote is a raw 252-session price return whose magnitude is "
            "irrelevant (only its sign counts).  The Kaufman efficiency ratio ER_n = |c_t-c_{t-n}| "
            "/ sum_{i=1..n}|c_{t-i+1}-c_{t-i}| is a scale-free measure of how much of the traversed "
            "path was net progress: ER near 1 in a clean trend, near 0 in chop.  Substituting it for "
            "the TSM252 vote asks the uptrend to be *efficient* rather than merely positive, which "
            "should cut the false re-entries that cost R2 its 2022-12/2023-01 whipsaw and leave the "
            "2001/2009/2023 recoveries (all high-ER) intact."
        ),
        "literature": [
            {"ref": "Kaufman, Trading Systems and Methods (adaptive moving average efficiency ratio)",
             "url": "https://www.wiley.com/en-us/Trading+Systems+and+Methods%2C+6th+Edition-p-9781119605355"},
            {"ref": "Moskowitz, Ooi & Pedersen, Time Series Momentum, JFE 2012 (scale-free trend strength)",
             "url": "https://doi.org/10.1016/j.jfineco.2011.11.003"},
        ],
        "closest_tested": {
            "study": "deriv_vote_v1 (C4 vol-scaled TSM hurdle tsv>0.25) and trend_robustness_v2",
            "index_row": "docs/research/deriv_vote_v1.md",
            "difference": (
                "deriv_vote replaced the vote with price *derivatives* (SMA cross, OLS log-price "
                "slope, rising midpoint) and with a 252-session return divided by annualised vol; "
                "the efficiency ratio is a path-length statistic that no arm in the record computes."
            ),
        },
        "signal_inputs": ["QQQ daily close (the arm's own instrument close in the OOS/ETF stages)"],
        "data": {
            "in_repo": ["data/historical/stress_robustness_v1_daily.csv (QQQ close)"],
            "new_downloads": [],
            "main_window": "full: QQQ 1999-03-10 -> 2026-09-29",
            "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"],
                    "note": "closes only; every OOS index file carries a close column"},
        },
        "plugin": {"model": "targets_from_data",
                   "detail": "wants() replacement in fe_signals.arm_signals; no engine change"},
        "arms": [
            {"id": "A", "rule": "2-of-3 vote with TSM252 replaced by ER_20 > 0.30"},
            {"id": "B", "rule": "2-of-3 vote with TSM252 replaced by ER_60 > 0.30"},
            {"id": "C", "rule": "E2 as production, and the early path's entry additionally requires ER_20 > 0.30"},
        ],
        "prior": "medium: a new statistic on the core vote, no new data, cheap to run.",
    },
    {
        "id": "IB02",
        "title": "Median-location trend vote (robust mean vs mean)",
        "category": "trend-signal",
        "rank": None,
        "mechanism": (
            "Production's level vote is close > SMA200 -- a mean over the trailing window, which a "
            "single crash bar or a spike high biases upward for 200 sessions and which therefore "
            "holds the vote *off* too long after any single extreme print.  The median of the "
            "trailing 200 closes is the same idea with a robust location statistic; the claimed "
            "difference is fewer post-crash false negatives without any lookback change, i.e. it "
            "attacks exactly the lag the early path was invented to patch."
        ),
        "literature": [
            {"ref": "Huber, Robust Statistics (location estimators); any trend-filter survey",
             "url": "https://doi.org/10.1002/0471725250"},
        ],
        "closest_tested": {
            "study": "trend_robustness_v2 (SMA 100/150/200/250 ensemble) and signal_ensemble_v1",
            "index_row": "docs/research/trend_robustness_v2.md",
            "difference": (
                "the closed axis is the SMA *length* and its aggregation; no arm swaps the mean for "
                "a robust location statistic at a fixed length."
            ),
        },
        "signal_inputs": ["QQQ daily close"],
        "data": {
            "in_repo": ["stress_robustness_v1_daily.csv"],
            "new_downloads": [],
            "main_window": "full",
            "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"},
        },
        "plugin": {"model": "targets_from_data", "detail": "sma_up -> rolling median; no engine change"},
        "arms": [
            {"id": "A", "rule": "MED200 (median of the last 200 closes) replaces SMA200 in the 2-of-3 vote"},
            {"id": "B", "rule": "MED100 replaces SMA200"},
            {"id": "C", "rule": "3-of-4 vote {SMA200, MED200, TSM252, DON126}, on iff >= 3"},
        ],
        "prior": "medium-low: tiny change, but the vote is the strategy's core and the change is free.",
    },
    {
        "id": "IB03",
        "title": "ATR/EMA channel (Keltner) vote instead of a Donchian midpoint",
        "category": "trend-signal",
        "rank": None,
        "mechanism": (
            "DON126 asks close > the midpoint of the 126-session max/min -- a *range* channel whose "
            "width is set by the largest historical excursion, so it is insensitive to how volatile "
            "the market currently is.  A Keltner channel (EMA_n +/- k*ATR_n) scales the breakout "
            "threshold with the instrument's own current true range: in a calm market it re-enters "
            "earlier than DON126, in a wild one later, which is the opposite of the fixed-width "
            "behaviour that made R2 sit out 2009-04 and 2020-04."
        ),
        "literature": [
            {"ref": "Keltner channel / ATR (Wilder 1978); Bollinger-band breakout literature",
             "url": "https://en.wikipedia.org/wiki/Keltner_channel"},
        ],
        "closest_tested": {
            "study": "signal_ensemble_v1 / e2_confirmation_v1 (DON126 = production vote)",
            "index_row": "docs/research/signal_ensemble_v1.md",
            "difference": (
                "DON126's threshold is the channel midpoint of *price extrema*; the ATR channel's is "
                "a volatility-scaled distance from an EMA.  The nearest measured neighbour is "
                "fast_reentry_v1 R3 (Donchian-126 AND SMA50), still a price-extrema channel."
            ),
        },
        "signal_inputs": ["QQQ daily high, low, close"],
        "data": {
            "in_repo": ["stress_robustness_v1_daily.csv (QQQ OHLC)",
                        "data/historical/ewma_band_v1_index.csv (^IXIC/^GSPC/^N225/^GDAXI/^FTSE OHLC)"],
            "new_downloads": [],
            "main_window": "full",
            "oos": {"evaluable": ["M1", "M2", "M3", "M4", "M5"],
                    "note": ("high/low exist in ewma_band_v1_index for all five market windows, "
                             "including ^GSPC 1927-1956; the ^GSPC 1957-1999 window is close-only "
                             "(ewma_band_v1_gspc.csv), so a true-range statistic cannot be built "
                             "there -> that one window is dropped and a reduced 5-window G7 is "
                             "pre-registered as credit_vote_v1 did for its 3-window gate.")},
        },
        "plugin": {"model": "targets_from_data", "detail": "don_up -> keltner_up; no engine change"},
        "arms": [
            {"id": "A", "rule": "DON126 replaced by close > EMA20 + 1.0*ATR20 in the 2-of-3 vote"},
            {"id": "B", "rule": "SMA200 replaced by close > EMA50 + 0.5*ATR20 in the 2-of-3 vote"},
            {"id": "C", "rule": "early-path entry gate 'close > SMA50' replaced by 'close > EMA20 + 1.0*ATR20'"},
        ],
        "prior": "medium-low: an established channel family, untested here.",
    },
    {
        "id": "IB04",
        "title": "Variance-ratio (Lo-MacKinlay) trend vote",
        "category": "trend-signal",
        "rank": 5,
        "mechanism": (
            "Instead of asking whether price rose, ask whether *returns are trending*: the variance "
            "ratio VR_q = Var(q-session return)/(q * Var(1-session return)) estimated on the trailing "
            "252 sessions is > 1 exactly when multi-day returns variance-add more than linearly, i.e. "
            "when returns are positively serially dependent.  Using VR_5 > 1 (or VR_20) as the "
            "momentum vote makes the on-state conditional on the *structure* of returns rather than "
            "their level, so a positive but mean-reverting drift does not buy 2x exposure."
        ),
        "literature": [
            {"ref": "Lo & MacKinlay, Stock market prices do not follow random walks, RFS 1988",
             "url": "https://doi.org/10.1093/rfs/1.1.41"},
            {"ref": "Moskowitz, Ooi & Pedersen 2012 (time-series momentum)", "url": "https://doi.org/10.1016/j.jfineco.2011.11.003"},
        ],
        "closest_tested": {
            "study": "deriv_vote_v1 (OLS slope of log close, golden cross, rising midpoint)",
            "index_row": "docs/research/deriv_vote_v1.md",
            "difference": (
                "deriv_vote's statistics are functions of the price path's *slope*; the variance "
                "ratio is a second-moment serial-dependence test with no price-derivative content. "
                "The scout inventory records 'autocorrelation / serial-dependence signals' as "
                "untested anywhere in the record."
            ),
        },
        "signal_inputs": ["QQQ daily close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "new numpy statistic feeding the vote; no engine change"},
        "arms": [
            {"id": "A", "rule": "2-of-3 vote with TSM252 replaced by VR_5(252-session estimate) > 1.0"},
            {"id": "B", "rule": "2-of-3 vote with TSM252 replaced by VR_20 > 1.0"},
            {"id": "C", "rule": "E2 as production AND VR_5 > 1.0"},
        ],
        "prior": "medium: literature-backed, low correlation with the tested price votes.",
    },
    {
        "id": "IB05",
        "title": "Hurst-exponent regime gate on the trend vote",
        "category": "trend-signal",
        "rank": None,
        "mechanism": (
            "The Hurst exponent estimated by rescaled range over the trailing 252 sessions is the "
            "classical persistence statistic: H > 0.5 means increments are persistent (trend "
            "friendly), H < 0.5 mean-reverting.  Gating the E2 vote (or just the early path) on "
            "H > 0.5 tests whether the strategy should be allowed to run at all in mean-reverting "
            "regimes -- the regime in which the 1935-56 and 2000-02 whipsaws live."
        ),
        "literature": [
            {"ref": "Hurst 1951; Peters, Fractal Market Analysis (R/S on equity indices)",
             "url": "https://www.wiley.com/en-us/Fractal+Market+Analysis-p-9780471585244"},
        ],
        "closest_tested": {
            "study": "deriv_vote_v1 / breadth_vote_v1 (regime-conditioned votes)",
            "index_row": "docs/research/deriv_vote_v1.md",
            "difference": "no arm computes a persistence exponent; the closest are price-derivative votes.",
        },
        "signal_inputs": ["QQQ daily close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "rolling R/S estimator added to fe_signals-compatible wants(); no engine change"},
        "arms": [
            {"id": "A", "rule": "E2 on iff the 2-of-3 vote holds AND H(252, block sizes 10/20/40/80) > 0.50"},
            {"id": "B", "rule": "same with the threshold H > 0.55"},
            {"id": "C", "rule": "early-path entry additionally requires H > 0.50"},
        ],
        "prior": "low-medium: heavy estimator, both-sided evidence in the literature.",
    },
    {
        "id": "IB06",
        "title": "Range-expansion / contraction entries and an ATR-chandelier exit",
        "category": "trend-signal",
        "rank": None,
        "mechanism": (
            "True range (max(high-low, |high-prev close|, |low-prev close|)) is the one statistic in "
            "the daily bar that the production rule ignores.  Two untested uses: (a) require an "
            "expansion bar (TR > 1.5*ATR20) on the session that triggers an early-path entry, so the "
            "entry is a breakout day and not a drift up; (b) exit the on-state when close falls more "
            "than 2.5*ATR20 below the highest close of the last 20 sessions (the chandelier exit), "
            "which is a trend-following exit that has no 200-session averaging lag."
        ),
        "literature": [
            {"ref": "Wilder 1978 (ATR); Crabel, Day Trading with Short Term Price Patterns (NR7/range expansion)",
             "url": "https://en.wikipedia.org/wiki/Average_true_range"},
        ],
        "closest_tested": {
            "study": "intraday_stop_v1 (T8/T12 stops) and dip_overlay_v1 (5-day return triggers)",
            "index_row": "docs/research/intraday_stop_v1.md",
            "difference": (
                "the tested exit overlays use *fixed percentage* distances below the prior close; "
                "the chandelier exit is volatility-scaled and anchored on the running high, and no "
                "arm has used true range at all."
            ),
        },
        "signal_inputs": ["QQQ high, low, close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv", "ewma_band_v1_index.csv"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["M1", "M2", "M3", "M4", "M5"],
                         "note": "same reduction as IB03: ^GSPC 1957-1999 is close-only"}},
        "plugin": {"model": "targets_from_data", "detail": "extra gates on the early path / an extra exit condition in the target series; no engine change"},
        "arms": [
            {"id": "A", "rule": "early entry additionally requires TR_t > 1.5*ATR20"},
            {"id": "B", "rule": "early entry additionally requires the 7-session range < 0.5*ATR20 (contraction, the opposite sign)"},
            {"id": "C", "rule": "on-state forced to 0 when close < (highest close of the last 20 sessions) - 2.5*ATR20"},
        ],
        "prior": "low-medium: an exit overlay, the family that has lost every time, but the statistic is new.",
    },
]

VOL = [
    {
        "id": "IB07",
        "title": "Mean-reverting GARCH(1,1) volatility forecast instead of backward-looking vol20",
        "category": "vol-estimator",
        "rank": None,
        "mechanism": (
            "vol20 is the realised standard deviation of the last 20 returns: it is unbiased for the "
            "recent past and blind to the fact that volatility mean-reverts, so it under-sizes "
            "exposure exactly after a spike (when the next 20 sessions are usually calmer) and "
            "over-sizes it after a long calm stretch.  A GARCH(1,1) one-step forecast with fixed "
            "constants, sigma^2_{t+1} = omega + 0.10*r_t^2 + 0.85*sigma^2_t, omega set so the "
            "unconditional annualised volatility is 20%, is the standard term-structure-aware "
            "answer and is the only *forecast* class the record has not used (the TimesFM arm is a "
            "neural forecaster, EWMA is an exponential average with no mean reversion)."
        ),
        "literature": [
            {"ref": "Bollerslev 1986 (GARCH); Andersen, Bollerslev, Diebold & Labys 2003 (realised vol forecastability)",
             "url": "https://doi.org/10.1016/0304-4076(86)90063-1"},
        ],
        "closest_tested": {
            "study": "ewma_band_v1 W1 (RiskMetrics lambda=0.94) and timesfm_v1 V1",
            "index_row": "docs/research/ewma_band_v1.md",
            "difference": (
                "EWMA is a variance recursion with no long-run mean and no separate reaction to the "
                "last squared return; GARCH(1,1) mean-reverts to a fixed unconditional level.  The "
                "vol-*estimator* axis is declared closed by idea_survey_v5 on the strength of "
                "vol_window_v1/ewma_band_v1/range_vol_v1-3; this is a different estimator family and "
                "is filed as an axis-closure *test*, not as a new axis."
            ),
        },
        "signal_inputs": ["the arm's own instrument close (QQQ in the main window)"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "vt_of(vol) -> vt_of(garch_sigma); no engine change"},
        "arms": [
            {"id": "A", "rule": "vt = min(1, 0.35/sigma_ann); sigma_ann = sqrt(252*sigma2), sigma2_{t+1} = omega + 0.10*r_t^2 + 0.85*sigma2_t with omega = 7.9365e-06 (=(0.20^2/252)*0.05), seeded with the sample variance of the first 20 returns"},
            {"id": "B", "rule": "vt = min(1, 0.35/max(vol20, sigma_ann))"},
            {"id": "C", "rule": "vt = min(1, 0.35/(0.5*vol20 + 0.5*sigma_ann))"},
        ],
        "prior": "medium-low: the estimator axis is closed, but this is the untested family inside it.",
    },
    {
        "id": "IB08",
        "title": "Realised-volatility term-structure sizing (short vol relative to long vol)",
        "category": "vol-estimator",
        "rank": None,
        "mechanism": (
            "vix_filter_v1 tested the *implied* term structure (VIX > VIX3M) and lost.  The realised "
            "analogue is untested and has a different information content: sizing on "
            "vol20 * (vol20/vol252)^lambda penalises exposure when short-horizon realised vol is "
            "elevated relative to the one-year level (the realised-curve is inverted), which is the "
            "state the vol-target trims are made in.  It is a *ratio*, not another window, so it is "
            "not a neighbour of the closed vol-window axis."
        ),
        "literature": [
            {"ref": "Harvey et al., The Impact of Volatility Targeting, JPM 2018",
             "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538"},
            {"ref": "Moreira & Muir 2017 (volatility-managed portfolios)", "url": "https://doi.org/10.1111/jofi.12513"},
        ],
        "closest_tested": {
            "study": "vol_window_v1 (V10/V40/V60/VMAX) and vix_filter_v1 X3/X4",
            "index_row": "docs/research/vol_window_v1.md",
            "difference": (
                "every tested arm picks a *level* (one window, or max of two, or an implied-vol "
                "level); none uses the short/long ratio as the sizing input."
            ),
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "vol_n(20) and vol_n(252) both already exist in fe_signals"},
        "arms": [
            {"id": "A", "rule": "vt = min(1, 0.35/(vol20*(vol20/vol252)^0.5))"},
            {"id": "B", "rule": "vt = min(1, 0.35/(vol20*(vol20/vol252)^1.0))"},
            {"id": "C", "rule": "vt = min(1, 0.35/vol20) * clip(vol252/vol20, 0.85, 1.00) (an upside-only term-structure penalty)"},
        ],
        "prior": "medium-low: a new functional form on a closed axis.",
    },
    {
        "id": "IB09",
        "title": "Vol-of-vol sizing (scale down when realised volatility is itself unstable)",
        "category": "vol-estimator",
        "rank": None,
        "mechanism": (
            "Uncertainty about *risk* is a distinct state from the level of risk: a market whose "
            "20-session vol is jumping around (high vol-of-vol) has a less reliable vol estimate, so "
            "a vol-targeting rule should size down.  Concrete arm: vov_t = the standard deviation of "
            "the last 20 daily values of vol20, and a multiplier clip(median_252(vov)/vov_t, floor, "
            "1.0) applied to the production weight.  vix_filter_v1 X4 (VXN-implied sizing) is the "
            "closest tested arm and it *replaced* the vol estimate rather than modulating it."
        ),
        "literature": [
            {"ref": "Baltussen, van Bekkum & van der Grient, Unknown Unknowns, JFQA 2018 (vol-of-vol)",
             "url": "https://doi.org/10.1017/S0022109018000658"},
        ],
        "closest_tested": {
            "study": "vix_filter_v1 X4 (vt(max(vol20, 2*VXN/100)))",
            "index_row": "docs/research/vix_filter_v1.md",
            "difference": (
                "X4 used option-implied vol as an alternative vol level and requires ^VXN (2001+); "
                "idea_survey_v2 section 7.3 rejected the option-implied vol-of-vol class (VVIX/SKEW) "
                "on data grounds (VVIX starts 2007).  This uses the *realised* dispersion of the "
                "realised vol estimator, exists for the full 2000-2026 window and for every pre-2000 "
                "OOS window, and no arm in the record computes it."
            ),
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only (needs 20+252 warm-up beyond the vote warm-up)"}},
        "plugin": {"model": "targets_from_data", "detail": "nested rolling std in numpy; no engine change"},
        "arms": [
            {"id": "A", "rule": "weight *= clip(median_252(vov)/vov_t, 0.60, 1.00); vov_t = std of the last 20 daily vol20 values"},
            {"id": "B", "rule": "the same with floor 0.80"},
            {"id": "C", "rule": "the same multiplier applied only while the vote is on (the early path unchanged)"},
        ],
        "prior": "low-medium: latent-state sizing with a documented literature, but only partial data (^VIX) in the tested neighbours.",
    },
    {
        "id": "IB10",
        "title": "ATR%-based volatility estimate (mean of true ranges, production-feasible)",
        "category": "vol-estimator",
        "rank": None,
        "mechanism": (
            "range_vol_v1-v3 showed that range-based vol estimates (Parkinson/Garman-Klass/"
            "Yang-Zhang) beat close-to-close vol20 in-sample, and then failed the implementability "
            "gate: the passing arm G3x needed a close-to-close/Parkinson ratio expanded from 1999.  "
            "A simple ATR%-based estimate, sigma_ann = 0.63*(ATR20/close)*sqrt(252), needs only 21 "
            "bars, is exactly implementable in production, and is the *mean* of true ranges rather "
            "than a variance of ranges -- the constant 0.63 is the a-priori Parkinson factor, not a "
            "fitted one."
        ),
        "literature": [
            {"ref": "Parkinson 1980; Garman & Klass 1980; Alizadeh, Brandt & Diebold 2002 (range-based vol)",
             "url": "https://doi.org/10.1016/S0304-405X(02)00140-X"},
        ],
        "closest_tested": {
            "study": "range_vol_v3 (F1 rolling 252/504 ratio, F2 plain Parkinson, F3 frozen ratio)",
            "index_row": "docs/research/range_vol_v3.md",
            "difference": (
                "all three tested arms rescale a range-variance estimator by an estimated ratio; the "
                "ATR form has no rescaling constant to estimate and no full-history requirement, "
                "which is precisely the objection that closed that campaign."
            ),
        },
        "signal_inputs": ["the arm's own instrument high, low, close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv", "ewma_band_v1_index.csv"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["M1", "M2", "M3", "M4", "M5"],
                         "note": "^GSPC 1957-1999 is close-only; there TR == |c_t-c_{t-1}| degenerates towards vol20 (disclosed)"}},
        "plugin": {"model": "targets_from_data", "detail": "vt_of(vol20) -> vt_of(atr_sigma); no engine change"},
        "arms": [
            {"id": "A", "rule": "vt = min(1, 0.35/sigma_atr), sigma_atr = 0.63*(ATR20/close)*sqrt(252)"},
            {"id": "B", "rule": "vt = min(1, 0.35/(0.5*sigma_atr + 0.5*vol20))"},
            {"id": "C", "rule": "vt = min(1, 0.35/max(sigma_atr, vol20))"},
        ],
        "prior": "medium-low: a production-feasible form of a family that failed for implementability.",
    },
    {
        "id": "IB11",
        "title": "Downside/upside vol asymmetry as a sizing modifier",
        "category": "vol-estimator",
        "rank": None,
        "mechanism": (
            "vol_window_v1 tested the downside semi-deviation *as the estimator* (V5).  Untested is "
            "the *ratio* of downside to upside semi-deviation: when realised downside vol leads "
            "upside vol (a crash-prone distribution), the same total vol is a worse risk, so the "
            "weight is multiplied by clip(semiUp/semiDown, 0.70, 1.00).  This is a shape-of-the-"
            "distribution signal that keeps vol20 as the level and uses the asymmetry only as a tilt."
        ),
        "literature": [
            {"ref": "Ang & Chen 2002; Bollerslev, Li & Zhao 2020 (good vs bad volatility)",
             "url": "https://doi.org/10.1016/S0304-405X(02)00138-1"},
        ],
        "closest_tested": {
            "study": "vol_window_v1 V5 (SEMI = sqrt(2*252/20*sum(min(r,0)^2)))",
            "index_row": "docs/research/vol_window_v1.md",
            "difference": "V5 replaced the vol level with the downside semi-deviation; this keeps vol20 and adds the asymmetry ratio.",
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "no engine change"},
        "arms": [
            {"id": "A", "rule": "weight *= clip(semiUp60/semiDown60, 0.70, 1.00)"},
            {"id": "B", "rule": "weight *= clip(semiUp252/semiDown252, 0.70, 1.00)"},
            {"id": "C", "rule": "weight *= clip(semiUp60/semiDown60, 0.70, 1.00), applied to the early-path leg only"},
        ],
        "prior": "low-medium.",
    },
]
