"""Cryptographically bound strategy approval manifests for fail-closed runtime."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import inspect
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioStrategy


class ApprovalError(RuntimeError):
    pass


class ApprovalStage(StrEnum):
    SHADOW = "shadow"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"


CURRENT_GATE_POLICY_VERSION = "portfolio-conservative-v1"


@dataclass(frozen=True)
class StrategyApprovalManifest:
    strategy_name: str
    strategy_version: str
    import_path: str
    module_sha256: str
    parameters: dict[str, object]
    market: Market
    symbols: tuple[str, ...]
    maximum_allocation: float
    gate_policy_version: str
    dataset_ids: tuple[str, ...]
    dataset_hashes: tuple[str, ...]
    validation_record_ids: tuple[str, ...]
    holdout_record_ids: tuple[str, ...]
    source_revision: str
    approved_at: datetime
    expires_at: datetime
    approved_stage: ApprovalStage
    approval_status: str = "green"
    archived: bool = False
    schema_version: int = 1
    checksum: str = ""

    def __post_init__(self) -> None:
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Approval timestamps must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("Approval expiry must be after approval time")
        if not 0 < self.maximum_allocation <= 0.60:
            raise ValueError("maximum_allocation must be in (0, 0.60]")
        required = (
            self.strategy_name,
            self.strategy_version,
            self.import_path,
            self.module_sha256,
            self.gate_policy_version,
            self.source_revision,
        )
        if any(not item.strip() for item in required):
            raise ValueError("Approval identity fields must not be empty")
        canonical = tuple(canonical_symbol(symbol, self.market) for symbol in self.symbols)
        if not canonical or len(set(canonical)) != len(canonical):
            raise ValueError("Approval symbols must be non-empty and unique")
        object.__setattr__(self, "symbols", canonical)
        if len(self.dataset_ids) != len(self.dataset_hashes) or not self.dataset_ids:
            raise ValueError("Approval requires matching dataset ids and hashes")
        if not self.validation_record_ids:
            raise ValueError("Approval requires validation evidence")
        if self.approved_stage != ApprovalStage.SHADOW and not self.holdout_record_ids:
            raise ValueError("Paper/canary/live approval requires holdout evidence")

    def unsigned_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("checksum", None)
        payload["market"] = self.market.value
        payload["approved_stage"] = self.approved_stage.value
        payload["approved_at"] = self.approved_at.astimezone(UTC).isoformat()
        payload["expires_at"] = self.expires_at.astimezone(UTC).isoformat()
        return payload

    def calculated_checksum(self) -> str:
        encoded = json.dumps(
            self.unsigned_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def signed(self) -> StrategyApprovalManifest:
        return replace(self, checksum=self.calculated_checksum())

    def to_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "checksum": self.checksum}

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> StrategyApprovalManifest:
        def strings(name: str) -> tuple[str, ...]:
            value = raw.get(name, ())
            return tuple(str(item) for item in value) if isinstance(value, list | tuple) else ()

        parameters = raw.get("parameters", {})
        return cls(
            strategy_name=str(raw.get("strategy_name", "")),
            strategy_version=str(raw.get("strategy_version", "")),
            import_path=str(raw.get("import_path", "")),
            module_sha256=str(raw.get("module_sha256", "")),
            parameters=dict(parameters) if isinstance(parameters, dict) else {},
            market=Market(str(raw.get("market", ""))),
            symbols=strings("symbols"),
            maximum_allocation=float(str(raw.get("maximum_allocation", 0))),
            gate_policy_version=str(raw.get("gate_policy_version", "")),
            dataset_ids=strings("dataset_ids"),
            dataset_hashes=strings("dataset_hashes"),
            validation_record_ids=strings("validation_record_ids"),
            holdout_record_ids=strings("holdout_record_ids"),
            source_revision=str(raw.get("source_revision", "")),
            approved_at=datetime.fromisoformat(str(raw.get("approved_at", ""))),
            expires_at=datetime.fromisoformat(str(raw.get("expires_at", ""))),
            approved_stage=ApprovalStage(str(raw.get("approved_stage", ""))),
            approval_status=str(raw.get("approval_status", "")),
            archived=bool(raw.get("archived", False)),
            schema_version=int(str(raw.get("schema_version", 1))),
            checksum=str(raw.get("checksum", "")),
        )

    @classmethod
    def load_verified(
        cls,
        path: str | Path,
        *,
        expected_checksum: str,
        current_source_revision: str,
        now: datetime | None = None,
    ) -> StrategyApprovalManifest:
        source = Path(path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApprovalError(f"Approval manifest is unreadable: {source}") from exc
        if not isinstance(raw, dict):
            raise ApprovalError("Approval manifest root must be an object")
        try:
            manifest = cls.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise ApprovalError(f"Approval manifest is invalid: {exc}") from exc
        calculated = manifest.calculated_checksum()
        if not expected_checksum or manifest.checksum != expected_checksum:
            raise ApprovalError("Approval checksum does not match configured checksum")
        if manifest.checksum != calculated:
            raise ApprovalError("Approval manifest contents were modified")
        if manifest.approval_status != "green" or manifest.archived:
            raise ApprovalError("Strategy is not GREEN or has been archived")
        if manifest.gate_policy_version != CURRENT_GATE_POLICY_VERSION:
            raise ApprovalError("Approval gate-policy version is not current")
        instant = now or datetime.now(UTC)
        if manifest.expires_at <= instant:
            raise ApprovalError("Strategy approval has expired")
        if not current_source_revision or manifest.source_revision != current_source_revision:
            raise ApprovalError("Approval source revision does not match runtime")
        strategy = manifest.instantiate_strategy()
        if strategy.version != manifest.strategy_version or strategy.market != manifest.market:
            raise ApprovalError("Approved strategy identity does not match implementation")
        return manifest

    def instantiate_strategy(self) -> PortfolioStrategy:
        module_name, separator, class_name = self.import_path.partition(":")
        if not separator:
            raise ApprovalError("import_path must use module:Class syntax")
        try:
            module = importlib.import_module(module_name)
            strategy_type = getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            raise ApprovalError(
                f"Approved strategy cannot be imported: {self.import_path}"
            ) from exc
        source_path = inspect.getsourcefile(strategy_type)
        if source_path is None or _sha256_file(Path(source_path)) != self.module_sha256:
            raise ApprovalError("Approved strategy module hash does not match")
        strategy = strategy_type(dict(self.parameters))
        if not isinstance(strategy, PortfolioStrategy) or strategy.name != self.strategy_name:
            raise ApprovalError("Approved import is not the expected PortfolioStrategy")
        return strategy


@dataclass(frozen=True)
class LiveOperatorApproval:
    operator_id: str
    account_id: str
    manifest_checksums: tuple[str, ...]
    maximum_dollar_exposure: float
    approved_at: datetime
    expires_at: datetime
    nonce: str
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.operator_id.strip() or not self.account_id.strip() or not self.nonce.strip():
            raise ValueError("Operator approval identity must not be empty")
        if not self.manifest_checksums or len(set(self.manifest_checksums)) != len(
            self.manifest_checksums
        ):
            raise ValueError("Operator approval requires unique manifest checksums")
        if self.maximum_dollar_exposure <= 0:
            raise ValueError("Operator approval requires a positive dollar cap")
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Operator approval timestamps must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("Operator approval expiry must follow approval")

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "operator_id": self.operator_id,
            "account_id": self.account_id,
            "manifest_checksums": list(self.manifest_checksums),
            # Canonicalize integers and floats identically across a JSON
            # write/read round trip before computing the HMAC.
            "maximum_dollar_exposure": float(self.maximum_dollar_exposure),
            "approved_at": self.approved_at.astimezone(UTC).isoformat(),
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "nonce": self.nonce,
        }

    def calculated_signature(self, secret: str) -> str:
        if len(secret) < 32:
            raise ValueError("Operator approval HMAC key must contain at least 32 characters")
        payload = json.dumps(self.unsigned_dict(), sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    def signed(self, secret: str) -> LiveOperatorApproval:
        return replace(self, signature=self.calculated_signature(secret))

    def to_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "signature": self.signature}

    @classmethod
    def load_verified(
        cls,
        path: str | Path,
        *,
        secret: str,
        account_id: str,
        manifest_checksums: tuple[str, ...],
        now: datetime | None = None,
    ) -> LiveOperatorApproval:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("root is not an object")
            approval = cls(
                operator_id=str(raw.get("operator_id", "")),
                account_id=str(raw.get("account_id", "")),
                manifest_checksums=tuple(str(item) for item in raw.get("manifest_checksums", ())),
                maximum_dollar_exposure=float(raw.get("maximum_dollar_exposure", 0)),
                approved_at=datetime.fromisoformat(str(raw.get("approved_at", ""))),
                expires_at=datetime.fromisoformat(str(raw.get("expires_at", ""))),
                nonce=str(raw.get("nonce", "")),
                signature=str(raw.get("signature", "")),
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApprovalError("Live operator approval is unreadable or invalid") from exc
        try:
            expected = approval.calculated_signature(secret)
        except ValueError as exc:
            raise ApprovalError("Live operator approval key is missing or too short") from exc
        if not hmac.compare_digest(approval.signature, expected):
            raise ApprovalError("Live operator approval signature is invalid")
        if approval.account_id != account_id:
            raise ApprovalError("Live operator approval account does not match")
        if set(approval.manifest_checksums) != set(manifest_checksums):
            raise ApprovalError("Live operator approval manifest set does not match")
        if approval.expires_at <= (now or datetime.now(UTC)):
            raise ApprovalError("Live operator approval has expired")
        return approval


def module_sha256(strategy_type: type[PortfolioStrategy]) -> str:
    source_path = inspect.getsourcefile(strategy_type)
    if source_path is None:
        raise ValueError("Strategy source file is unavailable")
    return _sha256_file(Path(source_path))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "ApprovalError",
    "ApprovalStage",
    "CURRENT_GATE_POLICY_VERSION",
    "LiveOperatorApproval",
    "StrategyApprovalManifest",
    "module_sha256",
]
