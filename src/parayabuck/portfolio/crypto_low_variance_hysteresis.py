from __future__ import annotations

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_low_variance import CryptoLowVariance1dV2
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight


def hysteresis_should_retain(rank: int | None, in_universe: bool, retention_rank: int = 4) -> bool:
    """Pure helper: should a held symbol be retained under hysteresis?

    Held symbol stays if currently scanner-eligible and rank <= retention_rank.
    rank is 1-indexed (1 = best vol). None = not ranked / insufficient data.
    """
    if not in_universe:
        return False
    if rank is None:
        return False
    return rank <= retention_rank


class CryptoLowVarianceHysteresis1dV1(CryptoLowVariance1dV2):
    """Tournament winner T1 — hysteresis low-variance (research frozen, PAPER).

    Alpha ranking is EXACTLY the validated T0 control (22d total realized
    volatility, 23 consecutive bars, lowest first). Only the portfolio
    retention changes at each 96h rebalance:

    - Existing held symbol stays if scanner-eligible AND current rank <= 4.
    - Otherwise it exits (ineligible or rank > 4).
    - Empty slots are filled from rank 1 upward.
    - Max 2 positions, equal weight, 96h.

    This halves turnover (8.72 → 4.00) and fees (-53%) and doubles net
    return (5.25% → 10.54%) and Sharpe (0.311 → 0.601) on the frozen
    2021-04-01 → 2026-07-31 window under identical PIT/costs/folds, passing
    all 13 hard gates (see docs/crypto_candidate_tournament_validation.md).

    Math is frozen for research reproducibility (no subsequent alpha
    changes). Promoted to PAPER as an active product strategy 2026-08
    (see docs/crypto_t1_promotion.md / audit script
    scripts/audit_crypto_t1_promotion_parity.py). Select
    ``crypto_low_variance_hysteresis_v1`` in PAPER config to trade it.
    """

    name = "crypto_low_variance_hysteresis_v1"
    version = "1.0.0"
    market = Market.CRYPTO
    timeframe = "1d"
    display_name = "Low Variance Hysteresis v1 (T1)"
    supported_timeframes = ("1d",)
    default_rebalance_hours = 96
    rebalance_policy_declared = "hours:96"
    cadence_label = "96h"
    RETENTION_RANK = 4

    def _validate_parameters(self) -> None:
        if "hysteresis_rank_threshold" in self.parameters:
            raise ValueError("hysteresis_rank_threshold is not a tunable parameter; retention rank is frozen at 4")
        if "retention_rank" in self.parameters:
            raise ValueError("retention_rank is not a tunable parameter; retention rank is frozen at 4")
        self.parameters.setdefault("rebalance_hours", self.default_rebalance_hours)
        super()._validate_parameters()

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        top_n = self.param_int("top_n", 2)
        ranked_scored = self._score_candidates(context)
        ranked = [sym for _sigma, sym in ranked_scored]
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_hysteresis")
        rank_map = {sym: i + 1 for i, sym in enumerate(ranked)}
        eligible_set = {sym for sym in ranked if context.in_universe(sym)}
        held = {sym for sym, weight in context.holdings.items() if weight > 1e-12}
        retained: list[str] = []
        for symbol in held:
            rank = rank_map.get(symbol)
            in_univ = symbol in eligible_set
            if hysteresis_should_retain(rank, in_univ, retention_rank=self.RETENTION_RANK):
                retained.append(symbol)
        retained.sort(key=lambda s: (rank_map.get(s, 999), s))
        retained = retained[:top_n]
        selected: list[str] = list(retained)
        for symbol in ranked:
            if len(selected) >= top_n:
                break
            if symbol not in selected:
                selected.append(symbol)
        selected = selected[:top_n]
        if not selected:
            return self._cash_portfolio(context, "hysteresis_empty")
        weight = 1.0 / len(selected)
        targets = tuple(TargetWeight(sym, weight, reason="t1_hysteresis_low_var") for sym in sorted(selected))
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(w.weight for w in targets)),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(ranked),
                "selected_symbols": tuple(selected),
                "retained": tuple(retained),
                "hypothesis": "t1 hysteresis low-var retention rank<=4",
                "explanations": {
                    sym: {
                        "alpha_rank": rank_map.get(sym),
                        "retained": sym in retained,
                        "target_weight": weight,
                        "current_weight": float(context.holdings.get(sym, 0.0)),
                    }
                    for sym in selected
                },
            },
        )


__all__ = ["CryptoLowVarianceHysteresis1dV1", "hysteresis_should_retain"]
