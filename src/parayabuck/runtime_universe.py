"""Product-membership universe — immutable snapshot plus fail-closed loader.

Does NOT own strategy math, broker execution or refresh scheduling: the
loader binds an already-validated scanner artifact to its eligible pool so
PAPER/LIVE decisions share one membership snapshot. Refresh scheduling and
retry backoff live in ``runtime.py`` (``_UNIVERSE_RETRY_BACKOFF_SECONDS``).

Threading: the snapshot is immutable; EngineRuntime rebuilds it between
refreshes under its own locks (not duplicated here).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol


@dataclass(frozen=True)
class ActiveProductUniverse:
    """Immutable product-membership snapshot used by PAPER/LIVE decisions.

    ``eligible_symbols`` is the scanner's broad tradable pool and therefore
    owns ``contains``. ``observation_symbols`` is the bounded stream/decision
    membership. Keeping the two sets explicit prevents stream capacity from
    silently redefining product eligibility.
    """

    market: Market
    eligible_symbols: frozenset[str]
    observation_symbols: tuple[str, ...]
    snapshot_id: str
    selection_timestamp: datetime | None = None
    data_timestamp: datetime | None = None
    config_digest: str = "configured"
    scanner_version: str = "configured"

    def __post_init__(self) -> None:
        eligible = frozenset(canonical_symbol(symbol, self.market) for symbol in self.eligible_symbols)
        observation = tuple(dict.fromkeys(canonical_symbol(symbol, self.market) for symbol in self.observation_symbols))
        if not eligible:
            raise ValueError("Active product universe requires eligible symbols")
        if not observation:
            raise ValueError("Active product universe requires observation symbols")
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("Active product universe snapshot_id must be non-empty")
        for timestamp in (self.selection_timestamp, self.data_timestamp):
            if timestamp is not None and timestamp.tzinfo is None:
                raise ValueError("Active product universe timestamps must be timezone-aware")
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
        """Build an explicit static/fallback membership snapshot.

        This is still a real, fail-closed ``contains`` adapter; it is not the
        permissive ``allow_static_universe`` guard bypass used by static tests.
        Runtime entry halts continue to decide whether a fallback may open
        new positions.
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
        """Return the same PIT eligibility snapshot with live stream pins.

        Managed-position/open-order pins may extend observation membership,
        but never become scanner-eligible alpha candidates.
        """

        return ActiveProductUniverse(
            market=self.market,
            eligible_symbols=self.eligible_symbols,
            observation_symbols=symbols,
            snapshot_id=self.snapshot_id,
            selection_timestamp=self.selection_timestamp,
            data_timestamp=self.data_timestamp,
            config_digest=self.config_digest,
            scanner_version=self.scanner_version,
        )

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        del timestamp
        return market == self.market and canonical_symbol(symbol, market) in self.eligible_symbols


def load_active_product_universe(
    path: str | Path,
    *,
    market: Market,
    expected_observation_symbols: tuple[str, ...],
    expected_config_digest: str,
) -> ActiveProductUniverse:
    """Load the exact active artifact already selected for runtime streaming.

    The existing scanner validator owns freshness, candidate-pool, strategy
    provenance and capacity policy. This second, fail-closed read binds that
    validated observation membership to its eligible pool and rejects a file
    that changed between validation and composition.
    """

    artifact_path = Path(path)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Active product universe payload must be an object")
        if payload.get("status") != "success":
            raise RuntimeError("Active product universe status is not success")
        config_digest = str(payload.get("config_digest", ""))
        if config_digest != expected_config_digest:
            raise RuntimeError("Active product universe config digest changed after validation")
        scanner_version = str(payload.get("scanner_version", ""))
        if not scanner_version:
            raise RuntimeError("Active product universe scanner version is missing")
        selection_timestamp = datetime.fromisoformat(str(payload["selection_timestamp"]))
        data_timestamp = datetime.fromisoformat(str(payload["data_timestamp"]))
        if selection_timestamp.tzinfo is None or data_timestamp.tzinfo is None:
            raise RuntimeError("Active product universe timestamps must be timezone-aware")
        raw_eligible = payload["selected_symbols"]
        raw_observation = payload["observation_set"]
        if not isinstance(raw_eligible, list) or not isinstance(raw_observation, list):
            raise RuntimeError("Active product universe symbol sets must be lists")
        eligible_ordered = tuple(canonical_symbol(str(symbol), market) for symbol in raw_eligible)
        observation = tuple(canonical_symbol(str(symbol), market) for symbol in raw_observation)
        if not eligible_ordered or len(eligible_ordered) != len(set(eligible_ordered)):
            raise RuntimeError("Active product universe eligible symbols are empty or duplicated")
        if not observation or len(observation) != len(set(observation)):
            raise RuntimeError("Active product universe observation symbols are empty or duplicated")
        if not set(observation).issubset(eligible_ordered):
            raise RuntimeError("Active product universe observation symbols are outside the eligible pool")
        expected = tuple(dict.fromkeys(canonical_symbol(symbol, market) for symbol in expected_observation_symbols))
        if observation != expected:
            raise RuntimeError("Active product universe changed after observation membership validation")
    except RuntimeError:
        raise
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Active product universe could not be loaded: {exc}") from exc

    identity = {
        "market": market.value,
        "eligible_symbols": eligible_ordered,
        "observation_symbols": observation,
        "selection_timestamp": selection_timestamp.astimezone(UTC).isoformat(),
        "data_timestamp": data_timestamp.astimezone(UTC).isoformat(),
        "scanner_version": scanner_version,
        "config_digest": config_digest,
    }
    snapshot_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ActiveProductUniverse(
        market=market,
        eligible_symbols=frozenset(eligible_ordered),
        observation_symbols=observation,
        snapshot_id=snapshot_id,
        selection_timestamp=selection_timestamp,
        data_timestamp=data_timestamp,
        config_digest=config_digest,
        scanner_version=scanner_version,
    )


__all__ = ["ActiveProductUniverse", "load_active_product_universe"]
