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
"""

from __future__ import annotations

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

    def __post_init__(self):
        self.vol = fe_signals.vol_n(self.legs[self.leg][1])
        self.year = np.array([int(d[:4]) for d in self.days], dtype=np.int64)
        self.month = np.array([int(d[5:7]) for d in self.days], dtype=np.int64)

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


def panel(days, *, price, div, rate, o2, c2, d2, o1=None, c1=None, d1=None, fin=0.0,
          exp_q=EXP_Q, leg=DEFAULT_LEG, window="", high=None, low=None, meta=None):
    """Build a `MarketData`: the 2x leg from the window's published input triple ``(o2, c2, d2)``
    and `fin`/`exp_q`, the 1x leg from the underlying's raw triple (defaults to the same one)."""
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
    return {"rule": rule, "leg": leg, "cash_yield": cash,
            "params": {k: v for k, v in spec.items() if k not in ("rule", "leg", "cash_yield", "needs_kernel")},
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
def call_targets(mod, data: MarketData, arm: dict) -> tuple[np.ndarray, str]:
    """`targets(data, spec)` normalised to ``(wants (P,T) float64, leg)``.

    A per-session leg selection is accepted by the API but is a mechanism the engine cannot
    execute (one instrument per account, see README) -- it raises `NeedsKernel`.
    """
    out = mod.targets(data, arm["params"])
    leg = None
    if isinstance(out, tuple):
        if len(out) != 2:
            raise PluginError("targets() must return wants or (wants, leg)")
        wants, leg = out
    else:
        wants = out
    wants = np.atleast_2d(np.asarray(wants, dtype=np.float64))
    want_shape = data.price.shape
    if wants.shape != want_shape:
        raise PluginError(f"targets() returned {wants.shape}, expected the panel shape {want_shape}")
    if not np.isfinite(wants).all():
        raise PluginError("targets() returned non-finite weights")
    if leg is None:
        leg = arm["leg"]
    elif np.ndim(leg) > 0:
        arr = np.asarray(leg).ravel()
        if arr.size not in (1, want_shape[0] * want_shape[1]):
            raise PluginError(f"targets() returned a leg array of size {arr.size}")
        # a *constant* array is just the leg; a varying one needs a kernel option
        if np.unique(arr).size == 1:
            leg = str(arr[0])
        else:
            raise NeedsKernel(
                "targets() selected the held instrument per session; the account holds one "
                "instrument (no portfolio layer) -- this needs a kernel option"
            )
    if leg not in LEGS:
        raise PluginError(f"targets() returned unknown leg {leg!r}; known: {LEGS}")
    return wants, str(leg)


__all__ = [
    "CASH_YIELDS",
    "DEFAULT_LEG",
    "DEFAULT_RULE",
    "IDEAS_DIR",
    "LEGS",
    "MarketData",
    "NeedsKernel",
    "PluginError",
    "ROOT",
    "arm_key",
    "call_targets",
    "code_hash",
    "idea_kernel_note",
    "idea_path",
    "load_arm",
    "load_idea",
    "panel",
    "resolve_arm",
    "spec_hash",
    "total_return_1x",
]
