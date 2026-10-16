# plugins_b3 — nine idea-bank plugins for the batch screen

Branch `research/plugins_b3`, started from `research/batch_screen_v1`. It adds the plugins (and the
data they need) for the idea-bank ids **IB03, IB07, IB11, IB15, IB19, IB23, IB27, IB32, IB38**
exactly as `docs/research/idea_bank_v1.json` specifies them: **27 registered arms**, 21 of them
simulated by the framework's screen, 6 of them (`IB27` ×3, `IB38` ×3) declared `NEEDS_KERNEL` and
refused with a stated reason. Nothing here is simulated, no NAV/CAGR/return of any strategy is
computed anywhere in this branch, and the framework is not edited (except the framework's own data
registry, `research/batch_screen/EXTRA_DATA.json`, which the task asks for).

| idea | arms | what changes vs production K | new data |
|---|---|---|---|
| IB03 | A, B, C | a Keltner channel (`close > EMA_n + k·ATR_n`) replaces DON126 (A), SMA200 (B) or the early leg's SMA50 entry gate (C) | relative high/low series (see §2) |
| IB07 | A, B, C | the sizing vol is the GARCH(1,1) forecast alone (A), `max(vol20, σ)` (B), or the 50/50 blend (C) | — |
| IB11 | A, B, C | the target is tilted by `clip(semiUp_n/semiDown_n, 0.70, 1.00)`, n = 60 (A), 252 (B), 60 on the early leg only (C) | — |
| IB15 | A, B, C | a VRP filter: halve while VRP < 0 (A), `clip(1 + 0.5z, 0.7, 1.1)` (B), a 4th vote `VRP > 0` (C) | `vix` (already registered) |
| IB19 | A, B, C | a US term-year multiplier: 0.85 in years 1-2 (A), in the election year (B), in years 3-4 (C) | — |
| IB23 | A, B, C | copper/gold: a 4th vote on `r(252) > 0` (A), an early-entry gate `r(126) ≥ 0` (B), `×0.75` below the 252-session median (C) | `gold` (GC=F, new); `copper` (HG=F, already registered) |
| IB27 | A, B, C | **NEEDS_KERNEL** — a two-leg (QQQ + QLD) account; the plugin contract holds one instrument | — |
| IB32 | A, B, C | a dead-band on the sizing vol (20 %, 35 %) or on the target (5 pp) | — |
| IB38 | A, B, C | **NEEDS_KERNEL** — tax regime/capital matrix, multi-account splitting, a routed two-book split | — |

Shared code: `research/plugins_b3/helpers.py` (trailing sum/std/median, EMA, Wilder ATR, the R2
state machine with a replaced early gate, the `3-of-4` vote, the production cap). Every arm's
numeric parameters are declared in its `ARMS[...]` spec, so the framework's `spec_hash` freezes
them (`rule` = `K`, `leg` = `2x`, `cash_yield` = `none` throughout).

## 1. How the arms read the idea bank's text (ambiguity decisions)

Each module documents its own readings at the top; the ones the orchestrator should know:

* **IB03** — `EMA_n` is the standard `alpha = 2/(n+1)` average seeded with the first `n` closes;
  `ATR_n` is **Wilder's** ATR (the reference the bank cites), i.e. `TR = max(h−l, |h−prev_c|,
  |l−prev_c|)` seeded with the mean of the first `n` TRs, then `ATR_t = ((n−1)ATR_{t−1}+TR_t)/n`
  (arm B's `EMA50 + 0.5·ATR20` is EMA-50 with ATR-**20**, as written). Arm C replaces the early
  leg's **entry** gate only: the production exit (below SMA50) and the vote override are
  unchanged, so an early position entered above the channel exits below the SMA50. `GSPC_1957_1999`
  is `NotApplicable` exactly as the bank pre-registers (the harness's loader for it is close-only).
* **IB07** — returns are daily simple returns of the arm's own held leg (`data.close`), the seed at
  session 20 is the `ddof = 1` variance of the first 20 returns, and `sigma2_t = ω + 0.10r²_{t−1} +
  0.85σ²_{t−1}` runs forward from there. Before the seed the forecast is NaN and the engine's own
  `vt_of` convention (NaN → `vt = 1.0`) applies, exactly as it does to production's own vol20
  warm-up.
* **IB11** — the semi-deviations use the campaign's V5 convention
  `sqrt(2·252/n · Σ min/max(r,0)²)`; the tilt multiplies the production target (both legs for A/B,
  the half-weight early leg only for C).
* **IB15** — the VRP is `(VIX/100)² − vol20²` with vol20 of the **price** series the vote runs on
  (an index-implied variance against realised index variance). ^VIX starts 1990-01-02, so **M1–M5
  are `NotApplicable`** as the bank declares; `GSPC_1957_1999` is applicable and follows
  `vix_filter_v1`'s convention that a missing VIX leaves the filter inactive.
* **IB19** — the term year is `year % 4` (US election years are the years divisible by 4), so arm A
  is 0.85 in years 1–2, B in the election year, C in years 3–4. **M3/M4/M5 are `NotApplicable`**
  (the cycle is US-specific).
* **IB23** — the ratio is `HG=F / GC=F` (both front-month continuations, aligned by date); arms A/B
  read its 252/126-session trailing return and arm C its level against the trailing 252-session
  median. Both futures start 2000-08-30, so **all six OOS windows are `NotApplicable`** (the bank
  pre-registers an empty OOS set and a reduced gate).
* **IB32** — C's 5 pp output band is implemented inside `targets`, because the engine's rule
  options are four fixed boolean combinations whose band is a 0.5 pp drift band with no
  configurable width; C therefore keeps rule `K` and holds the *raw* target until it moves 5 pp
  from the accepted value.
* **Availability convention.** Where a signal exists only *part* of a window (copper/gold before
  2000-08-30, ^VIX before 1990) the arm keeps the literal window and behaves like the K control
  there — multipliers 1.0, votes falling back to production's 2-of-3, the early gate as
  production's — instead of manufacturing a cash position or a signal out of a data gap. A window
  with **no** signal at all is `NotApplicable`. Both readings are documented at the top of the
  modules that use them (`ib15.py`, `ib23.py`).
* **The production cap binds after every tilt.** `helpers.scaled` re-applies `CAP = 0.98` to a
  tilted target, so IB15 arm B's up-side multiplier (1.1) can never lever the account past the
  production cap (a raw weight above 0.98 means negative cash). Multipliers at or below 1 are
  unaffected.

## 2. Data added

Derived, deterministic, from the committed harness inputs (the very files the framework builds the
panels' prices from), plus one download:

| file | series | source | range | rows |
|---|---|---|---|---|
| `data/historical/plugins_b3_qqq_hl.csv` | `QQQ-HL`, `QQQ-HL-LOW` | `stress_robustness_v1_daily.csv` (QQQ OHLC, Yahoo upstream) | 1999-03-10 → 2026-09-29 | 6,932 |
| `data/historical/plugins_b3_ixic_hl.csv` | `^IXIC-HL(-LOW)` | `ewma_band_v1_index.csv` | 1971-02-05 → 1999-12-31 | 7,304 |
| `data/historical/plugins_b3_gspc_hl.csv` | `^GSPC-HL(-LOW)` | `ewma_band_v1_index.csv` | 1927-12-30 → 1956-12-31 | 7,250 |
| `data/historical/plugins_b3_n225_hl.csv` | `^N225-HL(-LOW)` | `ewma_band_v1_index.csv` | 1965-01-05 → 2026-09-29 | 15,175 |
| `data/historical/plugins_b3_gdaxi_hl.csv` | `^GDAXI-HL(-LOW)` | `ewma_band_v1_index.csv` | 1987-12-30 → 2026-09-29 | 9,799 |
| `data/historical/plugins_b3_ftse_hl.csv` | `^FTSE-HL(-LOW)` | `ewma_band_v1_index.csv` | 1984-01-03 → 2026-09-29 | 10,797 |
| `data/historical/plugins_b3_gc_f.csv` | `GC=F` | Yahoo chart API, `period1=0` | 2000-08-30 → 2026-10-16 | 6,558 |

The columns of the six high/low files are `high_over_close` and `low_over_close`, not absolute
prices. On a real window the panel's price **is** the source file's close, so `price ×
high_over_close` is the literal high (within the 10-significant-digit precision of the framework's
own CSV writer, ≤ 5e-10 relative); inside a bootstrap chunk the panel's close is a reconstructed
path whose level is unrelated to the source session's price, so only the session's scale-free
high/close shape replays correctly. `research/plugins_b3/check_data.py` asserts that alignment on
every applicable window.

Keys registered in `research/batch_screen/EXTRA_DATA.json` (built by
`research/plugins_b3/build_extra.py`, which is idempotent and writes both the registry and
`research/plugins_b3/DATA_MANIFEST.json`): `underlying_relhigh` and `underlying_rellow`
(`{"windows": {main, M1…M5, GSPC_1957_1999, "*"}}`, the framework's every-key-resolves-every-window
convention, cf. `local_index`; the `*` catch-all is the main instrument's shape and is never
reached — the IB03 plugin uses the panel's own OHLC on the ETF windows and refuses
`GSPC_1957_1999` before any `extra` call), and `gold` → `GC=F`.

## 3. Verification on this branch (no performance number anywhere)

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.plugins_b3.build_extra        # rewrites data + registry, idempotent
PYTHONPATH=. $R -m research.plugins_b3.check_data         # high/low alignment per window: PASS
PYTHONPATH=. $R -m research.plugins_b3.check_arms         # 130+ unit checks: PASS
PYTHONPATH=. $R -m research.batch_screen.smoke --ideas ib03,ib07,ib11,ib15,ib19,ib23,ib27,ib32,ib38
PYTHONPATH=. $R -m research.batch_screen.extra --verify   # 44 series: PASS
bash research/batch_screen/tests/run.sh                   # 100 passed
```

* `smoke` (targets only, no metric): **21 of 27 arms OK** on the synthetic panel, the real main
  window, the `M2` OOS window, the `XLK` instrument window and a real bootstrap chunk; the 6
  remaining lines are `NEEDS_KERNEL` (IB27, IB38) by design, which is why the command exits 1.
  `NotApplicable` shows up exactly where §1 says: IB03 on `GSPC_1957_1999`, IB15 on M1–M5, IB23 on
  all six OOS windows (the smoke prints the reason next to each).
* `check_arms` compares every helper and every recursion against a naive per-path loop written from
  the same definition, checks the neutral-knob identities (`ib11` clip 1.0/1.0, `ib19` tilt 1.0,
  `ib32` band 0.0, `ib23` mult 1.0 all reproduce K bit-for-bit; `r2_state_with_entry` with
  production's gate equals `fe_signals.r2_state`; `vote_3_of_4` without the extra vote equals
  production's E2), reconstructs each arm's target series independently, sweeps every arm over the
  6 OOS windows, all 21 ETF windows, the main window, a chunk and the synthetic panel asserting the
  panel shape, finiteness and the `[0, 0.98]` range, and asserts the availability sets and the six
  kernel refusals.
* The framework's own suite passes unchanged (100 tests), including
  `tests/test_extra.py::test_committed_registry_verifies` and the "every key resolves in every
  window kind" contract.

The screen itself is the orchestrator's: this branch deliberately runs neither stage 1 nor stage 2,
writes no `REGISTRY.jsonl` line and no `results/` file, so nothing here can have been tuned on a
result.
