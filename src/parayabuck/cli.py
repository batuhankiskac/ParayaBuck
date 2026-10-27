from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import traceback
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any

import yaml

from parayabuck.backtest.product import (
    load_csv_bars,
    load_csv_corporate_actions,
    optimize_grid,
    run_product_backtest,
)
from parayabuck.config import DEFAULT_DECISION_DB_PATH, Config, RuntimeMode, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data import AlpacaHistoricalData
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.portfolio import portfolio_execution_policy_from_config, portfolio_risk_limits_from_config
from parayabuck.live_safety import LiveOperatorApproval
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.monitoring.operational_event import Severity
from parayabuck.portfolio import portfolio_strategy_from, product_strategy_from
from parayabuck.portfolio.runtime_service import configuration_digest
from parayabuck.universe import equity_universe_digest
from parayabuck.utils.io import atomic_write

log = get_logger("parayabuck.cli")

# Lower bound for an operator's --retention-days. Below a day the prune removes
# every older automatic backup, so a fraction of a day is refused.
_MIN_BACKUP_RETENTION_DAYS = 1.0


def _retention_days_arg(value: str) -> float:
    """--retention-days: a finite number of days, at least one day."""
    try:
        days = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from exc
    if not isfinite(days) or days < _MIN_BACKUP_RETENTION_DAYS:
        raise argparse.ArgumentTypeError(
            f"must be a finite number of at least {_MIN_BACKUP_RETENTION_DAYS:g} day(s): {value}"
        )
    return days


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parayabuck", description="ParayaBuck trading bot")
    parser.add_argument("--config", default=None, help="configuration YAML path")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("web", help="start the local operations console")
    from parayabuck.tax.cli import add_tax_parser

    add_tax_parser(commands)
    backup = commands.add_parser("backup", help="consistent SQLite backup of the runtime decision database")
    backup.add_argument(
        "--retention-days",
        type=_retention_days_arg,
        default=None,
        help="override the configured backup retention (days)",
    )
    backup.add_argument(
        "--verify-only",
        type=str,
        default=None,
        help="verify integrity of an existing backup file instead of creating one",
    )
    backup.add_argument(
        "--label",
        type=str,
        default=None,
        help="label this snapshot (e.g. predeploy) so retention pruning never deletes it",
    )
    doctor = commands.add_parser("doctor", help="read-only deployment preflight (no orders)")
    doctor.add_argument("--strict", action="store_true", help="fail on warnings")
    doctor_mode = doctor.add_mutually_exclusive_group()
    doctor_mode.add_argument("--paper", action="store_true", help="require paper mode invariants")
    doctor_mode.add_argument(
        "--configured-mode",
        action="store_true",
        help="validate the invariants of the configured runtime.mode (paper: exactly --paper; live: LIVE gates)",
    )
    doctor.add_argument("--offline", action="store_true", help="skip network reachability checks")
    authorize = commands.add_parser("authorize-live", help="create a short-lived account/config-bound live approval")
    authorize.add_argument("--operator", default=None)
    authorize.add_argument("--expires-minutes", type=int, default=15)
    authorize.add_argument(
        "--standing-days",
        type=int,
        default=None,
        help=(
            "instead create a STANDING LIVE authorization (1-30 days) that lets this trading host start "
            "LIVE unattended after a reboot or deploy; any config, cap, account or host change voids it"
        ),
    )
    authorize.add_argument(
        "--revoke-standing",
        action="store_true",
        help="delete the standing LIVE authorization (LIVE then needs a manual re-arm after every restart)",
    )
    halt = commands.add_parser(
        "halt",
        help="set the sticky operator halt: engine starts are refused until `parayabuck resume`",
    )
    halt.add_argument("--reason", default="operator halt", help="free-text reason stored in the halt marker")
    commands.add_parser("resume", help="clear the operator halt so the engine may start again")
    backtest = commands.add_parser("backtest", help="run a production-strategy backtest")
    _add_backtest_arguments(backtest, include_cost_stress=True)
    optimize = commands.add_parser("optimize", help="grid search on TRAIN and optional OOS data")
    _add_backtest_arguments(optimize, include_cost_stress=False)
    optimize.add_argument(
        "--grid",
        action="append",
        required=True,
        metavar="NAME=V1,V2",
        help="repeat for each parameter",
    )
    optimize.add_argument("--train-start")
    optimize.add_argument("--train-end")
    optimize.add_argument("--oos-start")
    optimize.add_argument("--oos-end")
    # --- Ops: operational observability (Mission 5) ---
    ops = commands.add_parser("ops", help="operational observability, diagnostics & forensics")
    ops_sub = ops.add_subparsers(dest="ops_cmd")
    status_p = ops_sub.add_parser("status", help="live runtime status from snapshot + health")
    status_p.add_argument("--json", action="store_true", help="stable JSON output")
    status_p.add_argument("--snapshot", default=None, help="snapshot path override")
    status_p.add_argument("--db", default=None, help="decision DB path override")
    doctor_p = ops_sub.add_parser("doctor", help="offline diagnostics, read-only")
    doctor_p.add_argument("--json", action="store_true")
    doctor_p.add_argument("--snapshot", default=None)
    doctor_p.add_argument("--db", default=None)
    doctor_p.add_argument("--journal", default=None, help="journal file to check (default: configured path)")
    events_p = ops_sub.add_parser("events", help="query operational event journal")
    events_p.add_argument("--since", default=None, help="since time (ISO or 30m,2h,1d)")
    events_p.add_argument("--until", default=None, help="until time (ISO or relative)")
    events_p.add_argument("--kind", default=None)
    # The journal matches severity exactly against the upper-case vocabulary,
    # so the flag is normalised and validated instead of silently matching
    # nothing on `--severity warning`.
    events_p.add_argument(
        "--severity",
        default=None,
        type=lambda value: value.strip().upper(),
        choices=[str(level) for level in Severity],
    )
    events_p.add_argument("--market", default=None)
    events_p.add_argument("--decision", default=None)
    events_p.add_argument("--order", default=None)
    events_p.add_argument("--incident", default=None)
    events_p.add_argument("--limit", type=int, default=50)
    events_p.add_argument("--json", action="store_true")
    events_p.add_argument("--journal", default=None)
    timeline_p = ops_sub.add_parser("timeline", help="correlation forensic view")
    timeline_p.add_argument("--decision", default=None)
    timeline_p.add_argument("--order", default=None)
    timeline_p.add_argument("--incident", default=None)
    timeline_p.add_argument("--limit", type=int, default=100)
    timeline_p.add_argument("--json", action="store_true")
    timeline_p.add_argument("--journal", default=None)
    timeline_p.add_argument("--db", default=None)
    explain_p = ops_sub.add_parser("explain", help="explain why latest decision produced no trade")
    explain_p.add_argument("target", nargs="?", default="latest", help="latest or decision_id")
    explain_p.add_argument("--json", action="store_true")
    explain_p.add_argument("--db", default=None)
    explain_p.add_argument("--journal", default=None)
    decision_p = ops_sub.add_parser("decision", help="alias for explain (decision latest)")
    decision_p.add_argument("target", nargs="?", default="latest")
    decision_p.add_argument("--json", action="store_true")
    decision_p.add_argument("--db", default=None)
    decision_p.add_argument("--journal", default=None)
    email_p = ops_sub.add_parser(
        "test-email",
        help="validate the alerting.email settings; with --confirm, send ONE test e-mail",
    )
    email_p.add_argument("--confirm", action="store_true", help="really send the test e-mail")
    bundle_p = ops_sub.add_parser("bundle", help="sanitized diagnostic bundle")
    bundle_p.add_argument("--output", default=None)
    bundle_p.add_argument("--snapshot", default=None)
    bundle_p.add_argument("--journal", default=None)
    bundle_p.add_argument("--db", default=None)
    rebase_p = ops_sub.add_parser(
        "rebase-ownership",
        help="adopt the broker's share count for one symbol (dry run unless --confirm)",
    )
    rebase_p.add_argument("symbol", help="symbol whose ownership must be rebased, e.g. QLD")
    rebase_p.add_argument(
        "--broker-qty",
        type=float,
        required=True,
        help="share count read off the broker position (the operator verifies it)",
    )
    rebase_p.add_argument(
        "--broker-average-cost",
        type=float,
        default=None,
        help="average entry price read off the broker position; lets a decrease that matches a "
        "standard reverse-split ratio be booked as a split rescale instead of a sale",
    )
    rebase_p.add_argument("--account-id", default=None, help="defaults to the latest runtime session's account")
    rebase_p.add_argument("--note", default="", help="audit note recorded with the adjustment")
    rebase_p.add_argument("--db", default=None, help="decision DB path override")
    rebase_p.add_argument("--confirm", action="store_true", help="write the adjustment (dry run without it)")
    return parser


def _add_backtest_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_cost_stress: bool,
) -> None:
    parser.add_argument("--market", choices=("equity",), default="equity")
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--data", help="offline CSV containing OHLCV bars")
    parser.add_argument(
        "--corporate-actions",
        help="optional CSV of explicit equity dividends (split-adjusted prices are expected)",
    )
    parser.add_argument("--symbols", help="comma-separated download candidates")
    parser.add_argument(
        "--start",
        help="evaluation start (default: first session with the strategy's full warmup; earlier bars are warmup)",
    )
    parser.add_argument("--end")
    parser.add_argument("--initial-cash", type=float, default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--param", action="append", default=[], metavar="NAME=VALUE", help="strategy override")
    if include_cost_stress:
        parser.add_argument(
            "--cost-stress",
            action="store_true",
            help="also run 2x and 3x slippage/spread/equity-regulatory-fee assumptions",
        )


def main(argv: list[str] | None = None) -> int:
    # Every file this CLI creates (decision DB, backups, bundles, reports) is
    # owner-only whatever the caller's umask; the units set UMask=0077 too, but
    # a manual run inherited the operator's shell umask.
    os.umask(0o077)
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        from parayabuck.web.app import main as web_main

        web_main()
        return 0
    args = _parser().parse_args(arguments)
    if args.command in {None, "web"}:
        from parayabuck.web.app import main as web_main

        # The console used to ignore --config and always read CONFIG_PATH,
        # so an operator could start a different trading configuration than
        # the one they named on the command line.
        web_main(config_path=args.config)
        return 0
    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "ops":
        return _run_ops(args)
    if args.command == "tax":
        from parayabuck.tax.cli import run_tax

        return run_tax(args)
    try:
        config = load_config(args.config)
    except Exception as exc:
        # The backup timer runs unattended: an invalid settings file must be
        # a clear ERROR in the journal, not a bare traceback.
        log.error("parayabuck %s: configuration could not be loaded: %s", args.command, exc)
        if args.command == "backup":
            _emit_ops_alert(
                "backup_failed",
                f"Backup skipped: configuration could not be loaded: {exc}",
                reason="config_invalid",
            )
        return 1
    if args.command == "authorize-live":
        return _authorize_live(config, args)
    if args.command in {"halt", "resume"}:
        return _run_halt(config, args)
    if args.command == "backup":
        return _run_backup(config, args)
    market = Market(args.market)
    start = _date(args.start) if args.start else None
    end = _date(args.end, end_of_day=True) if args.end else None
    bars, data_feed = _load_bars(config, args, market, start, end)
    corporate_actions = load_csv_corporate_actions(args.corporate_actions) if args.corporate_actions else None
    if args.command == "optimize":
        grid = _parse_grid(args.grid)
        parameters = _strategy_parameters(config, args.param)
        payload = optimize_grid(
            config,
            bars,
            market=market,
            grid=grid,
            strategy_name=args.strategy,
            base_parameters=parameters,
            initial_cash=args.initial_cash,
            train_start=_date(args.train_start) if args.train_start else start,
            train_end=_date(args.train_end, end_of_day=True) if args.train_end else end,
            oos_start=_date(args.oos_start) if args.oos_start else None,
            oos_end=_date(args.oos_end, end_of_day=True) if args.oos_end else None,
            output_dir=args.output_dir,
            corporate_actions=corporate_actions,
            data_feed=data_feed,
        )
        print(json.dumps(payload, indent=2, default=str))
        return 0
    parameters = _strategy_parameters(config, args.param)
    multipliers = (1.0, 2.0, 3.0) if args.cost_stress else (1.0,)
    for multiplier in multipliers:
        run = run_product_backtest(
            config,
            bars,
            market=market,
            strategy_name=args.strategy,
            parameters=parameters,
            start=start,
            end=end,
            initial_cash=args.initial_cash,
            output_dir=args.output_dir,
            cost_multiplier=multiplier,
            corporate_actions=corporate_actions,
            data_feed=data_feed,
        )
        _print_summary(run.report.summary, run.output_dir)
    return 0


def backtest_main(argv: list[str] | None = None) -> int:
    return main(["backtest", *(sys.argv[1:] if argv is None else argv)])


def _authorize_live(config: Config, args: argparse.Namespace) -> int:
    from parayabuck.live_authorization import standing_authorization_file

    if getattr(args, "revoke_standing", False):
        standing_file = standing_authorization_file(config)
        if standing_file is None:
            raise SystemExit("live.operator_approval_path is not configured")
        if standing_file.exists():
            standing_file.unlink()
            print(f"Standing LIVE authorization revoked: {standing_file}")
        else:
            print("No standing LIVE authorization to revoke")
        return 0
    if not args.operator or not str(args.operator).strip():
        raise SystemExit("--operator is required")
    if not config.live.enabled or config.live.hard_dollar_cap <= 0:
        raise SystemExit("live.enabled=true and a positive live.hard_dollar_cap are required")
    if not config.live.operator_approval_path:
        raise SystemExit("live.operator_approval_path is required")
    standing_days = getattr(args, "standing_days", None)
    if standing_days is not None:
        from parayabuck.live_safety import STANDING_AUTHORIZATION_MAX_DAYS

        if not 1 <= standing_days <= STANDING_AUTHORIZATION_MAX_DAYS:
            raise SystemExit(f"--standing-days must be between 1 and {STANDING_AUTHORIZATION_MAX_DAYS}")
    elif not 1 <= args.expires_minutes <= 1440:
        raise SystemExit("--expires-minutes must be between 1 and 1440")
    from parayabuck.config import load_env_file
    from parayabuck.live_safety import LIVE_API_KEY_ENV, LIVE_SECRET_KEY_ENV, mode_credentials

    load_env_file()
    # B3: LIVE approvals are bound to the LIVE key pair; the PAPER names are
    # never used here.
    key, secret = mode_credentials(live=True)
    hmac_secret = os.getenv(config.live.operator_approval_key_env, "")
    if not key or not secret:
        raise SystemExit(f"Live Alpaca credentials are required ({LIVE_API_KEY_ENV} / {LIVE_SECRET_KEY_ENV})")
    if len(hmac_secret) < 32:
        # Fail here, BEFORE the live-endpoint broker probe, with the same
        # clean SystemExit style as every other missing prerequisite —
        # LiveOperatorApproval.calculated_signature would only raise a raw
        # ValueError at signing time.
        raise SystemExit(f"{config.live.operator_approval_key_env} must be set to a key of at least 32 characters")
    broker = AlpacaBroker(key, secret, paper=False)
    try:
        broker.account()  # Read-only identity verification against the live endpoint.
        account_suffix = broker.account_suffix
        account_id = broker.account_id or ""
        account_number = broker.account_number
    finally:
        # Temporary live-credential verification sessions must be closed.
        broker.close()
    from parayabuck.live_safety import LiveApprovalError, verify_account_environment

    try:
        verify_account_environment(
            live=True,
            account_number=account_number,
            api_key=key,
            expected_account_id=config.broker.expected_account_id,
        )
    except LiveApprovalError as exc:
        raise SystemExit(str(exc)) from None
    if not account_id:
        raise SystemExit("The live Alpaca account identity could not be verified")
    if standing_days is not None:
        from parayabuck.live_authorization import create_standing_authorization

        try:
            standing = create_standing_authorization(
                config,
                operator_id=str(args.operator),
                account_id=account_id,
                days=standing_days,
                secret=hmac_secret,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        target = standing_authorization_file(config)
        assert target is not None
        atomic_write(target, json.dumps(standing.to_dict(), indent=2, sort_keys=True), mode=0o600)
        print(
            f"Standing LIVE authorization written for account ...{account_suffix} on this trading host; "
            f"expires {standing.expires_at.isoformat()}. LIVE now starts automatically after a restart; "
            "any settings, cap, account or host change voids it."
        )
        return 0
    digests: list[str] = []
    for market, selection, symbols, universe_version in (
        (
            Market.EQUITY,
            config.strategy.equity,
            config.universe.equity.symbols if config.universe.equity.enabled else (),
            equity_universe_digest(config.universe.equity),
        ),
    ):
        if not selection.enabled:
            continue
        if not symbols:
            raise SystemExit(f"No active {market.value} universe is available")
        # Live gate must use the product surface: only PAPER-supported strategies
        # may produce a live approval. Generic backtest callers keep
        # portfolio_strategy_from for non-product strategy construction.
        strategy = product_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
        digests.append(
            configuration_digest(
                strategy,
                "live",
                selection.maximum_allocation,
                universe_version,
                portfolio_risk_limits_from_config(config.risk),
                portfolio_execution_policy_from_config(config.execution),
                config.risk.drawdown_liquidation,
            )
        )
    now = datetime.now(UTC)
    approval = LiveOperatorApproval(
        operator_id=args.operator,
        account_id=account_id,
        configuration_digests=tuple(digests),
        maximum_dollar_exposure=config.live.hard_dollar_cap,
        approved_at=now,
        expires_at=now + timedelta(minutes=args.expires_minutes),
        nonce=secrets.token_hex(16),
    ).signed(hmac_secret)
    atomic_write(
        Path(config.live.operator_approval_path),
        json.dumps(approval.to_dict(), indent=2, sort_keys=True),
        mode=0o600,
    )
    print(f"Live approval written for account ...{account_suffix}; expires {approval.expires_at.isoformat()}")
    return 0


def _emit_ops_alert(kind: str, message: str, **fields: Any) -> None:
    """Emit one operator alert from a CLI command. Never raises."""
    try:
        from parayabuck.monitoring.alerts import emit as emit_alert

        emit_alert(kind, message=message, **fields)
    except Exception:  # pragma: no cover - alerting must never mask the failure
        log.exception("Could not emit %s alert", kind)


def _run_backup(config: Config, args: argparse.Namespace) -> int:
    """Snapshot the runtime decision database with the sqlite3 online backup
    API (safe while the runtime keeps the WAL database open), then prune
    backups older than the retention window.

    With --verify-only, verify an existing backup instead of creating one.
    With --label, publish the snapshot outside the retention prune glob so a
    pre-deploy/rollback restore point survives pruning. Every failure emits
    the ``backup_failed`` alert: the timer runs unattended, so a silent
    failure would only surface when a restore is already needed.
    """
    from parayabuck.backup import backup_database, verify_backup

    if args.verify_only:
        target = Path(args.verify_only).absolute()
        if not target.is_file():
            # Nothing to verify is not a corrupt backup: no alert, exit 2.
            print(f"Backup not found: {target}", file=sys.stderr)
            return 2
        ok = verify_backup(target)
        if ok:
            print(f"Backup OK: {target}")
            return 0
        print(f"Backup CORRUPT: {target}", file=sys.stderr)
        _emit_ops_alert(
            "backup_failed",
            f"Backup failed integrity check: {target}",
            reason="corrupt",
            backup_path=str(target),
        )
        return 1
    source = Path(config.runtime.decision_db_path).absolute()
    if not source.is_file():
        # Same alert as any failed backup (the timer is unattended), but no
        # traceback and no empty backup directory written for a missing source.
        print(f"Backup refused: runtime database not found: {source}", file=sys.stderr)
        _emit_ops_alert(
            "backup_failed",
            f"Backup could not be written: runtime database not found: {source}",
            reason="write_failed",
            source_path=str(source),
            backup_dir=str(config.backup.path),
        )
        return 2
    retention_days = args.retention_days if args.retention_days is not None else config.backup.retention_days
    try:
        destination = backup_database(
            config.runtime.decision_db_path,
            config.backup.path,
            retention_days=retention_days,
            label=getattr(args, "label", None),
        )
        verified = verify_backup(destination)
    except Exception as exc:
        _emit_ops_alert(
            "backup_failed",
            f"Backup could not be written: {exc}",
            reason="write_failed",
            source_path=str(config.runtime.decision_db_path),
            backup_dir=str(config.backup.path),
        )
        raise
    if not verified:
        _emit_ops_alert(
            "backup_failed",
            f"Backup failed integrity check: {destination}",
            reason="corrupt",
            backup_path=str(destination),
        )
        raise SystemExit(f"Backup failed integrity check: {destination}")
    print(f"Backup written: {Path(destination).absolute()}")
    print(f"Retention: {retention_days:g} day(s); older automatic backups pruned")
    _prune_decision_receipts()
    return 0


def _prune_decision_receipts() -> None:
    """Receipt retention, owned by the backup timer (one file per decision).

    Prunes at the soak/audit floor only, so the auditor can always replay its
    window. Best effort: a receipt-prune failure never fails a verified backup.
    """
    from parayabuck.monitoring import receipt as receipt_module

    try:
        removed = receipt_module.prune_receipts(
            receipt_module.default_receipt_dir(), retention_days=receipt_module.MIN_RECEIPT_RETENTION_DAYS
        )
    except Exception as exc:
        log.warning("decision receipt prune failed: %s", exc)
        print(f"Receipt prune failed (backup unaffected): {exc}", file=sys.stderr)
        return
    print(f"Receipts: {len(removed)} older than {receipt_module.MIN_RECEIPT_RETENTION_DAYS:g} day(s) pruned")


def _operator_halt_marker(config: Config) -> Path:
    """The halt marker of the runtime data directory the settings name."""
    from parayabuck.live_safety import operator_halt_marker_path

    return operator_halt_marker_path(Path(config.runtime.decision_db_path).parent)


def _run_halt(config: Config, args: argparse.Namespace) -> int:
    """`halt` writes the sticky marker; `resume` removes it. No broker calls.

    Halting does not stop a running engine: stop it with the console stop
    control or `systemctl stop parayabuck`. The marker then keeps it from
    restarting (automatic or manual) until `resume`.
    """
    import getpass

    from parayabuck.live_safety import clear_operator_halt, read_operator_halt, write_operator_halt

    # Relative settings resolve against the working directory: name the absolute
    # marker path, and refuse to create a marker in a directory that is not the
    # runtime data directory (a halt run from $HOME would halt nothing).
    marker = _operator_halt_marker(config).absolute()
    if args.command == "halt":
        if not marker.parent.is_dir():
            print(f"Operator halt NOT set: directory {marker.parent} does not exist", file=sys.stderr)
            return 2
        try:
            user = getpass.getuser()
        except Exception:
            user = "unknown"
        record = write_operator_halt(marker, reason=args.reason, user=user)
        print(f"Operator halt set: {marker}")
        print(f"reason={record['reason']} user={record['user']} created_at={record['created_at']}")
        print("Engine starts are refused while the marker exists. Stop a running engine with the console stop")
        print("control or `systemctl stop parayabuck`; `parayabuck resume` clears the halt.")
        return 0
    previous = read_operator_halt(marker)
    if clear_operator_halt(marker):
        print(f"Operator halt cleared: {marker} (was: {previous.get('reason') if previous else 'n/a'})")
    else:
        print(f"No operator halt present at {marker}; nothing to clear.")
    return 0


def _configured_config_path(args: argparse.Namespace) -> str | None:
    """``--config``, else CONFIG_PATH (process environment or .env), else None.

    The same order :func:`load_config` uses, so the checks that read the file
    directly look at the settings the engine loads.
    """
    explicit = getattr(args, "config", None)
    if explicit:
        return str(explicit)
    from parayabuck.config import load_env_file

    load_env_file()
    return os.getenv("CONFIG_PATH") or None


def _locked_pin_mismatches(lock_path: Path) -> tuple[int, list[str]]:
    """(pins checked, human-readable differences) between ``lock_path`` and the installed versions.

    Only ``name==version`` lines are compared; a pin that is not installed is a
    difference too. Read-only: importlib.metadata never imports the packages.
    """
    import importlib.metadata

    checked = 0
    differences: list[str] = []
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-") or "==" not in line:
            continue
        name, wanted = (part.strip() for part in line.split("==", 1))
        checked += 1
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            differences.append(f"{name} not installed (locked {wanted})")
            continue
        if installed != wanted:
            differences.append(f"{name} installed {installed} != locked {wanted}")
    return checked, differences


def _credential_permission_row(warning: str | None, *, strict: bool) -> tuple[str, str] | None:
    """(status, detail) for the credential file mode, None when owner-only.

    A loose credential file is a WARN normally and a FAIL under ``--strict``
    (the unit's ExecStartPre), so an insecure file blocks the engine start.
    """
    if warning is None:
        return None
    return ("FAIL" if strict else "WARN", warning)


def _run_doctor(args: argparse.Namespace) -> int:
    """Read-only deployment preflight. No orders."""
    import importlib.metadata

    checks: list[tuple[str, str, str]] = []
    blocker = False
    warning = False

    def add(name: str, ok: bool, detail: str = "", is_blocker: bool = True) -> None:
        nonlocal blocker, warning
        status = "PASS" if ok else "FAIL"
        checks.append((name, status, detail))
        if not ok:
            if is_blocker:
                blocker = True
            else:
                warning = True

    config_path = _configured_config_path(args)
    config: Config | None = None
    try:
        config = load_config(config_path)
        add("config parses", True, str(config_path or "config/settings.yaml"))
        sel = config.strategy.equity
        if sel.enabled:
            try:
                strat = product_strategy_from(sel.name, sel.parameters, timeframe=sel.timeframe)
                add(
                    f"strategy equity {sel.name}",
                    True,
                    f"{strat.spec().version} {strat.timeframe} alloc {sel.maximum_allocation}",
                )
            except Exception as exc:
                add(f"strategy equity {sel.name}", False, str(exc))
        else:
            add("strategy equity disabled", True, "skipped")
        configured_mode = bool(getattr(args, "configured_mode", False))
        # --configured-mode validates the mode the settings name: PAPER gets
        # exactly the --paper invariant set, LIVE gets the LIVE gate set.
        paper_checks = bool(args.paper) or (configured_mode and config.runtime.mode == RuntimeMode.PAPER)
        live_checks = configured_mode and config.runtime.mode == RuntimeMode.LIVE
        if configured_mode:
            add("configured runtime.mode", config.runtime.mode != RuntimeMode.DISABLED, str(config.runtime.mode))
        if paper_checks:
            add("runtime.mode paper", config.runtime.mode == RuntimeMode.PAPER, str(config.runtime.mode))
            add("live.enabled false", not config.live.enabled, str(config.live.enabled))
            add("live hard_dollar_cap 0", config.live.hard_dollar_cap == 0, str(config.live.hard_dollar_cap))
        from parayabuck.config import credential_file_mode_warning, dotenv_path, load_env_file

        load_env_file()
        mode_warning = credential_file_mode_warning(dotenv_path()) if dotenv_path().is_file() else None
        permission_row = _credential_permission_row(mode_warning, strict=bool(args.strict))
        if permission_row is not None:
            # Plain doctor: WARN only. --strict (the unit's ExecStartPre): FAIL
            # and a blocker, so a group/other-readable credential file stops start.
            checks.append(("credential file owner-only", *permission_row))
            if permission_row[0] == "FAIL":
                blocker = True
        from parayabuck.live_safety import credential_env_names, mode_credentials

        live_mode_config = config.runtime.mode == RuntimeMode.LIVE
        api_key, secret = mode_credentials(live=live_mode_config)
        key_names = "/".join(credential_env_names(live=live_mode_config))
        add(
            "credentials present",
            bool(api_key and secret),
            f"present ({key_names})" if api_key and secret else f"missing ({key_names})",
        )
        if paper_checks:
            live_env = os.getenv("PARAYABUCK_ENABLE_LIVE", "")
            add(
                "no live endpoint in --paper",
                live_env.lower() not in {"1", "true", "yes"},
                "PARAYABUCK_ENABLE_LIVE not set",
            )
        if live_checks:
            _doctor_live_checks(config, add, checks, api_key=api_key, secret=secret)
        _doctor_email_checks(config, add, checks, required=live_checks)
        for label, path_str in [
            ("runtime data dir writable", str(Path(config.runtime.decision_db_path).parent)),
            ("reports path writable", config.backtest.output_dir),
            ("backup path writable", config.backup.path),
        ]:
            p = Path(path_str)
            try:
                p.mkdir(parents=True, exist_ok=True)
                test = p / ".doctor_write_test"
                test.write_text("ok")
                test.unlink()
                add(label, True, str(p))
            except Exception as exc:
                add(label, False, f"{p}: {exc}")
        # Backup freshness. Reported as a WARN row only, never as a blocker
        # or a strict warning: the unit runs `doctor --strict` as
        # ExecStartPre, and a stale backup must never keep the engine down.
        try:
            from parayabuck.backup import MAX_BACKUP_AGE_HOURS, latest_backup_age_hours

            age_hours = latest_backup_age_hours(config.backup.path)
            if age_hours is None:
                detail = f"no automatic backup in {config.backup.path}"
            else:
                detail = f"newest automatic backup {age_hours:.1f}h old (limit {MAX_BACKUP_AGE_HOURS:g}h)"
            if age_hours is None or age_hours > MAX_BACKUP_AGE_HOURS:
                add("backup freshness", True, detail, is_blocker=False)
                checks.append(("backup stale", "WARN", detail))
            else:
                add("backup freshness", True, detail)
        except Exception as exc:
            add("backup freshness", True, f"check skipped: {exc}", is_blocker=False)
        cfg_p = Path(config_path) if config_path and Path(config_path).exists() else Path("config/settings.yaml")
        try:
            cfg_p.parent.mkdir(parents=True, exist_ok=True)
            add("config state writable", os.access(cfg_p.parent, os.W_OK), str(cfg_p.parent))
        except Exception as exc:
            add("config state writable", False, str(exc))
        db_path = Path(config.runtime.decision_db_path)
        if db_path.exists():
            try:
                conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=5)
                try:
                    # busy_timeout and foreign_keys are per-connection; set before checking capability
                    try:
                        conn.execute("PRAGMA busy_timeout=5000")
                    except Exception:
                        pass
                    try:
                        conn.execute("PRAGMA foreign_keys=ON")
                    except Exception:
                        pass
                    row = conn.execute("PRAGMA integrity_check").fetchone()
                    add("sqlite integrity_check", row is not None and row[0] == "ok", row[0] if row else "no row")
                    jm = conn.execute("PRAGMA journal_mode").fetchone()
                    jm_val = str(jm[0]).lower() if jm and jm[0] else ""
                    # Informational only: a restored backup opens in DELETE mode
                    # until the engine sets WAL, and this read-only check cannot
                    # switch it. A WARN here would fail `doctor --strict` and
                    # keep the unit from ever starting the engine that sets WAL.
                    checks.append(("sqlite journal_mode", "INFO", jm_val or "unknown"))
                    fk = conn.execute("PRAGMA foreign_keys").fetchone()
                    fk_val = int(fk[0]) if fk and fk[0] is not None else 0
                    add("sqlite foreign_keys ON", fk_val == 1, str(fk_val), is_blocker=False)
                    bt = conn.execute("PRAGMA busy_timeout").fetchone()
                    bt_val = int(bt[0]) if bt and bt[0] is not None else 0
                    add("sqlite busy_timeout >=5000", bt_val >= 5000, str(bt_val), is_blocker=False)
                finally:
                    conn.close()
            except Exception as exc:
                add("sqlite checks", False, str(exc))
        else:
            add("sqlite exists", True, "no DB yet (fresh install)", is_blocker=False)
            try:
                import tempfile

                with tempfile.TemporaryDirectory() as td:
                    from parayabuck.storage import SQLiteStore

                    probe = Path(td) / "probe.sqlite3"
                    store = SQLiteStore(probe)
                    store.acquire()
                    try:
                        add("sqlite WAL capability", True, "probe WAL ok")
                    finally:
                        store.release()
            except Exception as exc:
                add("sqlite WAL capability", False, str(exc))
        if config.universe.equity.enabled:
            add("universe equity static", True, ",".join(config.universe.equity.symbols))
        else:
            add("universe equity disabled", True, "skipped")
        try:
            result = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True, timeout=5)
            tracked = result.stdout.decode(errors="ignore").split("\0")
            has_env = any(t == ".env" or t.endswith("/.env") for t in tracked)
            add("no tracked .env", not has_env, "tracked .env found" if has_env else "clean")
        except Exception as exc:
            if args.strict:
                # A scan that cannot run is not a clean scan: the unit's
                # ExecStartPre (--strict) fails closed.
                add("no tracked .env", False, f"tracked-file scan could not run: {exc}")
            else:
                add("no tracked .env", True, "git not available (skip)", is_blocker=False)
        try:
            raw: dict[str, object] = {}
            cfg_file = Path(config_path) if config_path and Path(config_path).exists() else Path("config/settings.yaml")
            if cfg_file.exists():
                raw = yaml.safe_load(cfg_file.read_text()) or {}
            digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
            add("config digest", True, digest)
        except Exception as exc:
            add("config digest", False, str(exc))
        try:
            ver = importlib.metadata.version("parayabuck")
            add("package version", True, ver)
        except Exception as exc:
            add("package version", False, str(exc), is_blocker=False)
        # Installed versions against the reviewed lock. The lock's own
        # "skip when unchanged" deploy rule means a missed pin bump would
        # otherwise run on old dependencies with doctor green. WARN only, so
        # it never blocks the start.
        lock_path = Path("requirements.lock")
        if not lock_path.is_file():
            checks.append(("locked pins", "WARN", f"{lock_path} not found in the working directory"))
        else:
            try:
                pins_checked, pin_differences = _locked_pin_mismatches(lock_path)
                if pin_differences:
                    shown = "; ".join(pin_differences[:5])
                    checks.append(
                        ("locked pins", "WARN", f"{len(pin_differences)} of {pins_checked} differ: {shown}")
                    )
                else:
                    checks.append(("locked pins", "PASS", f"{pins_checked} installed versions match the lock"))
            except Exception as exc:
                checks.append(("locked pins", "WARN", f"check skipped: {exc}"))
        if not args.offline and paper_checks and api_key and secret:
            broker = None
            try:
                broker = AlpacaBroker(api_key, secret, paper=True)
                broker._client.get_account()
                add("alpaca PAPER reachable", True, "account ok")
            except Exception as exc:
                msg = str(exc).lower()
                transient = any(
                    w in msg
                    for w in ["timeout", "timed out", "connection", "temporary", "unreachable", "dns", "network"]
                )
                if transient:
                    # Transient network blip is intentionally non-blocking even with --strict:
                    # it is informational WARN, does NOT set warning flag, so strict paper
                    # preflight still passes when the network is flaky. Documented explicitly.
                    add("alpaca PAPER reachable", True, f"transient: {exc}", is_blocker=False)
                    checks.append(("network transient", "WARN", str(exc)))
                else:
                    add("alpaca PAPER reachable", False, str(exc))
            finally:
                if broker is not None:
                    try:
                        broker.close()
                    except Exception:
                        pass
        elif args.offline:
            add("network offline", True, "--offline skip")
    except Exception as exc:
        log.error("parayabuck doctor: configuration could not be loaded or checked: %s", exc)
        add("config parses", False, str(exc))
        blocker = True
    print("=== ParayaBuck Doctor ===")
    for name, status, detail in checks:
        print(f"{status:4} {name}: {detail}")
    total_fail = sum(1 for _, s, _ in checks if s == "FAIL")
    if blocker:
        print(f"\nDoctor: {total_fail} failure(s) — BLOCKER", file=sys.stderr)
        return 1
    if warning and args.strict:
        print("\nDoctor: warnings present with --strict — BLOCKER", file=sys.stderr)
        return 1
    print(f"\nDoctor: {len(checks) - total_fail}/{len(checks)} checks passed — READY")
    return 0


def _doctor_live_checks(
    config: Config,
    add: Any,
    checks: list[tuple[str, str, str]],
    *,
    api_key: str,
    secret: str,
) -> None:
    """LIVE invariants for ``doctor --configured-mode`` (offline, no orders)."""
    from parayabuck.live_authorization import standing_authorization_status
    from parayabuck.live_safety import mode_credentials

    add("live.enabled true", config.live.enabled, str(config.live.enabled))
    add("live hard_dollar_cap > 0", config.live.hard_dollar_cap > 0, str(config.live.hard_dollar_cap))
    approval_path = config.live.operator_approval_path
    add("live.operator_approval_path set", bool(approval_path), approval_path or "missing")
    add(
        "PARAYABUCK_ENABLE_LIVE=YES",
        os.getenv("PARAYABUCK_ENABLE_LIVE") == "YES",
        "set" if os.getenv("PARAYABUCK_ENABLE_LIVE") == "YES" else "not YES",
    )
    hmac_key = os.getenv(config.live.operator_approval_key_env, "")
    add(
        "operator approval key >= 32 chars",
        len(hmac_key) >= 32,
        config.live.operator_approval_key_env + (" set" if len(hmac_key) >= 32 else " missing or short"),
    )
    paper_key, paper_secret = mode_credentials(live=False)
    distinct = not (api_key and secret and (api_key == paper_key or secret == paper_secret))
    add("LIVE keys differ from PAPER keys", distinct, "distinct" if distinct else "LIVE and PAPER share a key")
    add(
        "LIVE ledger separate from PAPER",
        os.path.normpath(config.runtime.decision_db_path) != os.path.normpath(DEFAULT_DECISION_DB_PATH),
        config.runtime.decision_db_path,
    )
    status = standing_authorization_status(config)
    # Informational only: without a standing authorization LIVE simply waits
    # for a manual re-arm after a restart (never a preflight blocker).
    label = "WARN" if status.status != "valid" or status.expiring() else "PASS"
    checks.append(("standing LIVE authorization", label, f"{status.status}: {status.detail}"))


def _doctor_email_checks(config: Config, add: Any, checks: list[tuple[str, str, str]], *, required: bool) -> None:
    """E-mail alert configuration check; never sends anything."""
    from parayabuck.monitoring.email_alerts import validate_email_setup

    email = config.alerting.email
    if not email.enabled:
        if required:
            add("e-mail alerts enabled (LIVE)", False, "alerting.email.enabled is false; LIVE needs off-host alerts")
        else:
            add("e-mail alerts", True, "disabled (alerts stay on this host)")
        return
    problems = validate_email_setup(email)
    detail = (
        f"{email.security} {email.smtp_host}:{email.smtp_port} -> {len(email.to_addresses)} recipient(s)"
        if not problems
        else "; ".join(problems)
    )
    if required:
        add("e-mail alerts valid", not problems, detail)
    elif problems:
        # PAPER: a broken mail setup is reported but never keeps the engine
        # down (informational WARN that does not trip --strict).
        checks.append(("e-mail alerts", "WARN", detail))
    else:
        add("e-mail alerts valid", True, detail)


# ---------------------------------------------------------------------------
# Ops commands — operational observability (Mission 5)
# ---------------------------------------------------------------------------


def _parse_time_arg(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    # relative: 30m, 2h, 1d, 5s
    import re

    m = re.fullmatch(r"(\d+)([smhd])", value)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        delta = {
            "s": timedelta(seconds=num),
            "m": timedelta(minutes=num),
            "h": timedelta(hours=num),
            "d": timedelta(days=num),
        }[unit]
        return datetime.now(UTC) - delta
    # ISO
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        # The journal compares ISO text, so an offset such as +03:00 must be
        # rewritten to UTC first or the window is shifted by the offset.
        return dt.astimezone(UTC)
    except Exception:
        raise SystemExit(f"Invalid time value: {value} (use ISO or 30m/2h/1d)")


def _run_ops(args: argparse.Namespace) -> int:
    cmd = getattr(args, "ops_cmd", None)
    if cmd is None or cmd == "status":
        return _ops_status(args)
    if cmd == "doctor":
        return _ops_doctor(args)
    if cmd == "events":
        return _ops_events(args)
    if cmd == "timeline":
        return _ops_timeline(args)
    if cmd in {"explain", "decision"}:
        return _ops_explain(args)
    if cmd == "bundle":
        return _ops_bundle(args)
    if cmd == "rebase-ownership":
        return _ops_rebase_ownership(args)
    if cmd == "test-email":
        return _ops_test_email(args)
    print(f"Unknown ops command: {cmd}", file=sys.stderr)
    return 2


def _ops_test_email(args: argparse.Namespace) -> int:
    """Validate the alerting.email settings; send ONE test e-mail only with --confirm.

    Explicit operator command: it is never run by the engine, the units or the
    tests. Without --confirm nothing leaves the host.
    """
    from parayabuck.monitoring.email_alerts import send_test_email, validate_email_setup

    try:
        config = load_config(getattr(args, "config", None))
    except Exception as exc:
        print(f"Configuration could not be loaded: {exc}", file=sys.stderr)
        return 1
    email = config.alerting.email
    if not email.enabled:
        print("alerting.email.enabled is false: e-mail alerts are off (nothing to test)")
        return 1
    problems = validate_email_setup(email)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1
    target = ", ".join(email.to_addresses)
    if not getattr(args, "confirm", False):
        print(f"E-mail settings look valid ({email.security} {email.smtp_host}:{email.smtp_port} -> {target}).")
        print("Nothing was sent. Re-run with --confirm to send one test e-mail.")
        return 0
    try:
        send_test_email(email, mode=config.runtime.mode.value)
    except Exception as exc:
        print(f"Test e-mail FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Test e-mail sent to {target}.")
    return 0


def _snapshot_runtime_health(snapshot: object) -> dict[str, Any]:
    """The RuntimeHealth block of a health snapshot (wrapped or flat)."""
    if not isinstance(snapshot, dict):
        return {}
    inner = snapshot.get("runtime_health")
    return inner if isinstance(inner, dict) else snapshot


def _intent_watchdog_status_line(runtime_health: dict[str, Any]) -> str:
    """``ops status`` line for the intent watchdog (as in /readyz)."""
    label = f"{'Watchdog':<14}"
    watchdog = runtime_health.get("intent_watchdog")
    if not isinstance(watchdog, dict) or not watchdog:
        return f"{label} UNKNOWN  — no intent watchdog data in snapshot"
    if watchdog.get("enabled") is False:
        return f"{label} DISABLED"
    if not watchdog.get("evaluated_at"):
        return f"{label} PENDING  — not evaluated yet"
    alarms = [alarm for alarm in watchdog.get("alarms") or () if isinstance(alarm, dict)]
    if alarms:
        detail = "; ".join(f"{alarm.get('code')}: {alarm.get('message')}" for alarm in alarms)
        return f"{label} ALARM    — {detail}"
    return f"{label} OK       — no alarms (evaluated {watchdog.get('evaluated_at')})"


def _signal_cross_check_status_line(runtime_health: dict[str, Any]) -> str:
    """``ops status`` line for the IEX-vs-SIP signal cross-check."""
    label = f"{'Cross-check':<14}"
    cross_check = runtime_health.get("signal_cross_check")
    if not isinstance(cross_check, dict) or not cross_check:
        return f"{label} UNKNOWN  — no signal cross-check data in snapshot"
    if cross_check.get("enabled") is False:
        return f"{label} DISABLED"
    pending = cross_check.get("pending")
    if isinstance(pending, dict) and pending:
        sessions = ", ".join(sorted(str(session) for session in pending))
        return f"{label} PENDING  — waiting for the SIP daily bar: {sessions}"
    last = cross_check.get("last")
    if not isinstance(last, dict):
        return f"{label} NONE     — no session checked since start"
    status = str(last.get("status") or "unknown")
    detail = f"session {last.get('session_date')}"
    if last.get("hold"):
        detail += ", position held"
    if last.get("close_divergence"):
        detail += ", close divergence"
    if last.get("reason") and status != "agree":
        detail += f" ({last.get('reason')})"
    return f"{label} {status.upper():<8} — {detail}"


def _ops_status(args: argparse.Namespace) -> int:
    # Load config optionally
    try:
        config = load_config(getattr(args, "config", None))
    except Exception:
        config = Config()
    # Explicit flags win; only an absent flag falls back to the configured paths.
    # Dropping them here made the relative data/runtime defaults answer for a
    # database and snapshot the operator never asked about.
    snapshot_path = getattr(args, "snapshot", None) or config.observability.snapshot_path
    db_path = getattr(args, "db", None) or config.runtime.decision_db_path
    from parayabuck.monitoring.health import evaluate_health

    health = evaluate_health(snapshot_path=snapshot_path, database_path=db_path, config=config)
    if getattr(args, "json", False):
        print(json.dumps(health, indent=2, default=str))
        return _health_exit_code(health.get("overall_health"))
    # Text formatting similar to spec
    # Determine snapshot age string
    age = health.get("snapshot_age_seconds")
    age_str = f"{age:.0f}s ago" if isinstance(age, (int, float)) else "unknown"
    overall = health.get("overall_health", "UNKNOWN")
    trading = health.get("trading_state", "UNKNOWN")
    # Try to extract session / mode from snapshot
    snap_data = None
    try:
        import json as _json
        from pathlib import Path as _P

        p = _P(snapshot_path)
        if p.exists():
            snap_data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        snap_data = None
    mode = (snap_data.get("mode") if isinstance(snap_data, dict) else None) or config.runtime.mode.value
    session = (snap_data.get("session_id") if isinstance(snap_data, dict) else None) or "—"
    # Build checks map
    checks = {c["code"]: c for c in health.get("checks", []) if isinstance(c, dict)}

    def _check_line(code: str, label: str) -> str:
        c = checks.get(code)
        if not c:
            return f"{label:<14} UNKNOWN"
        status = c.get("status", "UNKNOWN")
        summary = c.get("summary", "")
        return f"{label:<14} {status:<8} — {summary}"

    print(f"ParayaBuck {str(mode).upper()}")
    print("=" * 16)
    print(f"Runtime       {overall}")
    print(f"Trading       {trading}")
    # Heartbeat
    hb = checks.get("runtime_heartbeat", {})
    print(f"Heartbeat     {hb.get('summary', 'unknown')} ({age_str})")
    print(f"Session       {session}")
    print("")
    # Markets
    print(_check_line("workers", "Workers"))
    print(_check_line("reconciliation", "Reconcile"))
    print(_check_line("account", "Account"))
    print(_check_line("signal_adjustment", "Adjustment"))
    runtime_health = _snapshot_runtime_health(snap_data)
    print(_intent_watchdog_status_line(runtime_health))
    print(_signal_cross_check_status_line(runtime_health))
    print(_check_line("market_halts", "Market halts"))
    print("")
    # Queue
    q = checks.get("queue", {})
    print(f"Queue         {q.get('observed', '—')}  {q.get('summary', '')}")
    # Risk
    print("")
    print("Risk")
    print(f"  {_check_line('risk', 'risk').strip()}")
    # Execution
    print("")
    print("Execution")
    print(f"  {_check_line('execution', 'execution').strip()}")
    # Alerts are local only (log + event journal); show the engine's counters
    print("")
    print("Alerts (local: `parayabuck ops events`, journalctl -u parayabuck)")
    alert_counts = snap_data.get("alerts") if isinstance(snap_data, dict) else None
    if isinstance(alert_counts, dict):
        delivered = alert_counts.get("delivered")
        emitted = sum(int(v or 0) for v in delivered.values()) if isinstance(delivered, dict) else 0
        print(f"  emitted {emitted}, throttled repeats {int(alert_counts.get('suppressed') or 0)}")
    print(f"  {_check_line('observability', 'journal').strip()}")
    # Active incidents summary if present
    try:
        from parayabuck.monitoring import metrics as _m

        snap = _m.runtime_metrics(database_path=db_path)
        inc = (snap.get("incidents") or {}).get("active") if isinstance(snap.get("incidents"), dict) else None  # type: ignore[attr-defined]
        if isinstance(inc, list) and inc:
            print("")
            print("Active incidents")
            for it in inc[:5]:
                print(f"  - {it}")
    except Exception:
        pass
    # Observability degradation
    return _health_exit_code(overall)


def _health_exit_code(overall: object) -> int:
    """Exit code for an overall health label, as ``ops doctor`` maps it.

    CRITICAL is 2; BLOCKED, DEGRADED and UNKNOWN are 1; OK is 0. A script that
    polls ``ops status`` must not read a CRITICAL runtime as success.
    """
    if overall == "CRITICAL":
        return 2
    if overall in {"BLOCKED", "DEGRADED", "UNKNOWN"}:
        return 1
    return 0


def _ops_doctor(args: argparse.Namespace) -> int:
    # Use diagnostic _run_doctor_checks
    config_path = _configured_config_path(args) or "config/settings.yaml"
    snapshot_path = getattr(args, "snapshot", None) or "data/runtime/health_snapshot.json"
    journal_path = getattr(args, "journal", None) or "data/runtime/observability.sqlite3"
    db_path = getattr(args, "db", None)
    # Try to get db from config if not provided
    try:
        cfg = load_config(config_path if Path(config_path).exists() else None)
        if not db_path:
            db_path = cfg.runtime.decision_db_path
        # Only an absent flag falls back to the configured path: an explicit
        # flag that happens to equal the relative default must still win.
        if not getattr(args, "snapshot", None):
            try:
                snapshot_path = cfg.observability.snapshot_path
            except Exception:
                pass
        if not getattr(args, "journal", None):
            try:
                journal_path = cfg.observability.journal_path
            except Exception:
                pass
    except Exception:
        if not db_path:
            db_path = "data/runtime/parayabuck.sqlite3"
    try:
        from parayabuck.monitoring.diagnostic import _run_doctor_checks

        result = _run_doctor_checks(
            config_path=config_path, snapshot_path=snapshot_path, journal_path=journal_path, decision_db_path=db_path
        )
    except Exception as exc:
        print(f"Doctor failed: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str))
    else:
        print("ParayaBuck Ops Doctor")
        print("=====================")
        for chk in result.get("checks", []):
            print(f"{chk.get('status', 'UNKNOWN'):9} {chk.get('code', '')}: {chk.get('summary', '')}")
        print(f"\nOverall: {result.get('overall', 'UNKNOWN')}")
    overall = result.get("overall", "UNKNOWN")
    if overall == "CRITICAL":
        return 2
    if overall in {"DEGRADED", "UNKNOWN"}:
        # UNKNOWN is degraded for script friendliness? spec says 1=degraded/warnings, 2=critical
        # Map UNKNOWN to 1 unless caller expects 2
        return 1
    return 0


def _ops_events(args: argparse.Namespace) -> int:
    since = _parse_time_arg(getattr(args, "since", None))
    until = _parse_time_arg(getattr(args, "until", None))
    journal_path = getattr(args, "journal", None) or "data/runtime/observability.sqlite3"
    # allow config override
    try:
        cfg = load_config(getattr(args, "config", None))
        if not getattr(args, "journal", None):
            try:
                journal_path = cfg.observability.journal_path
            except Exception:
                pass
    except Exception:
        pass
    from parayabuck.monitoring.journal import EventJournal

    # Handle missing DB gracefully
    if not Path(journal_path).exists():
        if getattr(args, "json", False):
            print(json.dumps([], indent=2))
        else:
            print("No event journal found — fresh install (no events)")
        return 0
    j = EventJournal.open_readonly(journal_path)
    events = j.query(
        since=since,
        until=until,
        kind=getattr(args, "kind", None),
        severity=getattr(args, "severity", None),
        market=getattr(args, "market", None),
        decision_id=getattr(args, "decision", None),
        client_order_id=getattr(args, "order", None),
        incident_id=getattr(args, "incident", None),
        limit=int(getattr(args, "limit", 50) or 50),
    )
    if getattr(args, "json", False):
        print(json.dumps([e.to_dict() for e in events], indent=2, default=str))
    else:
        if not events:
            print("No events matching filter")
        for e in events:
            ts = e.occurred_at.isoformat()
            corr = ""
            if e.decision_id:
                corr += f" decision={e.decision_id[:8]}"
            if e.client_order_id:
                corr += f" order={e.client_order_id[:8]}"
            print(f"{ts} {e.severity:8} {e.kind:30} {e.message}{corr}")
    return 0


def _ops_timeline(args: argparse.Namespace) -> int:
    decision_id = getattr(args, "decision", None)
    order_id = getattr(args, "order", None)
    incident_id = getattr(args, "incident", None)
    journal_path = getattr(args, "journal", None) or "data/runtime/observability.sqlite3"
    db_path = getattr(args, "db", None)
    # allow config override
    try:
        cfg = load_config(getattr(args, "config", None))
        if not getattr(args, "journal", None):
            try:
                journal_path = cfg.observability.journal_path
            except Exception:
                pass
        if not db_path:
            db_path = cfg.runtime.decision_db_path
    except Exception:
        if not db_path:
            db_path = "data/runtime/parayabuck.sqlite3"
    limit = int(getattr(args, "limit", 100) or 100)
    events: list[Any] = []
    # Query journal
    try:
        from parayabuck.monitoring.journal import EventJournal

        if Path(journal_path).exists():
            j = EventJournal.open_readonly(journal_path)
            if decision_id:
                events = j.query(decision_id=decision_id, limit=limit)
            elif order_id:
                events = j.query(client_order_id=order_id, limit=limit)
            elif incident_id:
                events = j.query(incident_id=incident_id, limit=limit)
            else:
                events = j.query(limit=limit)
    except Exception as exc:
        print(f"timeline journal query failed: {exc}", file=sys.stderr)
    # Also reconstruct decision if decision_id
    reconstruction = None
    if decision_id:
        try:
            from parayabuck.monitoring.forensics import decision_reconstruction

            reconstruction = decision_reconstruction(db_path, decision_id, journal_path=journal_path)
        except Exception as exc:
            reconstruction = {"error": str(exc)}
    if getattr(args, "json", False):
        payload = {
            "events": [e.to_dict() if hasattr(e, "to_dict") else e for e in events],
            "reconstruction": reconstruction,
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0
    # Text output
    if decision_id:
        print(f"Timeline for decision {decision_id}")
        print("=" * 40)
        if reconstruction:
            dec = reconstruction.get("decision")
            if dec:
                print(f"Decision: {dec}")
            orders = reconstruction.get("orders") or []
            print(f"Orders: {len(orders)}")
            for o in orders[:10]:
                print(
                    f"  {o.get('client_order_id')} {o.get('symbol')} {o.get('side')} {o.get('quantity')} {o.get('status')}"
                )
            fills = reconstruction.get("fills") or []
            print(f"Fills: {len(fills)}")
            rb = reconstruction.get("rebalance")
            if rb:
                print(f"Rebalance: {rb}")
            receipt = reconstruction.get("receipt")
            if receipt:
                print(f"Receipt: {receipt.get('decision_timestamp') if isinstance(receipt, dict) else receipt}")
    elif order_id:
        print(f"Timeline for order {order_id}")
        print("=" * 40)
    elif incident_id:
        print(f"Timeline for incident {incident_id}")
        print("=" * 40)
    else:
        print("Recent timeline")
        print("=" * 40)
    if not events:
        print("No operational events found for correlation")
    else:
        for e in events:
            ts = e.occurred_at.isoformat() if hasattr(e, "occurred_at") else str(e.get("occurred_at"))
            kind = e.kind if hasattr(e, "kind") else e.get("kind")
            msg = e.message if hasattr(e, "message") else e.get("message")
            print(f"{ts} {kind:30} {msg}")
            if hasattr(e, "fields") and e.fields:
                print(f"  fields: {e.fields}")
    return 0


def _print_explain_empty(args: argparse.Namespace, message: str) -> None:
    """Report that there is no decision to explain; --json stays one JSON object."""
    if getattr(args, "json", False):
        print(json.dumps({"decision": None, "message": message}))
    else:
        print(message)


def _ops_explain(args: argparse.Namespace) -> int:
    target = getattr(args, "target", "latest") or "latest"
    db_path = getattr(args, "db", None)
    journal_path = getattr(args, "journal", None) or "data/runtime/observability.sqlite3"
    snapshot_path = "data/runtime/health_snapshot.json"
    config_path = getattr(args, "config", None)
    cfg = None
    try:
        cfg = load_config(config_path)
        snapshot_path = cfg.observability.snapshot_path
        if not db_path:
            db_path = cfg.runtime.decision_db_path
        if not getattr(args, "journal", None):
            try:
                journal_path = cfg.observability.journal_path
            except Exception:
                pass
    except Exception:
        if not db_path:
            db_path = "data/runtime/parayabuck.sqlite3"
    # Find latest decision if needed
    decision_id = target if target != "latest" else None
    if decision_id is None:
        # Query latest portfolio_decisions
        try:
            import sqlite3

            if Path(str(db_path)).exists():
                from parayabuck.monitoring.forensics import expected_session_id, latest_decision_row

                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
                conn.row_factory = sqlite3.Row
                try:
                    # Scoped to the current account (after a credential rotation
                    # the old account's decision is not "latest") and to the
                    # engine's session: when that session persisted no decision,
                    # say so rather than explaining the previous session.
                    session_id = expected_session_id(conn)
                    row = latest_decision_row(
                        conn, "decision_id, decision_timestamp, market, strategy_name", session_id=session_id
                    )
                    if row:
                        decision_id = row["decision_id"]
                        # --json keeps stdout a single parseable document.
                        print(
                            f"Latest decision: {decision_id} at {row['decision_timestamp']} market={row['market']} strategy={row['strategy_name']}",
                            file=sys.stderr if getattr(args, "json", False) else sys.stdout,
                        )
                    elif session_id is not None:
                        _print_explain_empty(args, f"no decision persisted for {session_id}")
                        return 0
                    else:
                        _print_explain_empty(args, "No decisions found in DB")
                        return 0
                finally:
                    conn.close()
            else:
                _print_explain_empty(args, "No decision DB found — fresh install")
                return 0
        except Exception as exc:
            print(f"Failed to find latest decision: {exc}", file=sys.stderr)
            return 1
    # Reconstruct
    try:
        from parayabuck.monitoring.forensics import (
            UNKNOWN_NO_TRADE_REASON,
            decision_reconstruction,
            map_no_trade_reason,
        )
        from parayabuck.monitoring.journal import EventJournal

        recon = decision_reconstruction(db_path, decision_id, journal_path=journal_path)
        decision = recon.get("decision")
        orders = recon.get("orders") or []
        fills = recon.get("fills") or []
        rebalance = recon.get("rebalance")
        # Also query decision_summary events
        summary_events = []
        if Path(str(journal_path)).exists():
            j = EventJournal.open_readonly(str(journal_path))
            summary_events = [e for e in j.query(decision_id=decision_id, limit=20) if e.kind == "decision_summary"]
        # Determine no-trade reason
        no_trade_reason = None
        detail = ""
        if not orders:
            # Try to infer from decision target/risk or summary event
            if summary_events:
                se = summary_events[0]
                # Canonicalised like the target-JSON reason below: the engine's
                # raw hold string is not printed as the cause.
                raw_reason = se.fields.get("no_trade_reason") or se.fields.get("decision_status")
                no_trade_reason = map_no_trade_reason(str(raw_reason)) if raw_reason else None
                detail = str(se.fields)
            elif decision:
                try:
                    target_json = decision.get("target_json") if isinstance(decision, dict) else None
                    if target_json:
                        import json as _json

                        tj = _json.loads(target_json) if isinstance(target_json, str) else target_json
                        # heuristic: if target unchanged?
                        no_trade_reason = map_no_trade_reason(str(tj.get("reason") or tj.get("no_trade_reason") or ""))
                        detail = str(tj)[:500]
                except Exception:
                    pass
            if not no_trade_reason or no_trade_reason == UNKNOWN_NO_TRADE_REASON:
                # No evidence names a cause (summary event pruned, target JSON
                # carries no recognised reason): say so, with the surfaces
                # checked, instead of the "Normal" NO_TARGET_CHANGE.
                no_trade_reason = UNKNOWN_NO_TRADE_REASON
                checked = "checked: journal decision_summary (none), decision target reason (none recognised)"
                detail = f"{checked}; {detail}" if detail else checked
        # Build explanation
        if getattr(args, "json", False):
            payload = {
                "decision_id": decision_id,
                "decision": decision,
                "orders": orders,
                "fills": fills,
                "rebalance": rebalance,
                "no_trade_reason": no_trade_reason,
                "detail": detail,
                "reconstruction": recon,
                "summary_events": [e.to_dict() for e in summary_events],
            }
            print(json.dumps(payload, indent=2, default=str))
            return 0
        # Text
        if orders:
            print(f"\nDECISION TRADED — {len(orders)} orders")
            for o in orders:
                print(
                    f"  {o.get('client_order_id')} {o.get('symbol')} {o.get('side')} qty={o.get('quantity')} status={o.get('status')}"
                )
            if fills:
                print(f"Fills: {len(fills)}")
            if rebalance:
                print(f"Rebalance phase: {rebalance.get('phase')}")
        else:
            print("\nNO TRADE")
            print(f"Reason: {no_trade_reason}")
            if detail:
                print(f"Detail: {detail}")
            if decision:
                # show target change if available
                try:
                    import json as _json

                    tj = decision.get("target_json") if isinstance(decision, dict) else None
                    if tj:
                        parsed = _json.loads(tj) if isinstance(tj, str) else tj
                        print(f"Target: {str(parsed)[:500]}")
                    rj = decision.get("risk_json") if isinstance(decision, dict) else None
                    if rj:
                        parsed = _json.loads(rj) if isinstance(rj, str) else rj
                        print(f"Risk: {str(parsed)[:500]}")
                except Exception:
                    pass
            # Check account/universe staleness etc via health
            try:
                from parayabuck.monitoring.health import evaluate_health

                # Judge health with the config this command resolved (defaults
                # when it failed to load), never a CONFIG_PATH/cwd re-load.
                h = evaluate_health(
                    snapshot_path=snapshot_path,
                    database_path=db_path,
                    config=cfg if cfg is not None else Config(),
                )
                for chk in h.get("checks", []):
                    if chk.get("status") in {"BLOCKED", "DEGRADED", "CRITICAL"}:
                        print(f"Health {chk.get('code')}: {chk.get('summary')}")
            except Exception:
                pass
        return 0
    except Exception as exc:
        print(f"Explain failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


def _ops_bundle(args: argparse.Namespace) -> int:
    output = getattr(args, "output", None)
    snapshot_path = getattr(args, "snapshot", None) or "data/runtime/health_snapshot.json"
    journal_path = getattr(args, "journal", None) or "data/runtime/observability.sqlite3"
    db_path = getattr(args, "db", None)
    config_path = _configured_config_path(args) or "config/settings.yaml"
    # Allow config overrides
    try:
        cfg = load_config(config_path if Path(config_path).exists() else None)
        if not getattr(args, "snapshot", None):
            try:
                snapshot_path = cfg.observability.snapshot_path
            except Exception:
                pass
        if not getattr(args, "journal", None):
            try:
                journal_path = cfg.observability.journal_path
            except Exception:
                pass
        if not getattr(args, "db", None):
            db_path = cfg.runtime.decision_db_path
    except Exception:
        if not db_path:
            db_path = "data/runtime/parayabuck.sqlite3"
    try:
        from parayabuck.monitoring.diagnostic import create_bundle_zip

        out = create_bundle_zip(
            output,
            config_path=config_path,
            snapshot_path=snapshot_path,
            journal_path=journal_path,
            decision_db_path=db_path,
        )
        print(f"Bundle written: {out}")
        # print size
        try:
            sz = out.stat().st_size
            print(f"Size: {sz} bytes")
        except Exception:
            pass
        return 0
    except Exception as exc:
        print(f"Bundle failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


def _ops_rebase_ownership(args: argparse.Namespace) -> int:
    """Adopt the broker's share count for one symbol (the recovery for a
    stock split or a foreign trade the runtime could not prove).

    Dry run by default: the plan is printed and nothing is written without
    ``--confirm``. The broker quantity is an operator input on purpose — this
    is the verified recovery path, not a second guess at the broker. An
    audited adjustment row records the change; open orders are refused because
    the broker quantity they would move is not final yet.
    """
    from parayabuck.portfolio.runtime_repository import PortfolioStateRepository

    symbol = canonical_symbol(str(args.symbol).strip().upper(), Market.EQUITY)
    broker_qty = float(args.broker_qty)
    if not isfinite(broker_qty) or broker_qty < 0:
        print(f"Invalid --broker-qty {args.broker_qty!r}: must be finite and >= 0", file=sys.stderr)
        return 2
    broker_average_cost = getattr(args, "broker_average_cost", None)
    if broker_average_cost is not None:
        broker_average_cost = float(broker_average_cost)
        if not isfinite(broker_average_cost) or broker_average_cost <= 0:
            print(
                f"Invalid --broker-average-cost {args.broker_average_cost!r}: must be finite and > 0",
                file=sys.stderr,
            )
            return 2
    db_path = getattr(args, "db", None)
    if not db_path:
        # A write command must never fall back to the default config: that
        # could rebase ownership in a different database than the engine's.
        try:
            db_path = load_config(getattr(args, "config", None)).runtime.decision_db_path
        except Exception as exc:
            print(f"Cannot load the configuration ({exc}); fix it or pass --db explicitly.", file=sys.stderr)
            return 2
    repository = PortfolioStateRepository(db_path)
    try:
        account_id = getattr(args, "account_id", None) or repository.latest_account_id()
        if not account_id:
            print(
                "No runtime session account in this database; pass --account-id explicitly.",
                file=sys.stderr,
            )
            return 1
        owned = repository.owned_quantities(account_id, Market.EQUITY).get(symbol, 0.0)
        if owned <= 1e-12 and broker_qty <= 1e-12:
            print(f"{symbol}: account {account_id} owns nothing and the broker holds nothing; nothing to rebase.")
            return 0
        if owned <= 1e-12 and not getattr(args, "confirm", False):
            print(f"{symbol}: account {account_id} owns nothing; nothing to rebase.", file=sys.stderr)
            print("Re-run with --confirm to adopt the broker quantity as an explicit operator rebase.")
            return 1
        pending = repository.open_orders(account_id)
        if pending:
            print(
                f"Refusing: {len(pending)} non-terminal portfolio order(s) for account {account_id}. "
                "Wait for them to settle (or stop the engine) so the broker quantity is final.",
                file=sys.stderr,
            )
            return 1
        note = str(getattr(args, "note", "") or "").strip()
        print(f"Symbol      : {symbol} (market equity)")
        print(f"Account     : {account_id}")
        print(f"Database    : {db_path}")
        print(f"Owned now   : {owned:g}")
        print(f"Broker says : {broker_qty:g}")
        print(f"Adjustment  : {broker_qty - owned:+g} ({'increase' if broker_qty > owned else 'decrease'})")
        if abs(broker_qty - owned) <= 1e-12:
            print("Nothing to do: ownership already matches the broker quantity.")
            return 0
        if not getattr(args, "confirm", False):
            print("Dry run. Re-run with --confirm to write the audited adjustment.")
            return 0
        repository.rebase_ownership_to_broker_quantity(
            account_id,
            symbol,
            Market.EQUITY,
            broker_qty,
            note=note,
            broker_average_cost=broker_average_cost,
        )
        log.warning(
            "Operator ownership rebase: account=%s symbol=%s owned=%s -> %s",
            account_id,
            symbol,
            owned,
            broker_qty,
        )
        print("Ownership rebased. Reset the safety lock once reconciliation is clean.")
        return 0
    finally:
        repository.close()


def _load_bars(
    config: Config,
    args: argparse.Namespace,
    market: Market,
    start: datetime | None,
    end: datetime | None,
) -> tuple[list[Bar], str | None]:
    """Bars for the run plus the feed they actually came from.

    The second element is ``None`` when the bars came from an offline CSV: no
    provider was asked, so no feed may be claimed. A download reports the
    provider's EFFECTIVE historical feed, which is IEX when a configured SIP
    request hit an entitlement downgrade.
    """
    if args.data:
        return load_csv_bars(args.data, default_market=market), None
    from parayabuck.config import load_env_file

    load_env_file()
    from parayabuck.live_safety import mode_credentials

    key, secret = mode_credentials(live=False)
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for downloads")
    symbols = _candidate_symbols(config, market, args.symbols)
    if not symbols:
        raise SystemExit("No candidate symbols are available")
    selection = config.strategy.equity
    end_at = end or datetime.now(UTC)
    if start is None:
        start_at = end_at - timedelta(days=5 * 365)
    else:
        # Download enough history before the requested evaluation start so the
        # strategy warmup has real bars.
        # run_product_backtest splits warmup from the evaluation window. The
        # session density is anchored at the evaluation start (a 2020 backtest
        # must size its warmup with the 2020 holiday calendar, not today's).
        warmup_days = _warmup_window_days(config, end_date=start)
        start_at = start - timedelta(days=warmup_days)
    provider = AlpacaHistoricalData(
        key,
        secret,
        stock_feed=config.alpaca.stock_historical_feed,
        data_delayed=config.alpaca.data_delayed,
        sip_embargo_minutes=config.signal_cross_check.sip_embargo_minutes,
    )
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    minimum_bars = strategy.minimum_history_bars
    # Bounded warmup extension: a symbol with fewer pre-start bars than the
    # strategy declares would silently drop out of the early evaluation
    # window. Re-fetch deficient symbols with an extended window (at most
    # three attempts), then surface any residual shortfall loudly instead of
    # letting the backtest run with truncated history.
    by_symbol: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
    attempts = 0
    try:
        while attempts < 3:
            attempts += 1
            expected_per_symbol = max(
                1,
                int((end_at - start_at).total_seconds() / 86_400) * (24 if selection.timeframe == "1h" else 1),
            )
            batch_size = max(1, min(100, 9_000 // expected_per_symbol))
            for offset in range(0, len(symbols), batch_size):
                batch = symbols[offset : offset + batch_size]
                downloaded = provider.get_bars_batch(
                    batch,
                    strategy.timeframe,
                    start=start_at,
                    end=end_at,
                    adjustment="splits",
                )
                for symbol in batch:
                    by_symbol[symbol] = [bar for bar in downloaded.get(symbol, ()) if bar.timestamp >= start_at]
            deficient = [
                symbol
                for symbol in symbols
                if start is not None and sum(1 for bar in by_symbol[symbol] if bar.timestamp < start) < minimum_bars
            ]
            if not deficient:
                break
            # Extend the window backward for every symbol. The shortfall is
            # converted to calendar days anchored at the evaluation start.
            from parayabuck.markets.timeframes import bars_to_calendar_days

            extra_days = bars_to_calendar_days(
                strategy.timeframe,
                minimum_bars,
                end_date=start,
            )
            extended_start = min(
                start_at,
                start - timedelta(days=extra_days) if start is not None else start_at,
            )
            # A window that does not move would only re-download the same bars:
            # stop and report the residual shortfall instead.
            if attempts >= 3 or extended_start >= start_at:
                log.warning(
                    "Warmup still short of %d pre-start bars for %d symbol(s) after %d download attempt(s): %s",
                    minimum_bars,
                    len(deficient),
                    attempts,
                    ", ".join(sorted(deficient)[:20]),
                )
                break
            start_at = extended_start
        effective_feed = str(provider.feed_status()["effective"])
    finally:
        provider.close()
    return [bar for symbol in symbols for bar in by_symbol.get(symbol, ())], effective_feed


def _warmup_window_days(config: Config, end_date: datetime | None = None) -> int:
    """Calendar days of pre-start history for strategy warmup.

    ``end_date`` anchors the strategy-warmup span at the evaluation start so
    a historical backtest sizes its warmup with that era's session density.
    """
    selection = config.strategy.equity
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    from parayabuck.markets.timeframes import bars_to_calendar_days

    # Strategy warmup is expressed in bars of the strategy timeframe (e.g. 150
    # daily bars). Bars are converted to a calendar span through the number of
    # trading sessions, not 1 bar == 1 calendar day.
    return bars_to_calendar_days(
        strategy.timeframe,
        strategy.minimum_history_bars,
        end_date=end_date,
    )


def _candidate_symbols(
    config: Config,
    market: Market,
    explicit: str | None,
) -> list[str]:
    if explicit:
        # Canonical keys: the download batch is keyed by canonical symbols.
        return list(dict.fromkeys(canonical_symbol(item, market) for item in explicit.split(",") if item.strip()))
    return list(config.universe.equity.symbols)


def _strategy_parameters(config: Config, raw_overrides: list[str]) -> dict[str, object]:
    selection = config.strategy.equity
    parameters = dict(selection.parameters)
    for item in raw_overrides:
        if "=" not in item:
            raise SystemExit(f"Invalid --param {item!r}; expected NAME=VALUE")
        name, value = item.split("=", 1)
        parameters[name] = yaml.safe_load(value)
    return parameters


def _parse_grid(items: list[str]) -> dict[str, list[object]]:
    grid: dict[str, list[object]] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --grid {item!r}; expected NAME=V1,V2")
        name, values = item.split("=", 1)
        parsed = [yaml.safe_load(value) for value in values.split(",")]
        if not name or not parsed:
            raise SystemExit(f"Invalid --grid {item!r}")
        grid[name] = parsed
    return grid


def _date(value: str, *, end_of_day: bool = False) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if end_of_day and len(value) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _print_summary(summary: dict[str, object], output_dir: str) -> None:
    def number(name: str) -> float:
        value = summary[name]
        if not isinstance(value, (int, float)):
            raise TypeError(f"Backtest metric {name} must be numeric")
        return float(value)

    print(
        f"{summary['strategy_name']} | {summary['start_date']} -> {summary['end_date']} | "
        f"return={number('total_return'):.2%} "
        f"sharpe={number('sharpe'):.2f} "
        f"max_dd={number('max_drawdown'):.2%} "
        f"trades={summary['number_of_trades']}"
    )
    print(f"Results: {output_dir}")
