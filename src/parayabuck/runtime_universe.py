"""Product-membership universe — immutable snapshot of the static equity universe.

Does NOT own strategy math or broker execution: the snapshot binds the
configured equity universe to the runtime's observation membership so
PAPER/LIVE decisions share one membership snapshot.

Threading: the snapshot is immutable; EngineRuntime rebinds it at startup
under its own locks (not duplicated here).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol


@dataclass(frozen=True)
class ActiveProductUniverse:
    """Immutable product-membership snapshot used by PAPER/LIVE decisions.

    ``eligible_symbols`` is the tradable pool and therefore owns
    ``contains``. ``observation_symbols`` is the bounded stream/decision
    membership. Keeping the two sets explicit prevents managed-position pins
    from silently redefining product eligibility.
    """

    market: Market
    eligible_symbols: frozenset[str]
    observation_symbols: tuple[str, ...]
    snapshot_id: str
    config_digest: str = "configured"

    def __post_init__(self) -> None:
        eligible = frozenset(canonical_symbol(symbol, self.market) for symbol in self.eligible_symbols)
        observation = tuple(dict.fromkeys(canonical_symbol(symbol, self.market) for symbol in self.observation_symbols))
        if not eligible:
            raise ValueError("Active product universe requires eligible symbols")
        if not observation:
            raise ValueError("Active product universe requires observation symbols")
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("Active product universe snapshot_id must be non-empty")
        object.__setattr__(self, "eligible_symbols", eligible)
        object.__setattr__(self, "observation_symbols", observation)

    @classmethod
    def configured(
        cls,
        market: Market,
        symbols: tuple[str, ...],
        *,
        snapshot_id: str,
        config_digest: str = "configured",
    ) -> ActiveProductUniverse:
        """Build an explicit static membership snapshot.

        This is still a real, fail-closed ``contains`` adapter; it is not the
        permissive ``allow_static_universe`` guard bypass used by static tests.
        """

        canonical = tuple(dict.fromkeys(canonical_symbol(symbol, market) for symbol in symbols))
        return cls(
            market=market,
            eligible_symbols=frozenset(canonical),
            observation_symbols=canonical,
            snapshot_id=snapshot_id,
            config_digest=config_digest,
        )

    def with_observation_symbols(self, symbols: tuple[str, ...]) -> ActiveProductUniverse:
        """Return the same eligibility snapshot with live stream pins.

        Managed-position/open-order pins may extend observation membership,
        but never become eligible alpha candidates.
        """

        return ActiveProductUniverse(
            market=self.market,
            eligible_symbols=self.eligible_symbols,
            observation_symbols=symbols,
            snapshot_id=self.snapshot_id,
            config_digest=self.config_digest,
        )

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        del timestamp
        return market == self.market and canonical_symbol(symbol, market) in self.eligible_symbols


__all__ = ["ActiveProductUniverse"]
