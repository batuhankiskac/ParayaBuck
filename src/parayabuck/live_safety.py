from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path


class LiveApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveOperatorApproval:
    """Short-lived HMAC proof bound to account, runtime config, and dollar cap."""

    operator_id: str
    account_id: str
    configuration_digests: tuple[str, ...]
    maximum_dollar_exposure: float
    approved_at: datetime
    expires_at: datetime
    nonce: str
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.operator_id.strip() or not self.account_id.strip() or not self.nonce.strip():
            raise ValueError("Operator approval identity must not be empty")
        if not self.configuration_digests or len(set(self.configuration_digests)) != len(self.configuration_digests):
            raise ValueError("Operator approval requires unique configuration digests")
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
            "configuration_digests": list(self.configuration_digests),
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
        configuration_digests: tuple[str, ...],
        now: datetime | None = None,
    ) -> LiveOperatorApproval:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("root is not an object")
            approval = cls(
                operator_id=str(raw.get("operator_id", "")),
                account_id=str(raw.get("account_id", "")),
                configuration_digests=tuple(str(item) for item in raw.get("configuration_digests", ())),
                maximum_dollar_exposure=float(raw.get("maximum_dollar_exposure", 0)),
                approved_at=datetime.fromisoformat(str(raw.get("approved_at", ""))),
                expires_at=datetime.fromisoformat(str(raw.get("expires_at", ""))),
                nonce=str(raw.get("nonce", "")),
                signature=str(raw.get("signature", "")),
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LiveApprovalError("Live operator approval is unreadable or invalid") from exc
        try:
            expected = approval.calculated_signature(secret)
        except ValueError as exc:
            raise LiveApprovalError("Live operator approval key is missing or too short") from exc
        if not hmac.compare_digest(approval.signature, expected):
            raise LiveApprovalError("Live operator approval signature is invalid")
        if approval.account_id != account_id:
            raise LiveApprovalError("Live operator approval account does not match")
        if set(approval.configuration_digests) != set(configuration_digests):
            raise LiveApprovalError("Live operator approval configuration does not match")
        if approval.expires_at <= (now or datetime.now(UTC)):
            raise LiveApprovalError("Live operator approval has expired")
        return approval


__all__ = ["LiveApprovalError", "LiveOperatorApproval"]
