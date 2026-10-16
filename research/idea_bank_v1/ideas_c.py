"""Idea bank part C -- off-state holdings (IB24-IB26), on-state instrument choice (IB27-IB28)
and drawdown/recovery-state sizing (IB29-IB32).  Data only; see ideas_a.py's docstring."""

OFF_STATE = [
    {
        "id": "IB24",
        "title": "T-bill sleeve with an entry/exit hysteresis (kill the failed S1 round trips)",
        "category": "off-state",
        "rank": None,
        "mechanism": (
            "tbill_sweep_v1's S1 (hold BIL whenever the E2 vote is off) failed on the bootstrap by "
            "-0.08 pp because a 0.5 pp-banded trend rule turns off and on briefly, paying a BIL "
            "round trip each time; S2_10 fixed the *residual* but not that.  This arm leaves the "
            "bleed-the-asset question alone and prices only the *timing*: enter the bill sleeve only "
            "once the vote has been off for N consecutive sessions (A: 5, B: 10) and, in arm C, exit "
            "it only once the vote has been on for 5 consecutive sessions (an exit-side lag instead)."
        ),
        "literature": [
            {"ref": "tbill_sweep_v1's own post-mortem (repo); no external literature needed",
             "url": "docs/research/tbill_sweep_v1.md"},
        ],
        "closest_tested": {
            "study": "tbill_sweep_v1 S1 / S2_5 / S2_10 and r2_tbill_combo_v1 RS",
            "index_row": "docs/research/tbill_sweep_v1.md",
            "difference": ("S1 switches the sleeve on the same session the vote switches; the tested "
                           "arms vary the *residual band*, not the entry/exit lag on the sleeve itself."),
        },
        "signal_inputs": ["the E2 vote state (derived from the arm's own price series)"],
        "data": {"in_repo": ["stress_robustness_v1_dtb3.csv (BIL* = DTB3 - 0.1356%/yr, tbill_sweep's construction)"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "BIL* is synthetic from DTB3/TB3MS, available in every window"}},
        "plugin": {"model": "targets_from_data",
                   "detail": ("the sleeve is a second leg, but it is *cash-like and never rebalanced against the "
                              "equity leg*, so it can be represented as a cash modifier on the target series in "
                              "the pre-tax stage; the after-tax stage needs the existing engine's cash-yield "
                              "knob (fe_sim.RunEnv(cash_rate=...)) rather than a new kernel")},
        "arms": [
            {"id": "A", "rule": "enter BIL* on the residual + off-state weight after 5 consecutive off sessions; exit on re-entry"},
            {"id": "B", "rule": "the same with N = 10 consecutive off sessions"},
            {"id": "C", "rule": "enter BIL* immediately on the first off session, exit only after 5 consecutive on sessions"},
        ],
        "prior": "low-medium: the family loses after tax; this arm attacks the measured cost, not the tax.",
    },
    {
        "id": "IB25",
        "title": "Off-state residual in short-duration Treasuries (SHY) instead of bills",
        "category": "off-state",
        "rank": None,
        "mechanism": (
            "Every tested defensive asset is either cash-like (BIL/SGOV), long duration (TLT/IEF/"
            "BOND10*) or gold.  The 1-3 year Treasury point has never been used, and it is the only "
            "one that carries a genuine yield pick-up over a bill (≈ +40-80 bp at 2026 curve levels) "
            "with moderate rate risk, and it is the classic 'off-state carry' instrument.  A: hold "
            "0.98 in SHY while the vote is off; B: hold only the *residual* (0.98 - w) in SHY, "
            "re-sized only outside a 10 pp band (tbill_sweep S2_10's shape, different asset)."
        ),
        "literature": [
            {"ref": "riskoff_rotation_v1-v4 (repo) for the BOND10*/GOLD* precedent",
             "url": "docs/research/riskoff_rotation_v1.md"},
        ],
        "closest_tested": {
            "study": "tbill_sweep_v1 S2_10 (BIL) and leverage_frontier_v1 (BOND10* = 10y par bond)",
            "index_row": "docs/research/tbill_sweep_v1.md",
            "difference": ("BIL* is DTB3 - 0.1356%/yr and BOND10* is a 10-year constant-maturity par "
                           "bond; the 1-3 year point, which is the only intermediate-carry cell, is "
                           "untested.  The scout inventory lists 'leveraged/other bond funds: NOT tested'."),
        },
        "signal_inputs": ["SHY daily close (new)"],
        "data": {"in_repo": ["stress_robustness_v1_dtb3.csv (for the cash alternative)"],
                 "new_downloads": ["Yahoo chart API SHY (inception 2002-07, ->), saved as "
                                   "data/historical/idea_bank_v1_shy.csv with URL + SHA-256"],
                 "main_window": "reduced start: 2002-08",
                 "oos": {"evaluable": [],
                         "note": "SHY starts 2002 -> reduced gate (main window, rolling starts, bootstraps)."}},
        "plugin": {"model": "targets_from_data",
                   "detail": "a cash-like sleeve; the same cash-rate-knob treatment as IB24, or the two-leg kernel for exactness"},
        "arms": [
            {"id": "A", "rule": "hold 0.98 in SHY whenever the trend vote is off (production target otherwise)"},
            {"id": "B", "rule": "hold the residual (0.98 - w) in SHY, re-sized only outside a 10 pp band"},
        ],
        "prior": "low: BIL lost after tax through realised sleeve gains, and SHY has more of them; the arm is here to measure the carry-vs-tax trade.",
    },
    {
        "id": "IB26",
        "title": "Curve-conditioned off-state leg: intermediate Treasuries only when the curve pays",
        "category": "off-state",
        "rank": None,
        "mechanism": (
            "Both the credit_vote_v1 curve arm and the riskoff_rotation bond legs use the bond market "
            "in isolation.  This arm conditions the *defensive leg* on the curve: hold IEF instead of "
            "cash while the vote is off **and** the 10y-3m slope exceeds 1.0 pp (roll-down and carry "
            "are paid), fall back to cash otherwise.  A: the 1.0 pp threshold; B: the threshold plus "
            "a requirement that the 10y yield is not falling faster than -0.5 pp over 60 sessions; "
            "C: the mirror (IEF only when the curve is *flat or inverted*, the recession-timing trade)."
        ),
        "literature": [
            {"ref": "Campbell, Sunderam & Viceira, Inflation bets or deflation hedges? (bond risk premia and the curve)",
             "url": "https://doi.org/10.3386/w21466"},
        ],
        "closest_tested": {
            "study": "riskoff_rotation_v1 R3/R4 (trend-gated BOND10*/GOLD* while off) and credit_vote_v1 K3 (inversion veto)",
            "index_row": "docs/research/riskoff_rotation_v1.md",
            "difference": ("R3/R4 gate the defensive leg on its own 252-session *trend*; K3 uses the "
                           "curve as an equity veto.  Neither conditions the defensive asset on the "
                           "curve slope."),
        },
        "signal_inputs": ["IEF close (repo)", "FRED T10Y3M (repo, 1982+)", "FRED DGS10 (repo)"],
        "data": {"in_repo": ["riskoff_rotation_v4_etfs.csv (IEF 2002-07-30+)", "credit_vote_v1_T10Y3M.csv",
                             "leverage_frontier_v1_dgs10.csv"],
                 "new_downloads": [],
                 "main_window": "reduced start: 2003-07 (IEF + 252 warm-up)",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2"],
                         "note": ("monthly GS10-TB3MS (1953+) exists for the US windows but BOND10* "
                                  "would have to be built from GS10 as riskoff_rotation did; pre-"
                                  "register that as the reduced OOS construction, or drop the OOS "
                                  "clause for this arm.")}},
        "plugin": {"model": "targets_from_data", "detail": "same treatment as IB24/IB25"},
        "arms": [
            {"id": "A", "rule": "while off: hold IEF at 0.98 if T10Y3M > 1.0 pp, else cash"},
            {"id": "B", "rule": "as A plus a requirement that the 60-session DGS10 change > -0.50 pp"},
            {"id": "C", "rule": "the mirror: hold IEF while off only if T10Y3M <= 1.0 pp"},
        ],
        "prior": "low.",
    },
]

INSTRUMENT = [
    {
        "id": "IB27",
        "title": "Beta-matched QQQ + QLD blend: same Nasdaq beta-2 exposure at a lower carry",
        "category": "on-state-instrument",
        "rank": 1,
        "mechanism": (
            "K delivers a target Nasdaq beta of B = 2*f*vt (f is the R2 path factor: 1 on, 0.5 early) "
            "by holding w_QLD = f*vt of a 2x fund, i.e. it pays the fund's expense plus the internal "
            "financing on the *whole* notional.  Half of that beta can be bought more cheaply with the "
            "unlevered index fund: QQQ costs ~20 bp/yr and no financing, and one unit of QQQ plus one "
            "unit of QLD delivers three units of beta at a cost of (20 bp + 95 bp + DTB3 + spread).  "
            "The minimum-carry construction that delivers exactly beta B with C = min(0.98, B) of cash "
            "invested is: w_QLD = B - C, w_QQQ = C - w_QLD (with w_QQQ = 0 and w_QLD = C when C = B).  "
            "At the measured mean vol-target level (mean(vt) = 0.8397, f = 1) that is w_QLD = 0.70, w_QQQ = 0.28 "
            "instead of 0.84 QLD: about 45 bp/yr of carry saved at an identical instantaneous beta, "
            "with the same 0.98 capital deployed (idle cash earns nothing, so deploying it is free).  "
            "This is a pure cost arbitrage between two Nasdaq instruments, not a signal, and it is the "
            "only idea in the bank whose prior is arithmetic rather than statistical."
        ),
        "literature": [
            {"ref": "Leveraged-ETF decay/financing literature (Cheng & Madhavan 2009; Tang & Xu 2014)",
             "url": "https://doi.org/10.2139/ssrn.1343444"},
            {"ref": "instrument_cost_v1's own finding that financing dominates expense (repo)",
             "url": "docs/research/instrument_cost_v1.md"},
        ],
        "closest_tested": {
            "study": ("instrument_cost_v1 (TQQQ at 2/3 weight, a beta-matched substitution) and "
                      "early_qqq_v1 (the early leg in unlevered QQQ*/SPY*/SSO*)"),
            "index_row": "docs/research/instrument_cost_v1.md",
            "difference": (
                "TQQQ at 2/3 keeps the *leveraged notional* at 2x and merely swaps the fund; "
                "early_qqq_v1 tested an unlevered substitution only for the half-weight early leg and "
                "measured +0.02 pp/yr.  Neither builds the cash-constrained *blend* that holds the "
                "unlevered fund up to the 0.98 cap: at vt >= 0.49 the unlevered fund alone cannot "
                "reach beta B, so the arm must mix, and the mixture is what removes the financing on "
                "the (0.98 - w_QLD) share of the position.  The saving scales with the weight (zero at "
                "vt = 0.98, maximal near vt = 0.49), which is exactly the region early_qqq_v1 never "
                "operated in."
            ),
        },
        "signal_inputs": ["the arm's own instrument close (QQQ in the main window)", "the production vt and the R2 path factor"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv (QQQ OHLC+dividend)",
                             "the harness's synthetic 2x QLD* (built from QQQ in every window)"],
                 "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"],
                         "note": ("both legs are derivable in every window: the index itself is the "
                                  "unlevered leg (1x total return minus a 20 bp expense) and the "
                                  "existing synthetic 2x is the levered leg, so the pre-tax OOS and "
                                  "21-ETF stages are evaluable without new data.")}},
        "plugin": {"model": "hybrid",
                   "detail": ("pre-tax / OOS / 21-ETF stages: run the blend as one *synthetic blended "
                              "instrument* whose daily return is the blend's own return on the arm's own "
                              "weight path -- a targets-from-data change, no engine edit (disclosed "
                              "approximation: it prices the blend as one fund).  After-tax stages: two legs "
                              "have two FIFO lot books and one cash account, which the engine does not "
                              "model (see fast_engine_v1 'One instrument per account'), so an exact "
                              "after-tax result needs a two-leg kernel option in a copy of the engine plus "
                              "an off/on parity cell; otherwise the after-tax number must be reported as the "
                              "single-instrument approximation and labelled as such.")},
        "arms": [
            {"id": "A", "rule": "blend in the on-state only (f=1); the early leg stays production half-weight QLD"},
            {"id": "B", "rule": "blend everywhere, including the early leg (f=0.5), which makes the early leg unlevered QQQ"},
            {"id": "C", "rule": "blend only when vt >= 0.60; production QLD otherwise"},
        ],
        "prior": "high: a deterministic cost saving with no signal content; the risk is the daily-reset "
                 "tracking difference between the blend and pure QLD, and (arm B) early_qqq_v1's measured switch cost.",
    },
    {
        "id": "IB28",
        "title": "Two-leg execution: route each resize to the leg with the cheaper FIFO lots",
        "category": "on-state-instrument",
        "rank": 6,
        "mechanism": (
            "idea_survey_v2 I1 (sell the lowest-gain lots) was blocked because the broker offers no "
            "lot-level sells under FIFO.  A two-leg account creates the one legitimate version of the "
            "same economics: with QQQ and QLD both held (IB27), a required downsize can be taken from "
            "whichever leg's FIFO head carries the lower USD gain fraction, subject to a route cap of "
            "10 pp on how far the other leg may drift from its share of the target.  Exits to zero and "
            "tax-funding sales are never routed (they must clear the position).  This is a genuine "
            "tax-reduction mechanism inside the broker's rules, and it is the tax-side twin of IB27."
        ),
        "literature": [
            {"ref": "Berkin & Ye, Tax management, loss harvesting and HIFO accounting, FAJ 2003",
             "url": "https://doi.org/10.2469/faj.v59.n4.2536"},
            {"ref": "Krasner & Sosner, Loss harvesting or gain deferral? JWM 2024",
             "url": "https://doi.org/10.3905/jwm.2023.1.213"},
        ],
        "closest_tested": {
            "study": "gain_band_v3 A1u (the FIFO gain fraction decides *whether* a downsize fills) and idea_survey_v2 I1 (blocked lot selection)",
            "index_row": "docs/research/gain_band_v3.md",
            "difference": ("A1u uses the gain fraction of the single leg's FIFO head to *block* a sale; "
                           "this uses it to *choose the leg*, which requires the two-leg account and a "
                           "route cap and has never been run."),
        },
        "signal_inputs": ["the two legs' own price/lot history"],
        "data": {"in_repo": ["as IB27"], "new_downloads": [],
                 "main_window": "full; after-tax stages only (it changes nothing pre-tax beyond IB27's carry)",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"],
                         "note": "pre-tax it is identical to IB27 arm A/B, so OOS reports the IB27 cells"}},
        "plugin": {"model": "kernel_option",
                   "detail": ("requires the two-leg account from IB27 plus a routing rule with the FIFO "
                              "books visible in the fill decision; parity cell = routing disabled reproduces "
                              "IB27 bit-for-bit")},
        "arms": [
            {"id": "A", "rule": "downsizes fill in the leg with the lower USD FIFO gain fraction, route cap 10 pp"},
            {"id": "B", "rule": "as A with a route cap of 5 pp"},
            {"id": "C", "rule": "as A, plus up-resizes fill in the leg furthest below its target share (the mirror preference)"},
        ],
        "prior": "medium: the mechanism is real and broker-legal; the size depends on how often the two legs' gain fractions differ materially.",
    },
]

DRAWDOWN = [
    {
        "id": "IB29",
        "title": "Drawdown-depth-scaled exposure (two-sided test)",
        "category": "drawdown-recovery",
        "rank": None,
        "mechanism": (
            "The only state the strategy currently reacts to is vol and trend; its *own* drawdown is "
            "not a state variable.  Depth-scaled exposure multiplies the target by clip(1 - dd/D, "
            "floor, 1.0) with dd the drawdown from the 252-session high, so exposure is cut as the "
            "drawdown deepens (a risk-parity-of-losses idea), and the mirror arm *raises* it as the "
            "drawdown deepens (the recovery-value bet).  Both directions are pre-registered because "
            "dip_overlay_v1's tested trigger was a 5-day return (a fast dip), not a slow drawdown "
            "state, and the two have different trade sets."
        ),
        "literature": [
            {"ref": "Grossman & Zhou, Optimal investment strategies for controlling drawdowns, Math Finance 1993",
             "url": "https://doi.org/10.1111/j.1467-9965.1993.tb00044.x"},
        ],
        "closest_tested": {
            "study": "dip_overlay_v1 P1/P2/P3 (5-day -5%, RSI(2), 5-day +5%) and crash_entry_v1",
            "index_row": "docs/research/dip_overlay_v1.md",
            "difference": ("P1-P3 are *fast* price triggers; the drawdown state is a slow, continuous "
                           "state variable and no arm scales exposure by it.  crash_entry's entry path "
                           "requires an exit >= 20% below the 252-high *and* falling vol, and never fired."),
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "multiplier on the target series; no engine change"},
        "arms": [
            {"id": "A", "rule": "weight *= clip(1 - dd252/0.30, 0.50, 1.00)"},
            {"id": "B", "rule": "weight *= clip(1 - ddATH/0.40, 0.50, 1.00), ddATH from the expanding all-time high"},
            {"id": "C", "rule": "mirror: weight *= clip(1 + dd252/0.30, 1.00, 1.50)"},
        ],
        "prior": "low-medium: arm A is a de-risking bet (the DD gate is easy, the return gate is not); arm C is the value bet.",
    },
    {
        "id": "IB30",
        "title": "Time-under-water (duration of the all-time-high drawdown) scaling",
        "category": "drawdown-recovery",
        "rank": None,
        "mechanism": (
            "Depth and duration are different states: 2000-02 was deep and long, 2020 deep and short, "
            "2022 moderate and medium.  Time-under-water (sessions since the last all-time high) has "
            "never been a state variable here, and in the trend-following literature the duration of "
            "the recovery is the practical proxy for 'the regime has changed'.  A: weight *= "
            "clip(1 - u/756, 0.50, 1.00); B: the same with u/378 (twice as aggressive); C: the mirror "
            "weight *= clip(1 + u/756, 1.00, 1.25)."
        ),
        "literature": [
            {"ref": "Underwater/duration-based risk control (drawdown duration literature; Grossman-Zhou 1993)",
             "url": "https://doi.org/10.1111/j.1467-9965.1993.tb00044.x"},
        ],
        "closest_tested": {
            "study": "rule_variants_v1 (exit buffers, min-hold, staging) and exposure_k_v1",
            "index_row": "docs/research/rule_variants_v1.md",
            "difference": "no tested arm carries a duration-of-underwater state; the closest are fixed-session min-holds.",
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "no engine change"},
        "arms": [
            {"id": "A", "rule": "weight *= clip(1 - u/756, 0.50, 1.00), u = sessions since the last all-time high"},
            {"id": "B", "rule": "weight *= clip(1 - u/378, 0.50, 1.00)"},
            {"id": "C", "rule": "mirror: weight *= clip(1 + u/756, 1.00, 1.25)"},
        ],
        "prior": "low.",
    },
    {
        "id": "IB31",
        "title": "Volatility-spike exit for the early leg (replace the SMA50 break)",
        "category": "drawdown-recovery",
        "rank": None,
        "mechanism": (
            "The early path's entry is vol-filtered but its *exit* is purely a price level (close < "
            "SMA50), which is what produced the -3.10 pp and -1.85 pp whipsaws in December 2022 and "
            "January 2023 -- the two largest early-path losses.  Exiting on a vol spike instead (or "
            "as well) removes the position in the deteriorating-vol state rather than after the price "
            "has already crossed the average.  A: exit when vol20 > 1.5x its trailing 252-session "
            "median; B: 2.0x; C: exit on the union of the production SMA50 break and the 1.5x vol "
            "spike (an exit that can only fire earlier)."
        ),
        "literature": [
            {"ref": "fast_reentry_v1's own whipsaw episode table (repo); Moreira & Muir 2017 for the vol state",
             "url": "docs/research/fast_reentry_v1.md"},
        ],
        "closest_tested": {
            "study": "fast_reentry_v1/v2 (the early path's SMA50 exit and its vol-median *entry* filter)",
            "index_row": "docs/research/fast_reentry_v1.md",
            "difference": ("the vol-median test is part of the *entry* condition; the exit has always "
                           "been the price level.  Nothing in the record keys an exit on a vol spike "
                           "(intraday_stop_v1 keys on a fixed price distance; vix_filter on the VIX level)."),
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "the early-state machine's exit condition in numpy; no engine change"},
        "arms": [
            {"id": "A", "rule": "early leg exits when vol20 > 1.5 * median252(vol20)"},
            {"id": "B", "rule": "early leg exits when vol20 > 2.0 * median252(vol20)"},
            {"id": "C", "rule": "early leg exits on (close < SMA50) OR (vol20 > 1.5 * median252(vol20))"},
        ],
        "prior": "medium-low: aimed at the measured early-path losses rather than at the rule's average behaviour.",
    },
    {
        "id": "IB32",
        "title": "Sticky sizing input: a dead-band on vol20 instead of on the target weight",
        "category": "drawdown-recovery",
        "rank": 10,
        "mechanism": (
            "Every tested band (ewma_band_v1's 2.5/5/10 pp, A1u's gain-scaled band) acts on the "
            "target weight *after* the vol input has moved.  Untested is a dead-band on the input: "
            "keep using the last accepted vol20 until it has moved by more than X% (relative) from "
            "the value in force, so the target only changes when the vol *state* changes rather than "
            "when the estimate wiggles.  Because A1u already blocks the small gain-heavy trims, this "
            "arm's measurable effect is on the 0.5-3 pp drifts that still pass the band; it is the "
            "cheapest possible two-line change and it interacts directly with the 91%-of-gain trims "
            "diagnosis."
        ),
        "literature": [
            {"ref": "Bongaerts, Kang & van Dijk, Conditional vol targeting / dead-band trading (FAJ 2020)",
             "url": "https://doi.org/10.1080/0015198X.2020.1790853"},
        ],
        "closest_tested": {
            "study": "ewma_band_v1 (band widths 2.5/5/10 pp) and smooth_target_v1 (target averaging/partial adjustment)",
            "index_row": "docs/research/ewma_band_v1.md",
            "difference": ("both tested families band the *output*; a dead-band on the vol *input* has "
                           "a different trade set (it also changes the level held when the input is stale)."),
        },
        "signal_inputs": ["the arm's own instrument close"],
        "data": {"in_repo": ["stress_robustness_v1_daily.csv"], "new_downloads": [],
                 "main_window": "full",
                 "oos": {"evaluable": ["GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"], "note": "closes only"}},
        "plugin": {"model": "targets_from_data", "detail": "the vol series is filtered before vt_of; no engine change"},
        "arms": [
            {"id": "A", "rule": "hold the last accepted vol20 until |vol20_new/vol20_accepted - 1| > 0.20"},
            {"id": "B", "rule": "the same with a 0.35 relative dead-band"},
            {"id": "C", "rule": "hold the last accepted *target weight* until the new target differs by more than 5 pp (the plain-band control)"},
        ],
        "prior": "medium-low: it targets the trim channel that carries 91% of the realised gain, but the band mechanism is already heavily mined.",
    },
]
