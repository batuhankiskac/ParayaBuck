"""batch_screen.api -- the idea plugin contract.

An **idea** is a module ``research/ideas/<idea_id>.py`` exposing

    ARMS = {arm_id: spec}                 # spec: fixed parameters, JSON-serialisable
    def targets(data, spec): ...          # vectorised numpy -> per-session target weights

and optionally ``NEEDS_KERNEL = "why"`` when the idea needs an execution mechanism the engine
does not implement (it is registered, never simulated -- see `NeedsKernel`).

`targets` receives a `MarketData` panel whose arrays are all ``(P, T)``: ``P`` paths (``P = 1``
for a real window, a bootstrap chunk otherwise), ``T`` sessions. It must return ``wants``, the
target weight of the account's leg at each session close (decided at the close, filled at the
next open by the engine), or ``(wants, leg)`` where ``leg`` picks the instrument the account
holds. `leg` is constant per arm: a per-session (varying) leg selection is a mechanism the
single-instrument account cannot execute, so it raises `NeedsKernel` -- declare the arm with
``"needs_kernel"`` in its spec and screen it separately.

`screened engine RULE options` come from the spec:

    rule        "K" | "R2" | "C0" | "A1u" | "H2" | a name registered by `register_rule`
    leg         "2x" (default) | "1x"
    cash_yield  "none" (default) | "tbill"

Everything else in the spec is the idea's own fixed parameter, hashed into the registry.

Beyond its own instrument's OHLC the panel offers the **extra series** of
`research/batch_screen/EXTRA_DATA.json` through `data.extra(key[, ffill])` (`^VIX`, FRED yields,
the sector ETFs, GLD/TLT/IEF, the other markets' indices: see `extra.py`), aligned to the panel's
sessions and `NaN` where a session has no value. `targets` may also raise `NotApplicable(reason)`
for a window whose inputs do not exist (`^VIX` before 1990, a European index for `^N225`); the
framework then marks that window (or, on the main window, the whole arm) not evaluable.
"""

from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import EXP_Q

ROOT = Path(__file__).resolve().parents[2]
IDEAS_DIR = ROOT / "research" / "ideas"

#: the legs every window offers (see `leg` below). "2x" is the campaign's synthetic daily-reset
#: 2x leg (the published one); "1x" is the same underlying at gearing 1 with no cost.
LEGS = ("2x", "1x")
DEFAULT_LEG = "2x"
DEFAULT_RULE = "K"
CASH_YIELDS = ("none", "tbill")


class NeedsKernel(Exception):
    """The arm declares an execution mechanism the engine does not implement."""


@dataclass(frozen=True)
class Targets:
    """What `call_targets` normalised a plugin's return value into.

    `wants`/`leg` are the account's main leg (leg A). `wants_b`/`leg_b` describe the second
    instrument of a *two-leg* arm -- one cash pool, two units/FIFO books, one taxpayer -- and are
    `None` for a single-leg arm. `leg_b` is a panel leg (`LEGS`) or a registered extra-data key
    (`EXTRA_DATA.json`); the arm's spec declares it (`"leg_b": "shy"`) and the two must agree.
    """

    wants: np.ndarray
    leg: str = DEFAULT_LEG
    wants_b: "np.ndarray | None" = None
    leg_b: "str | None" = None

    @property
    def two_leg(self) -> bool:
        return self.wants_b is not None


class NotApplicable(Exception):
    """The arm's inputs do not exist for this window.

    `targets` raises it (e.g. `raise NotApplicable("^VIX starts 1990")`); the framework then treats
    the window as not evaluable: stage 1 requires the main window to be applicable, and stage 2
    evaluates the OOS/ETF holdout stages on the applicable windows only (see `README.md` §2).
    """

    def __init__(self, reason: str = ""):
        super().__init__(reason or "not applicable")
        self.reason = str(reason) or "not applicable"


class PluginError(Exception):
    """The idea module does not honour the plugin contract."""


# ----------------------------------------------------------------------------- leg construction
def total_return_1x(q_open, q_close, q_div):
    """The published `fe_signals.synthetic_2x` gearing-2 recurrence at gearing 1, no cost.

    Built from the *same* input triple the window's 2x leg is built from (so both legs of a
    window share the window's own price convention: raw OHLC+dividend on the QQQ main window,
    already-total-return on a bootstrap path, price+zero-dividend on the OOS indices/ETFs):

        close[t] = close[t-1] * (1 + r_cc[t]),   r_cc = (close[t] + div[t]) / close[t-1] - 1
        open[t]  = close[t-1] * (1 + r_on[t]),   r_on = (open[t]  + div[t]) / close[t-1] - 1
    """
    q_open = np.asarray(q_open, dtype=np.float64)
    q_close = np.asarray(q_close, dtype=np.float64)
    q_div = np.asarray(q_div, dtype=np.float64)
    prev = q_close[..., :-1]
    r_cc = (q_close[..., 1:] + q_div[..., 1:]) / prev - 1
    r_on = (q_open[..., 1:] + q_div[..., 1:]) / prev - 1
    close = np.concatenate([np.ones(q_close.shape[:-1] + (1,)), np.cumprod(1 + r_cc, axis=-1)], axis=-1)
    opn = np.concatenate([np.ones(q_close.shape[:-1] + (1,)), close[..., :-1] * (1 + r_on)], axis=-1)
    return opn, close


# ----------------------------------------------------------------------------- the panel
@dataclass
class MarketData:
    """One window (or one bootstrap chunk) as a plugin sees it. Every array is ``(P, T)``.

    `legs` holds every leg the window offers, each ``(open, close)``; `leg` is the leg this arm
    holds, and `open`/`close`/`vol` are that leg's. `price` is the underlying's *price* close --
    the series the production vote runs on (QQQ's price close with no dividends on the main
    window, the bootstrap path's reconstructed price index, an index/ETF close on the OOS and
    instrument windows). `div`/`rate` are per-session and are what the 2x leg's financing and
    the total-return conventions are built from.

    `days` labels the window's sessions; `column_days` is the same label per *column* of every
    `(P, T)` array (they are the same array on a real window; on a bootstrap chunk `column_days`
    carries the chunk's first label one extra time, because the arrays have one artificial
    leading base row). `year`/`month` are built on `column_days`.
    """

    days: np.ndarray
    window: str
    price: np.ndarray
    div: np.ndarray
    rate: np.ndarray
    legs: dict
    leg: str = DEFAULT_LEG
    high: np.ndarray | None = None
    low: np.ndarray | None = None
    meta: dict = field(default_factory=dict)
    #: extra-series context (see `extra()`): the pool calendar the registered series are aligned
    #: to, the `(P, T)` pool index of each session, and the window key the key->series map is
    #: resolved with. All three are None on a plain window (the panel's own days are the pool and
    #: the identity alignment is used).
    extra_days: np.ndarray | None = None
    extra_index: np.ndarray | None = None
    extra_window: str | None = None
    #: the day label of every column of the `(P, T)` arrays (see `column_days`); a chunk whose
    #: arrays are not the window's own sessions must pass it (the OOS pooled bootstrap does)
    col_days: np.ndarray | None = None

    def __post_init__(self):
        self.vol = fe_signals.vol_n(self.legs[self.leg][1])
        # Every `(P, T)` array of the panel is labelled by `days`, except on a bootstrap chunk:
        # its market arrays carry one artificial leading base row, so `days` (the drawn session
        # list) is one shorter than the arrays and *column* `c >= 1` is `days[c - 1]` (column 0
        # repeats the first drawn session -- `boot._extra_index`'s convention). `column_days` is
        # that mapping, and the calendar arrays are built on it so that `year`/`month` are (T,)
        # like every other panel array.
        n = int(self.price.shape[1])
        cd = None if self.col_days is None else np.asarray(self.col_days)
        if cd is None:
            if n == self.days.size:
                cd = self.days
            elif n == self.days.size + 1:
                cd = np.concatenate([np.repeat(self.days[:1], 1), self.days])
            else:
                raise ValueError(f"panel {self.window!r}: {n} price columns and "
                                 f"{self.days.size} days -- pass col_days")
        if cd.size != n:
            raise ValueError(f"panel {self.window!r}: {cd.size} column days for {n} columns")
        self.column_days = cd
        self.year = np.array([int(d[:4]) for d in self.column_days], dtype=np.int64)
        self.month = np.array([int(d[5:7]) for d in self.column_days], dtype=np.int64)
        self._extra = {}
        self.extra_log = []

    @property
    def open(self):
        return self.legs[self.leg][0]

    @property
    def close(self):
        return self.legs[self.leg][1]

    @property
    def P(self):
        return self.price.shape[0]

    @property
    def T(self):
        return self.price.shape[1]

    def extra(self, key: str, ffill: bool = False) -> np.ndarray:
        """A registered extra series (`research/batch_screen/EXTRA_DATA.json`), `(P, T)`.

        Values are aligned to the panel's own sessions by date, and inside a bootstrap chunk to the
        session that produced each column: the market path's column `t` is the close of the drawn
        source session `extra_index[p, t - 1]` (column 0 is the chunk's artificial base row and
        repeats the first drawn session), so `extra` is exactly as wide as the market arrays.
        `NaN` where the series has no value on that date; `ffill=True` carries the last known value
        forward within each path instead. Raises `batch_screen.extra.ExtraError` for an unknown key
        or a missing/undeclared file.
        """
        from . import extra as _ext

        ck = (key, bool(ffill))
        if ck not in self._extra:
            arr = _ext.aligned(key, self.extra_window or self.window, self.days,
                               self.extra_days, self.extra_index, self.price.shape)
            if ffill:
                arr = _ext.forward_fill(arr)
            self._extra[ck] = arr
        arr = self._extra[ck]
        # logged on every call, cache hit or not: `smoke` reads this to tell a plugin author which
        # keys the arm touched and how much of each series the panel carries
        self.extra_log.append({"key": key, "ffill": bool(ffill),
                               "nan_share": float(np.isnan(arr).mean())})
        return arr

    def leg_series(self, key: str) -> tuple:
        """`(open, close)` of a leg-B instrument: a panel leg, or a registered extra series.

        An extra series carries one column (its adjusted close), so it is used for **both** the
        fill price and the close of that leg -- an explicit convention, stated where it is used,
        not an interpolation. No forward fill: a session the series does not reach is `NaN`, and
        the framework refuses the window (`NotApplicable`) rather than invent a price.
        """
        if key in self.legs:
            return self.legs[key]
        v = self.extra(key)
        return v, v

    def leg_b_usable(self, key: str, i0: int, i1: int) -> tuple:
        """`(ok, reason)`: is `key`'s series finite on every session the engine reads?

        The engine reads opens/closes on `[i0, i1]` and the previous close on `i0 - 1`, so a
        series that starts after the window begins (or has an interior gap, e.g. a FRED series
        that is not published daily) makes the window not applicable. `reason` names the first
        session the series does not reach -- the label of the *column* (`column_days`), which is
        what a bootstrap chunk's extra series is indexed by.
        """
        opn, _cls = self.leg_series(key)
        opn = np.asarray(opn, dtype=np.float64)
        lo = max(0, int(i0) - 1)
        hi = int(i1)
        row = opn if opn.ndim == 1 else opn[0]
        bad = ~np.isfinite(row[lo : hi + 1])
        if not bad.any():
            return True, ""
        first = int(np.argmax(bad)) + lo
        finite = np.flatnonzero(np.isfinite(row))
        span = (f"{self.column_days[finite[0]]}..{self.column_days[finite[-1]]}" if finite.size
                else "empty in this window")
        return False, (f"the leg-B instrument {key!r} has no price on {self.column_days[first]} "
                       f"(the series covers {span} of this window)")


def panel(days, *, price, div, rate, o2, c2, d2, o1=None, c1=None, d1=None, fin=0.0,
          exp_q=EXP_Q, leg=DEFAULT_LEG, window="", high=None, low=None, meta=None,
          extra_days=None, extra_index=None, extra_window=None, col_days=None):
    """Build a `MarketData`: the 2x leg from the window's published input triple ``(o2, c2, d2)``
    and `fin`/`exp_q`, the 1x leg from the underlying's raw triple (defaults to the same one).

    `extra_days`/`extra_index`/`extra_window` are the extra-series context [`MarketData.extra`]:
    a bootstrap chunk passes the pool calendar it draws from, the `(P, T)` pool index of each
    drawn session and the window key the key->series map resolves through.
    """
    o2, c2, d2 = (np.atleast_2d(np.asarray(x, dtype=np.float64)) for x in (o2, c2, d2))
    o1 = o2 if o1 is None else np.atleast_2d(np.asarray(o1, dtype=np.float64))
    c1 = c2 if c1 is None else np.atleast_2d(np.asarray(c1, dtype=np.float64))
    d1 = d2 if d1 is None else np.atleast_2d(np.asarray(d1, dtype=np.float64))
    so, sc = fe_signals.synthetic_2x(o2, c2, d2, np.atleast_2d(np.asarray(rate, dtype=np.float64)) + fin, exp_q)
    so1, sc1 = total_return_1x(o1, c1, d1)
    return MarketData(
        days=np.asarray(days),
        window=window,
        price=np.atleast_2d(np.asarray(price, dtype=np.float64)),
        div=np.atleast_2d(np.asarray(div, dtype=np.float64)),
        rate=np.atleast_2d(np.asarray(rate, dtype=np.float64)),
        legs={"2x": (so, sc), "1x": (so1, sc1)},
        leg=leg,
        high=None if high is None else np.atleast_2d(np.asarray(high, dtype=np.float64)),
        low=None if low is None else np.atleast_2d(np.asarray(low, dtype=np.float64)),
        meta=dict(meta or {}),
        extra_days=None if extra_days is None else np.asarray(extra_days),
        extra_index=None if extra_index is None else np.asarray(extra_index, dtype=np.int64),
        extra_window=extra_window,
        col_days=None if col_days is None else np.asarray(col_days),
    )


# ----------------------------------------------------------------------------- plugin loading
def idea_path(idea_id: str) -> Path:
    return IDEAS_DIR / f"{idea_id}.py"


def code_hash(idea_id: str) -> str:
    """SHA-256 of the idea module's bytes (the code the registry pins)."""
    return hashlib.sha256(idea_path(idea_id).read_bytes()).hexdigest()


def spec_hash(spec: dict) -> str:
    """SHA-256 of the canonical JSON of the arm's fixed parameters."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def arm_key(idea_id: str, arm_id: str) -> str:
    return f"{idea_id}/{arm_id}"


def load_idea(idea_id: str):
    """Load and validate `research/ideas/<idea_id>.py`."""
    path = idea_path(idea_id)
    if not path.exists():
        raise PluginError(f"no such idea module: {path}")
    spec = importlib.util.spec_from_file_location(f"research_ideas_{idea_id}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    arms = getattr(mod, "ARMS", None)
    if not isinstance(arms, dict) or not arms:
        raise PluginError(f"{path}: ARMS must be a non-empty dict{{arm_id: spec}}")
    if not callable(getattr(mod, "targets", None)):
        raise PluginError(f"{path}: targets(data, spec) is missing")
    for arm_id, aspec in arms.items():
        if not isinstance(arm_id, str) or not isinstance(aspec, dict):
            raise PluginError(f"{path}: ARMS[{arm_id!r}] must be a dict")
        try:
            json.dumps(aspec, sort_keys=True)
        except TypeError as exc:
            raise PluginError(f"{path}: ARMS[{arm_id!r}] is not JSON-serialisable: {exc}") from exc
    return mod


def idea_kernel_note(mod) -> str | None:
    """The module's module-level `NEEDS_KERNEL` declaration, if any."""
    note = getattr(mod, "NEEDS_KERNEL", None)
    return str(note) if note else None


def resolve_arm(mod, arm_id: str) -> dict:
    """The arm's spec, with the engine options filled in and validated. Raises `NeedsKernel`."""
    from .rules import RULES  # local import: rules imports api

    spec = dict(mod.ARMS[arm_id])
    note = spec.get("needs_kernel") or idea_kernel_note(mod)
    if note:
        raise NeedsKernel(f"{note}")
    rule = spec.get("rule", DEFAULT_RULE)
    if rule not in RULES:
        raise PluginError(f"arm {arm_id!r}: unknown rule {rule!r}; known: {sorted(RULES)}")
    leg = spec.get("leg", DEFAULT_LEG)
    if leg not in LEGS:
        raise PluginError(f"arm {arm_id!r}: unknown leg {leg!r}; known: {LEGS}")
    cash = spec.get("cash_yield", "none")
    if cash not in CASH_YIELDS:
        raise PluginError(f"arm {arm_id!r}: unknown cash_yield {cash!r}; known: {CASH_YIELDS}")
    leg_b = spec.get("leg_b")
    if leg_b is not None:
        if not isinstance(leg_b, str):
            raise PluginError(f"arm {arm_id!r}: leg_b must be a string (a leg or an extra key), "
                              f"got {type(leg_b).__name__}")
        if leg_b not in leg_b_keys():
            raise PluginError(f"arm {arm_id!r}: unknown leg_b {leg_b!r}; known: {leg_b_keys()}")
        if leg_b == leg:
            raise PluginError(f"arm {arm_id!r}: leg_b {leg_b!r} is leg A's own instrument")
    start = spec.get("start")
    if start is not None:
        if start != "auto":
            try:
                datetime.date.fromisoformat(str(start))
            except ValueError:
                raise PluginError(f"arm {arm_id!r}: start must be \"auto\" or YYYY-MM-DD, "
                                  f"got {start!r}") from None
    inputs = spec.get("inputs", [])
    if not isinstance(inputs, (list, tuple)) or not all(isinstance(k, str) for k in inputs):
        raise PluginError(f"arm {arm_id!r}: inputs must be a list of extra-data key strings")
    from . import extra as _ext

    for k in inputs:
        if k not in _ext.keys():
            raise PluginError(f"arm {arm_id!r}: unknown input {k!r}; known: {sorted(_ext.keys())}")
    return {"rule": rule, "leg": leg, "leg_b": leg_b, "two_leg": leg_b is not None,
            "cash_yield": cash, "start": start, "inputs": tuple(inputs),
            # `leg_b` stays in `params` (unlike the engine options `rule`/`leg`/`cash_yield`): a
            # two-leg plugin has to *return* it, so its `targets(data, spec)` must be able to read
            # the instrument the arm was registered with.
            "params": {k: v for k, v in spec.items()
                       if k not in ("rule", "leg", "cash_yield", "needs_kernel")},
            "spec": spec}


def load_arm(idea_id: str, arm_id: str) -> dict:
    """Resolve one arm of one idea, with its module attached under ``"mod"``.

    This is the object every driver takes: `stages.*` and `boot.*` read `arm["mod"]`,
    `arm["rule"]`, `arm["leg"]`, `arm["params"]` and `arm["id"]`.
    """
    mod = load_idea(idea_id)
    arm = resolve_arm(mod, arm_id)
    arm.update({"id": arm_key(idea_id, arm_id), "idea": idea_id, "arm_id": arm_id, "mod": mod})
    return arm


# ----------------------------------------------------------------------------- targets call
def _leg_or_error(leg, default, want_shape, what):
    """A leg label from a plugin: `None` -> the spec's, a constant array -> that label."""
    if leg is None:
        return default
    if np.ndim(leg) > 0:
        arr = np.asarray(leg).ravel()
        if arr.size not in (1, want_shape[0] * want_shape[1]):
            raise PluginError(f"targets() returned a {what} leg array of size {arr.size}")
        # a *constant* array is just the leg; a varying one needs a kernel option
        if np.unique(arr).size == 1:
            return str(arr[0])
        raise NeedsKernel(
            f"targets() selected the {what} instrument per session; the account holds a fixed "
            "set of instruments per arm -- a per-session selection needs a kernel option"
        )
    return str(leg)


def _panel_weights(wants, want_shape, what):
    """One leg's target panel, validated against the panel shape."""
    wants = np.atleast_2d(np.asarray(wants, dtype=np.float64))
    if wants.shape != want_shape:
        raise PluginError(f"targets() returned {what} {wants.shape}, expected the panel shape "
                          f"{want_shape}")
    if not np.isfinite(wants).all():
        raise PluginError(f"targets() returned non-finite {what} weights")
    return wants


def call_targets(mod, data: MarketData, arm: dict) -> Targets:
    """`targets(data, spec)` normalised to a `Targets`.

    Accepted return values:

    * `wants` (P, T) -- the arm's `leg`, one instrument;
    * `(wants, leg)` -- the same, with the held leg named;
    * `(wants_a, wants_b, leg_b)` -- **two legs**: leg A (`arm["leg"]` or the spec's) and leg B (a
      panel leg or a registered extra key), simulated in one cash pool by
      `research.fast_engine2.fe2_sim.simulate2`. The arm's spec must declare `leg_b` and it must
      agree with the returned one (the registry pins the spec, so the declaration is what the
      trial is registered with).

    A per-session instrument selection is still refused (`NeedsKernel`): an arm's instruments are
    fixed, only their weights vary.
    """
    out = mod.targets(data, arm["params"])
    wb = leg_b = None
    leg = None
    if isinstance(out, tuple):
        if len(out) == 2:
            wants, leg = out
        elif len(out) == 3:
            wants, wb, leg_b = out
        else:
            raise PluginError("targets() must return wants, (wants, leg) or "
                              "(wants_a, wants_b, leg_b)")
    else:
        wants, leg = out, None
    want_shape = data.price.shape
    wants = _panel_weights(wants, want_shape, "leg-A")
    leg = _leg_or_error(leg, arm["leg"], want_shape, "held")
    if leg not in LEGS:
        raise PluginError(f"targets() returned unknown leg {leg!r}; known: {LEGS}")
    # `resolve_arm` puts the declaration at the top level; a hand-built arm may carry it only in
    # its params (the spec), which is what the registry pins.
    spec_leg_b = arm.get("leg_b", (arm.get("params") or {}).get("leg_b"))
    if wb is None and leg_b is None:
        if spec_leg_b is not None:
            raise PluginError(f"arm {arm.get('id')!r} declares leg_b = {spec_leg_b!r} but targets() "
                              "returned a single-leg target panel")
        return Targets(wants=wants, leg=str(leg))
    if wb is None or leg_b is None:
        raise PluginError("targets() must return (wants_a, wants_b, leg_b) for a two-leg arm")
    if spec_leg_b is None:
        raise PluginError("targets() returned a second leg but the arm's spec has no leg_b; add "
                          'leg_b to ARMS[arm_id] so the registry pins the instrument')
    wb = _panel_weights(wb, want_shape, "leg-B")
    leg_b = _leg_or_error(leg_b, spec_leg_b, want_shape, "leg-B")
    if str(leg_b) != spec_leg_b:
        raise PluginError(f"targets() named leg_b = {leg_b!r} but the spec declares "
                          f"{spec_leg_b!r}")
    if str(leg_b) == str(leg):
        raise PluginError(f"leg_b = {leg_b!r} is leg A's own instrument; one instrument has one "
                          "FIFO book per taxpayer (two books of one instrument need a kernel option)")
    return Targets(wants=wants, leg=str(leg), wants_b=wb, leg_b=str(leg_b))


def leg_b_keys() -> list:
    """Every instrument an arm may declare as `leg_b`: the panel legs and the extra keys."""
    from . import extra

    return sorted(list(LEGS) + list(extra.keys()))



__all__ = [
    "CASH_YIELDS",
    "DEFAULT_LEG",
    "DEFAULT_RULE",
    "IDEAS_DIR",
    "LEGS",
    "MarketData",
    "NeedsKernel",
    "Targets",
    "NotApplicable",
    "PluginError",
    "ROOT",
    "arm_key",
    "call_targets",
    "code_hash",
    "idea_kernel_note",
    "idea_path",
    "leg_b_keys",
    "load_arm",
    "load_idea",
    "panel",
    "resolve_arm",
    "spec_hash",
    "total_return_1x",
]
