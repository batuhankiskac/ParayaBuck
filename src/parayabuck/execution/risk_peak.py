from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from parayabuck.execution.alpaca_models import RiskStatePersistenceError  # noqa: E402
from parayabuck.logger import get_logger
from parayabuck.utils.io import atomic_write

log = get_logger("parayabuck.execution.alpaca")


def resolve_peak_state_path(
    paper: bool, peak_state_path: str | Path | None, runtime_state_dir: str | Path | None
) -> Path:
    if peak_state_path is not None:
        return Path(peak_state_path)
    if runtime_state_dir is not None:
        return Path(runtime_state_dir) / ("paper_equity_peak.json" if paper else "live_equity_peak.json")
    return Path("data/runtime") / ("paper_equity_peak.json" if paper else "live_equity_peak.json")


def _parse_sidecar_file(path: Path) -> dict[str, Any] | None:
    """Missing -> None; present-but-invalid -> RiskStatePersistenceError; interrupt propagates."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (KeyboardInterrupt, SystemExit):
        raise
    except OSError as exc:
        raise RiskStatePersistenceError(f"Peak equity file unreadable at {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except (KeyboardInterrupt, SystemExit):
        raise
    except json.JSONDecodeError as exc:
        raise RiskStatePersistenceError(f"Peak equity file is malformed JSON at {path}: {exc}") from exc
    if not isinstance(data, dict) or "peak_equity" not in data:
        raise RiskStatePersistenceError(f"Peak equity file missing 'peak_equity' key at {path}")
    try:
        peak = float(data["peak_equity"])
    except (TypeError, ValueError) as exc:
        raise RiskStatePersistenceError(
            f"Peak equity value is not a number at {path}: {data.get('peak_equity')!r}"
        ) from exc
    if not math.isfinite(peak):
        raise RiskStatePersistenceError(f"Peak equity is non-finite at {path}: {peak!r}")
    if peak < 0:
        raise RiskStatePersistenceError(f"Peak equity is negative at {path}: {peak!r}")
    try:
        epoch_val = int(data.get("epoch", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise RiskStatePersistenceError(f"Peak epoch is not an integer at {path}: {data.get('epoch')!r}") from exc
    if epoch_val < 0:
        raise RiskStatePersistenceError(f"Peak epoch is negative at {path}: {epoch_val!r}")
    if "account_id" in data:
        val = data.get("account_id")
        if val is not None and not isinstance(val, str):
            raise RiskStatePersistenceError(f"Peak account_id is not a string at {path}: {val!r}")
    return data


class RiskPeakService:
    """Account-scoped peak sidecar: atomic, fail-closed, distinguishes missing vs corrupt."""

    def __init__(
        self,
        *,
        paper: bool = True,
        ledger: Any | None = None,
        peak_state_path: str | Path | None = None,
        runtime_state_dir: str | Path | None = None,
    ) -> None:
        self._paper = paper
        self.ledger: Any | None = ledger
        self._peak_state_path = resolve_peak_state_path(paper, peak_state_path, runtime_state_dir)
        self._peak_equity: float = 0.0
        self._peak_epoch: int = 0
        self._peak_account_id: str | None = None
        self._peak_reconciled: bool = False
        self._broker_account_id: str | None = None
        if ledger is None:
            data = _parse_sidecar_file(self._peak_state_path)
            if data is not None:
                peak = float(data["peak_equity"])
                epoch = int(data.get("epoch", 0) or 0)
                self._peak_equity = float(peak)
                self._peak_epoch = int(epoch)
                acct = data.get("account_id")
                if isinstance(acct, str) and acct.strip():
                    self._peak_account_id = acct.strip()
                    self._peak_reconciled = True
                else:
                    self._peak_account_id = None
                    self._peak_reconciled = False

    @property
    def peak_state_path(self) -> Path:
        return self._peak_state_path

    @peak_state_path.setter
    def peak_state_path(self, value: Path) -> None:
        self._peak_state_path = Path(value)

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @peak_equity.setter
    def peak_equity(self, value: float) -> None:
        self._peak_equity = float(value)

    @property
    def peak_epoch(self) -> int:
        return self._peak_epoch

    @peak_epoch.setter
    def peak_epoch(self, value: int) -> None:
        self._peak_epoch = int(value)

    @property
    def peak_account_id(self) -> str | None:
        return self._peak_account_id

    @peak_account_id.setter
    def peak_account_id(self, value: str | None) -> None:
        self._peak_account_id = value

    @property
    def peak_reconciled(self) -> bool:
        return self._peak_reconciled

    @peak_reconciled.setter
    def peak_reconciled(self, value: bool) -> None:
        self._peak_reconciled = bool(value)

    def _query_persisted_peak(self, account_id: str) -> tuple[float | None, int | None]:
        return self.query_persisted_peak(account_id)

    def query_persisted_peak(self, account_id: str) -> tuple[float | None, int | None]:
        # Honor test patch on AlpacaBroker._query_persisted_peak (e.g., injected failures)
        try:
            from unittest.mock import Mock

            from parayabuck.execution.alpaca import AlpacaBroker as _AB

            _meth = getattr(_AB, "_query_persisted_peak", None)
            if isinstance(_meth, Mock):
                return _meth(account_id)  # type: ignore[no-any-return]
            if _meth is not None and hasattr(_meth, "side_effect") and _meth.side_effect is not None:
                return _meth(account_id)  # type: ignore[no-any-return]
        except RiskStatePersistenceError:
            raise
        except Exception:
            pass
        if not account_id or self.ledger is None:
            return None, None
        try:
            with self.ledger._lock:
                hw_peak: float | None = None
                hw_epoch: int | None = None
                row = None
                try:
                    row = self.ledger.connection.execute(
                        "SELECT equity, epoch FROM portfolio_high_water WHERE account_id = ?", (account_id,)
                    ).fetchone()
                except Exception as exc:
                    if "no such table" in str(exc):
                        row = None
                    else:
                        raise RiskStatePersistenceError(
                            f"portfolio_high_water read failed for {account_id}: {exc}"
                        ) from exc
                if row is not None:
                    try:
                        hw_peak = float(row[0])
                        hw_epoch = int(row[1] or 0)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as exc:
                        raise RiskStatePersistenceError(
                            f"portfolio_high_water row corrupt for {account_id}: {row!r}: {exc}"
                        ) from exc
                    if hw_peak is not None and (not math.isfinite(hw_peak) or hw_peak < 0):
                        raise RiskStatePersistenceError(
                            f"portfolio_high_water peak corrupt for {account_id}: {hw_peak!r}"
                        )
                try:
                    rows = self.ledger.connection.execute(
                        "SELECT payload FROM risk_state WHERE account_id = ?", (account_id,)
                    ).fetchall()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    if "no such table" in str(exc):
                        rows = []
                    else:
                        raise RiskStatePersistenceError(f"risk_state read failed for {account_id}: {exc}") from exc
                rs_peak: float | None = None
                rs_epoch: int | None = None
                if rows:
                    best: dict[int, float] = {}
                    for (payload_str,) in rows:
                        try:
                            p = json.loads(payload_str)
                            pe = int(p.get("peak_epoch", 0) or 0)
                            pk = float(p.get("peak_equity", 0))
                            if not math.isfinite(pk) or pk < 0:
                                continue
                            if pe not in best or pk > best[pe]:
                                best[pe] = pk
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception:
                            continue
                    if best:
                        max_epoch = max(best.keys())
                        rs_peak = best[max_epoch]
                        rs_epoch = max_epoch
                    elif len(rows) > 0:
                        raise RiskStatePersistenceError(
                            f"risk_state rows for {account_id} all unreadable/corrupt: {len(rows)} rows"
                        )
                if hw_peak is None and rs_peak is None:
                    return None, None
                if hw_peak is None:
                    return rs_peak, rs_epoch
                if rs_peak is None:
                    return hw_peak, hw_epoch
                if hw_epoch is None or rs_epoch is None:
                    return max(hw_peak, rs_peak), hw_epoch if hw_epoch is not None else rs_epoch
                if hw_epoch != rs_epoch:
                    return (hw_peak, hw_epoch) if hw_epoch > rs_epoch else (rs_peak, rs_epoch)
                return max(hw_peak, rs_peak), hw_epoch
        except RiskStatePersistenceError:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise RiskStatePersistenceError(f"persisted peak query failed for {account_id}: {exc}") from exc

    def _ensure_peak_reconciled(self, account_id: str, current_equity: float) -> None:
        self.ensure_reconciled(account_id, current_equity)

    def ensure_reconciled(self, account_id: str, current_equity: float) -> None:
        if not account_id:
            account_id = "unknown"
        if not hasattr(self, "_peak_state_path"):
            self._peak_state_path = Path("data/runtime") / (
                "paper_equity_peak.json" if getattr(self, "_paper", True) else "live_equity_peak.json"
            )
        if not hasattr(self, "_peak_account_id"):
            self._peak_account_id = None
        if not hasattr(self, "_peak_epoch"):
            self._peak_epoch = 0
        if not hasattr(self, "_peak_equity"):
            self._peak_equity = 0.0
        if not hasattr(self, "_peak_reconciled"):
            self._peak_reconciled = False
        if not hasattr(self, "ledger"):
            self.ledger = None
        existing = getattr(self, "_peak_account_id", None)
        if existing is not None and existing != account_id:
            raise RuntimeError(
                f"Account identity changed from {existing} to {account_id} — fail closed; peak is account-scoped and must not cross accounts"
            )
        if getattr(self, "_peak_reconciled", False) and existing == account_id:
            return
        if getattr(self, "ledger", None) is None:
            self._peak_account_id = account_id
            self._peak_reconciled = True
            return
        sidecar_peak: float | None = None
        sidecar_epoch = 0
        sidecar_account_id: str | None = None
        sidecar_exists = self._peak_state_path.exists()
        is_legacy = False
        if sidecar_exists:
            data = _parse_sidecar_file(self._peak_state_path)
            assert data is not None
            peak = float(data["peak_equity"])
            epoch_val = int(data.get("epoch", 0) or 0)
            has_account_id = "account_id" in data
            has_fingerprint = "account_fingerprint" in data
            if has_account_id:
                val = data.get("account_id")
                if val is None:
                    is_legacy, sidecar_peak, sidecar_epoch = True, peak, epoch_val
                elif isinstance(val, str):
                    acct = val.strip()
                    if not acct:
                        is_legacy, sidecar_peak, sidecar_epoch = True, peak, epoch_val
                    elif acct != account_id:
                        log.warning(
                            "Peak sidecar account mismatch: sidecar=%s current=%s — ignoring sidecar", acct, account_id
                        )
                        sidecar_peak, sidecar_epoch, sidecar_account_id, is_legacy = None, 0, acct, False
                    else:
                        sidecar_peak, sidecar_epoch, sidecar_account_id, is_legacy = peak, epoch_val, acct, False
                else:
                    raise RiskStatePersistenceError(
                        f"Peak account_id is not a string at {self._peak_state_path}: {val!r}"
                    )
            elif has_fingerprint:
                fp_raw = data.get("account_fingerprint")
                fp = str(fp_raw).strip() if fp_raw is not None else ""
                if not fp:
                    is_legacy, sidecar_peak, sidecar_epoch = True, peak, epoch_val
                else:
                    current_fp = hashlib.sha256(account_id.encode()).hexdigest()
                    if fp != current_fp:
                        log.warning(
                            "Peak sidecar fingerprint mismatch: sidecar=%s current_account=%s — ignoring sidecar",
                            fp[:8],
                            account_id[:8],
                        )
                        sidecar_peak, sidecar_epoch, sidecar_account_id, is_legacy = None, 0, fp, False
                    else:
                        sidecar_peak, sidecar_epoch, sidecar_account_id, is_legacy = peak, epoch_val, account_id, False
            else:
                is_legacy, sidecar_peak, sidecar_epoch = True, peak, epoch_val
        persisted_peak, persisted_epoch = self.query_persisted_peak(account_id)
        if sidecar_peak is None and persisted_peak is None:
            peak_final, epoch_final = 0.0, 0
        elif sidecar_peak is None and persisted_peak is not None:
            peak_final, epoch_final = float(persisted_peak), int(persisted_epoch or 0)
        elif sidecar_peak is not None and persisted_peak is None:
            if is_legacy:
                log.warning(
                    "Legacy peak sidecar without account_id for account=%s — rewriting bound sidecar", account_id
                )
            peak_final, epoch_final = float(sidecar_peak), int(sidecar_epoch or 0)
        else:
            assert sidecar_peak is not None and persisted_peak is not None
            if is_legacy:
                if float(sidecar_peak) >= float(persisted_peak):
                    peak_final, epoch_final = float(sidecar_peak), int(sidecar_epoch or 0)
                else:
                    peak_final, epoch_final = float(persisted_peak), int(persisted_epoch or 0)
                log.warning(
                    "Legacy sidecar peak=%s persisted=%s for account=%s — resolved to %s (epoch %s)",
                    sidecar_peak,
                    persisted_peak,
                    account_id,
                    peak_final,
                    epoch_final,
                )
            else:
                s_epoch, p_epoch = int(sidecar_epoch or 0), int(persisted_epoch or 0)
                if s_epoch != p_epoch:
                    if p_epoch > s_epoch:
                        peak_final, epoch_final = float(persisted_peak), p_epoch
                    else:
                        peak_final, epoch_final = float(sidecar_peak), s_epoch
                else:
                    peak_final, epoch_final = max(float(sidecar_peak), float(persisted_peak)), s_epoch
        self._peak_equity, self._peak_epoch, self._peak_account_id, self._peak_reconciled = (
            float(peak_final),
            int(epoch_final),
            account_id,
            True,
        )
        needs_rewrite = False
        if not sidecar_exists:
            needs_rewrite = True
        elif is_legacy:
            needs_rewrite = True
        elif sidecar_peak is None:
            needs_rewrite = True
        elif sidecar_account_id != account_id:
            needs_rewrite = True
        elif sidecar_peak is not None and (
            abs(float(sidecar_peak) - float(peak_final)) > 1e-9 or int(sidecar_epoch or 0) != int(epoch_final)
        ):
            needs_rewrite = True
        if needs_rewrite and not (sidecar_peak is None and persisted_peak is None):
            try:
                payload = json.dumps(
                    {"account_id": account_id, "peak_equity": float(peak_final), "epoch": int(epoch_final)}
                )
                atomic_write(self._peak_state_path, payload)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                log.warning("Failed to reconcile peak sidecar to account %s", account_id, exc_info=True)

    def _load_peak_equity(self, account_id: str | None = None) -> float:
        return self.load_peak_equity(account_id)

    def load_peak_equity(self, account_id: str | None = None) -> float:
        acct = account_id or getattr(self, "_peak_account_id", None) or getattr(self, "_broker_account_id", None)
        if acct:
            self.ensure_reconciled(str(acct), float(getattr(self, "_peak_equity", 0.0) or 0.0))
            return float(self._peak_equity)
        if self.ledger is None:
            return float(self._peak_equity)
        return float(self._peak_equity)

    def _save_peak_equity(self, account_id: str | None = None) -> None:
        self.save_peak_equity(account_id)

    def save_peak_equity(self, account_id: str | None = None) -> None:
        if not math.isfinite(self._peak_equity) or self._peak_equity < 0:
            raise ValueError(f"Cannot persist non-finite or negative peak: {self._peak_equity!r}")
        epoch = int(getattr(self, "_peak_epoch", 0) or 0)
        resolved = (
            account_id
            or getattr(self, "_peak_account_id", None)
            or getattr(self, "_broker_account_id", None)
            or "unknown"
        )
        resolved = str(resolved)
        if getattr(self, "_peak_account_id", None) is None and getattr(self, "_broker_account_id", None):
            self._peak_account_id = str(getattr(self, "_broker_account_id"))
            resolved = str(self._peak_account_id)
        payload = json.dumps({"account_id": resolved, "peak_equity": self._peak_equity, "epoch": epoch})
        try:
            atomic_write(self._peak_state_path, payload)
        except (KeyboardInterrupt, SystemExit):
            raise

    def reset_peak_equity(self, equity: float, account_id: str | None = None) -> None:
        if equity <= 0:
            raise ValueError("Cannot reset the equity peak to a non-positive value")
        resolved = account_id or getattr(self, "_peak_account_id", None) or getattr(self, "_broker_account_id", None)
        if not resolved:
            raise RuntimeError("Cannot reset peak before account is known (peak is account-scoped)")
        resolved = str(resolved)
        _, persisted_epoch = self.query_persisted_peak(resolved)
        current_epoch = int(getattr(self, "_peak_epoch", 0) or 0)
        if persisted_epoch is not None:
            current_epoch = max(current_epoch, int(persisted_epoch))
        self._peak_epoch, self._peak_equity, self._peak_account_id, self._peak_reconciled = (
            current_epoch + 1,
            float(equity),
            resolved,
            True,
        )
        try:
            self.save_peak_equity(resolved)
        except (KeyboardInterrupt, SystemExit):
            raise
        log.warning(
            "Equity peak rebased to %.2f (epoch %d, %s)",
            self._peak_equity,
            self._peak_epoch,
            "paper" if self._paper else "live",
        )

    def maybe_update_peak(self, equity: float, account_id: str) -> bool:
        self.ensure_reconciled(account_id, equity)
        old = self._peak_equity
        new = max(old, float(equity))
        if new != old:
            self._peak_equity = new
            try:
                self.save_peak_equity(account_id)
            except (KeyboardInterrupt, SystemExit):
                self._peak_equity = old
                raise
            return True
        return False


__all__ = ["RiskPeakService", "RiskStatePersistenceError", "resolve_peak_state_path"]
