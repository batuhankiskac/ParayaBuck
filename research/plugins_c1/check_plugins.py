"""research/plugins_c1/check_plugins.py -- mechanical unit checks for this branch's plugins.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_c1.check_plugins

No simulation, no NAV, no CAGR, no return of the strategy, no gate, no registry write: it calls
each arm's `targets` on the real panels (main, the six OOS windows, the XLK instrument window, the
clean bootstrap chunk and three OOS pooled-bootstrap chunks, built by `plugins_b2.check_plugins`)
and checks the transforms against independent re-derivations.  Only counts and target-weight
comparisons are printed.
"""

from __future__ import annotations

import sys

import numpy as np

from research.fast_engine import fe_signals, fe_sim
from research.plugins_b2.check_plugins import call, panels, production_wants
from research.plugins_c1 import c1_lib

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def same(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return a.shape == b.shape and bool(np.array_equal(a, b, equal_nan=a.dtype.kind == "f"))


def naive_trailing(a, n, f):
    out = np.full(a.shape, np.nan)
    for t in range(n - 1, a.shape[1]):
        out[:, t] = f(a[:, t - n + 1:t + 1])
    return out


def main() -> int:
    pan = panels()
    names = sorted(pan)

    # ---- the library reduces to the engine's own functions
    for k in names:
        p = pan[k]
        e2 = fe_signals.e2_up(p.price)
        comps = fe_signals.early_components(p.price)
        ref = fe_signals.r2_state(p.price, e2, comps)
        got = c1_lib.r2_state_gated(p.price, e2, np.ones(p.price.shape, dtype=bool), comps)
        check(f"r2_state_gated(gate=True) == r2_state [{k}]", same(ref[0], got[0]) and same(ref[1], got[1]))
        check(f"vote_2_of_3(TSM252) == e2_up [{k}]",
              same(c1_lib.vote_2_of_3(p.price, fe_signals.tsm_up_price(p.price)), e2))
        check(f"sized_wants_vt(vt_of) == sized_wants [{k}]",
              same(c1_lib.sized_wants_vt(*ref, fe_signals.vt_of(p.vol)), fe_signals.sized_wants(*ref, p.vol)))

    rng = np.random.default_rng(7)
    a = rng.standard_normal((3, 400))
    check("trailing_max == naive", same(c1_lib.trailing_max(a, 252), naive_trailing(a, 252, lambda w: w.max(1))))
    check("trailing_min == naive", same(c1_lib.trailing_min(a, 126), naive_trailing(a, 126, lambda w: w.min(1))))
    tm = naive_trailing(a, 252, lambda w: np.sort(w, axis=1)[:, :13].mean(1))
    check("trailing_tail_mean == naive sort", bool(np.allclose(c1_lib.trailing_tail_mean(a, 252, 13), tm,
                                                              equal_nan=True, rtol=0, atol=1e-15)))
    lab = np.array(["2020-12-%02d" % d for d in (1, 2, 3, 4, 7, 8, 9, 10, 11, 14, 15, 16, 17, 18, 21, 22,
                                                23, 24, 28, 29, 30, 31)] + ["2021-01-04"])
    check("december_tail_mask == fe_sim.december_tail",
          all(same(c1_lib.december_tail_mask(lab, n), fe_sim.december_tail(list(lab), 0, len(lab) - 1, n))
              for n in (10, 20)))

    # ---- applicability
    na = {}
    for idea in ("IC01", "IC06", "IC11", "IC16", "IC21", "IC26", "IC47", "IC60"):
        for arm in ("A", "B", "C"):
            for k in names:
                r = call(idea, arm, pan[k])
                if isinstance(r, tuple):
                    na.setdefault((idea, arm), []).append(k)
                else:
                    ok = (r.shape == pan[k].price.shape and np.isfinite(r).all()
                          and r.min() >= 0 and r.max() <= 0.98 + 1e-12)
                    check(f"{idea}/{arm} finite, in [0, 0.98] [{k}]", ok)
    for (idea, arm), ks in sorted(na.items()):
        print(f"  info  {idea}/{arm} NotApplicable on {ks}")
    check("IC06 n/a set == {GSPC_1957_1999, M2} (+ their chunks)",
          all(sorted(na.get(("IC06", a), [])) == ["boot:M2", "oos:GSPC_1957_1999", "oos:M2"] for a in "ABC"))
    check("IC21 n/a set == {GSPC_1957_1999, M1, M2}",
          all({"oos:GSPC_1957_1999", "oos:M1", "oos:M2"} <= set(na.get(("IC21", a), []))
              and not {"main", "oos:M3", "oos:M4", "oos:M5"} & set(na.get(("IC21", a), [])) for a in "ABC"))
    others = [k for k in na if k[0] not in ("IC06", "IC21")]
    check("every other arm applicable everywhere", not others, str(others))

    # ---- per-idea transforms
    m = pan["main"]
    prod = production_wants(m)
    o2, c2 = m.legs["2x"]
    o1, c1 = m.legs["1x"]
    s2 = c1_lib.trailing_sum(np.c_[np.full((1, 1), np.nan), np.log(o2[:, 1:] / c2[:, :-1])], 252)
    s1 = c1_lib.trailing_sum(np.c_[np.full((1, 1), np.nan), np.log(o1[:, 1:] / c1[:, :-1])], 252)
    sl = slice(253, None)
    print(f"  info  IC06 overnight-sum sign agreement 2x vs 1x on main: "
          f"{100 * np.mean((s2[:, sl] > 0) == (s1[:, sl] > 0)):.2f} % of {s2[:, sl].size} sessions")

    for k in names:
        p = pan[k]
        base = production_wants(p)
        c = call("IC26", "C", p)
        check(f"IC26/C == production [{k}]", same(c, base))
        for arm in ("A", "B"):
            w = call("IC26", arm, p)
            check(f"IC26/{arm} <= production, >= (1-coef) x production [{k}]",
                  bool(np.all(w <= base) and np.all(w >= base * (0.85 if arm == "A" else 0.70) - 1e-15)))
        e2 = fe_signals.e2_up(p.price)
        on, fac = fe_signals.r2_state(p.price, e2)
        early = on & ~e2
        for arm in ("A", "B", "C"):
            w = call("IC47", arm, p)
            check(f"IC47/{arm} == production off the early state [{k}]", same(w[~early], base[~early]))
            check(f"IC47/{arm} early weight in [lo, 1] x production-full [{k}]",
                  bool(np.all(w[early] <= 2 * base[early] + 1e-15) and np.all(w[early] >= base[early] * (
                      0.5 if arm == "A" else 0.25 if arm == "B" else 1.0) - 1e-15)))
        mask = c1_lib.december_tail_mask(c1_lib.account_labels(p), 10)
        for arm in ("A", "B", "C"):
            w = call("IC60", arm, p)
            mk = mask if arm != "C" else c1_lib.december_tail_mask(c1_lib.account_labels(p), 20)
            check(f"IC60/{arm} == production outside the December tail [{k}]",
                  same(w[:, ~mk], base[:, ~mk]) and bool(np.all(w[:, mk] >= base[:, mk])))

    # IC60's account calendar in the clean bootstrap is the fixed tax calendar (one label short)
    b = pan["boot:main"]
    lab = c1_lib.account_labels(b)
    check("IC60 account labels on the clean bootstrap == the tax calendar",
          len(b.days) == b.T - 1 and same(lab[:-1], np.asarray(b.days, dtype="<U10")))
    yrs = sorted({d[:4] for d in lab[c1_lib.december_tail_mask(lab, 10)]})
    print(f"  info  IC60 December-tail years on the bootstrap calendar: {yrs}")

    # IC26: the factor peaks at the autumn equinox
    from research.ideas import IC26
    doy = np.arange(1, 366)
    nf = np.clip(IC26.night_change(doy, 30) / IC26._max_year_change(30), 0, 1)
    peak = int(doy[np.argmax(nf)])
    check("IC26 night factor peaks within 10 days of the autumn equinox (doy 265)", abs(peak - 265) <= 10,
          f"peak doy {peak}")
    check("IC26 night factor is 0 from the spring equinox to the summer solstice",
          bool(np.all(nf[80:172] == 0)))

    # IC16: the change uses pp, the trigger set is non-empty and not everywhere on main
    from research.ideas import IC16, IC21
    for mod, key, thr in ((IC16, "A", 0.75), (IC21, "A", 0.40)):
        chg = mod.change(m, mod.ARMS[key])
        frac = float(np.mean(chg[:, 253:] > thr))
        check(f"{mod.__name__.split('.')[-1]} trigger fires on 0 < share < 50 % of main sessions",
              0 < frac < 0.5, f"{100 * frac:.1f} %")

    print(f"\n{'ALL PASS' if not FAILED else 'FAILED: ' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
