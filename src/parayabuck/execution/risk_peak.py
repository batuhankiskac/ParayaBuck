from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from parayabuck.execution.alpaca_models import RiskStatePersistenceError  # noqa: E402
from parayabuck.logger import get_logger
from parayabuck.monitoring.operational_event import mask_account_number
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
    return check_peak_record(data, path)


def check_peak_record(data: object, path: str | Path) -> dict[str, Any]:
    """Return ``data`` when it is a peak record the runtime accepts, else raise.

    The one validator for peak sidecars: the runtime load path and the backup
    set (``parayabuck.backup``) both call it, so they cannot disagree about
    what a valid sidecar is. Oversized integers and infinities raise
    ``RiskStatePersistenceError`` instead of ``OverflowError``.
    """
    if not isinstance(data, dict) or "peak_equity" not in data:
        raise RiskStatePersistenceError(f"Peak equity file missing 'peak_equity' key at {path}")
    try:
        peak = float(data["peak_equity"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise RiskStatePersistenceError(
            f"Peak equity value is not a number at {path}: {data.get('peak_equity')!r}"
        ) from exc
    if not math.isfinite(peak):
        raise RiskStatePersistenceError(f"Peak equity is non-finite at {path}: {peak!r}")
    if peak < 0:
        raise RiskStatePersistenceError(f"Peak equity is negative at {path}: {peak!r}")
    try:
        epoch_val = int(data.get("epoch", 0) or 0)
    except (TypeError, ValueError, OverflowError) as exc:
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
        # Set when no sidecar and no persisted high-water existed: the next
        # saved peak is the bootstrap baseline and is announced once.
        self._bootstrap_pending: bool = False
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

    def query_persisted_peak(self, account_id: str) -> tuple[float | None, int | None]:
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
                            # A row without its peak is not a zero peak: fail closed.
                            if not isinstance(p, dict) or "peak_equity" not in p:
                                raise RiskStatePersistenceError(f"risk_state row for {account_id} lacks peak_equity")
                            pe = int(p.get("peak_epoch", 0) or 0)
                            pk = float(p["peak_equity"])
                            if not math.isfinite(pk) or pk < 0:
                                continue
                            if pe not in best or pk > best[pe]:
                                best[pe] = pk
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except RiskStatePersistenceError:
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

    def ensure_reconciled(self, account_id: str) -> None:
        if not account_id:
            account_id = "unknown"
        existing = self._peak_account_id
        if existing is not None and existing != account_id:
            raise RuntimeError(
                f"Account identity changed from {existing} to {account_id} — fail closed; peak is account-scoped and must not cross accounts"
            )
        if self._peak_reconciled and existing == account_id:
            return
        if self.ledger is None:
            self._peak_account_id = account_id
            self._peak_reconciled = True
            return
        sidecar_peak: float | None = None
        sidecar_epoch = 0
        sidecar_account_id: str | None = None
        # One read decides both "missing" and "present": no exists() check
        # that a concurrent writer could race (C2-4-6).
        data = _parse_sidecar_file(self._peak_state_path)
        sidecar_exists = data is not None
        is_legacy = False
        if data is not None:
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
                        # Fail closed (docs/execution_architecture.md): a foreign peak is never
                        # ignored and overwritten, because the drawdown baseline would re-base
                        # to the new account's equity.
                        log.error("Peak sidecar account mismatch: sidecar=%s current=%s", acct, account_id)
                        raise RiskStatePersistenceError(
                            f"Peak sidecar at {self._peak_state_path} is bound to account {acct}, "
                            f"not {account_id}; refusing to start. Verify the account before moving the sidecar aside."
                        )
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
                        # Fail closed, as for an account_id mismatch (R2-6-1).
                        log.error(
                            "Peak sidecar fingerprint mismatch: sidecar=%s current_account=%s",
                            fp[:8],
                            account_id[:8],
                        )
                        raise RiskStatePersistenceError(
                            f"Peak sidecar at {self._peak_state_path} is bound to another account "
                            "(fingerprint mismatch); refusing to start. Verify the account before moving the sidecar aside."
                        )
                    else:
                        sidecar_peak, sidecar_epoch, sidecar_account_id, is_legacy = peak, epoch_val, account_id, False
            else:
                is_legacy, sidecar_peak, sidecar_epoch = True, peak, epoch_val
        persisted_peak, persisted_epoch = self.query_persisted_peak(account_id)
        if sidecar_peak is None and persisted_peak is None:
            # Genuine first run or lost state: the baseline becomes the first
            # observed equity (recorded and announced by save_peak_equity).
            peak_final, epoch_final = 0.0, 0
            self._bootstrap_pending = True
            log.warning(
                "No peak sidecar or persisted high-water for account=%s; drawdown baseline bootstraps from the first observed equity",
                account_id,
            )
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
                # The newer epoch is the newer baseline; a peak from an older epoch
                # is stale even when it is higher (R2-6-4). Equal epochs keep the max peak.
                s_epoch, p_epoch = int(sidecar_epoch or 0), int(persisted_epoch or 0)
                if s_epoch == p_epoch:
                    peak_final = max(float(sidecar_peak), float(persisted_peak))
                    epoch_final = s_epoch
                elif p_epoch > s_epoch:
                    peak_final, epoch_final = float(persisted_peak), p_epoch
                else:
                    peak_final, epoch_final = float(sidecar_peak), s_epoch
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
                log.warning(
                    "Failed to reconcile peak sidecar to account %s", mask_account_number(account_id), exc_info=True
                )

    def save_peak_equity(self, account_id: str | None = None) -> tuple[str, float] | None:
        """Persist the peak. Returns the ``(account_id, baseline)`` of a bootstrap
        announcement that this save completed, for the caller to emit once it
        has released the peak lock (C3-1-2); the alert is not raised here."""
        if not math.isfinite(self._peak_equity) or self._peak_equity < 0:
            raise ValueError(f"Cannot persist non-finite or negative peak: {self._peak_equity!r}")
        epoch = int(self._peak_epoch or 0)
        resolved = str(account_id or self._peak_account_id or self._broker_account_id or "unknown")
        if self._peak_account_id is None and self._broker_account_id:
            self._peak_account_id = str(self._broker_account_id)
            resolved = self._peak_account_id
        payload = json.dumps({"account_id": resolved, "peak_equity": self._peak_equity, "epoch": epoch})
        try:
            atomic_write(self._peak_state_path, payload)
        except (KeyboardInterrupt, SystemExit):
            raise
        if self._bootstrap_pending and self._peak_equity > 0:
            self._bootstrap_pending = False
            return resolved, self._peak_equity
        return None

    def reset_peak_equity(
        self, equity: float, account_id: str | None = None, *, expected_epoch: int | None = None
    ) -> bool:
        """Start a new peak epoch at ``equity``; False when nothing changed.

        The sidecar is the commit point: it is written before any in-memory
        state moves, so a failed write leaves the peak, epoch and account as
        they were. ``expected_epoch`` is the epoch the caller's snapshot was
        taken against; a newer epoch already active means the reset was
        applied (or superseded) meanwhile, so it is a no-op instead of a
        second re-baseline at whatever equity is current.
        """
        if equity <= 0:
            raise ValueError("Cannot reset the equity peak to a non-positive value")
        resolved = account_id or self._peak_account_id or self._broker_account_id
        if not resolved:
            raise RuntimeError("Cannot reset peak before account is known (peak is account-scoped)")
        resolved = str(resolved)
        _, persisted_epoch = self.query_persisted_peak(resolved)
        active_epoch = int(self._peak_epoch or 0)
        if expected_epoch is not None:
            if active_epoch > int(expected_epoch):
                log.info(
                    "Risk epoch %d already superseded by epoch %d; peak reset is a no-op",
                    int(expected_epoch),
                    active_epoch,
                )
                return False
            if active_epoch < int(expected_epoch):
                raise RiskStatePersistenceError(
                    f"Peak epoch regressed for {resolved}: active {active_epoch}, expected {int(expected_epoch)}"
                )
        current_epoch = active_epoch
        if persisted_epoch is not None:
            current_epoch = max(current_epoch, int(persisted_epoch))
        new_epoch = current_epoch + 1
        atomic_write(
            self._peak_state_path,
            json.dumps({"account_id": resolved, "peak_equity": float(equity), "epoch": new_epoch}),
        )
        self._peak_epoch, self._peak_equity, self._peak_account_id, self._peak_reconciled = (
            new_epoch,
            float(equity),
            resolved,
            True,
        )
        self._bootstrap_pending = False
        log.warning(
            "Equity peak rebased to %.2f (epoch %d, %s)",
            self._peak_equity,
            self._peak_epoch,
            "paper" if self._paper else "live",
        )
        return True

    def restore_peak_state(self, state: tuple[float, int, str | None, bool]) -> None:
        """Roll the peak back to ``state`` (taken before ``reset_peak_equity``).

        Used when a reset's downstream steps fail after the sidecar commit, so
        the rebase is not left applied in one store and not the other.
        """
        peak, epoch, account_id, reconciled = state
        self._peak_equity, self._peak_epoch, self._peak_account_id, self._peak_reconciled = (
            float(peak),
            int(epoch),
            account_id,
            bool(reconciled),
        )
        resolved = account_id or self._broker_account_id
        if not resolved:
            return  # reset never reached the sidecar without an account
        payload = json.dumps({"account_id": str(resolved), "peak_equity": float(peak), "epoch": int(epoch)})
        try:
            atomic_write(self._peak_state_path, payload)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            log.error(
                "Could not restore the peak sidecar after a failed risk-epoch reset; the file may still hold the new epoch",
                exc_info=True,
            )


__all__ = ["RiskPeakService", "RiskStatePersistenceError", "resolve_peak_state_path"]
