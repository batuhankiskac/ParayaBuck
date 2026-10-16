# research/plugins_d2 — plugin-writing worker for IC41–IC46 and IB25/IB26 (reduced window)

Deliverable: `research/ideas/` plugins for **IC41, IC42, IC43, IC44, IC45, IC46** of
`docs/research/idea_bank_v2.json` and the new **`_RW`** arms of `idea_bank_v1`'s **IB25/IB26**,
exactly as their arm specs word them (parameters frozen, no tuning, no extra arms). **No arm was
screened**: no stage 1, no stage 2, no NAV/CAGR/return, no `REGISTRY.jsonl` line, no gate; the
orchestrator runs the batch after this branch merges. **No data was downloaded** — every input these
arms read is already registered (see `DATA_MANIFEST.json`).

| idea | arms | what it adds | extra data | windows |
|---|---|---|---|---|
| `IC41` | A, B, C | dual-core QLD + SSO, one FIFO book per index, gain-aware routing | Yahoo `SSO` (not added) | **NEEDS_KERNEL** (all three) |
| `IC42` | A, B, C | routing a downsize to the leg with the smaller **TRY-indexed** FIFO fraction (`route_cap_pp` 10 / 5 / 10 + `band_score` indexed) | — | all |
| `IC43` | A, B, C | two-leg **loss netting** on a permitted downsize (`loss_net_frac` 0.25 / 0.50 / 0.25 + a 100k TRY YTD gate) | — | all |
| `IC44` | A, B, C | K's A1u widths keyed to the indexed fraction (`band_score` indexed / indexed + 12 pp step / max) | — | all |
| `IC45` | A, B | permanent GLD sleeve (0.08 / 0.18) funded by 0.90 / 0.80 × production | Yahoo `GLD` (registered) | main `w20041119`; OOS/ETF not applicable |
| `IC46` | A, B | permanent IEF sleeve (0.10 / 0.20) funded by 0.88 / 0.78 × production | Yahoo `IEF` (registered) | main `w20020731`; OOS/ETF not applicable |
| `ib25_RW` | A_RW, B_RW | IB25's SHY off-state sleeve (0.98) and its residual sleeve (`resize_band` 0.10), now with `start = "auto"` | Yahoo `SHY` (registered) | main `w20020731`; OOS/ETF not applicable |
| `IB26_RW` | A_RW, B_RW, C_RW | IB26's curve-conditioned off-state IEF leg (T10Y3M > 1.0 pp / + 60-session DGS10 change / the mirror), `start = "auto"` | FRED `T10Y3M`, `DGS10`, Yahoo `IEF` (registered) | main `w20020731`; OOS/ETF not applicable |

23 arms: **18 runnable, 5 declared `NEEDS_KERNEL`** (`IC41/A`,`B`,`C`, `IC45/C`, `IC46/C`).

## Conventions and readings (fixed before any run)

* **Control = production K.** Every runnable arm declares `rule = "K"`, `leg = "2x"`,
  `cash_yield = "none"`; the production state machine is `fe_signals.r2_state` and the sizing
  `fe_signals.sized_wants`, unchanged, except where an arm's own spec says otherwise (IC45/IC46
  scale it down; IC42–44 hold two books of it). A two-leg arm declares `leg_b`, so the registry
  pins the second instrument.
* **IC42/IC43/IC44's two books (the one underdetermined spec).** The bank's arm text for these
  three ideas fixes only the *fill decision*; it names no weight split. Its own routing idea IB28
  is defined "with QQQ and QLD both held (IB27)" and IC41's `closest_tested` records IB28's
  mechanism as "defined in the bank only for QQQ+QLD", so the account holds **IB27's beta-matched
  QQQ + QLD blend** — leg A the panel's synthetic 2x QLD*, leg B the panel's `1x` — in IB27's
  arm-B form (applied on every session, so the early leg is unlevered QQQ*):
  `beta = 2·min(0.98, f·vt)`, `cash = min(0.98, beta)`, `w_QLD = beta − cash`,
  `w_QQQ = cash − w_QLD`. The books deliver one index exposure at a total weight
  `min(0.98, beta) ≤ 0.98`, which is what the bank's pre-tax note ("a pure lot-selection rule …
  changes nothing") assumes and what keeps every arm inside the account's cap. The framework's own
  `split` probe (a 50/50 weight split of production between the 2x and 1x legs) is an engineering
  fixture for the knobs, not this reading: it delivers only 1.5× production's beta, which would
  swamp the routing effect the arms are about.
* **IC42/IC43/IC44's fill decisions** are the framework's option copy
  (`research/fast_engine2/fe2_opts.py`; `README.md` §1b): `route_cap_pp`, `band_score`,
  `band_step4_pp`, `loss_net_frac` / `loss_net_min_ytd_try`, and `kernel_scope`
  `"after_tax_stage_only"` for IC42/IC43 (only the taxed stages carry the option, the pre-tax
  stages stay the published kernel) and `"all_stages"` for IC44 (its widths change the pre-tax
  trim schedule, and G9/turnover applies).
* **IC45/IC46's 2 pp rebalance band — documented deviation.** The bank's band is a band on the
  *held* sleeve weight; the kernel's resize trigger is the engine's fixed 0.5 pp drift
  (`fe_sim.C0_BAND`; `rules.register_rule` carries three booleans, no width) with K's A1u gain band
  on top, and a target-weight plugin cannot observe the held weight. The arms are therefore
  screened with the bank's fixed weights and the engine's own band — the sleeve's realisations stay
  damped by K's A1u gain band, which is the idea's substance — and no parameter is added to
  compensate. (`ib32`'s C arm and `ib25_2L`'s B_2L document the same limitation from the target
  side.)
* **IC45/C and IC46/C are `NEEDS_KERNEL`.** Their frozen sleeve formulas
  (`0.10·clip(vol20(QLD)/vol20(GLD), 0.5, 2.0)`, `0.08·clip(…, 0.5, 3.0)`) on top of
  `0.88`/`0.90 ×` production hold **more than the account's 0.98 cap** in 44.5% / 58.6% of the
  window's sessions (mean excess 2.6 / 7.4 pp of NAV). The kernel funds the excess with negative
  cash and no interest (`fe2_sim._buy_leg`), i.e. an interest-free loan — the cash-loan boundary
  `framework_v2`/§6.2 names for IC39. Screening them as written would hand them a phantom financing
  advantage; making them runnable needs a loan/interest mechanism in a kernel copy or a *funded*
  variant of the formula, and both change the frozen parameters.
* **IC41 is `NEEDS_KERNEL` in all three arms.** Leg B must be a 2x S&P-500 book (the bank:
  `second_symbol=SSO`, "synthetic 2x from SPY before 2006-06 and the real SSO afterwards in the
  cross-check"). The panel builds the `2x` leg only from the window's own underlying and an
  extra-data leg is held at gearing 1, so a SPY-based 2x book is a new leg construction (a second
  data triple + the levered recurrence in the kernel) — the IC40 boundary. The cheap alternative
  (register a Yahoo `SSO` key, hold the real fund at gearing 1) is rejected: it mixes the fund's own
  expense/tracking into a campaign that uses synthetic legs, and it starts 2006-06-21, dropping the
  2000-02 episode the arm exists to dilute.
* **`_RW` arm names.** `ib25.py`/`IB26.py` and `ib25_2L.py`/`IB26_2L.py` are untouched: the ledger
  pins their registered arms and their `spec_hash`, so changing a spec in place would make
  `--verify-registry` report `spec_drift`. The reduced-window arms are therefore **new arm names in
  new modules**, exactly the bank's arm specs plus `start = "auto"`.
* **Applicability.** An arm whose leg B is an extra series is evaluated on the window derived from
  its own series (`start = "auto"`: the first session every declared input covers, plus one) with K
  re-simulated on the same window as the paired control; the six OOS windows and the 21 ETFs are
  other markets, where those series have no price, so `leg_b_usable` refuses them
  (`NotApplicable`) — the bank's own "SHY/IEF 2002+ / GLD 2004+ → reduced gate" notes. The bank's
  reduced-OOS construction for IB26 (a BOND10* built from the monthly GS10) is a third-instrument
  construction and is not attempted.
* **NaN semantics** (the bank batch's convention, `research/plugins_b1/README.md`): a condition
  whose input is missing is **False** — so a warm-up session or a window without a curve holds cash,
  never a guess. FRED series are read with `ffill=True` (they are absent on non-publication days);
  `T10Y3M`/`DGS10` are read in their own FRED *percent* units, so the bank's "1.0 pp" is the level
  1.0 and its "-0.50 pp over 60 sessions" is a level change of -0.50.
* **The trend vote off** = `on_eff == False` of `fe_signals.r2_state` (E2's 2-of-3 vote *and* the
  early re-entry inactive), the reading `ib25_2L` and `research/two_leg_arms_v1/PREREGISTRATION.md`
  fix; the production target of leg A is exactly zero there, so the off-state sleeves never overlap
  a QLD* position (`check_plugins.py` asserts it).

## Data

Nothing was downloaded and `research/batch_screen/EXTRA_DATA.json` is unchanged: `gld`, `ief`,
`shy`, `t10y3m`, `dgs10` and the panel inputs are all committed and registered.
`DATA_MANIFEST.json` lists each input with its file, source URL, range and SHA-256 (re-hashed from
the files on disk, and re-checkable with `python -m research.batch_screen.extra --verify`).

## Reproduce

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.batch_screen.extra --verify                        # 33 series, PASS
PYTHONPATH=. $R -m research.plugins_d2.check_plugins                          # weight/blend/knob invariants
PYTHONPATH=. $R -m research.batch_screen.smoke --ideas IC42,IC43,IC44,IC45,IC46,ib25_RW,IB26_RW
PYTHONPATH=. $R -m research.batch_screen.smoke --ideas IC41                    # NEEDS_KERNEL, by design
PYTHONPATH=. $R -m research.batch_screen.smoke --ideas IC45 --arms IC45/C      # NEEDS_KERNEL, by design
```

`smoke` reports `FAIL` when an arm of the run is `NEEDS_KERNEL` (its own exit-code rule); the
runnable set is green (`smoke OK (18 arms)`). `check_plugins.py` computes no return, NAV or gate: it
calls `targets` on the same panels `smoke` builds and checks the two-leg invariants `smoke` does
not (finiteness, `[0, CAP]`, `wants_a + wants_b <= CAP`, the blend identity `2·w_QLD + w_QQQ =
beta`, the off-state non-overlap, and that each arm's `kernel_scope` reaches the kernel).
