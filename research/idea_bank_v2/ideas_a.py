"""idea_bank_v2 -- ideas A: trend-signal (IC01-IC07), vol/risk estimators (IC08-IC11),
macro-regime (IC12-IC22).

No simulation lives here: every entry is a fixed specification (mechanism, literature, the closest
tested study, exact arms with frozen parameters, the data each arm needs and whether the plugin
model can express it).  `render.py` turns A+B+C+D into `docs/research/idea_bank_v2.md` and
`docs/research/idea_bank_v2.json`.
"""

ALL_OOS = ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"]
NO_M2 = ["GSPC_1957_1999", "M1", "M3", "M4", "M5"]
MODERN_ONLY = ["M3", "M4", "M5"]

IDEAS = [
    # ------------------------------------------------------------------ trend-signal
    dict(
        id="IC01",
        title="52-week-high proximity vote",
        category="trend-signal",
        strength_rank=1,
        mechanism=(
            "Momentum is strongest when price sits near its 52-week high: the nearness-to-the-high "
            "statistic close/max(close,252) predicts returns better than the trailing return itself, "
            "because investors anchor on the reference point of the running maximum and are slow to "
            "bid price through it (George & Hwang). Production's momentum vote is a 252-session "
            "return whose magnitude is discarded, so it reads ON during a dead-cat bounce that "
            "recovers half of a crash (2001-10, 2008-12) and OFF during a shallow dip in a strong "
            "uptrend. Trading the return for the distance to the high keeps the recoveries that "
            "reclaim the high quickly and rejects the ones that stall below it, attacking at the "
            "vote level the whipsaw the early path was invented to patch."
        ),
        literature=[
            dict(ref="George & Hwang, The 52-Week High and Momentum Investing, Journal of Finance 2004",
                 url="https://doi.org/10.1111/j.1540-6261.2004.00695.x"),
            dict(ref="Moskowitz, Ooi & Pedersen, Time Series Momentum, JFE 2012",
                 url="https://doi.org/10.1016/j.jfineco.2011.11.003"),
        ],
        closest_tested=dict(
            study="deriv_vote_v1 (golden cross, OLS log-price slope, rising midpoint, vol-scaled TSM hurdle)",
            index_row="docs/research/deriv_vote_v1.md",
            difference=(
                "deriv_vote replaced the vote with price *derivatives* and with a 252-session return "
                "divided by realised vol; trend_robustness_v2 swept SMA lengths 100-250 and their "
                "ensemble. No tested arm uses the running maximum as the reference, i.e. a statistic "
                "whose level is the high itself rather than an average or a slope."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close (QQQ on the main window, the local index in the OOS windows)"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ close)",
                     "data/historical/ewma_band_v1_index.csv (^IXIC/^N225/^GDAXI/^FTSE close)"],
            new_downloads=[],
            main_window="full: QQQ 1999-03-10 -> 2026-09-29 (252-session warm-up consumed inside the window)",
            oos=dict(evaluable=ALL_OOS, note="closes only; every OOS index file carries a close column"),
        ),
        plugin=dict(model="targets_from_data",
                    detail="rolling-max of the close inside fe_signals.arm_signals' wants(); no engine change"),
        arms=[
            dict(id="A", rule="2-of-3 vote with TSM252 replaced by close/max(close over the last 252 sessions) > 0.90",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="2-of-3 vote with TSM252 replaced by close/max(close,252) > 0.95",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, close/max(close,252) > 0.90}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium-high: one new statistic on the core vote, no new data, the strongest single documented momentum predictor.",
    ),
    dict(
        id="IC02",
        title="Skip-month (12-1) momentum vote",
        category="trend-signal",
        strength_rank=4,
        mechanism=(
            "The standard momentum convention skips the most recent month because short-horizon "
            "returns reverse (Jegadeesh & Titman). Production's TSM252 runs to the last close, so a "
            "sharp 20-session drop inside an otherwise positive year leaves the momentum vote ON "
            "while the recent information is negative, and a 20-session spike inside a negative year "
            "leaves it OFF. Voting on r(t-252 -> t-21) > 0 removes the reversal-contaminated month "
            "and makes the vote a pure 11-month trend statement; the same horizon, a different "
            "estimator."
        ),
        literature=[
            dict(ref="Jegadeesh & Titman, Returns to Buying Winners and Selling Losers, Journal of Finance 1993",
                 url="https://doi.org/10.1111/j.1540-6261.1993.tb04702.x"),
            dict(ref="Asness, Moskowitz & Pedersen, Value and Momentum Everywhere, Journal of Finance 2013",
                 url="https://doi.org/10.1111/jofi.12021"),
        ],
        closest_tested=dict(
            study="signal_ensemble_v1 / e2_confirmation_v1 (the 252-session TSM vote inside E2)",
            index_row="docs/research/signal_ensemble_v1.md",
            difference=(
                "E2's momentum leg is the 252-session return to the decision close; deriv_vote_v1's C4 "
                "divides that return by vol. The 12-1 convention changes *which* sessions the return "
                "spans (excluding the last 20), a specification the record never varies."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ close)",
                     "data/historical/ewma_band_v1_index.csv"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=ALL_OOS, note="closes only; the 21-session offset needs no extra data"),
        ),
        plugin=dict(model="targets_from_data", detail="index-shifted return inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="2-of-3 vote with TSM252 replaced by r(t-252 -> t-21) > 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="2-of-3 vote with TSM252 replaced by r(t-126 -> t-21) > 0 (a 6-1 momentum leg)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="E2 as production and the early path's entry additionally requires r(t-252 -> t-21) > 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium: free, literature-standard, and it only changes which sessions the vote spans.",
    ),
    dict(
        id="IC03",
        title="CUSUM (change-point) filter vote",
        category="trend-signal",
        strength_rank=None,
        mechanism=(
            "Replace the fixed-window level test with an event detector: accumulate the daily return "
            "normalised by a trailing volatility estimate into a symmetric CUSUM and switch the vote "
            "to ON when the sum crosses +h and OFF when it crosses -h (Page's filter; the sampling "
            "scheme in Lopez de Prado's CUSUM filter). Sensitivity is then measured in units of "
            "current volatility rather than in moving-average lag, and the state resets at each "
            "crossing, so exposure follows regimes instead of levels; a 3-sigma threshold is the "
            "classical control-chart calibration."
        ),
        literature=[
            dict(ref="Page, Continuous Inspection Schemes, Biometrika 1954",
                 url="https://doi.org/10.1093/biomet/41.1-2.100"),
            dict(ref="Lopez de Prado, Advances in Financial Machine Learning (the CUSUM filter), 2018",
                 url="https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086"),
        ],
        closest_tested=dict(
            study="deriv_vote_v1 (rising midpoint, golden cross) and vote_hysteresis_v1 (asymmetric entry/exit thresholds)",
            index_row="docs/research/deriv_vote_v1.md",
            difference=(
                "vote_hysteresis_v1 changed the *counts* of the 2-of-3 vote that trigger entry/exit and "
                "added confirmation days; deriv_vote used window differences. A vol-normalised "
                "cumulative-sum crossing has no window and no vote count - it is a control-chart "
                "change-point detector."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/ewma_band_v1_index.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="closes only")),
        plugin=dict(model="targets_from_data", detail="recursive CUSUM inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="the momentum vote replaced by CUSUM(h = 3.0 * vol20_daily, reset at each crossing) > 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="same with h = 2.0 * vol20_daily",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, CUSUM(h = 3.0) > 0}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium-low: an established event filter, no tuned parameter beyond h, but it replaces the core vote.",
    ),
    dict(
        id="IC04",
        title="Kalman local-linear-trend slope vote",
        category="trend-signal",
        strength_rank=None,
        mechanism=(
            "A state-space local-linear-trend filter (level and slope as latent states, fixed "
            "signal-to-noise ratio q) produces a recursively smoothed slope whose estimation "
            "uncertainty is itself an output, so the vote can be a significance statement - "
            "slope/sqrt(P_slope) > z - rather than a sign. Unlike an OLS slope over a fixed window the "
            "gain adapts: the filter learns faster after a break and slower in noise, which is exactly "
            "the behaviour the fixed 200-session SMA lacks."
        ),
        literature=[
            dict(ref="Harvey, Forecasting, Structural Time Series Models and the Kalman Filter, 1989",
                 url="https://doi.org/10.1017/CBO9780511599286"),
            dict(ref="Durbin & Koopman, Time Series Analysis by State Space Methods, 2nd ed., 2012",
                 url="https://doi.org/10.1093/acprof:oso/9780199641178.001.0001"),
        ],
        closest_tested=dict(
            study="deriv_vote_v1 (OLS slope of log close, golden cross) and timesfm_v1 (neural forecast)",
            index_row="docs/research/timesfm_v1.md",
            difference=(
                "The tested slope is a fixed-window regression (or a 50/200 SMA cross); the Kalman "
                "slope is recursive with an adaptive gain and carries its own variance, so the arm can "
                "gate on significance instead of sign. timesfm_v1 tested a *learned* sequence model - "
                "this is a two-state linear filter with no estimated parameters."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/ewma_band_v1_index.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="closes only")),
        plugin=dict(model="targets_from_data", detail="a numpy recursion inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="2-of-3 vote with TSM252 replaced by Kalman slope (q = 1e-5, r = 1) > 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="same with the significance test slope/sqrt(P_slope) > 1.0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="E2 as production and the early path's entry additionally requires Kalman slope > 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: the family is untested here, the parameter set is fixed and tiny, but the gain may just re-express vol20.",
    ),
    dict(
        id="IC05",
        title="Return-entropy regime gate",
        category="trend-signal",
        strength_rank=None,
        mechanism=(
            "Approximate/sample entropy of the last 126 daily returns measures how repetitive the "
            "return sequence is: low entropy means the process has structure a trend rule can "
            "exploit, high entropy means noise in which any rule whipsaws. Gating the vote on entropy "
            "below its own trailing median is self-calibrating (no absolute threshold), so the arm "
            "says only 'trade when the market is currently more structured than it usually is', an "
            "orthogonal conditioning variable to every tested price statistic."
        ),
        literature=[
            dict(ref="Pincus, Approximate entropy as a measure of system complexity, PNAS 1991",
                 url="https://doi.org/10.1073/pnas.88.6.2297"),
            dict(ref="Maasoumi & Racine, Entropy and predictability of stock market returns, Journal of Econometrics 2002",
                 url="https://doi.org/10.1016/S0304-4076(02)00100-1"),
        ],
        closest_tested=dict(
            study="deriv_vote_v1 (regime-conditioned votes) and breadth_vote_v1",
            index_row="docs/research/deriv_vote_v1.md",
            difference=(
                "Every tested regime gate is built from prices (slopes, vote counts, spreads); entropy "
                "is computed on the return *sequence* and is invariant to the level and to scale - it "
                "cannot be rewritten as a price statistic."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/ewma_band_v1_index.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="closes only; the estimator needs 126+ sessions of warm-up")),
        plugin=dict(model="targets_from_data", detail="a rolling entropy estimator inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="E2 on iff the 2-of-3 vote holds AND ApEn(126, m=2) <= its trailing 252-session median",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="same with the threshold at the trailing 252-session 40th percentile",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="early-path entry additionally requires ApEn(126, m=2) <= its trailing 252-session median",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low: a heavy estimator with two-sided literature evidence; cheap enough to screen.",
    ),
    dict(
        id="IC06",
        title="Overnight-drift trend vote",
        category="trend-signal",
        strength_rank=None,
        mechanism=(
            "The equity premium accrues overnight: on this very data QQQ earns about +9.8%/yr from "
            "close to open and about -2.2%/yr from open to close, and the strategy fills at the open "
            "and holds through the night (moc_execution_v1 measured the decomposition). A vote built "
            "on the cumulative *overnight* return therefore measures the component of the drift the "
            "position actually earns; the total-return vote is half intraday noise, which is one "
            "source of the false re-entries the early path patches."
        ),
        literature=[
            dict(ref="Lou, Polk & Skouras, A Tug of War: Overnight versus Intraday Expected Returns, JFE 2019",
                 url="https://doi.org/10.1016/j.jfineco.2019.03.011"),
            dict(ref="Lachance, The Night Trading Session and the Equity Premium (overnight drift), 2023",
                 url="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622"),
        ],
        closest_tested=dict(
            study="moc_execution_v1 (measured the overnight/intraday split) and overnight_hold_v1 (held overnight only)",
            index_row="docs/research/moc_execution_v1.md",
            difference=(
                "moc_execution_v1 *measured* the second-moment split and concluded that same-close fills "
                "lose; overnight_hold_v1 traded only overnight (335 trades/yr). Neither ever used the "
                "overnight sub-return as a *signal*: the vote on the cumulative overnight return is new, "
                "and it keeps production's next-open execution."
            ),
        ),
        signal_inputs=["the arm's own instrument daily open and close"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ OHLC)",
                     "data/historical/ewma_band_v1_index.csv (^IXIC/^N225/^GDAXI/^FTSE OHLC)"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=NO_M2,
                     note="opens are needed; ^GSPC 1957-1999 is loaded close-only by fe_data.load_gspc (open := previous close), so the overnight return is identically zero there and that window is dropped; M2 carries OHLC but is 1935-56 -> also dropped for the reduced 5-window G7 (4 live windows + M1)"),
        ),
        plugin=dict(model="targets_from_data", detail="open/close decomposition inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="2-of-3 vote with TSM252 replaced by the 252-session cumulative close->open log return > 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="as A AND the 252-session cumulative open->close log return <= 0 (both conditions)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="early-path entry additionally requires the 20-session cumulative close->open log return > 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: the decomposition is real and large, but a 252-session overnight sum may be noisier than the total return it replaces.",
    ),
    dict(
        id="IC07",
        title="Swing-structure (higher-high / higher-low) vote",
        category="trend-signal",
        strength_rank=None,
        mechanism=(
            "Define 26-session swing pivots (a pivot high is a session whose high exceeds the highs of "
            "the surrounding 13 sessions on each side; confirmed with the 13-session delay, so causal) "
            "and declare the trend up when the last two confirmed pivots are a higher high AND a higher "
            "low - the Dow-theory structural test. Reference levels are actual prior extremes rather "
            "than a window average or a channel midpoint, and the test is a discrete two-condition "
            "statement, so it is neither a return nor a level-vs-average rule."
        ),
        literature=[
            dict(ref="Dow theory / Edwards & Magee, Technical Analysis of Stock Trends (swing structure)",
                 url="https://doi.org/10.1201/9781420063905"),
            dict(ref="Lo, Mamaysky & Wang, Foundations of Technical Analysis, Journal of Finance 2000 (pattern recognition on prices)",
                 url="https://doi.org/10.1111/0022-1082.00265"),
        ],
        closest_tested=dict(
            study="signal_ensemble_v1 (Donchian-126 midpoint) and trend_robustness_v2",
            index_row="docs/research/signal_ensemble_v1.md",
            difference=(
                "DON126 is a continuous close-vs-(max+min)/2 test with one comparison; the swing test "
                "is a discrete sequence of two confirmed extrema. fast_reentry_v1's R3 (Donchian-126 AND "
                "SMA50) is still a price-extrema *channel*, not a pivot sequence."
            ),
        ),
        signal_inputs=["the arm's own instrument daily high, low and close"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ OHLC)",
                     "data/historical/ewma_band_v1_index.csv (OHLC for M1-M5)"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=NO_M2,
                     note="highs/lows are needed; the ^GSPC 1957-1999 loader is close-only (opens := previous close), so that window is dropped and the reduced 5-window G7 is pre-registered as credit_vote_v1 did"),
        ),
        plugin=dict(model="targets_from_data", detail="pivot detection inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="2-of-3 vote with TSM252 replaced by swing structure up (last two confirmed 26-session pivots: HH and HL)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="2-of-3 vote with SMA200 replaced by the same swing-structure test",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="early-path entry additionally requires swing structure up",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: the family is untested, the confirmation delay makes it slow by construction.",
    ),

    # ------------------------------------------------------------------ vol / risk estimators
    dict(
        id="IC08",
        title="HAR volatility blend instead of vol20",
        category="vol-estimator",
        strength_rank=6,
        mechanism=(
            "HAR-RV (Corsi) forecasts realised volatility by blending daily, weekly and monthly "
            "components with fixed weights. With weights frozen at 0.40*vol5 + 0.35*vol20 + 0.25*vol60 "
            "the estimate reacts faster than vol20 after a spike - so the strategy leaves the vol floor "
            "sooner and holds fewer sessions de-levered - and it is smoother in calm markets, so fewer "
            "sessions cross the 0.5 pp resize band. Both effects act on the trim channel, which carries "
            "about 91% of the realised taxable gain; that is why an estimator change can matter after "
            "tax even when the pre-tax difference is a few basis points."
        ),
        literature=[
            dict(ref="Corsi, A Simple Approximate Long-Memory Model of Realized Volatility, Journal of Financial Econometrics 2009",
                 url="https://doi.org/10.1093/jjfinec/nbp001"),
            dict(ref="Harvey et al., The Impact of Volatility Targeting, Journal of Portfolio Management 2018",
                 url="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538"),
        ],
        closest_tested=dict(
            study="ewma_band_v1 W1 (RiskMetrics lambda 0.94) and vol_window_v1 (vol10/40/60, max(vol20,vol60))",
            index_row="docs/research/vol_window_v1.md",
            difference=(
                "Every tested estimator is a *single* recursion or a single window (or a max of two). "
                "HAR is an additive blend of three horizons with declared fixed weights, i.e. a level "
                "combination whose reaction speed is independent of the longest window. IB07 (GARCH) is "
                "the same axis-closure test in a different family and is in the bank, not tested."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close (the traded 2x leg on the main window)"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/ewma_band_v1_index.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="closes only; vol252 needs a 252-session warm-up")),
        plugin=dict(model="targets_from_data", detail="vt_of(vol) -> vt_of(sigma_har) in wants(); no engine change"),
        arms=[
            dict(id="A", rule="vt = min(1, 0.35/sigma_har), sigma_har = 0.40*vol5 + 0.35*vol20 + 0.25*vol60 (all annualised, of the traded leg)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="vt = min(1, 0.35/sigma_har2), sigma_har2 = 0.50*vol20 + 0.30*vol60 + 0.20*vol252",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="vt = min(1, 0.35/max(vol20, sigma_har))",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium: the estimator axis is formally closed, but the HAR blend is the one canonical family inside it that was never run, and it acts on the trim channel.",
    ),
    dict(
        id="IC09",
        title="Cross-sectional correlation sizing",
        category="vol-estimator",
        strength_rank=13,
        mechanism=(
            "The mean pairwise correlation of the nine sector ETFs rises in stress (correlation risk; "
            "Pollet & Wilson): when everything moves together, the diversification inside the market "
            "has gone and the index's own volatility understates portfolio risk. Scaling the target by "
            "clip(corr_t / median252(corr), 0.7, 1.0) de-risks exactly when internal correlation breaks "
            "out, and it is a cross-sectional second-moment statistic, so it is not a member of the "
            "tested single-series volatility family."
        ),
        literature=[
            dict(ref="Pollet & Wilson, Average correlation and stock market returns, JFE 2010",
                 url="https://doi.org/10.1016/j.jfineco.2010.02.004"),
            dict(ref="Ang & Chen, Asymmetric correlations of equity portfolios, JFE 2002",
                 url="https://doi.org/10.1016/S0304-405X(02)00068-5"),
        ],
        closest_tested=dict(
            study="vol_window_v1 and range_vol_v1-v3 (own-series volatility estimators); breadth_vote_v1 (a panel vote)",
            index_row="docs/research/range_vol_v3.md",
            difference=(
                "breadth_vote_v1 turned a panel into *votes* (count of indices above their SMA) and lost; "
                "range_vol tested OHLC estimators of the arm's own series. Nothing uses the panel's "
                "*correlation* as the sizing input."
            ),
        ),
        signal_inputs=["the nine sector SPDR daily closes (registered extra keys sec_xlb..sec_xly)",
                       "the arm's own instrument close"],
        data=dict(
            in_repo=["research/batch_screen/EXTRA_DATA.json (sec_xlb/xle/xlf/xli/xlk/xlp/xlu/xlv/xly, 1998-12-22 ->)",
                     "data/historical/batch_screen_v1_xl*.csv"],
            new_downloads=[],
            main_window="full (the sector ETFs start 1998-12-22, before the 2000-03-09 main window)",
            oos=dict(evaluable=MODERN_ONLY,
                     note="the sector ETFs exist only on the modern spans of M3/M4/M5; GSPC_1957_1999, M1 and M2 are un-evaluable -> 3 applicable windows, so the arm risks holdout_insufficient and the reduced 3-window G7 must be pre-registered (credit_vote_v1 precedent)"),
        ),
        plugin=dict(model="targets_from_data",
                    detail="data.extra('sec_xl*') panel correlation inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="weight *= clip(corr_t/median252(corr), 0.70, 1.00), corr = 20-session mean of the nine SPDRs' average pairwise 60-session return correlation",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= clip(1 - 0.5*(corr_t/median252(corr) - 1), 0.70, 1.00), same corr",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="the same multiplier as A applied only while the vote is on (early path unchanged)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium: an orthogonal risk input with a clear mechanism; the 3-window OOS coverage is the structural weakness.",
    ),
    dict(
        id="IC10",
        title="Day/night volatility decomposition sizing",
        category="vol-estimator",
        strength_rank=None,
        mechanism=(
            "The strategy fills at the open and holds through the night, so the risk it actually bears "
            "is the close-to-open variance: an overnight-only volatility estimate is the matched risk "
            "model, and the intraday variance should be down-weighted (the position's intraday "
            "exposure is a single open print, not the full open-to-close path). The overnight share of "
            "daily variance varies enough across regimes (2018-2020) that this is a different risk "
            "model, not another window length."
        ),
        literature=[
            dict(ref="Lou, Polk & Skouras, A Tug of War: Overnight versus Intraday Expected Returns, JFE 2019",
                 url="https://doi.org/10.1016/j.jfineco.2019.03.011"),
            dict(ref="Andersen, Bollerslev & Cai, Intraday and interday volatility in the Japanese market, 2000",
                 url="https://doi.org/10.1016/S0927-5398(99)00014-3"),
        ],
        closest_tested=dict(
            study="vol_window_v1 and ewma_band_v1 (alternative estimators of the close-to-close series)",
            index_row="docs/research/vol_window_v1.md",
            difference=(
                "All tested estimators are computed on close-to-close returns. A decomposition into "
                "close->open and open->close components is a different statistic about the same series, "
                "and it matches the fill convention (decide at the close, fill at the next open)."
            ),
        ),
        signal_inputs=["the arm's own instrument daily open and close"],
        data=dict(
            in_repo=["data/historical/stress_robustness_v1_daily.csv (QQQ OHLC)",
                     "data/historical/ewma_band_v1_index.csv (OHLC)"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=NO_M2,
                     note="^GSPC 1957-1999 is loaded close-only (open := previous close), so the decomposition is degenerate there and the window is dropped; reduced 5-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="open/close variance decomposition inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="vt = min(1, 0.35/sigma_night20), sigma_night20 = annualised stdev of log(open_t/close_{t-1}) over 20 sessions",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="vt = min(1, 0.35/sigma_mix), sigma_mix = sqrt(sigma_night20^2 + 0.5*sigma_day20^2)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="vt = min(1, 0.35/max(vol20, 1.20*sigma_night20))",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: an economically matched risk model, but overnight variance is a noisier estimate than close-to-close.",
    ),
    dict(
        id="IC11",
        title="Tail-percentile (CVaR) risk sizing",
        category="vol-estimator",
        strength_rank=None,
        mechanism=(
            "The vol target is a second-moment statistic, but equities have a fat left tail: two periods "
            "with equal variance can have very different worst days. Sizing on the mean of the worst 5% "
            "of the last 252 daily returns (a 95% CVaR), rescaled to be Gaussian-comparable "
            "(x 2.063 x sqrt(252)), makes the target respond to how bad the bad days are rather than to "
            "total variance. This is a quantile-tail statistic, which no tested estimator computes."
        ),
        literature=[
            dict(ref="Rockafellar & Uryasev, Optimization of Conditional Value-at-Risk, Journal of Risk 2000",
                 url="https://doi.org/10.21314/JOR.2000.038"),
            dict(ref="Barndorff-Nielsen & Shephard, Power and Bipower Variation with Stochastic Volatility and Jumps, Journal of Financial Econometrics 2004",
                 url="https://doi.org/10.1093/jjfinec/nbh001"),
        ],
        closest_tested=dict(
            study="vol_window_v1 (downside semi-deviation) and range_vol_v1-v3",
            index_row="docs/research/range_vol_v1.md",
            difference=(
                "vol_window_v1 tested a downside *second moment* (semi-deviation); a CVaR is a "
                "quantile-average of the tail and reacts to the shape of the loss distribution, not "
                "only to its variance below the mean."
            ),
        ),
        signal_inputs=["the arm's own instrument daily close"],
        data=dict(in_repo=["data/historical/stress_robustness_v1_daily.csv",
                           "data/historical/ewma_band_v1_index.csv"],
                  new_downloads=[], main_window="full",
                  oos=dict(evaluable=ALL_OOS, note="closes only; 252-session warm-up")),
        plugin=dict(model="targets_from_data", detail="rolling tail-mean estimator inside wants(); no engine change"),
        arms=[
            dict(id="A", rule="vt = min(1, 0.35/sigma_cvar), sigma_cvar = 2.063*sqrt(252)*|mean of the worst 5% of the last 252 daily log returns|",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="vt = min(1, 0.35/(0.5*sigma_cvar + 0.5*vol20))",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="vt = min(1, 0.35/max(sigma_cvar, vol20))",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: a new estimator with a matching Gaussian calibration; the tail mean is a high-variance estimate over 252 sessions.",
    ),

    # ------------------------------------------------------------------ macro-regime
    dict(
        id="IC12",
        title="Financial-conditions (NFCI) veto",
        category="macro-regime",
        strength_rank=7,
        mechanism=(
            "The Chicago Fed National Financial Conditions Index aggregates 105 weekly activity and "
            "financial indicators into one z-score (positive = tighter than average). It is the "
            "broadest published measure of the conditions that precede equity drawdowns, and unlike a "
            "single market spread it is not the same variable as the price it is meant to forecast. "
            "The arm vetoes the on-state while NFCI is above its own 3-year median AND rising over the "
            "last four weeks - the level-with-momentum state of 2000-02, 2007-08 and 2022 - so a "
            "high-but-improving reading does not block the recovery."
        ),
        literature=[
            dict(ref="Brave & Butters, Monitoring Financial Stability: A Financial Conditions Index Approach, Economic Perspectives 2011",
                 url="https://www.chicagofed.org/publications/economic-perspectives/2011/1q-brave-butters"),
            dict(ref="Gilchrist & Zakrajsek, Credit Spreads and Business Cycle Fluctuations, AER 2012",
                 url="https://doi.org/10.1257/aer.102.4.1692"),
        ],
        closest_tested=dict(
            study="credit_vote_v1 (BAA10Y level vs its SMA and yield-curve votes/vetoes)",
            index_row="docs/research/credit_vote_v1.md",
            difference=(
                "credit_vote_v1 tested one market spread and the curve at their *levels*; NFCI is a "
                "105-indicator composite and the arm conditions on the *combination* of a relative "
                "level and a 4-week change, which no tested macro veto uses."
            ),
        ),
        signal_inputs=["FRED NFCI (weekly, ffill to sessions, released with a 1-week lag)"],
        data=dict(
            in_repo=[],
            new_downloads=[dict(series="NFCI (weekly financial conditions index, 1971-01-08 ->)",
                                needed_by=["IC12"], source="FRED CSV (fredgraph.csv?id=NFCI)")],
            main_window="full (NFCI starts 1971)",
            oos=dict(evaluable=NO_M2,
                     note="weekly series with a one-week lag; M2 (1935-1956) is un-evaluable -> reduced 5-window G7"),
        ),
        plugin=dict(model="targets_from_data",
                    detail="data.extra('nfci', ffill=True) inside wants(); the key must be registered in EXTRA_DATA.json"),
        arms=[
            dict(id="A", rule="weight = 0 while NFCI(1-week lag) > its trailing 156-week median AND NFCI_t > NFCI_{t-4 weeks}",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="the same state scales weight by 0.5 instead of 0",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, NFCI <= its trailing 156-week median}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium: the broadest available financial-conditions state with a documented publication lag; the veto family has lost before, but always as a single market spread.",
    ),
    dict(
        id="IC13",
        title="Real-activity regime (CFNAI) vote",
        category="macro-regime",
        strength_rank=None,
        mechanism=(
            "The Chicago Fed National Activity Index is a monthly 85-indicator real-activity composite "
            "whose 3-month moving average has a published interpretation: below -0.70 the economy is "
            "historically in recession. Using that threshold as a veto is a regime model with an "
            "*externally published* parameter rather than a fitted one, and it conditions exposure on "
            "the state of the real economy, a different information set from the market prices every "
            "tested filter uses. (ISM's own PMI has no continuous free FRED series past 2016, so CFNAI "
            "is the honest vehicle for an activity release.)"
        ),
        literature=[
            dict(ref="Chicago Fed National Activity Index (CFNAI) background and the -0.70 recession threshold",
                 url="https://www.chicagofed.org/research/data/cfnai/current-data"),
            dict(ref="Estrella & Mishkin, Predicting U.S. Recessions (published-threshold regime rules), 1998",
                 url="https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr31.pdf"),
        ],
        closest_tested=dict(
            study="credit_vote_v1 (macro votes/vetoes built on market spreads and the curve)",
            index_row="docs/research/credit_vote_v1.md",
            difference=(
                "credit_vote_v1's inputs are all traded prices; CFNAI is a real-activity release "
                "combining production, employment, consumption and housing, with a published threshold "
                "and a 1-month publication lag - a macro-release regime, not a market state."
            ),
        ),
        signal_inputs=["FRED CFNAI (monthly, 1-month publication lag, ffill to sessions)"],
        data=dict(
            in_repo=[],
            new_downloads=[dict(series="CFNAI (monthly, 1967-01 ->)", needed_by=["IC13"], source="FRED CSV (fredgraph.csv?id=CFNAI)")],
            main_window="full",
            oos=dict(evaluable=NO_M2, note="monthly series, 1-month lag; M2 un-evaluable -> reduced 5-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('cfnai', ffill=True) inside wants()"),
        arms=[
            dict(id="A", rule="weight = 0 while CFNAI_3mma (1-month lag) < -0.70",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= 0.5 while CFNAI_3mma (1-month lag) < -0.70",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, CFNAI_3mma >= -0.70}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium-low: a published recession threshold, but activity releases are slow and the 2001 and 2020 recessions were short.",
    ),
    dict(
        id="IC14",
        title="Initial-claims shock veto",
        category="macro-regime",
        strength_rank=None,
        mechanism=(
            "Weekly initial jobless claims are the highest-frequency labour release and the labour "
            "market turns before the equity cycle's worst stretches. A shock defined relative to the "
            "series' own recent low (4-week average more than 10% above its 26-week minimum) is "
            "scale-free, needs no fitting, and has the correct sign convention: it fires when firing "
            "*accelerates*, not when claims are merely high (the 2020 spike and the 2009-10 plateau "
            "both stay below their own new lows afterwards)."
        ),
        literature=[
            dict(ref="DOL ETA weekly initial claims (release definition and timing)",
                 url="https://oui.doleta.gov/unemploy/claims.asp"),
            dict(ref="Ludvigson & Ng, Macro Factors in Bond Risk Premia (weekly labour indicators as leading states), 2009",
                 url="https://doi.org/10.1093/rfs/hhp001"),
        ],
        closest_tested=dict(
            study="credit_vote_v1 (market-spread macro states)",
            index_row="docs/research/credit_vote_v1.md",
            difference=(
                "No tested macro arm touches a labour-market release; the closest is valuation_v1's "
                "monthly Shiller data, which is a valuation level rather than an activity shock."
            ),
        ),
        signal_inputs=["FRED ICSA (weekly initial claims, 1-week lag, ffill)"],
        data=dict(
            in_repo=[],
            new_downloads=[dict(series="ICSA (weekly initial claims, 1967-01-07 ->)", needed_by=["IC14"], source="FRED CSV (fredgraph.csv?id=ICSA)")],
            main_window="full",
            oos=dict(evaluable=NO_M2, note="weekly, 1-week lag; M2 un-evaluable -> reduced 5-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('icsa', ffill=True) inside wants()"),
        arms=[
            dict(id="A", rule="weight *= 0.5 while ICSA_4w_mean / min(ICSA_4w_mean over 26 weeks) - 1 > 0.10",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight = 0 while the same ratio > 0.20",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, the ICSA ratio <= 0.10}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium-low: orthogonal and cheap, but claims are noisy and seasonally adjusted revisions are large.",
    ),
    dict(
        id="IC15",
        title="CPI-acceleration veto",
        category="macro-regime",
        strength_rank=None,
        mechanism=(
            "Real yields (IB12, in the bank) look benign when inflation is falling; the state that "
            "forces policy tightening and de-rates long-duration equity is inflation *accelerating*. "
            "The arm vetoes while 6-month annualised CPI inflation exceeds the 12-month rate by more "
            "than 0.5 pp - a momentum statement about the release with no fitted level, so the "
            "2013-2019 low-inflation regime and the 2023 disinflation both leave the strategy alone."
        ),
        literature=[
            dict(ref="FRED CPIAUCSL (CPI, all urban consumers, seasonally adjusted, 1947-01 ->)",
                 url="https://fred.stlouisfed.org/series/CPIAUCSL"),
            dict(ref="Bansal & Yaron, Risks for the Long Run (inflation and long-duration equity), 2004",
                 url="https://doi.org/10.1111/j.1540-6261.2004.00665.x"),
        ],
        closest_tested=dict(
            study="valuation_v1 (CAPE / earnings-yield level) and IB12/IB13 (real-yield level, nominal-10y momentum — in the bank)",
            index_row="docs/research/valuation_v1.md",
            difference=(
                "valuation_v1 conditioned on a *valuation level*; every tested rates arm conditions on a "
                "yield level or a yield change. This conditions on the release's own acceleration, the "
                "variable the reaction function responds to."
            ),
        ),
        signal_inputs=["FRED CPIAUCSL (monthly index, 1-month publication lag) - already registered as extra key cpi"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_cpiaucsl.csv (1947-01-01 -> 2026-09-01, registered as extra key cpi)"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=ALL_OOS, note="monthly 1947+ with a 1-month lag covers all six windows, including M2 (1935-1956) from 1947"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('cpi', ffill=True) inside wants()"),
        arms=[
            dict(id="A", rule="weight *= 0.5 while annualised 6-month CPI inflation > trailing 12-month CPI inflation + 0.5 pp",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight = 0 in the same state",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, CPI acceleration <= +0.5 pp}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: the mechanism is well motivated but the state is rare (2021-22 and 1972-74) and slow.",
    ),
    dict(
        id="IC16",
        title="Fed-funds tightening-cycle veto",
        category="macro-regime",
        strength_rank=None,
        mechanism=(
            "The level of the policy rate is not the risk; the *speed and direction* of policy is. A "
            "mechanical veto while the effective fed funds rate has risen at least 75 bp over the last "
            "126 sessions is the de-rating state (1994, 1999-2000, 2004-06, 2015-18, 2022) and it never "
            "fires in the zero-rate and easing regimes that contain the best recovery returns. The "
            "series is an administered rate, not a market price, so it carries no information already "
            "in the price the strategy trades."
        ),
        literature=[
            dict(ref="FRED DFF (effective federal funds rate, daily, 1954-07-01 ->)",
                 url="https://fred.stlouisfed.org/series/DFF"),
            dict(ref="Bernanke & Kuttner, What Explains the Stock Market's Reaction to Federal Reserve Policy?, JF 2005",
                 url="https://doi.org/10.1111/j.1540-6261.2005.00760.x"),
        ],
        closest_tested=dict(
            study="credit_vote_v1 (the yield curve and credit spreads) and vix_filter_v1",
            index_row="docs/research/credit_vote_v1.md",
            difference=(
                "The curve and the spreads are market prices that partly anticipate policy; DFF is the "
                "policy instrument itself, and the condition is a cumulative 6-month change rather than "
                "a level or a slope."
            ),
        ),
        signal_inputs=["FRED DFF (daily effective fed funds rate)"],
        data=dict(
            in_repo=[],
            new_downloads=[dict(series="DFF (daily effective federal funds rate, 1954-07-01 ->)", needed_by=["IC16"], source="FRED CSV (fredgraph.csv?id=DFF)")],
            main_window="full",
            oos=dict(evaluable=ALL_OOS, note="DFF starts 1954, so all six windows (incl. M2 1935-1956 from 1954) are evaluable"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('dff', ffill=True) inside wants()"),
        arms=[
            dict(id="A", rule="weight = 0 while DFF_t - DFF_{t-126} >= +0.75 pp",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= 0.5 while DFF_t - DFF_{t-126} >= +0.50 pp",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, DFF 126-session change <= +0.75 pp}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: cheap and orthogonal, but 2022 shows an equity market that kept falling into easing and rallying into hikes.",
    ),
    dict(
        id="IC17",
        title="High-yield credit-spread (OAS) veto",
        category="macro-regime",
        strength_rank=3,
        mechanism=(
            "The ICE BofA US high-yield OAS is the market's price of default risk and the canonical "
            "early-warning series: it widened before equity volatility in 2000, 2007, 2011, 2015, 2018, "
            "2020 and 2022. Unlike the investment-grade BAA-10y spread tested by credit_vote_v1, the "
            "high-yield market is where credit-supply shocks and the excess bond premium sit "
            "(Gilchrist & Zakrajsek build their measure from the same universe). The veto is a *state*, "
            "not a level: spread above 1.20x its own 252-session median AND still widening over 20 "
            "sessions, so a high but improving spread does not block a recovery entry."
        ),
        literature=[
            dict(ref="Gilchrist & Zakrajsek, Credit Spreads and Business Cycle Fluctuations, AER 2012",
                 url="https://doi.org/10.1257/aer.102.4.1692"),
            dict(ref="FRED BAMLH0A0HYM2 (ICE BofA US High Yield Index Option-Adjusted Spread)",
                 url="https://fred.stlouisfed.org/series/BAMLH0A0HYM2"),
        ],
        closest_tested=dict(
            study="credit_vote_v1 (BAA10Y relative to its SMA, plus curve votes/vetoes)",
            index_row="docs/research/credit_vote_v1.md",
            difference=(
                "credit_vote_v1 used the investment-grade risk spread (BAA minus the 10-year yield) at "
                "a *level* comparison; this uses the high-yield spread - a different segment whose "
                "information content is concentrated in default-risk pricing - and conditions on a "
                "magnitude state plus its own 20-session momentum."
            ),
        ),
        signal_inputs=["FRED BAMLH0A0HYM2 (daily, 1996-12-31 ->)"],
        data=dict(
            in_repo=[],
            new_downloads=[dict(series="BAMLH0A0HYM2 (US high-yield OAS, daily, 1996-12-31 ->)", needed_by=["IC17"], source="FRED CSV (fredgraph.csv?id=BAMLH0A0HYM2)")],
            main_window="full (1996-12-31 is before the main window)",
            oos=dict(evaluable=NO_M2, note="1996-12-31 starts inside GSPC_1957_1999 and M1 (live 1997-1999) and before M3/M4/M5; M2 un-evaluable -> reduced 5-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('hyoas', ffill=True) inside wants()"),
        arms=[
            dict(id="A", rule="weight = 0 while HYOAS_t > 1.20 * median(HYOAS, 252 sessions) AND HYOAS_t > HYOAS_{t-20}",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= 0.5 in the same state",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, HYOAS <= 1.20x its 252-session median}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium: the best-documented early-warning series with a two-condition state; credit_vote_v1's failure was on an investment-grade level, not this state.",
    ),
    dict(
        id="IC18",
        title="Credit-quality-spread (Baa-Aaa) veto",
        category="macro-regime",
        strength_rank=None,
        mechanism=(
            "The quality spread (Moody's Baa minus Aaa) isolates the marginal borrower's extra default "
            "risk from the level of rates, so it is a purer aversion-to-credit state than BAA-10y and "
            "than the high-yield OAS in one respect: it has genuine pre-1950 history, which makes it the "
            "only credit series that can be tested on the 1927-1956 OOS windows. A veto while the "
            "spread exceeds 1.30x its median since 1929 is a slow, expanding-window state with no "
            "parameter tied to the modern sample."
        ),
        literature=[
            dict(ref="FRED BAA and AAA (Moody's seasoned corporate bond yields, monthly, 1919-01 ->)",
                 url="https://fred.stlouisfed.org/series/BAA"),
            dict(ref="Bernanke, Gertler & Gilchrist, The financial accelerator in a quantitative business cycle framework, 1999",
                 url="https://doi.org/10.1016/S1574-0048(99)10034-X"),
        ],
        closest_tested=dict(
            study="credit_vote_v1 (BAA10Y) and gold_riskoff_oos_v1 / e2_confirmation_v1 (the pre-2000 OOS stage)",
            index_row="docs/research/e2_confirmation_v1.md",
            difference=(
                "Same issuer universe as credit_vote_v1's BAA series but differenced against Aaa rather "
                "than against the 10-year yield, and - decisively for the holdout stage - it is the one "
                "credit state old enough to be evaluated in the 1927-1956 windows, which no tested "
                "credit arm could be."
            ),
        ),
        signal_inputs=["FRED BAA and AAA (monthly, 1-month lag)"],
        data=dict(
            in_repo=[],
            new_downloads=[dict(series="BAA and AAA (monthly Moody's yields, 1919-01 ->)",
                                needed_by=["IC18"], source="FRED CSV (fredgraph.csv?id=BAA, fredgraph.csv?id=AAA)")],
            main_window="full",
            oos=dict(evaluable=ALL_OOS,
                     note="monthly 1919+ covers all six windows including M2 (1935-1956); this is the bank's best OOS coverage for a credit state"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('baa'), data.extra('aaa') inside wants()"),
        arms=[
            dict(id="A", rule="weight = 0 while (BAA - AAA)_t (1-month lag) > 1.30 * its expanding median since 1929",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight *= 0.5 in the same state",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, quality spread <= 1.30x its expanding median}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="medium-low: the pre-1950 coverage makes it a rare macro idea that the holdout stage can actually confirm.",
    ),
    dict(
        id="IC19",
        title="Oil-shock veto",
        category="macro-regime",
        strength_rank=None,
        mechanism=(
            "An oil-price spike is the archetypal supply shock: it raises headline inflation, compresses "
            "margins and precedes equity drawdowns (1973, 1979, 1990, 2008, 2022). Copper/gold (IB23, in "
            "the bank) measures *demand*; oil measures supply, so the two are complements rather than "
            "variants. The two-condition form - WTI up more than 50% over 126 sessions AND above its "
            "252-session median - avoids the 2009 and 2020 rebounds, where oil rose sharply off a crash "
            "bottom while the median was still far above."
        ),
        literature=[
            dict(ref="Hamilton, Oil and the Macroeconomy since World War II, JPE 1983",
                 url="https://doi.org/10.1086/261140"),
            dict(ref="FRED DCOILWTICO (Cushing WTI spot, daily, 1986-01-02 ->)",
                 url="https://fred.stlouisfed.org/series/DCOILWTICO"),
        ],
        closest_tested=dict(
            study="vix_filter_v1 (VIX states) and credit_vote_v1 (spread states)",
            index_row="docs/research/vix_filter_v1.md",
            difference=(
                "No tested macro arm uses a commodity price. copper/gold (IB23) is in the bank and is a "
                "*ratio* whose difference from this arm is the demand-vs-supply information; the "
                "registered extra data already carries HG=F but no oil series."
            ),
        ),
        signal_inputs=["FRED DCOILWTICO or CL=F (new download)"],
        data=dict(
            in_repo=[],
            new_downloads=[dict(series="DCOILWTICO (daily WTI spot, 1986-01-02 ->)", needed_by=["IC19"], source="FRED CSV (fredgraph.csv?id=DCOILWTICO)")],
            main_window="full",
            oos=dict(evaluable=NO_M2, note="1986+ covers GSPC_1957_1999 and M1 from 1986; M2 un-evaluable -> reduced 5-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('oil', ffill=True) inside wants()"),
        arms=[
            dict(id="A", rule="weight *= 0.5 while r_oil(126) > +50% AND WTI > median252(WTI)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight = 0 in the same state",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, r_oil(126) <= +50%}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: a clean mechanism; the state is rare and 2003-08 shows an oil bull market alongside a strong equity market.",
    ),
    dict(
        id="IC20",
        title="Bond-volatility regime veto",
        category="macro-regime",
        strength_rank=None,
        mechanism=(
            "Rate volatility is the transmission channel of the 2022 equity drawdown and leads risk-off "
            "episodes generally: when bond vol doubles, duration-heavy and long-duration equity is "
            "re-priced. The canonical series (ICE BofA MOVE) has no long free daily history, so the arm "
            "uses the IEF realised 20-session volatility against its own 252-session median - a bond "
            "volatility state computed from a tradable instrument, the analogue of vix_filter_v1's "
            "equity-option state for the bond market."
        ),
        literature=[
            dict(ref="ICE BofA US Bond Market Option Volatility Estimate Index (MOVE; the 'bond VIX')",
                 url="https://developer.ice.com/fixed-income-data-services/catalog/ice-data-indices-move-index"),
            dict(ref="Baele, Bekaert, Inghelbrecht & Wei, Flights to Safety (rate volatility and risk-off), RFS 2020",
                 url="https://doi.org/10.1093/rfs/hhz055"),
        ],
        closest_tested=dict(
            study="vix_filter_v1 (VIX level, spike and term structure; VXN sizing)",
            index_row="docs/research/vix_filter_v1.md",
            difference=(
                "vix_filter_v1's inputs are all equity-option-implied. A *realised bond* volatility state "
                "is a different market's risk price; it was unavailable to vix_filter_v1 (no bond vol "
                "series) and is available now from the registered IEF extra key."
            ),
        ),
        signal_inputs=["IEF daily total-return close (registered extra key ief)"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_ief.csv (2002-07-30 ->, extra key ief)"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=MODERN_ONLY,
                     note="IEF starts 2002-07, so only the modern spans of M3/M4/M5 are live -> 3 applicable windows (holdout_insufficient risk); pre-register the reduced 3-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('ief') inside wants()"),
        arms=[
            dict(id="A", rule="weight *= 0.5 while vol20(IEF) > 1.5 * median252(vol20(IEF))",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight = 0 while vol20(IEF) > 2.0 * median252(vol20(IEF))",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="the multiplier of A applied only while the vote is on (early path unchanged)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: a genuinely different volatility market, but 3-window OOS coverage and the VIX family's past failures weigh against it.",
    ),
    dict(
        id="IC21",
        title="Breakeven-inflation momentum veto",
        category="macro-regime",
        strength_rank=None,
        mechanism=(
            "The 10-year breakeven (T10YIE) is the market's expected inflation; its *change* over 126 "
            "sessions is the state in which the reaction function tightens and long-duration equity "
            "de-rates, isolated from the nominal yield's real component. IB12 (real-yield level) and "
            "IB13 (nominal-10y momentum) are in the bank; neither isolates expectations, which is the "
            "component that moved in 2021-22 and that the Fed reacts to."
        ),
        literature=[
            dict(ref="FRED T10YIE (10-year breakeven inflation rate, daily, 2003-01-02 ->)",
                 url="https://fred.stlouisfed.org/series/T10YIE"),
            dict(ref="Campbell, Sunderam & Viceira, Inflation Bets or Deflation Hedges? The Changing Risks of Nominal Bonds, 2017",
                 url="https://doi.org/10.3386/w21318"),
        ],
        closest_tested=dict(
            study="valuation_v1 (a valuation level) and IB12 (real 10-year yield level, in the bank)",
            index_row="docs/research/valuation_v1.md",
            difference=(
                "Real-yield *levels* and valuation *levels* were tested; breakeven *momentum* is the "
                "expectations component of the nominal yield, a distinct state variable that decomposes "
                "the same curve point."
            ),
        ),
        signal_inputs=["FRED T10YIE (daily breakeven, registered extra key t10yie)"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_t10yie.csv (2003-01-02 ->, extra key t10yie)"],
            new_downloads=[],
            main_window="full",
            oos=dict(evaluable=MODERN_ONLY,
                     note="2003-01 start -> only M3/M4/M5 have live values; reduced 3-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('t10yie', ffill=True) inside wants()"),
        arms=[
            dict(id="A", rule="weight *= 0.5 while T10YIE_t - T10YIE_{t-126} > +0.40 pp",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight = 0 while T10YIE_t - T10YIE_{t-126} > +0.70 pp",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, breakeven 126-session change <= +0.40 pp}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: the mechanism is sharp but the series is short (2003+) and the state is dominated by 2021-22.",
    ),
    dict(
        id="IC22",
        title="Dollar (broad trade-weighted) regime veto",
        category="macro-regime",
        strength_rank=None,
        mechanism=(
            "A broad trade-weighted dollar surge is a global liquidity shock: dollar-funded carry "
            "unwinds and the earnings of the index's non-US revenue base de-rate (2014-15, 2018, 2022). "
            "The state used is a rate of change (60 sessions, more than +4%) *combined* with the dollar "
            "above its 252-session median, so the 2020 crash-to-recovery and the 2021 whipsaw (a mixed "
            "dollar) do not fire. No arm in the record uses a currency series as a risk state."
        ),
        literature=[
            dict(ref="FRED DTWEXBGS (nominal broad US dollar index, daily, 2006-01-02 ->)",
                 url="https://fred.stlouisfed.org/series/DTWEXBGS"),
            dict(ref="Bruno & Shin, Cross-Border Banking and Global Liquidity (the dollar as a risk factor), 2015",
                 url="https://doi.org/10.1093/restud/rdu028"),
        ],
        closest_tested=dict(
            study="IB22 international lead-lag (foreign *equity* index returns, in the bank) and vix_filter_v1",
            index_row="docs/research/vix_filter_v1.md",
            difference=(
                "This idea and the banked IB22 are complements: IB22 uses foreign equity moves as an intraday "
                "causal signal, while this uses the *currency* - the global funding variable - with a "
                "level-plus-change state. Nothing tested uses FX."
            ),
        ),
        signal_inputs=["FRED DTWEXBGS (registered extra key dtwexbgs)"],
        data=dict(
            in_repo=["data/historical/batch_screen_v1_dtwexbgs.csv (2006-01-02 ->, extra key dtwexbgs)"],
            new_downloads=[],
            main_window="partial: DTWEXBGS starts 2006-01-02, so on the main window the veto is only live from 2006 (a fixed convention: the state is treated as absent, weight unchanged, before 2006)",
            oos=dict(evaluable=MODERN_ONLY,
                     note="only M3/M4/M5 have live values from 2006 -> 3 applicable windows; reduced 3-window G7"),
        ),
        plugin=dict(model="targets_from_data", detail="data.extra('dtwexbgs', ffill=True) inside wants()"),
        arms=[
            dict(id="A", rule="weight *= 0.5 while the 60-session dollar change > +4% AND dollar > median252(dollar)",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="B", rule="weight = 0 while the 60-session dollar change > +6%",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
            dict(id="C", rule="3-of-4 vote {SMA200, TSM252, DON126, 60-session dollar change <= +4%}, on iff >= 3",
                 targets_from_data=True, needs_kernel_option=False, kernel_scope=None, params_frozen=True),
        ],
        expected_evidence="low-medium: a distinct, macro-motivated state; 2006+ coverage and the currency-equity correlation's instability are the risks.",
    ),
]
