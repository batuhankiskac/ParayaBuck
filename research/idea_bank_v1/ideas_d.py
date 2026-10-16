"""Idea bank part D -- ML-free statistical filters (IB33-IB35), execution timing (IB36-IB37)
and tax-structure / tax-regime arms (IB38-IB40).  Data only; see ideas_a.py's docstring."""

STATISTICAL = [
    {
        "id": "IB33",
        "title": "Realised-skewness crash-risk scaling",
        "category": "statistical-filter",
        "rank": None,
        "mechanism": (
            "The vol target reads the second moment only.  Realised skewness of the last 252 daily "
            "returns is the third: a negative skew means the return distribution has a fat left tail, "
            "the state in which a 2x daily-reset position is most exposed to reset decay.  Arm A "
            "multiplies the target by 0.75 while the 252-session skew is below -0.5; B uses -1.0; C "
            "uses a continuous multiplier clip(1 + skew, 0.60, 1.10).  No options data is needed, "
            "unlike every tested tail measure (VIX/VXN/VIX3M)."
        ),
        "literature": [
            {"ref": "Harvey & Siddique, Conditional skewness in asset pricing tests, JF 2000",
             "url": "https://doi.org/10.1111/0022-1082.00265"},
        ],
        "closest_tested": {
            "study": "vix_filter_v1 (X1-X4, all option-implied) and vol_window_v1 V5 (downside semi-deviation)",
            "index_row": "docs/research/vix_filter_v1.md",
            "difference": ("V5 is a second-moment down-side measure; the tested tail filters are "
                           "option-implied levels, and idea_survey_v2 section 7.3 rejected the "
                           "option-implied SKEW index class on data grounds.  Realised skewness as "
                           "a sizing input is untested and covers the full pre-2000 OOS windows."),
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "no engine change"},
        "arms": [
            {"id": "A", "rule": "weight *= 0.75 while skew252 < -0.5"},
            {"id": "B", "rule": "weight *= 0.75 while skew252 < -1.0"},
            {"id": "C", "rule": "weight *= clip(1 + skew252, 0.60, 1.10)"},
        ],
        "prior": "low-medium.",
    },
    {
        "id": "IB34",
        "title": "Lag-1 return autocorrelation gate on the trend vote",
        "category": "statistical-filter",
        "rank": None,
        "mechanism": (
            "The cheapest possible serial-dependence test: rho1 = the lag-1 autocorrelation of the "
            "trailing 252 daily returns.  A trend rule should not be exposed to a 2x instrument when "
            "returns are negatively autocorrelated (a bid-ask-bounce / mean-reverting tape).  Arms: A "
            "requires rho1 > 0 for the E2 vote; B relaxes to rho1 > -0.05 (a nuisance-threshold "
            "version); C requires rho1 > 0 only for the early path.  IB04's variance ratio is the "
            "longer-horizon cousin of this; both are pre-registered so the screen can price the "
            "cheap version against the expensive one."
        ),
        "literature": [
            {"ref": "Lo & MacKinlay 1988; Campbell, Lo & MacKinlay, The Econometrics of Financial Markets, ch. 2",
             "url": "https://doi.org/10.1093/rfs/1.1.41"},
        ],
        "closest_tested": {
            "study": "deriv_vote_v1 (price-derivative votes); scout inventory: serial-dependence signals untested",
            "index_row": "docs/research/deriv_vote_v1.md",
            "difference": "no arm in the record computes any return autocorrelation statistic.",
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "no engine change"},
        "arms": [
            {"id": "A", "rule": "E2 on iff the 2-of-3 vote holds AND rho1(252) > 0"},
            {"id": "B", "rule": "E2 on iff the 2-of-3 vote holds AND rho1(252) > -0.05"},
            {"id": "C", "rule": "early-path entry additionally requires rho1(252) > 0"},
        ],
        "prior": "low-medium.",
    },
    {
        "id": "IB35",
        "title": "Conditional vol targeting: adjust exposure only in the volatility extremes",
        "category": "statistical-filter",
        "rank": None,
        "mechanism": (
            "Bongaerts-Kang-van Dijk's conditional vol targeting: a vol-targeting rule pays for "
            "continuous re-sizing in the middle of the vol distribution and gains most of its risk "
            "reduction in the tails, so an arm that adjusts exposure only when vol20 is outside its "
            "own 252-session quartiles (A) or deciles (B) should keep most of the drawdown control "
            "with fewer resizes.  idea_survey_v3 set this aside as 'bounded at < 0 pp by the dead-band "
            "arms' but never measured it; arm C is the plain fixed 3 pp band, a width between the "
            "tested 2.5 and 5 pp (the only untested width in that family), as the like-for-like control."
        ),
        "literature": [
            {"ref": "Bongaerts, Kang & van Dijk, Conditional volatility targeting, FAJ 2020",
             "url": "https://doi.org/10.1080/0015198X.2020.1790853"},
        ],
        "closest_tested": {
            "study": "ewma_band_v1 B25/B50/B100 (2.5/5/10 pp output bands) and exposure_k_v1 (level)",
            "index_row": "docs/research/ewma_band_v1.md",
            "difference": ("the tested bands hold the weight while the *target* drifts; this holds it "
                           "unless the *vol state* is extreme, which is a state-conditional rule, and "
                           "the conditional-targeting form has never been run (only set aside)."),
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "no engine change"},
        "arms": [
            {"id": "A", "rule": "re-size only when vol20 is outside its trailing 252-session [P25, P75]; otherwise hold the weight"},
            {"id": "B", "rule": "the same with [P10, P90]"},
            {"id": "C", "rule": "the plain fixed 3 pp band (control; between the tested 2.5 and 5 pp)"},
        ],
        "prior": "low: the survey's own prior was '< 0 pp', so this is an axis-closing measurement.",
    },
]

EXECUTION = [
    {
        "id": "IB36",
        "title": "Gap-aware fill filter: do not sell into the opening gap",
        "category": "execution",
        "rank": None,
        "mechanism": (
            "Every tested fill-timing arm shifts *when* the fill happens (next close, t+2, MOC, "
            "overnight).  Untested is conditioning *whether* it happens on the fill price itself: a "
            "band-permitted downsize is skipped if the next open has already gapped more than 1% below "
            "the decision close (the sale would lock in the gap), and the ordinary rule is re-evaluated "
            "at the next close.  A/B differ in the gap threshold (1.0% / 0.5%); C applies the mirror to "
            "up-resizes (skip buying an opening gap above +1.0%).  This is the only idea in the bank "
            "that uses the *open* price as a decision input, and the mechanism can only help the "
            "execution cost, never the signal."
        ),
        "literature": [
            {"ref": "Almgren & Chriss, Optimal execution of portfolio transactions (implementation shortfall)",
             "url": "https://doi.org/10.21314/JOR.2001.041"},
            {"ref": "Bogousslavsky, The cross-section of intraday and overnight returns, JFE 2021",
             "url": "https://doi.org/10.1016/j.jfineco.2020.09.001"},
        ],
        "closest_tested": {
            "study": "exec_delay_v1 (D1-D4) and moc_execution_v1 (X1-X3)",
            "index_row": "docs/research/exec_delay_v1.md",
            "difference": ("those arms move every fill to another session; none conditions the fill on "
                           "the realised execution price, and the scout inventory lists "
                           "'VWAP/TWAP/limit ladders' as untested."),
        },
        "signal_inputs": ["the arm's own instrument open and close (with a one-session decision lag)"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv (QQQ open/close)",
                             "ewma_band_v1_index.csv (index OHLC for M1-M5, M2 included)"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["M1", "M2", "M3", "M4", "M5"],
                         "note": "the harness fills ^GSPC 1957-1999 opens as the previous close, so the gap filter is inert there -> reduced 5-window G7"}},
        "plugin": {"model": "kernel_option",
                   "detail": ("sim_one's fill decision must see the fill session's open and the prior close; "
                              "parity cell = threshold set to 0 reproduces K bit-for-bit")},
        "arms": [
            {"id": "A", "rule": "skip a permitted downsize when the next open < decision close * (1 - 0.010); re-evaluate next close"},
            {"id": "B", "rule": "the same with a 0.005 threshold"},
            {"id": "C", "rule": "the same at 0.010 for up-resizes (skip buying a gap up) and no filter on downsizes"},
        ],
        "prior": "medium-low: execution-cost arms have lost, but this one cannot damage the signal (it only defers fills).",
    },
    {
        "id": "IB37",
        "title": "VWAP-proxy fills: exit at the session's typical price",
        "category": "execution",
        "rank": None,
        "mechanism": (
            "Fill price is a modelling choice: production fills at the next open, and the tested "
            "alternatives are the next close and the decision close.  The typical price (high+low+"
            "close)/3 of the fill session is the standard VWAP proxy for a *market order worked "
            "through the session* and has never been used; it is the honest way to model a large "
            "resize that would be spread over the day.  A: exits and downsizes fill at the typical "
            "price, entries and up-resizes stay at the open; B: all fills at the typical price; C: "
            "conservative -- sells at min(open, typical), buys at max(open, typical)."
        ),
        "literature": [
            {"ref": "Berkowitz, Logue & Noser 1988 (VWAP as an execution benchmark)",
             "url": "https://doi.org/10.3905/jpm.1988.409144"},
        ],
        "closest_tested": {
            "study": "moc_execution_v1 (same-close fills) and exec_delay_v1 (D1-D4)",
            "index_row": "docs/research/moc_execution_v1.md",
            "difference": ("every tested variant uses an auction price (open or close) on some session; "
                           "execution_model_audit_v1 records the *live* fill VWAP as a diagnostic but no "
                           "the intra-session typical price has never been modelled, and "
                           "overnight_hold_v1's finding (QQQ earns ~+9.8%/yr overnight, ~-2.2% "
                           "intraday) makes the sign of this arm genuinely uncertain in both directions."),
        },
        "signal_inputs": ["the arm's own high, low, close (fill session)"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv", "ewma_band_v1_index.csv"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["M1", "M2", "M3", "M4", "M5"],
                         "note": "same reduction for ^GSPC 1957-1999 (harness opens := previous close)"}},
        "plugin": {"model": "kernel_option",
                   "detail": "sim_one's fill price; parity cell = 'open' reproduces K bit-for-bit"},
        "arms": [
            {"id": "A", "rule": "exits and downsizes fill at (h+l+c)/3 of the fill session; entries/up-resizes at the open"},
            {"id": "B", "rule": "all fills at (h+l+c)/3"},
            {"id": "C", "rule": "sells at min(open, (h+l+c)/3); buys at max(open, (h+l+c)/3)"},
        ],
        "prior": "low-medium: a modelling-choice arm whose outcome informs the cost model as much as the strategy.",
    },
]

TAX = [
    {
        "id": "IB38",
        "title": "Tax-unit and tax-regime layer: 20/D eligibility, rate-regime switch table, income splitting",
        "category": "tax-structure",
        "rank": 2,
        "mechanism": (
            "Every arm in 80-odd studies is priced for one taxpayer under the frozen 2026 progressive "
            "tariff, and the same rule earns 11.69% after tax at $10k against 9.19% at $1M -- i.e. the "
            "*tariff*, not the rule, moves the answer by 1.5 pp.  Three things follow, none ever "
            "simulated: (a) a regime switch table that re-runs R2/A1u/H2/K under tau = 0, flat 20% and "
            "the progressive tariff on each arm's own path, which measures the rate at which K's "
            "execution layer stops paying for itself; (b) income splitting: two independent accounts "
            "of $50k (or three of $33.3k) each running K, household CAGR = the per-account CAGR, which "
            "multiplies the progressive ladder and the loss pool; (c) a routed split, where downsizes "
            "fill in whichever account's FIFO head is cheaper.  The 20/D exemption (GVK muk. 20/D, a "
            "20-year exemption on foreign-source capital gains for individuals becoming Turkish "
            "residents from 1/1/2026) is a *precondition* answered by an adviser, not an arm: if any "
            "household member qualifies, the entire ~5 pp/yr drag moves taxpayer and the pre-tax "
            "optimum becomes the after-tax optimum."
        ),
        "literature": [
            {"ref": "Kleven, Kreiner & Svarer, Taxation of couples (tax-unit neutrality)",
             "url": "https://doi.org/10.1111/j.1468-0297.2009.02259.x"},
            {"ref": "AQR, The Enduring Appeal of Gain Deferral, Parts 1-3",
             "url": "https://www.aqr.com/Insights/Research/Journal-Article/The-Enduring-Appeal-of-Gain-Deferral"},
            {"ref": "Krasner & Sosner, Loss harvesting or gain deferral? JWM 2024",
             "url": "https://doi.org/10.3905/jwm.2023.1.213"},
        ],
        "closest_tested": {
            "study": "cash_yield_v1 (the one account-setting study) and vt_level_tax_v1 / tax_asym_v3 (capital scaling)",
            "index_row": "docs/research/cash_yield_v1.md",
            "difference": ("capital was swept in the after-tax stages ($10k-$1M) but the *tax unit* "
                           "(how many taxpayers, which regime) has never been varied, and idea_survey_v5 "
                           "I1 -- whose arms these are -- was never run.  The scout inventory records "
                           "the tax-unit/tax-regime layer as 'SURVEY ONLY, never simulated'."),
        },
        "signal_inputs": ["none (accounting and tax-regime inputs only)"],
        "data": {"in_repo": ["after_tax_tr_v1_usdtry_cbrt.csv", "after_tax_tr_v1_yiufe.csv",
                             "the engine's TARIFF_2026 and the flat-20% account (fe_tax)"],
                 "new_downloads": [],
                 "main_window": "full (after-tax stages only; the pre-tax stages are regime-invariant)",
                 "oos": {"evaluable": [],
                         "note": ("the TRY/YI-UFE calendar starts 2000, so no after-tax OOS exists; the "
                                  "roller of E2 starts, E3 bootstraps and B1/B2 are the substitutes, as in "
                                  "every after-tax study.")}},
        "plugin": {"model": "hybrid",
                   "detail": ("arm A is the existing engine with mode/capital varied (targets-from-data, no "
                              "kernel edit); arm B is two/three independent engine runs summed (an accounting "
                              "layer, no kernel edit); arm C needs a two-book kernel option with a route cap, "
                              "and an off/on parity cell proving two unrouted identical accounts reproduce "
                              "K's NAV exactly.")},
        "arms": [
            {"id": "A", "rule": "regime switch table: {R2, A1u, H2, K} x {tau=0, flat-20%, progressive-2026} x {$10k,$100k,$1M} x the six windows (report-only for the arms; it *is* the decision input)"},
            {"id": "B", "rule": "two $50k accounts and three $33.3k accounts, each running K; household CAGR reported vs K at $100k and vs K at $50k/$33.3k"},
            {"id": "C", "rule": "routed split: two accounts, downsizes routed to the cheaper FIFO head, route cap 10 pp; up-resizes to the more-below-target account"},
        ],
        "prior": "high for the measurement, unknown for the gain: +3..+5 pp if a household member qualifies for 20/D, ~+0.2..+0.5 pp from splitting alone, 0 if not.",
    },
    {
        "id": "IB39",
        "title": "Tax-regime stress (TRY/PPI decoupling) and an FX-regime band switch",
        "category": "tax-structure",
        "rank": 7,
        "mechanism": (
            "K's A1u+H2 premium is a function of indexation relief, which is a function of the gap "
            "between USD/TRY depreciation and YI-UFE inflation; that gap has never been varied, and "
            "idea_survey_v4 named the decoupled calendar as the missing regime check.  Arm A is that "
            "stress as a pre-registered arm: re-run R2/A1u/H2/K on synthetic calendars (TRY flat with "
            "20%/yr PPI; TRY depreciating 30%/yr with PPI at 5%; and the realised 2016-2026 calendar as "
            "the anchor) and report whether K's edge inverts.  Arms B and C then *act* on the regime: "
            "multiply A1u's three band widths by 1.5 (0.5/3/8 -> 0.75/4.5/12 pp) while the trailing "
            "252-session realised indexation relief on the account's own sales exceeds 0.5 (relief is "
            "large, so each deferred sale is worth more), and by 0.5 in the mirror arm."
        ),
        "literature": [
            {"ref": "Constantinides 1983; AQR gain-deferral series (deferral value rises with the rate and the horizon)",
             "url": "https://www.aqr.com/Insights/Research/Journal-Article/The-Enduring-Appeal-of-Gain-Deferral"},
            {"ref": "idea_survey_v4 section 6.3 (the named, never-run regime stress)",
             "url": "docs/research/idea_survey_v4.md"},
        ],
        "closest_tested": {
            "study": "state_defer_v1 (D3: key deferral on the estimated tax > 0.05% of NAV) and monthly_defer_v1",
            "index_row": "docs/research/state_defer_v1.md",
            "difference": ("state_defer keyed a *deferral trigger* on the lot book's own indexed gain and "
                           "declared the realization-timing axis closed; this arm keeps K's calendar and "
                           "gain-band triggers and modulates the *band width* on a macro state, plus a "
                           "calendar stress that no after-tax study has run.  It is filed as an axis-closing "
                           "measurement with a low prior, not as a new deferral device."),
        },
        "signal_inputs": ["USD/TRY (CBRT, repo)", "YI-UFE (repo)", "the engine's own realised-relief fraction"],
        "data": {"in_repo": ["data/historical/after_tax_tr_v1_usdtry_cbrt.csv (2000-01-03 ->)",
                             "data/historical/after_tax_tr_v1_yiufe.csv (1999-01 ->)"],
                 "new_downloads": [],
                 "main_window": "after-tax stages only",
                 "oos": {"evaluable": [],
                         "note": ("the pre-tax OOS stage has no TRY calendar (the R-USD rule), so G7/G8 "
                                  "cannot see this arm; pre-register the reduced gate on the after-tax "
                                  "windows + B1/B2 + the 21-ETF after-tax stage, and report the stress "
                                  "table separately.")}},
        "plugin": {"model": "kernel_option",
                   "detail": ("band widths are the kernel's RULE constants (0.5/3/8 pp); making them "
                              "state-dependent needs a per-session width multiplier in a copy of the engine, "
                              "with an off/on parity cell (multiplier = 1.0 reproduces K bit-for-bit).  Arm A "
                              "needs no kernel edit at all (the calendar is an existing input).")},
        "arms": [
            {"id": "A", "rule": "stress table: K vs R2/A1u/H2 on synthetic calendars {TRY flat + PPI 20%/yr; TRY -30%/yr + PPI 5%; realised 2016-2026} at $100k"},
            {"id": "B", "rule": "A1u band widths x 1.5 while the trailing 252-session realised indexation relief fraction > 0.5"},
            {"id": "C", "rule": "A1u band widths x 0.5 while the same state holds (mirror)"},
        ],
        "prior": "low-medium: arm A is a risk measurement that can invalidate K's execution layer; B/C are small and adjacent to a closed axis.",
    },
    {
        "id": "IB40",
        "title": "Instalment funding source: satisfy the tax liability from the target reduction, not from an extra sale",
        "category": "tax-structure",
        "rank": None,
        "mechanism": (
            "The engine funds the March/July instalments by selling units, which realises gain on top "
            "of the ordinary resizes (about 1% of the realised base, on the report's own accounting).  "
            "Two untested routings remove that extra sale: (A) when a band-permitted downsize occurs "
            "in the same year as an unpaid liability, route its proceeds to the tax account instead of "
            "reinvesting to the target; (B) suppress the next up-resize while a liability is unpaid "
            "(fund the tax by simply holding less); (C) both.  It is a cash-flow rule, not a signal, "
            "and it only ever removes a trade."
        ),
        "literature": [
            {"ref": "After-tax rebalancing / tax-aware trading: Berkin & Ye, FAJ 2003; Israel & Moskowitz, The role of shorting, tax-aware portfolios",
             "url": "https://doi.org/10.2469/faj.v59.n4.2536"},
        ],
        "closest_tested": {
            "study": ("tax_budget_v2 (Y1/Y2/Y3 all *create* trades), exit_timing_v2 X1-X3 and "
                      "cash_yield_v1 (which measures the *yield* on money parked for the March/July instalments)"),
            "index_row": "docs/research/tax_budget_v2.md",
            "difference": ("every tested tax arm creates, defers or re-times a realisation, and cash_yield_v1 "
                           "prices the interest earned on the parked instalment money; none changes the "
                           "*funding source* of a payment the account already owes."),
        },
        "signal_inputs": ["the engine's own tax-payment schedule and realised-gain state"],
        "data": {"in_repo": ["after_tax_tr_v1_usdtry_cbrt.csv", "after_tax_tr_v1_yiufe.csv",
                             "the engine's instalment schedule (fe_tax)"],
                 "new_downloads": [],
                 "main_window": "after-tax stages only",
                 "oos": {"evaluable": [],
                         "note": "same reduced gate as IB39 (no after-tax OOS exists)."}},
        "plugin": {"model": "kernel_option",
                   "detail": ("the payment/funding interaction lives in fe_tax plus sim_one's cash routing; "
                              "needs an enable flag with a parity cell (flag off = K bit-for-bit).")},
        "arms": [
            {"id": "A", "rule": "route a band-permitted downsize's proceeds to the unpaid liability (up to the amount due) instead of to the target"},
            {"id": "B", "rule": "suppress the next up-resize while a liability is unpaid and the cash is short"},
            {"id": "C", "rule": "both A and B"},
        ],
        "prior": "low: the effect is bounded by the ~1% of the base that instalment sales carry, but it can only remove a trade.",
    },
]
