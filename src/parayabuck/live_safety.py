from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import stat
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from parayabuck.config import DrawdownLiquidationConfig, read_credential


class LiveApprovalError(RuntimeError):
    pass


# The approval HMAC key is public while it still holds the tracked template
# value from .env.example (or any other "replace_with..." template text), so
# such a key can never sign or verify a LIVE approval.
APPROVAL_KEY_PLACEHOLDER_PREFIX = "replace_with"
APPROVAL_KEY_MIN_LENGTH = 32
# "k" * 32 passes the length rule; a random key of this length has far more
# distinct characters, so a repeated-character key is refused (S2-8-2).
APPROVAL_KEY_MIN_DISTINCT_CHARACTERS = 10
# Operator-owned approval documents are a few hundred bytes; anything larger
# is refused before it is parsed (S2-8-5).
APPROVAL_DOCUMENT_MAX_BYTES = 64 * 1024


def check_approval_key(secret: str) -> None:
    """Raise ValueError unless the approval HMAC key is usable (length, placeholder, entropy)."""
    if len(secret) < APPROVAL_KEY_MIN_LENGTH:
        raise ValueError(f"Operator approval HMAC key must contain at least {APPROVAL_KEY_MIN_LENGTH} characters")
    if secret.strip().lower().startswith(APPROVAL_KEY_PLACEHOLDER_PREFIX):
        raise ValueError("Operator approval HMAC key is the .env.example placeholder; set a real random key")
    if len(set(secret)) < APPROVAL_KEY_MIN_DISTINCT_CHARACTERS:
        raise ValueError(
            f"Operator approval HMAC key must use at least {APPROVAL_KEY_MIN_DISTINCT_CHARACTERS} distinct characters"
        )


def _read_regular_bytes(path: str | os.PathLike[str]) -> bytes:
    """Bytes of a small regular file. Symlinks, FIFOs and oversized files raise OSError/ValueError.

    The start path reads the approval documents with the same guards the
    display path uses, so a FIFO cannot block a LIVE start (S2-8-5).
    """
    # O_NOFOLLOW refuses a symlink and O_NONBLOCK keeps a FIFO from blocking the open.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(os.fspath(path), flags)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError("not a regular file")
        raw = handle.read(APPROVAL_DOCUMENT_MAX_BYTES + 1)
    if len(raw) > APPROVAL_DOCUMENT_MAX_BYTES:
        raise ValueError("larger than the approval document limit")
    return raw


def _unsigned_hmac(secret: str, unsigned: dict[str, object]) -> str:
    check_approval_key(secret)
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _signatures_equal(left: str, right: str) -> bool:
    """Constant-time equality for any str: compare_digest rejects non-ASCII str."""
    return hmac.compare_digest(left.encode("utf-8", "surrogatepass"), right.encode("utf-8", "surrogatepass"))


# --- Separate LIVE credentials (docs/live_readiness_v1.md B3) ---------------
#
# PAPER and LIVE never share key names: LIVE reads only the ALPACA_LIVE_*
# pair and PAPER only the ALPACA_* pair, so a LIVE engine can never start on
# a PAPER key (or the reverse) by falling back to the other mode's names.
PAPER_API_KEY_ENV = "ALPACA_API_KEY"
PAPER_SECRET_KEY_ENV = "ALPACA_SECRET_KEY"
LIVE_API_KEY_ENV = "ALPACA_LIVE_API_KEY"
LIVE_SECRET_KEY_ENV = "ALPACA_LIVE_SECRET_KEY"


def credential_env_names(*, live: bool) -> tuple[str, str]:
    """The (key, secret) environment names of one mode; never the other's."""
    return (LIVE_API_KEY_ENV, LIVE_SECRET_KEY_ENV) if live else (PAPER_API_KEY_ENV, PAPER_SECRET_KEY_ENV)


def mode_credentials(*, live: bool, env: Mapping[str, str | None] | None = None) -> tuple[str, str]:
    """The stripped (key, secret) pair of one mode.

    Each name is read from ``$CREDENTIALS_DIRECTORY/<name>`` (systemd
    credentials) first and from ``env`` (default os.environ) second.
    """
    source: Mapping[str, str | None] = os.environ if env is None else env
    key_name, secret_name = credential_env_names(live=live)
    return read_credential(key_name, source), read_credential(secret_name, source)


# Alpaca account numbers of paper accounts start with "PA"; paper API key ids
# start with "PK" and live key ids with "AK". Either positive marker decides;
# no marker at all is "unknown".
ACCOUNT_ENVIRONMENT_PAPER = "paper"
ACCOUNT_ENVIRONMENT_LIVE = "live"
ACCOUNT_ENVIRONMENT_UNKNOWN = "unknown"


def classify_account_environment(account_number: str, api_key: str) -> str:
    """Paper, live or unknown from the account endpoint's number and the key id.

    Paper evidence: a ``PA`` account number or a ``PK`` key id. Live evidence:
    an ``AK`` key id, or a non-empty account number that is not ``PA``.
    Conflicting or missing evidence classifies as unknown.
    """
    number = str(account_number or "").strip().upper()
    key = str(api_key or "").strip().upper()
    paper = number.startswith("PA") or key.startswith("PK")
    live = key.startswith("AK") or (bool(number) and not number.startswith("PA"))
    if paper and not live:
        return ACCOUNT_ENVIRONMENT_PAPER
    if live and not paper:
        return ACCOUNT_ENVIRONMENT_LIVE
    return ACCOUNT_ENVIRONMENT_UNKNOWN


def verify_account_environment(
    *,
    live: bool,
    account_number: str,
    api_key: str,
    expected_account_id: str | None = None,
    account_id: str | None = None,
) -> None:
    """Fail closed when the broker account is not the mode's environment.

    LIVE requires the account endpoint to report a live account (a non-``PA``
    account number) and no paper marker on the key. PAPER is refused only on
    unambiguous live evidence (a live ``AK`` key id with no ``PA`` account
    number), so PAPER behaviour is otherwise unchanged.

    ``expected_account_id`` (``broker.expected_account_id``) pins the account:
    when set, the broker account number must equal it, or the broker
    ``account_id`` when the caller supplies one. ``None`` skips the pin. The
    pin applies in PAPER and LIVE alike and fails closed when no identifier
    is available to compare.
    """
    environment = classify_account_environment(account_number, api_key)
    if live and (environment != ACCOUNT_ENVIRONMENT_LIVE or not str(account_number or "").strip()):
        raise LiveApprovalError(
            f"LIVE start refused: the configured {LIVE_API_KEY_ENV} does not resolve to a live Alpaca account "
            f"(account environment: {environment})"
        )
    number = str(account_number or "").strip().upper()
    if not live and str(api_key or "").strip().upper().startswith("AK") and not number.startswith("PA"):
        raise LiveApprovalError(f"PAPER start refused: the configured {PAPER_API_KEY_ENV} is a LIVE key")
    pinned = str(expected_account_id or "").strip()
    if pinned:
        mode = "LIVE" if live else "PAPER"
        observed = {str(value).strip() for value in (account_number, account_id) if str(value or "").strip()}
        if not observed:
            raise LiveApprovalError(
                f"{mode} start refused: broker.expected_account_id is set but the broker account id could not be read"
            )
        if pinned not in observed:
            raise LiveApprovalError(
                f"{mode} start refused: the broker account does not match broker.expected_account_id "
                f"(pinned account is not the account the credentials resolve to)"
            )


# --- Sticky operator halt (cli `halt` / `resume`) ---------------------------
#
# A marker file in the runtime data directory that refuses every automatic
# and manual engine start until `parayabuck resume` removes it. Any existing
# marker, however unreadable, counts as a halt (fail closed).
HALT_MARKER_FILENAME = "OPERATOR_HALT"
HALT_MARKER_MAX_BYTES = 64 * 1024


def operator_halt_marker_path(runtime_dir: str | os.PathLike[str]) -> Path:
    """The halt marker inside one runtime data directory."""
    return Path(runtime_dir) / HALT_MARKER_FILENAME


def operator_halt_active(marker: str | os.PathLike[str]) -> bool:
    """True while the halt marker exists (a broken symlink counts too)."""
    return os.path.lexists(os.fspath(marker))


def read_operator_halt(marker: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Parsed halt record, or ``None`` when no marker exists.

    A marker that cannot be parsed still halts; it is reported with an
    ``unreadable`` reason instead of being ignored.
    """
    path = Path(marker)
    if not os.path.lexists(os.fspath(path)):
        return None
    unreadable = {"reason": "unreadable halt marker", "created_at": None, "user": None}
    try:
        # O_NOFOLLOW refuses a symlink and O_NONBLOCK keeps a FIFO from
        # blocking the open; fstat then insists on a regular file.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(os.fspath(path), flags)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return unreadable
            raw = handle.read(HALT_MARKER_MAX_BYTES + 1)
        if len(raw) > HALT_MARKER_MAX_BYTES:
            return unreadable
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return unreadable
    return data if isinstance(data, dict) else unreadable


def write_operator_halt(marker: str | os.PathLike[str], *, reason: str, user: str) -> dict[str, Any]:
    """Create or replace the halt marker and return the record written."""
    from parayabuck.utils.io import atomic_write

    record = {
        "reason": str(reason or "").strip() or "operator halt",
        "created_at": datetime.now(UTC).isoformat(),
        "user": str(user or "").strip() or "unknown",
    }
    path = Path(marker)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def clear_operator_halt(marker: str | os.PathLike[str]) -> bool:
    """Remove the halt marker. Returns False when there was none to remove."""
    path = Path(marker)
    if not os.path.lexists(os.fspath(path)):
        return False
    path.unlink()
    return True


@dataclass(frozen=True)
class LiveStartupProof:
    """Short-lived, immutable proof passed from the web arm to the runtime.

    The web layer verifies the interactive account confirmation and creates
    this neutral value before consuming its in-process arm token.  The runtime
    independently checks the account, credential fingerprint, configuration
    digests, and freshness before it reaches any broker mutation boundary.
    """

    account_id: str
    credential_fingerprint: str
    configuration_digests: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    # "operator_approval": the interactive arm plus the short-lived operator
    # approval file. "standing": an unattended start authorized by the
    # standing LIVE authorization (verified again by the runtime).
    authorization: str = "operator_approval"

    def __post_init__(self) -> None:
        if self.authorization not in {"operator_approval", "standing"}:
            raise ValueError("Live startup proof authorization must be operator_approval or standing")
        if not self.account_id.strip() or not self.credential_fingerprint.strip():
            raise ValueError("Live startup proof identity must not be empty")
        if not self.configuration_digests or any(not digest.strip() for digest in self.configuration_digests):
            raise ValueError("Live startup proof requires configuration digests")
        if len(set(self.configuration_digests)) != len(self.configuration_digests):
            raise ValueError("Live startup proof configuration digests must be unique")
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Live startup proof timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("Live startup proof expiry must follow issuance")

    def is_fresh(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return self.issued_at <= current < self.expires_at


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
    # The trading host the approval was issued on (S2-5-6). Signed when set; a
    # caller that passes its host refuses an approval without a matching one.
    trading_host: str = ""
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.operator_id.strip() or not self.account_id.strip() or not self.nonce.strip():
            raise ValueError("Operator approval identity must not be empty")
        if not self.configuration_digests or len(set(self.configuration_digests)) != len(self.configuration_digests):
            raise ValueError("Operator approval requires unique configuration digests")
        if not math.isfinite(self.maximum_dollar_exposure) or self.maximum_dollar_exposure <= 0:
            raise ValueError("Operator approval requires a positive dollar cap")
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Operator approval timestamps must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("Operator approval expiry must follow approval")

    def unsigned_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "operator_id": self.operator_id,
            "account_id": self.account_id,
            "configuration_digests": list(self.configuration_digests),
            "maximum_dollar_exposure": float(self.maximum_dollar_exposure),
            "approved_at": self.approved_at.astimezone(UTC).isoformat(),
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "nonce": self.nonce,
        }
        if self.trading_host:
            document["trading_host"] = self.trading_host
        return document

    def calculated_signature(self, secret: str) -> str:
        return _unsigned_hmac(secret, self.unsigned_dict())

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
        trading_host: str | None = None,
        now: datetime | None = None,
    ) -> LiveOperatorApproval:
        """Verified approval. ``trading_host`` (the caller's host identity) must match when given."""
        try:
            raw = json.loads(_read_regular_bytes(path).decode("utf-8"))
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
                trading_host=str(raw.get("trading_host", "")),
                signature=str(raw.get("signature", "")),
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LiveApprovalError("Live operator approval is unreadable or invalid") from exc
        try:
            expected = approval.calculated_signature(secret)
        except ValueError as exc:
            raise LiveApprovalError(f"Live operator approval key is unusable: {exc}") from exc
        if not _signatures_equal(approval.signature, expected):
            raise LiveApprovalError("Live operator approval signature is invalid")
        if approval.account_id != account_id:
            raise LiveApprovalError("Live operator approval account does not match")
        if set(approval.configuration_digests) != set(configuration_digests):
            raise LiveApprovalError("Live operator approval configuration does not match")
        if trading_host is not None and not _signatures_equal(approval.trading_host, trading_host):
            raise LiveApprovalError("Live operator approval was issued for a different trading host")
        current = now or datetime.now(UTC)
        if approval.approved_at > current:
            raise LiveApprovalError("Live operator approval is not yet valid")
        if approval.expires_at <= current:
            raise LiveApprovalError("Live operator approval has expired")
        return approval


# --- Single use of an interactive approval (S2-5-6) -------------------------
#
# A manual approval starts LIVE once. The runtime records its nonce after the
# start has committed; a later start with the same approval file is refused
# and the operator must run authorize-live again. Standing authorizations are
# reusable by design and never recorded here.

APPROVAL_CONSUMED_FILENAME = "live_approval_consumed.json"
APPROVAL_CONSUMED_MAX_ENTRIES = 256


def approval_consumed_path(operator_approval_path: str | os.PathLike[str]) -> Path:
    """The consumed-approval record, next to the operator approval file."""
    return Path(operator_approval_path).with_name(APPROVAL_CONSUMED_FILENAME)


def _consumed_approval_nonces(operator_approval_path: str | os.PathLike[str]) -> list[str]:
    try:
        raw = json.loads(_read_regular_bytes(approval_consumed_path(operator_approval_path)).decode("utf-8"))
    except FileNotFoundError:
        return []
    nonces = raw.get("nonces") if isinstance(raw, dict) else None
    if not isinstance(nonces, list) or not all(isinstance(item, str) for item in nonces):
        raise ValueError("consumed-approval record is malformed")
    return nonces


def approval_nonce_consumed(operator_approval_path: str | os.PathLike[str], nonce: str) -> bool:
    """True when this approval already started LIVE, or the record cannot be read (fail closed)."""
    try:
        return nonce in _consumed_approval_nonces(operator_approval_path)
    except (OSError, ValueError):
        return True


def record_approval_consumed(operator_approval_path: str | os.PathLike[str], nonce: str) -> None:
    """Persist that an approval started LIVE. Raises when the record cannot be written."""
    from parayabuck.utils.io import atomic_write

    nonces = [item for item in _consumed_approval_nonces(operator_approval_path) if item != nonce]
    nonces.append(nonce)
    document = {"nonces": nonces[-APPROVAL_CONSUMED_MAX_ENTRIES:]}
    atomic_write(
        approval_consumed_path(operator_approval_path),
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )


# --- Standing LIVE authorization (docs/live_readiness_v1.md B2) -------------
#
# An explicit, owner-created, time-boxed (at most 30 days) authorization that
# lets the designated trading host start LIVE unattended after a reboot or a
# deploy. It is HMAC-signed with the operator approval key and bound to the
# Alpaca account id, the live configuration digests, a fingerprint of the
# whole loaded configuration, the hard dollar cap and the trading host. Any
# change to one of them invalidates it, and the engine then waits for a
# manual re-arm exactly as without it.

STANDING_AUTHORIZATION_MAX_DAYS = 30
STANDING_AUTHORIZATION_FILENAME = "live_standing_authorization.json"
STANDING_AUTHORIZATION_KIND = "standing_live_authorization"
STANDING_AUTHORIZATION_EXPIRY_WARNING = timedelta(days=3)


def standing_authorization_path(operator_approval_path: str) -> Path | None:
    """The standing authorization file, next to the operator approval file."""
    if not str(operator_approval_path or "").strip():
        return None
    return Path(operator_approval_path).with_name(STANDING_AUTHORIZATION_FILENAME)


# Config sections that decide what the bot trades, risks or submits. Only these
# are fingerprinted: an unrelated edit (app, alerting, observability, backup,
# backtest, the placeholder data block) must not void a standing LIVE
# authorization. Adding or removing a section here changes every fingerprint once.
CONFIG_FINGERPRINT_SECTIONS: tuple[str, ...] = (
    "runtime",
    "alpaca",
    "broker",
    "universe",
    "strategy",
    "risk",
    "live",
    "engine",
    "execution",
    "signal_cross_check",
    "intent_watchdog",
)


def config_fingerprint(config: Any) -> str:
    """Digest of the trading-relevant configuration sections (see CONFIG_FINGERPRINT_SECTIONS).

    The default-off ``risk.drawdown_liquidation`` block is left out while it
    equals its disabled default, so the fingerprint of an unchanged
    configuration is the same before and after the brake was added.
    """
    document: dict[str, Any] = {name: asdict(getattr(config, name)) for name in CONFIG_FINGERPRINT_SECTIONS}
    risk = document.get("risk")
    if isinstance(risk, dict) and risk.get("drawdown_liquidation") == asdict(DrawdownLiquidationConfig()):
        risk.pop("drawdown_liquidation")
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


STANDING_REVOCATION_FILENAME = "live_standing_revoked.json"


def standing_revocation_path(standing_path: str | os.PathLike[str]) -> Path:
    """The revocation record, next to the standing authorization file."""
    return Path(standing_path).with_name(STANDING_REVOCATION_FILENAME)


def _revoked_standing_digests(standing_path: str | os.PathLike[str]) -> frozenset[str]:
    """SHA-256 digests of revoked standing files; raises when the record is unreadable."""
    try:
        raw = json.loads(_read_regular_bytes(standing_revocation_path(standing_path)).decode("utf-8"))
    except FileNotFoundError:
        return frozenset()
    digests = raw.get("revoked_sha256") if isinstance(raw, dict) else None
    if not isinstance(digests, list) or not all(isinstance(item, str) for item in digests):
        raise ValueError("standing revocation record is malformed")
    return frozenset(digests)


def revoke_standing_authorization(path: str | os.PathLike[str]) -> bool:
    """Delete the standing authorization and tombstone its exact bytes.

    A copy restored from backup is byte-identical, so its digest stays refused
    until a new authorization (with a new nonce) is written (S2-8-4). Returns
    False when there was nothing to revoke. An unreadable file is not deleted.
    """
    from parayabuck.utils.io import atomic_write

    target = Path(path)
    if not os.path.lexists(os.fspath(target)):
        return False
    digest = hashlib.sha256(_read_regular_bytes(target)).hexdigest()
    revoked = sorted(_revoked_standing_digests(target) | {digest})
    atomic_write(
        standing_revocation_path(target),
        json.dumps({"revoked_sha256": revoked}, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    target.unlink()
    return True


@dataclass(frozen=True)
class StandingLiveAuthorization:
    """Owner opt-in for unattended LIVE starts on the trading host (<= 30 days)."""

    operator_id: str
    account_id: str
    configuration_digests: tuple[str, ...]
    config_fingerprint: str
    maximum_dollar_exposure: float
    trading_host: str
    approved_at: datetime
    expires_at: datetime
    nonce: str
    signature: str = ""

    def __post_init__(self) -> None:
        identity = (self.operator_id, self.account_id, self.nonce, self.config_fingerprint, self.trading_host)
        if not all(str(value).strip() for value in identity):
            raise ValueError("Standing LIVE authorization identity must not be empty")
        if not self.configuration_digests or len(set(self.configuration_digests)) != len(self.configuration_digests):
            raise ValueError("Standing LIVE authorization requires unique configuration digests")
        if not math.isfinite(self.maximum_dollar_exposure) or self.maximum_dollar_exposure <= 0:
            raise ValueError("Standing LIVE authorization requires a positive dollar cap")
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Standing LIVE authorization timestamps must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("Standing LIVE authorization expiry must follow approval")
        if self.expires_at - self.approved_at > timedelta(days=STANDING_AUTHORIZATION_MAX_DAYS):
            raise ValueError(f"Standing LIVE authorization may last at most {STANDING_AUTHORIZATION_MAX_DAYS} days")

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "kind": STANDING_AUTHORIZATION_KIND,
            "operator_id": self.operator_id,
            "account_id": self.account_id,
            "configuration_digests": list(self.configuration_digests),
            "config_fingerprint": self.config_fingerprint,
            "maximum_dollar_exposure": float(self.maximum_dollar_exposure),
            "trading_host": self.trading_host,
            "approved_at": self.approved_at.astimezone(UTC).isoformat(),
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "nonce": self.nonce,
        }

    def calculated_signature(self, secret: str) -> str:
        return _unsigned_hmac(secret, self.unsigned_dict())

    def signed(self, secret: str) -> StandingLiveAuthorization:
        return replace(self, signature=self.calculated_signature(secret))

    def to_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "signature": self.signature}

    def remaining(self, now: datetime | None = None) -> timedelta:
        return self.expires_at - (now or datetime.now(UTC))

    @classmethod
    def read_signed(cls, path: str | Path, *, secret: str) -> StandingLiveAuthorization:
        """Parse the file and verify its signature (no binding or expiry checks)."""
        try:
            raw_bytes = _read_regular_bytes(path)
            raw = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(raw, dict) or raw.get("kind") != STANDING_AUTHORIZATION_KIND:
                raise ValueError("not a standing LIVE authorization")
            authorization = cls(
                operator_id=str(raw.get("operator_id", "")),
                account_id=str(raw.get("account_id", "")),
                configuration_digests=tuple(str(item) for item in raw.get("configuration_digests", ())),
                config_fingerprint=str(raw.get("config_fingerprint", "")),
                maximum_dollar_exposure=float(raw.get("maximum_dollar_exposure", 0)),
                trading_host=str(raw.get("trading_host", "")),
                approved_at=datetime.fromisoformat(str(raw.get("approved_at", ""))),
                expires_at=datetime.fromisoformat(str(raw.get("expires_at", ""))),
                nonce=str(raw.get("nonce", "")),
                signature=str(raw.get("signature", "")),
            )
        except FileNotFoundError as exc:
            raise LiveApprovalError("No standing LIVE authorization exists") from exc
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LiveApprovalError("Standing LIVE authorization is unreadable or invalid") from exc
        try:
            expected = authorization.calculated_signature(secret)
        except ValueError as exc:
            raise LiveApprovalError(f"Operator approval key is unusable: {exc}") from exc
        if not _signatures_equal(authorization.signature, expected):
            raise LiveApprovalError("Standing LIVE authorization signature is invalid")
        try:
            revoked = _revoked_standing_digests(path)
        except (OSError, ValueError) as exc:
            raise LiveApprovalError("Standing LIVE authorization revocation record is unreadable") from exc
        if hashlib.sha256(raw_bytes).hexdigest() in revoked:
            raise LiveApprovalError("Standing LIVE authorization was revoked")
        return authorization

    def verify_binding(
        self,
        *,
        configuration_digests: tuple[str, ...],
        config_fingerprint: str,
        hard_dollar_cap: float,
        trading_host: str,
        account_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Raise ``LiveApprovalError`` unless every binding still holds."""
        current = now or datetime.now(UTC)
        if self.approved_at > current:
            raise LiveApprovalError("Standing LIVE authorization is not yet valid")
        if self.expires_at <= current:
            raise LiveApprovalError("Standing LIVE authorization has expired")
        if set(self.configuration_digests) != set(configuration_digests):
            raise LiveApprovalError("Standing LIVE authorization does not match the strategy configuration")
        if not _signatures_equal(self.config_fingerprint, config_fingerprint):
            raise LiveApprovalError("Standing LIVE authorization does not match the current settings")
        if not math.isclose(self.maximum_dollar_exposure, float(hard_dollar_cap), rel_tol=0.0, abs_tol=1e-9):
            raise LiveApprovalError("Standing LIVE authorization does not match live.hard_dollar_cap")
        if not _signatures_equal(self.trading_host, trading_host):
            raise LiveApprovalError("Standing LIVE authorization was issued for a different trading host")
        if account_id is not None and self.account_id != account_id:
            raise LiveApprovalError("Standing LIVE authorization account does not match")

    @classmethod
    def load_verified(
        cls,
        path: str | Path,
        *,
        secret: str,
        configuration_digests: tuple[str, ...],
        config_fingerprint: str,
        hard_dollar_cap: float,
        trading_host: str,
        account_id: str | None = None,
        now: datetime | None = None,
    ) -> StandingLiveAuthorization:
        authorization = cls.read_signed(path, secret=secret)
        authorization.verify_binding(
            configuration_digests=configuration_digests,
            config_fingerprint=config_fingerprint,
            hard_dollar_cap=hard_dollar_cap,
            trading_host=trading_host,
            account_id=account_id,
            now=now,
        )
        return authorization


__all__ = [
    "ACCOUNT_ENVIRONMENT_LIVE",
    "ACCOUNT_ENVIRONMENT_PAPER",
    "ACCOUNT_ENVIRONMENT_UNKNOWN",
    "LIVE_API_KEY_ENV",
    "HALT_MARKER_FILENAME",
    "LIVE_SECRET_KEY_ENV",
    "PAPER_API_KEY_ENV",
    "PAPER_SECRET_KEY_ENV",
    "STANDING_AUTHORIZATION_EXPIRY_WARNING",
    "STANDING_AUTHORIZATION_FILENAME",
    "STANDING_AUTHORIZATION_MAX_DAYS",
    "LiveApprovalError",
    "LiveOperatorApproval",
    "LiveStartupProof",
    "StandingLiveAuthorization",
    "approval_nonce_consumed",
    "check_approval_key",
    "classify_account_environment",
    "clear_operator_halt",
    "config_fingerprint",
    "credential_env_names",
    "mode_credentials",
    "operator_halt_active",
    "operator_halt_marker_path",
    "read_operator_halt",
    "record_approval_consumed",
    "revoke_standing_authorization",
    "standing_authorization_path",
    "verify_account_environment",
    "write_operator_halt",
]
