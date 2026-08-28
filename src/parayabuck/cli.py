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
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from parayabuck.backtest.product import (
    load_csv_bars,
    load_csv_corporate_actions,
    optimize_grid,
    run_product_backtest,
)
from parayabuck.config import Config, RuntimeMode, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data import AlpacaHistoricalData
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.portfolio import portfolio_risk_limits_from_config
from parayabuck.live_safety import LiveOperatorApproval
from parayabuck.logger import get_logger
from parayabuck.portfolio import portfolio_strategy_from, product_strategy_from
from parayabuck.portfolio.runtime_service import configuration_digest
from parayabuck.universe import (
    ensure_active_universes,
    load_active_crypto_symbols,
    load_active_symbols,
    validate_active_crypto_universe,
    validate_active_universe,
)
from parayabuck.universe.crypto_scanner import crypto_scanner_config_digest
from parayabuck.universe.scanner import scanner_config_digest
from parayabuck.utils.io import atomic_write

log = get_logger("parayabuck.cli")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parayabuck", description="ParayaBuck trading bot")
    parser.add_argument("--config", default=None, help="configuration YAML path")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("web", help="start the local operations console")
    backup = commands.add_parser("backup", help="consistent SQLite backup of the runtime decision database")
    backup.add_argument(
        "--retention-days",
        type=float,
        default=None,
        help="override the configured backup retention (days)",
    )
    backup.add_argument(
        "--verify-only",
        type=str,
        default=None,
        help="verify integrity of an existing backup file instead of creating one",
    )
    doctor = commands.add_parser("doctor", help="read-only deployment preflight (no orders)")
    doctor.add_argument("--strict", action="store_true", help="fail on warnings")
    doctor.add_argument("--paper", action="store_true", help="require paper mode invariants")
    doctor.add_argument("--offline", action="store_true", help="skip network reachability checks")
    authorize = commands.add_parser("authorize-live", help="create a short-lived account/config-bound live approval")
    authorize.add_argument("--operator", required=True)
    authorize.add_argument("--expires-minutes", type=int, default=15)
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
    research = commands.add_parser("research", help="run research batch with invariant reuse and parallel trials")
    # Subcommands for Mission 3 experiment platform (plan/run/resume/inspect/compare)
    research_sub = research.add_subparsers(dest="research_cmd", help="experiment platform")
    # plan
    plan_p = research_sub.add_parser("plan", help="resolve experiment without running")
    plan_p.add_argument("spec", help="spec YAML/JSON file")
    plan_p.add_argument("--workers", default=None, help="override workers (1, N, auto)")
    plan_p.add_argument("--output-dir", default=None, help="override output parent dir")
    plan_p.add_argument("--allow-large", action="store_true", help="allow grid explosion beyond limit")
    plan_p.add_argument("--config", default=None, help="config YAML path")
    # run
    run_p = research_sub.add_parser("run", help="run experiment (or resume if --resume)")
    run_p.add_argument("spec", help="spec YAML/JSON file")
    run_p.add_argument("--workers", default=None, help="override workers")
    run_p.add_argument("--output-dir", default=None, help="override output parent dir")
    run_p.add_argument("--allow-large", action="store_true", help="allow large grid")
    run_p.add_argument("--config", default=None, help="config YAML path")
    run_p.add_argument("--resume", action="store_true", help="resume if run exists (validate experiment_id)")
    run_p.add_argument("--retry-failed", action="store_true", help="retry failed trials on resume")
    run_p.add_argument("--fail-fast", action="store_true", help="fail fast")
    # resume
    resume_p = research_sub.add_parser("resume", help="resume an existing run directory")
    resume_p.add_argument("run_dir", help="research_runs/<run> directory")
    resume_p.add_argument("--retry-failed", action="store_true", help="retry failed trials")
    resume_p.add_argument("--workers", default=None, help="override workers for resume")
    resume_p.add_argument("--config", default=None, help="config YAML path")
    # inspect
    inspect_p = research_sub.add_parser("inspect", help="inspect a run (read-only)")
    inspect_p.add_argument("run_dir", help="research_runs/<run> directory")
    inspect_p.add_argument("--json", action="store_true", help="json output")
    # compare
    compare_p = research_sub.add_parser("compare", help="compare two runs")
    compare_p.add_argument("run_a", help="first run directory")
    compare_p.add_argument("run_b", help="second run directory")
    compare_p.add_argument("--output", default=None, help="optional output JSON path")
    # list
    list_p = research_sub.add_parser("list", help="list research runs", aliases=["ls"])
    list_p.add_argument("--root", default=None, help="research runs parent dir (default research_runs)")
    list_p.add_argument("--status", default=None, help="filter by status")
    list_p.add_argument("--strategy", default=None, help="filter by strategy")
    list_p.add_argument("--market", default=None, help="filter by market")
    list_p.add_argument("--tag", default=None, help="filter by tag")
    list_p.add_argument("--limit", type=int, default=None, help="limit rows")
    list_p.add_argument(
        "--sort", choices=("created", "best_sharpe", "best_cagr", "status"), default="created", help="sort key"
    )
    list_p.add_argument("--json", action="store_true", help="json output")
    # top
    top_p = research_sub.add_parser("top", help="show top trials for a run")
    top_p.add_argument("run_dir", help="research_runs/<run> directory")
    top_p.add_argument(
        "--metric",
        choices=("sharpe", "sortino", "cagr", "calmar", "max_drawdown", "turnover"),
        default="sharpe",
        help="metric to rank",
    )
    top_p.add_argument("--limit", type=int, default=10, help="top N")
    top_p.add_argument("--json", action="store_true", help="json output")
    research.add_argument("legacy_spec", nargs="?", help=argparse.SUPPRESS)
    research.add_argument("--fail-fast", action="store_true", help="fail fast on first trial error")
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
    doctor_p.add_argument("--journal", default=None)
    events_p = ops_sub.add_parser("events", help="query operational event journal")
    events_p.add_argument("--since", default=None, help="since time (ISO or 30m,2h,1d)")
    events_p.add_argument("--until", default=None, help="until time (ISO or relative)")
    events_p.add_argument("--kind", default=None)
    events_p.add_argument("--severity", default=None)
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
    bundle_p = ops_sub.add_parser("bundle", help="sanitized diagnostic bundle")
    bundle_p.add_argument("--output", default=None)
    bundle_p.add_argument("--snapshot", default=None)
    bundle_p.add_argument("--journal", default=None)
    bundle_p.add_argument("--db", default=None)
    return parser


def _add_backtest_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_cost_stress: bool,
) -> None:
    parser.add_argument("--market", choices=("equity", "crypto"), default="equity")
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--data", help="offline CSV containing OHLCV bars")
    parser.add_argument(
        "--corporate-actions",
        help="optional CSV of explicit equity dividends (split-adjusted prices are expected)",
    )
    parser.add_argument("--symbols", help="comma-separated download candidates")
    parser.add_argument("--candidate-limit", type=int, default=None)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--initial-cash", type=float, default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--param", action="append", default=[], metavar="NAME=VALUE", help="strategy override")
    if include_cost_stress:
        parser.add_argument(
            "--cost-stress",
            action="store_true",
            help="also run 2x and 3x slippage/spread/crypto-fee assumptions",
        )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        from parayabuck.web.app import main as web_main

        web_main()
        return 0
    args = _parser().parse_args(arguments)
    if args.command in {None, "web"}:
        from parayabuck.web.app import main as web_main

        web_main()
        return 0
    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "ops":
        return _run_ops(args)
    config = load_config(args.config)
    if args.command == "authorize-live":
        return _authorize_live(config, args)
    if args.command == "backup":
        return _run_backup(config, args)
    if args.command == "research":
        return _run_research(config, args)
    market = Market(args.market)
    start = _date(args.start) if args.start else None
    end = _date(args.end, end_of_day=True) if args.end else None
    bars = _load_bars(config, args, market, start, end)
    corporate_actions = load_csv_corporate_actions(args.corporate_actions) if args.corporate_actions else None
    if args.command == "optimize":
        grid = _parse_grid(args.grid)
        parameters = _strategy_parameters(config, market, args.param)
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
        )
        print(json.dumps(payload, indent=2, default=str))
        return 0
    parameters = _strategy_parameters(config, market, args.param)
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
            data_feed=config.alpaca.stock_historical_feed,
        )
        _print_summary(run.report.summary, run.output_dir)
    return 0


def backtest_main(argv: list[str] | None = None) -> int:
    return main(["backtest", *(sys.argv[1:] if argv is None else argv)])


def _authorize_live(config: Config, args: argparse.Namespace) -> int:
    if not config.live.enabled or config.live.hard_dollar_cap <= 0:
        raise SystemExit("live.enabled=true and a positive live.hard_dollar_cap are required")
    if not config.live.operator_approval_path:
        raise SystemExit("live.operator_approval_path is required")
    if not 1 <= args.expires_minutes <= 1440:
        raise SystemExit("--expires-minutes must be between 1 and 1440")
    load_dotenv()
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    hmac_secret = os.getenv(config.live.operator_approval_key_env, "")
    if not key or not secret:
        raise SystemExit("Live Alpaca credentials are required")
    if len(hmac_secret) < 32:
        # Fail here, BEFORE the live-endpoint broker probe, with the same
        # clean SystemExit style as every other missing prerequisite —
        # LiveOperatorApproval.calculated_signature would only raise a raw
        # ValueError at signing time.
        raise SystemExit(f"{config.live.operator_approval_key_env} must be set to a key of at least 32 characters")
    equity_universe = config.universe.equity
    if config.strategy.equity.enabled and equity_universe.enabled and equity_universe.dynamic_selection_enabled:
        _symbols, status = validate_active_universe(equity_universe)
        if status != "success" and not equity_universe.allow_static_fallback_trading:
            raise SystemExit(f"Live authorization requires a valid dynamic equity universe; status={status}")
    crypto_universe = config.universe.crypto
    if config.strategy.crypto.enabled and crypto_universe.enabled and crypto_universe.dynamic_selection_enabled:
        _symbols, status = validate_active_crypto_universe(crypto_universe)
        if status != "success" and not crypto_universe.allow_static_fallback_trading:
            raise SystemExit(f"Live authorization requires a valid dynamic crypto universe; status={status}")
    broker = AlpacaBroker(key, secret, paper=False)
    try:
        broker.account()  # Read-only identity verification against the live endpoint.
        account_suffix = broker.account_suffix
        account_id = broker.account_id or ""
    finally:
        # Temporary live-credential verification sessions must be closed.
        broker.close()
    digests: list[str] = []
    for market, selection, symbols, universe_version in (
        (
            Market.EQUITY,
            config.strategy.equity,
            load_active_symbols(config.universe.equity, log_rejection=False),
            scanner_config_digest(config.universe.equity),
        ),
        (
            Market.CRYPTO,
            config.strategy.crypto,
            load_active_crypto_symbols(config.universe.crypto, log_rejection=False),
            crypto_scanner_config_digest(config.universe.crypto),
        ),
    ):
        if not selection.enabled:
            continue
        if not symbols:
            raise SystemExit(f"No active {market.value} universe is available")
        # Live gate must use the product surface: only PAPER-supported strategies
        # may produce a live approval. Research paths keep portfolio_strategy_from.
        strategy = product_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
        digests.append(
            configuration_digest(
                strategy,
                "live",
                selection.maximum_allocation,
                universe_version,
                portfolio_risk_limits_from_config(config.risk),
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


def _run_backup(config: Config, args: argparse.Namespace) -> int:
    """Snapshot the runtime decision database with the sqlite3 online backup
    API (safe while the runtime keeps the WAL database open), then prune
    backups older than the retention window.

    With --verify-only, verify an existing backup instead of creating one.
    """
    from parayabuck.backup import backup_database, verify_backup

    if args.verify_only:
        target = Path(args.verify_only)
        ok = verify_backup(target)
        if ok:
            print(f"Backup OK: {target}")
            return 0
        print(f"Backup CORRUPT: {target}", file=sys.stderr)
        return 1
    retention_days = args.retention_days if args.retention_days is not None else config.backup.retention_days
    destination = backup_database(
        config.runtime.decision_db_path,
        config.backup.path,
        retention_days=retention_days,
    )
    if not verify_backup(destination):
        raise SystemExit(f"Backup failed integrity check: {destination}")
    print(f"Backup written: {destination}")
    print(f"Retention: {retention_days:g} day(s); older backups pruned")
    return 0


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

    config_path = args.config
    config: Config | None = None
    try:
        config = load_config(config_path)
        add("config parses", True, str(config_path or "config/settings.yaml"))
        for market_name in ("equity", "crypto"):
            sel = config.strategy.equity if market_name == "equity" else config.strategy.crypto
            if sel.enabled:
                try:
                    strat = product_strategy_from(sel.name, sel.parameters, timeframe=sel.timeframe)
                    add(
                        f"strategy {market_name} {sel.name}",
                        True,
                        f"{strat.spec().version} {strat.timeframe} alloc {sel.maximum_allocation}",
                    )
                except Exception as exc:
                    add(f"strategy {market_name} {sel.name}", False, str(exc))
            else:
                add(f"strategy {market_name} disabled", True, "skipped")
        if args.paper:
            add("runtime.mode paper", config.runtime.mode == RuntimeMode.PAPER, str(config.runtime.mode))
            add("live.enabled false", not config.live.enabled, str(config.live.enabled))
            add("live hard_dollar_cap 0", config.live.hard_dollar_cap == 0, str(config.live.hard_dollar_cap))
        env_path = Path(os.getenv("ENV_PATH") or ".env")
        load_dotenv(env_path, override=False)
        load_dotenv(Path(".env"), override=False)
        api_key = os.getenv("ALPACA_API_KEY", "").strip()
        secret = os.getenv("ALPACA_SECRET_KEY", "").strip()
        add("credentials present", bool(api_key and secret), "present" if api_key and secret else "missing")
        if args.paper:
            live_env = os.getenv("PARAYABUCK_ENABLE_LIVE", "")
            add(
                "no live endpoint in --paper",
                live_env.lower() not in {"1", "true", "yes"},
                "PARAYABUCK_ENABLE_LIVE not set",
            )
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
        cfg_p = Path(config_path) if config_path and Path(config_path).exists() else Path("config/settings.yaml")
        if not cfg_p.exists():
            cfg_p = Path("config/settings.yaml")
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
                    add("sqlite journal_mode WAL", jm_val == "wal", jm_val, is_blocker=False)
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
                    add("sqlite WAL capability", True, "probe WAL ok")
                    store.connection.close()
            except Exception as exc:
                add("sqlite WAL capability", False, str(exc))
        for market in (Market.EQUITY, Market.CRYPTO):
            uni_cfg = config.universe.equity if market == Market.EQUITY else config.universe.crypto
            strat_sel = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
            if not uni_cfg.enabled:
                add(f"universe {market.value} disabled", True, "skipped")
                continue
            if not uni_cfg.dynamic_selection_enabled:
                # Static universe - no snapshot validation required.
                add(f"universe {market.value} static", True, "dynamic_selection disabled")
                continue
            # Enabled dynamic universe: use canonical validators.
            # Validators evaluate: snapshot exists, JSON parses, freshness vs
            # active_universe_max_age_hours, scanner config digest match,
            # observation strategy identity (name/version/timeframe/params digest),
            # and scanner version.
            if market == Market.EQUITY:
                eq_cfg = config.universe.equity
                symbols, status = validate_active_universe(eq_cfg)
                cfg_digest = scanner_config_digest(eq_cfg)[:12]
            else:
                cr_cfg = config.universe.crypto
                symbols, status = validate_active_crypto_universe(cr_cfg)
                cfg_digest = crypto_scanner_config_digest(cr_cfg)[:12]
            obs = f"{uni_cfg.observation_strategy_name}/{uni_cfg.observation_strategy_version}/{uni_cfg.observation_strategy_timeframe}"
            detail = (
                f"status={status} symbols={len(symbols)} cfg={cfg_digest} obs={obs} path={uni_cfg.active_universe_path}"
            )
            if status == "success":
                add(f"universe {market.value} active", True, detail)
            else:
                requires = (
                    config.runtime.mode == RuntimeMode.PAPER
                    and strat_sel.enabled
                    and uni_cfg.enabled
                    and uni_cfg.dynamic_selection_enabled
                )
                fallback_allowed = uni_cfg.allow_static_fallback_trading
                if requires and not fallback_allowed:
                    if args.offline:
                        # Offline preflight (ExecStartPre): snapshot stale/missing but runtime
                        # can refresh after network startup. Classify as REFRESH_NEEDED
                        # explicitly, not PASS. Non-blocking even with --strict so first
                        # boot / ExecStartPre does not fail when network is still down.
                        checks.append((f"universe {market.value} active", "REFRESH_NEEDED", detail))
                    else:
                        # Strict paper online (no --offline): fail-closed, runtime would halt.
                        add(f"universe {market.value} active", False, detail)
                else:
                    # Degraded: fallback allowed or non-PAPER runtime; emit WARN, not FAIL.
                    # WARN means strict failure (warning=True) except for transient network
                    # below which is explicitly non-blocking.
                    checks.append((f"universe {market.value} active", "WARN", detail))
                    warning = True
        try:
            result = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True, timeout=5)
            tracked = result.stdout.decode(errors="ignore").split("\0")
            has_env = any(t == ".env" or t.endswith("/.env") for t in tracked)
            add("no tracked .env", not has_env, "tracked .env found" if has_env else "clean")
        except Exception:
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
        if not args.offline and args.paper and api_key and secret:
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
        return dt
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
    print(f"Unknown ops command: {cmd}", file=sys.stderr)
    return 2


def _ops_status(args: argparse.Namespace) -> int:
    # Load config optionally
    config = None
    try:
        config = load_config(getattr(args, "config", None))
    except Exception:
        from parayabuck.config import Config as _Cfg

        config = _Cfg()
    snapshot_path = (
        getattr(args, "snapshot", None)
        or getattr(config, "observability", None)
        and getattr(config.observability, "snapshot_path", None)
        or "data/runtime/health_snapshot.json"
    )
    db_path = (
        getattr(args, "db", None)
        or getattr(config, "runtime", None)
        and getattr(config.runtime, "decision_db_path", None)
        or "data/runtime/parayabuck.sqlite3"
    )
    # allow explicit snapshot path via config.observability
    if config is not None and hasattr(config, "observability"):
        if not getattr(args, "snapshot", None):
            snapshot_path = config.observability.snapshot_path
        if not getattr(args, "db", None):
            db_path = config.runtime.decision_db_path
    from parayabuck.monitoring.health import evaluate_health

    health = evaluate_health(snapshot_path=snapshot_path, database_path=db_path, config=config)
    if getattr(args, "json", False):
        print(json.dumps(health, indent=2, default=str))
        return 0
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
    mode = (snap_data.get("mode") if isinstance(snap_data, dict) else None) or (
        config.runtime.mode.value if config and hasattr(config, "runtime") else "unknown"
    )
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
    for market_label in ("equity", "crypto"):
        # health markets are per market? We have workers check etc. For now show from checks
        pass
    print(_check_line("workers", "Workers"))
    print(_check_line("reconciliation", "Reconcile"))
    print(_check_line("account", "Account"))
    print(_check_line("universe", "Universe"))
    print(_check_line("signal_adjustment", "Adjustment"))
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
    # Alerts
    print("")
    print("Alerts")
    print(f"  {_check_line('alerts', 'alerts').strip()}")
    # Observability
    print(f"  {_check_line('observability', 'observability').strip()}")
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
    return 0


def _ops_doctor(args: argparse.Namespace) -> int:
    # Use diagnostic _run_doctor_checks
    config_path = getattr(args, "config", None) or "config/settings.yaml"
    snapshot_path = getattr(args, "snapshot", None) or "data/runtime/health_snapshot.json"
    journal_path = getattr(args, "journal", None) or "data/runtime/observability.sqlite3"
    db_path = getattr(args, "db", None)
    # Try to get db from config if not provided
    if not db_path:
        try:
            cfg = load_config(config_path if Path(config_path).exists() else None)
            db_path = cfg.runtime.decision_db_path
            if not snapshot_path or snapshot_path == "data/runtime/health_snapshot.json":
                # allow observability snapshot path
                try:
                    snapshot_path = cfg.observability.snapshot_path
                except Exception:
                    pass
            if not journal_path or journal_path == "data/runtime/observability.sqlite3":
                try:
                    journal_path = cfg.observability.journal_path
                except Exception:
                    pass
        except Exception:
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
    j = EventJournal(journal_path)
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
    if not db_path:
        try:
            cfg = load_config(getattr(args, "config", None))
            db_path = cfg.runtime.decision_db_path
            if not getattr(args, "journal", None):
                try:
                    journal_path = cfg.observability.journal_path
                except Exception:
                    pass
        except Exception:
            db_path = "data/runtime/parayabuck.sqlite3"
    limit = int(getattr(args, "limit", 100) or 100)
    events: list[Any] = []
    # Query journal
    try:
        from parayabuck.monitoring.journal import EventJournal

        if Path(journal_path).exists():
            j = EventJournal(journal_path)
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


def _ops_explain(args: argparse.Namespace) -> int:
    target = getattr(args, "target", "latest") or "latest"
    db_path = getattr(args, "db", None)
    journal_path = getattr(args, "journal", None) or "data/runtime/observability.sqlite3"
    config_path = getattr(args, "config", None)
    cfg = None
    try:
        cfg = load_config(config_path)
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
                conn = sqlite3.connect(str(db_path), timeout=2.0)
                conn.row_factory = sqlite3.Row
                try:
                    cur = conn.execute(
                        "SELECT decision_id, decision_timestamp, market, strategy_name FROM portfolio_decisions ORDER BY decision_timestamp DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row:
                        decision_id = row["decision_id"]
                        print(
                            f"Latest decision: {decision_id} at {row['decision_timestamp']} market={row['market']} strategy={row['strategy_name']}"
                        )
                    else:
                        print("No decisions found in DB")
                        return 0
                finally:
                    conn.close()
            else:
                print("No decision DB found — fresh install")
                return 0
        except Exception as exc:
            print(f"Failed to find latest decision: {exc}", file=sys.stderr)
            return 1
    # Reconstruct
    try:
        from parayabuck.monitoring.forensics import decision_reconstruction, map_no_trade_reason
        from parayabuck.monitoring.journal import EventJournal

        recon = decision_reconstruction(db_path, decision_id, journal_path=journal_path)
        decision = recon.get("decision")
        orders = recon.get("orders") or []
        fills = recon.get("fills") or []
        rebalance = recon.get("rebalance")
        # Also query decision_summary events
        summary_events = []
        if Path(str(journal_path)).exists():
            j = EventJournal(str(journal_path))
            summary_events = [e for e in j.query(decision_id=decision_id, limit=20) if e.kind == "decision_summary"]
        # Determine no-trade reason
        no_trade_reason = None
        detail = ""
        if not orders:
            # Try to infer from decision target/risk or summary event
            if summary_events:
                se = summary_events[0]
                no_trade_reason = se.fields.get("no_trade_reason") or se.fields.get("decision_status")
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
            if not no_trade_reason:
                no_trade_reason = "NO_TARGET_CHANGE"
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

                h = evaluate_health(database_path=db_path)
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
    config_path = getattr(args, "config", None) or "config/settings.yaml"
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


def _load_bars(
    config: Config,
    args: argparse.Namespace,
    market: Market,
    start: datetime | None,
    end: datetime | None,
) -> list[Bar]:
    if args.data:
        return load_csv_bars(args.data, default_market=market)
    load_dotenv()
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for downloads")
    ensure_active_universes(config, key, secret)
    symbols = _candidate_symbols(config, market, args.symbols, args.candidate_limit)
    if not symbols:
        raise SystemExit("No candidate symbols are available")
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    end_at = end or datetime.now(UTC)
    if start is None:
        start_at = end_at - timedelta(days=5 * 365 if market == Market.EQUITY else 2 * 365)
    else:
        # Download enough history before the requested evaluation start so the
        # strategy warmup and the trailing scanner lookback have real bars.
        # run_product_backtest splits warmup from the evaluation window. The
        # session density is anchored at the evaluation start (a 2020 backtest
        # must size its warmup with the 2020 holiday calendar, not today's).
        warmup_days = _warmup_window_days(config, market, end_date=start)
        start_at = start - timedelta(days=warmup_days)
    provider = AlpacaHistoricalData(
        key,
        secret,
        stock_feed=config.alpaca.stock_historical_feed,
        data_delayed=config.alpaca.data_delayed,
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
                    adjustment="splits" if market == Market.EQUITY else "raw",
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
            if attempts >= 3:
                log.warning(
                    "Warmup still short of %d pre-start bars for %d symbol(s) after 3 extension attempts: %s",
                    minimum_bars,
                    len(deficient),
                    ", ".join(sorted(deficient)[:20]),
                )
                continue
            # Extend the window backward for the deficient symbols only. The
            # shortfall is converted to calendar days anchored at the evaluation
            # start, plus a growth buffer so the next attempt makes real progress.
            from parayabuck.markets.timeframes import bars_to_calendar_days

            extra_days = bars_to_calendar_days(
                "SPY" if market == Market.EQUITY else "BTC/USD",
                strategy.timeframe,
                minimum_bars,
                end_date=start,
            )
            symbols_to_retry = sorted(set(deficient))
            symbols = [s for s in symbols if s not in symbols_to_retry] + symbols_to_retry
            start_at = min(
                start_at,
                start - timedelta(days=extra_days) if start is not None else start_at,
            )
    finally:
        provider.close()
    return [bar for symbol in symbols for bar in by_symbol.get(symbol, ())]


def _warmup_window_days(config: Config, market: Market, end_date: datetime | None = None) -> int:
    """Calendar days of pre-start history for strategy and scanner warmup.

    ``end_date`` anchors the strategy-warmup span at the evaluation start so
    a historical backtest sizes its warmup with that era's session density.
    """
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    lookback_days = 60 if market == Market.EQUITY else config.universe.crypto.scanner.lookback_days
    from parayabuck.markets.timeframes import bars_to_calendar_days

    # Strategy warmup is expressed in bars of the strategy timeframe (e.g. 150
    # daily bars); the scanner lookback is already calendar days and must not
    # be scaled by the bar duration. Bars are converted to a calendar span
    # through the number of trading sessions, not 1 bar == 1 calendar day.
    strategy_days = bars_to_calendar_days(
        "SPY" if market == Market.EQUITY else "BTC/USD",
        strategy.timeframe,
        strategy.minimum_history_bars,
        end_date=end_date,
    )
    return max(strategy_days, lookback_days)


def _candidate_symbols(
    config: Config,
    market: Market,
    explicit: str | None,
    limit: int | None,
) -> list[str]:
    if explicit:
        return list(dict.fromkeys(item.strip().upper() for item in explicit.split(",") if item.strip()))
    if market == Market.EQUITY:
        # Always download the broad eligible universe. The historical Stage-A
        # shortlist is applied per date inside the backtest universe builder,
        # so 2022 liquidity cannot be hidden behind today's shortlist.
        from alpaca.trading.client import TradingClient

        from parayabuck.universe.preflight import eligible_equity_symbols

        load_dotenv()
        trading = TradingClient(
            os.getenv("ALPACA_API_KEY", ""),
            os.getenv("ALPACA_SECRET_KEY", ""),
            paper=config.runtime.mode.value != "live",
        )
        if (session := getattr(trading, "_session", None)) is not None:
            from parayabuck.utils.http import patch_session_timeout

            patch_session_timeout(session, timeout=15.0)
        eligible = eligible_equity_symbols(config, trading, limit=limit)
        # The scanner reserves references, approved inverse ETFs and pinned
        # symbols in every snapshot: those must be present even when an
        # explicit --candidate-limit excludes them alphabetically, otherwise
        # the backtest gate cannot evaluate a period at all.
        equity_config = config.universe.equity
        required_order = list(
            dict.fromkeys(
                (
                    *equity_config.reference_symbols,
                    *equity_config.inverse_trade_symbols,
                    *equity_config.fallback_trade_symbols,
                )
            )
        )
        required_set = set(required_order)
        candidates = [symbol for symbol in dict.fromkeys(eligible) if symbol not in required_set]
        if limit is not None:
            candidates = candidates[: max(0, limit - len(required_order))]
        return [*required_order, *candidates]
    source = Path(config.universe.crypto.active_universe_path)
    payload = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
    candidates = [str(symbol) for symbol in payload.get("candidate_symbols", ())]
    symbols = candidates or list(config.universe.crypto.trade_symbols)
    required = config.universe.crypto.pinned_symbols
    required_order = list(dict.fromkeys(required))
    required_set = set(required_order)
    candidates = [symbol for symbol in dict.fromkeys(symbols) if symbol not in required_set]
    return [*required_order, *candidates[: max(0, limit or len(candidates))]]


def _strategy_parameters(config: Config, market: Market, raw_overrides: list[str]) -> dict[str, object]:
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
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


def _run_research(config: Config, args: argparse.Namespace) -> int:
    # Dispatch to Mission 3 experiment platform if subcommand present
    cmd = getattr(args, "research_cmd", None)
    if cmd == "plan":
        return _run_research_plan(config, args)
    if cmd == "run":
        return _run_research_experiment_run(config, args)
    if cmd == "resume":
        return _run_research_resume(config, args)
    if cmd == "inspect":
        return _run_research_inspect(config, args)
    if cmd == "compare":
        return _run_research_compare(config, args)
    if cmd in ("list", "ls"):
        return _run_research_list(config, args)
    if cmd == "top":
        return _run_research_top(config, args)
    import json
    from pathlib import Path

    from parayabuck.core.models import Market
    from parayabuck.research import ResearchJob, ResearchTier, ResearchTrial
    from parayabuck.research.runner import ResearchRunner

    tier = ResearchTier(args.tier)
    workers: int | str = args.workers
    market = Market(args.market)
    # Base parameters from --param
    base_params: dict[str, object] | None = {}
    for item in args.param:
        if "=" not in item:
            raise SystemExit(f"Invalid --param {item!r}; expected NAME=VALUE")
        name, value = item.split("=", 1)
        base_params[name] = __import__("yaml").safe_load(value)  # type: ignore[index]
    if not base_params:
        base_params = None
    start = _date(args.start) if args.start else None
    end = _date(args.end, end_of_day=True) if args.end else None
    trials: list[ResearchTrial] = []
    legacy_spec = getattr(args, "legacy_spec", None)
    if legacy_spec:
        spec_path = Path(legacy_spec)
        if not spec_path.exists():
            raise SystemExit(f"Spec file not found: {spec_path}")
        raw = spec_path.read_text(encoding="utf-8")
        if spec_path.suffix in {".yaml", ".yml"}:
            spec = __import__("yaml").safe_load(raw)
        else:
            spec = json.loads(raw)
        if not isinstance(spec, dict):
            raise SystemExit("Spec must be a mapping")
        # Override CLI if spec provides values
        if "tier" in spec:
            tier = ResearchTier(str(spec["tier"]))
        if "workers" in spec:
            workers = spec["workers"]
        if "market" in spec:
            market = Market(str(spec["market"]))
        if "strategy" in spec and spec["strategy"]:
            args.strategy = str(spec["strategy"])
        if "dataset" in spec and spec["dataset"]:
            args.data = str(spec["dataset"])
        if "start" in spec and spec["start"]:
            start = _date(str(spec["start"]))
        if "end" in spec and spec["end"]:
            end = _date(str(spec["end"]), end_of_day=True)
        if "base_parameters" in spec and isinstance(spec["base_parameters"], dict):
            base_params = dict(spec["base_parameters"])
        if "trials" in spec and isinstance(spec["trials"], list):
            for idx, t in enumerate(spec["trials"]):
                if not isinstance(t, dict):
                    raise SystemExit(f"Trial {idx} must be a mapping")
                trials.append(
                    ResearchTrial(
                        label=str(t.get("label", f"trial_{idx}")),
                        parameters=t.get("parameters"),
                        cost_multiplier=float(t.get("cost_multiplier", 1.0)),
                        metadata=dict(t.get("metadata", {})),
                        execution_overrides=t.get("execution_overrides"),
                    )
                )
        if "output_dir" in spec and spec["output_dir"]:
            args.output_dir = str(spec["output_dir"])
    if not trials:
        # Build from CLI cost_stress or single
        if args.cost_stress:
            for mult in (1.0, 2.0, 3.0):
                trials.append(ResearchTrial(label=f"cost_{mult}x", cost_multiplier=mult))
        else:
            trials.append(ResearchTrial(label="trial_0", cost_multiplier=1.0))
    job = ResearchJob(
        market=market,
        strategy_name=args.strategy,
        dataset_path=args.data,
        start=start,
        end=end,
        base_parameters=base_params,
        trials=tuple(trials),
        tier=tier,
        workers=workers,
        persist_artifacts=args.persist_artifacts,
        persist_db=None,
        output_dir=args.output_dir,
        fail_fast=bool(args.fail_fast),
    )
    runner = ResearchRunner(config=config)
    result = runner.run(job)
    # Print summary
    print(f"Research batch: tier={result.tier} workers={result.workers} elapsed={result.elapsed_s:.2f}s")
    print(f"Universe reused: {result.universe_reused} prepared reused: {result.prepared_reused}")
    for r in result.results:
        if r.status == "ok":
            print(
                f"{r.label}: return={r.total_return:.4f} cagr={r.cagr:.4f} sharpe={r.sharpe:.4f} trades={r.trade_count} elapsed={r.elapsed_s:.2f}s"
            )
        else:
            print(f"{r.label}: FAILED {r.error}")
    # Optionally write results JSON if output_dir
    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        payload = {
            "tier": str(result.tier),
            "workers": result.workers,
            "elapsed_s": result.elapsed_s,
            "results": [
                {
                    "label": r.label,
                    "status": r.status,
                    "total_return": r.total_return,
                    "cagr": r.cagr,
                    "sharpe": r.sharpe,
                    "sortino": r.sortino,
                    "max_drawdown": r.max_drawdown,
                    "trade_count": r.trade_count,
                    "elapsed_s": r.elapsed_s,
                    "error": r.error,
                }
                for r in result.results
            ],
        }
        (out / "research_results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


def _run_research_plan(config: Config, args: argparse.Namespace) -> int:
    from parayabuck.config import load_config
    from parayabuck.research.experiment import plan_experiment, resolve_experiment

    cfg = load_config(args.config) if getattr(args, "config", None) else config
    try:
        resolved = resolve_experiment(
            args.spec,
            config=cfg,
            workers_override=getattr(args, "workers", None),
            allow_large=bool(getattr(args, "allow_large", False)),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    # Try to prepare invariants for cache estimates (best effort, no failure)
    inv = None
    try:
        from parayabuck.research.experiment import _prepare_invariants

        inv = _prepare_invariants(resolved.job, cfg, enable_cache=True)
    except Exception:
        inv = None
    plan = plan_experiment(resolved, inv)
    # Human-readable output
    print(f"Experiment: {plan['name']}")
    print(f"Experiment ID: {plan['experiment_id']}")
    print(f"Strategy: {plan['strategy']}")
    print(f"Market: {plan['market']} Tier: {plan['tier']}")
    ds = plan.get("dataset") or "(synthetic/default)"
    print(f"Dataset: {ds}")
    if plan.get("dataset_fingerprint_id"):
        print(f"Dataset fingerprint: {plan['dataset_fingerprint_id']}")
    win = plan.get("window", {})
    print(f"Window: {win.get('start')} -> {win.get('end')}")
    print(f"Trials: {plan['trial_count']}")
    print(f"Workers: {plan['workers_resolved']} (requested {plan['workers_requested']})")
    print(
        f"Estimated reusable inputs: bars cache: {'hit' if plan.get('bars_cache_hit') else 'miss' if plan.get('bars_cache_hit') is not None else 'unknown'}"
    )
    print(
        f"  universe cache: {'hit' if plan.get('universe_cache_hit') else 'miss' if plan.get('universe_cache_hit') is not None else 'unknown'}"
    )
    print(f"  numeric history: {plan.get('numeric_history')}")
    if plan.get("baseline"):
        print(f"Baseline: {plan['baseline']}")
    if plan.get("tags"):
        print(f"Tags: {', '.join(plan['tags'])}")
    print("Trial list:")
    for t in plan.get("trials", []):
        print(f"  - {t['label']} (id {t['trial_id']}) params={t['parameters']}")
    return 0


def _run_research_experiment_run(config: Config, args: argparse.Namespace) -> int:
    from pathlib import Path

    from parayabuck.config import load_config
    from parayabuck.research.experiment import resolve_experiment, run_experiment
    from parayabuck.research.persistence import ExperimentStore

    cfg = load_config(args.config) if getattr(args, "config", None) else config
    workers_override = getattr(args, "workers", None)
    try:
        resolved = resolve_experiment(
            args.spec,
            config=cfg,
            workers_override=workers_override,
            allow_large=bool(getattr(args, "allow_large", False)),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    # Determine output base
    output_base = getattr(args, "output_dir", None)
    # If --resume and output_base is None, try to find latest run for this experiment? Spec says
    # `research run spec.yaml --resume` should resume existing. We need to locate existing run dir by experiment_id.
    if bool(getattr(args, "resume", False)):
        # If output_base points to a run_dir that exists, use it
        if output_base and Path(output_base).exists() and (Path(output_base) / "manifest.json").exists():
            store = ExperimentStore.load(Path(output_base))
            store.validate_resume(resolved.experiment_id)
            store = run_experiment(
                resolved, cfg, store=store, resume=True, retry_failed=bool(getattr(args, "retry_failed", False))
            )
            print(f"Resumed: {store.run_dir}")
            print(
                f"Completed: {len([t for t in store.load_trials() if t.get('status') == 'ok'])} Failed: {len([t for t in store.load_trials() if t.get('status') == 'failed'])}"
            )
            return 0
        # Otherwise, search for existing run with same experiment_id under research_runs
        parent = Path(output_base) if output_base else Path("research_runs")
        candidates: list[Path] = []
        if parent.exists():
            for child in parent.iterdir():
                if child.is_dir() and (child / "manifest.json").exists():
                    try:
                        m = ExperimentStore.load(child).load_manifest()
                        if m.get("experiment_id") == resolved.experiment_id:
                            candidates.append(child)
                    except Exception:
                        continue
        if candidates:
            # Pick most recent by mtime
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            store_path = candidates[0]
            store = ExperimentStore.load(store_path)
            store = run_experiment(
                resolved, cfg, store=store, resume=True, retry_failed=bool(getattr(args, "retry_failed", False))
            )
            print(f"Resumed: {store.run_dir}")
            print(
                f"Completed: {len([t for t in store.load_trials() if t.get('status') == 'ok'])} Failed: {len([t for t in store.load_trials() if t.get('status') == 'failed'])}"
            )
            return 0
        # No existing found, fall through to new
        print("No existing run found for --resume, creating new")
    # Normal new run
    # Use output_base as parent dir if provided, else default research_runs
    base = output_base
    # run_experiment will create new bundle
    # Need to handle fail_fast override from CLI
    if getattr(args, "fail_fast", False):
        # Patch job fail_fast
        resolved = replace(resolved, job=replace(resolved.job, fail_fast=True))
    store = run_experiment(
        resolved, cfg, output_base=base, resume=False, retry_failed=bool(getattr(args, "retry_failed", False))
    )
    print(f"Experiment: {resolved.spec.name} ID {resolved.experiment_id} Run {resolved.run_id}")
    print(f"Output: {store.run_dir}")
    trials = store.load_trials()
    ok = sum(1 for t in trials if t.get("status") == "ok")
    failed = sum(1 for t in trials if t.get("status") == "failed")
    print(f"Completed: {ok} Failed: {failed} Total: {len(trials)}")
    # Print best
    if ok:
        try:
            best = max([t for t in trials if t.get("status") == "ok"], key=lambda x: x.get("sharpe") or float("-inf"))
            print(f"Best: {best.get('label')} Sharpe {best.get('sharpe'):.4f} CAGR {best.get('cagr'):.4f}")
            if resolved.spec.baseline:
                base_t = next((t for t in trials if t.get("label") == resolved.spec.baseline), None)
                if base_t and base_t.get("status") == "ok":
                    for t in trials:
                        if t.get("status") != "ok" or t.get("label") == resolved.spec.baseline:
                            continue
                        d_sharpe = (t.get("sharpe") or 0) - (base_t.get("sharpe") or 0)
                        d_cagr = (t.get("cagr") or 0) - (base_t.get("cagr") or 0)
                        print(
                            f"  {t.get('label')}: Sharpe {t.get('sharpe'):.4f} ({d_sharpe:+.4f}) CAGR {t.get('cagr'):.4f} ({d_cagr:+.4f})"
                        )
        except Exception:
            pass
    return 0


def _run_research_resume(config: Config, args: argparse.Namespace) -> int:
    from pathlib import Path

    from parayabuck.config import load_config
    from parayabuck.research.persistence import ExperimentStore

    cfg = load_config(args.config) if getattr(args, "config", None) else config
    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")
    store = ExperimentStore.load(run_dir)
    return _resume_from_store(
        store,
        cfg,
        retry_failed=bool(getattr(args, "retry_failed", False)),
        workers_override=getattr(args, "workers", None),
    )


def _resume_from_store(store: Any, config: Config, retry_failed: bool = False, workers_override: Any = None) -> int:
    from pathlib import Path

    from parayabuck.research.experiment import _prepare_invariants, _trial_result_to_dict
    from parayabuck.research.models import ResearchJob, ResearchTier, ResearchTrial, TrialResult
    from parayabuck.research.runner import _execute_single_trial, _resolve_workers

    manifest = store.load_manifest()
    job_data = store.load_job().get("job") or {}
    # Reconstruct ResearchJob from job_data (may be normalized dict)
    try:
        from datetime import datetime

        from parayabuck.core.models import Market

        market = (
            Market(job_data.get("market", "equity"))
            if job_data.get("market")
            else Market(manifest.get("market", "equity"))
        )
        tier = ResearchTier(job_data.get("tier", manifest.get("tier", "quick")))
        # Trials from manifest's trial list (preserves label/params)
        trials_info = manifest.get("trials") or []
        trials: list[ResearchTrial] = []
        for t in trials_info:
            trials.append(
                ResearchTrial(
                    label=t.get("label", "trial"),
                    parameters=t.get("parameters"),
                    cost_multiplier=float(t.get("cost_multiplier", 1.0)),
                    metadata=t.get("metadata") or {},
                    execution_overrides=t.get("execution_overrides"),
                )
            )
        # Fallback: if trials_info empty, use job_data trials
        if not trials and job_data.get("trials"):
            for idx, t in enumerate(job_data.get("trials") or []):
                trials.append(
                    ResearchTrial(
                        label=t.get("label", f"trial_{idx}"),
                        parameters=t.get("parameters"),
                        cost_multiplier=float(t.get("cost_multiplier", 1.0)),
                        metadata=t.get("metadata") or {},
                        execution_overrides=t.get("execution_overrides"),
                    )
                )
        start = None
        end = None
        if job_data.get("start"):
            try:
                start = datetime.fromisoformat(job_data["start"])
            except Exception:
                start = None
        if job_data.get("end"):
            try:
                end = datetime.fromisoformat(job_data["end"])
            except Exception:
                end = None
        job = ResearchJob(
            market=market,
            strategy_name=job_data.get("strategy_name") or manifest.get("strategy_name"),
            dataset_path=job_data.get("dataset_path") or manifest.get("dataset_path"),
            start=start,
            end=end,
            base_parameters=job_data.get("base_parameters") or manifest.get("base_parameters"),
            trials=tuple(trials),
            tier=tier,
            workers=workers_override or job_data.get("workers") or manifest.get("workers_requested") or 1,
            persist_artifacts=None,
            persist_db=None,
            output_dir=str(store.run_dir),
            fail_fast=bool(job_data.get("fail_fast", False)),
            corporate_actions_path=job_data.get("corporate_actions_path") or manifest.get("corporate_actions_path"),
            initial_cash=job_data.get("initial_cash") or manifest.get("initial_cash"),
        )
    except Exception as exc:
        raise SystemExit(f"Failed to reconstruct job from store: {exc}\n{traceback.format_exc()}") from exc
    # Prepare invariants
    inv = _prepare_invariants(job, config, enable_cache=True)
    bars = inv["bars"]
    universe = inv["universe"]
    prepared = inv["prepared"]
    start = inv["start"]
    end = inv["end"]
    # Determine pending
    existing = store.load_trials()
    existing_by_label = {t.get("label"): t for t in existing}
    # Use trial_ids from manifest if available, else label
    trial_ids = [t.get("trial_id") or t.get("label") for t in manifest.get("trials", [])]
    if not trial_ids:
        trial_ids = [f"trial_{i}" for i in range(len(trials))]
    pending: list[int] = []
    for idx, tid in enumerate(trial_ids):
        # Find existing trial with same id
        found = next((x for x in existing if x.get("trial_id") == tid), None)
        if found is None:
            # Try label
            found = existing_by_label.get(trials[idx].label)
        if found is None:
            pending.append(idx)
        else:
            if found.get("status") == "ok":
                continue
            if found.get("status") == "failed" and retry_failed:
                pending.append(idx)
    if not pending:
        print(
            f"Completed: {len([t for t in existing if t.get('status') == 'ok'])} Pending: 0 Failed: {len([t for t in existing if t.get('status') == 'failed'])}"
        )
        store.mark_completed("completed")
        return 0
    print(
        f"Resuming {store.run_dir}: completed {len([t for t in existing if t.get('status') == 'ok'])} pending {len(pending)} failed {len([t for t in existing if t.get('status') == 'failed'])}"
    )
    # Execute pending (use same logic as run_experiment but directly)
    import multiprocessing
    import os
    import pickle
    import tempfile
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from dataclasses import replace

    from parayabuck.research.runner import _pool_initializer, _pool_worker
    from parayabuck.research.tiers import resolve_persist_artifacts, resolve_persist_db

    persist_artifacts = resolve_persist_artifacts(job.tier, job.persist_artifacts)
    persist_db = resolve_persist_db(job.tier, job.persist_db)
    corporate_actions = None
    if job.corporate_actions_path:
        from parayabuck.backtest.product import load_csv_corporate_actions

        try:
            corporate_actions = load_csv_corporate_actions(job.corporate_actions_path)
        except Exception:
            corporate_actions = None
    requested_workers = _resolve_workers(workers_override or job.workers) if hasattr(job, "workers") else 1
    workers = min(requested_workers, len(pending)) if pending else requested_workers
    if workers == 1:
        for idx in pending:
            trial = trials[idx]
            tid = trial_ids[idx]
            res = _execute_single_trial(
                config,
                bars,
                universe,
                prepared,
                job.market,
                job.strategy_name,
                job.base_parameters,
                trial,
                start,
                end,
                job.initial_cash,
                persist_db,
                persist_artifacts,
                None,
                corporate_actions,
            )
            from dataclasses import replace as rep

            res = rep(res, trial_index=idx)
            d = _trial_result_to_dict(
                res,
                tid,
                {
                    "label": trial.label,
                    "parameters": trial.parameters,
                    "execution_overrides": trial.execution_overrides,
                },
            )
            store.append_trial(d)
            if res.status == "failed" and job.fail_fast:
                store.mark_completed("failed")
                raise RuntimeError(f"Trial {res.label} failed: {res.error}")
    else:
        ctx = multiprocessing.get_context("spawn")
        invariants = (
            config,
            universe,
            prepared,
            job.market,
            job.strategy_name,
            job.base_parameters,
            start,
            end,
            job.initial_cash,
            persist_db,
            persist_artifacts,
            None,
            corporate_actions,
        )
        fd, tmp_name = tempfile.mkstemp(suffix=".pkl", prefix="parayabuck_invariants_")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            with tmp_path.open("wb") as f:
                pickle.dump(invariants, f, protocol=4)
            payloads = [(trials[i], i) for i in pending]
            idx_to_tid = {i: trial_ids[i] for i in pending}
            with ProcessPoolExecutor(
                max_workers=workers, mp_context=ctx, initializer=_pool_initializer, initargs=(str(tmp_path),)
            ) as executor:
                future_to_idx = {executor.submit(_pool_worker, p): p[1] for p in payloads}
                for fut in as_completed(future_to_idx):
                    idx = future_to_idx[fut]
                    trial = trials[idx]
                    tid = idx_to_tid[idx]
                    try:
                        res = fut.result()
                    except Exception as exc:
                        tb = traceback.format_exc()
                        res = TrialResult(
                            trial_index=idx,
                            label=trial.label,
                            status="failed",
                            parameters=trial.parameters or {},
                            cost_multiplier=trial.cost_multiplier,
                            elapsed_s=0.0,
                            error=f"Worker exception: {exc}\n{tb}",
                        )
                    if res.trial_index == -1:
                        res = replace(res, trial_index=idx)
                    d = _trial_result_to_dict(
                        res,
                        tid,
                        {
                            "label": trial.label,
                            "parameters": trial.parameters,
                            "execution_overrides": trial.execution_overrides,
                        },
                    )
                    store.append_trial(d)
                    if res.status == "failed" and job.fail_fast:
                        for fut2 in future_to_idx:
                            fut2.cancel()
                        executor.shutdown(wait=False, cancel_futures=True)
                        store.mark_completed("failed")
                        raise RuntimeError(f"Trial {res.label} failed")
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
    store.mark_completed("completed")
    print(f"Resumed completed. Dir: {store.run_dir}")
    print(
        f"Completed: {len([t for t in store.load_trials() if t.get('status') == 'ok'])} Failed: {len([t for t in store.load_trials() if t.get('status') == 'failed'])}"
    )
    return 0


def _run_research_inspect(config: Config, args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from parayabuck.research.persistence import ExperimentStore

    store = ExperimentStore.load(Path(args.run_dir))
    info = store.inspect()
    manifest = info["manifest"]
    if getattr(args, "json", False):
        # JSON output: manifest + computed best + counts
        payload = {
            "manifest": manifest,
            "completed": info["completed"],
            "failed": info["failed"],
            "pending": info["pending"],
            "best": info.get("best"),
            "trials": info["trials"],
            "run_dir": str(store.run_dir),
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print(f"Status: {manifest.get('status')}")
    print(f"Experiment ID: {manifest.get('experiment_id')}")
    print(f"Run ID: {manifest.get('run_id')}")
    print(f"Strategy: {manifest.get('strategy_name')} Market: {manifest.get('market')} Tier: {manifest.get('tier')}")
    print(f"Window: {manifest.get('start')} -> {manifest.get('end')}")
    print(f"Dataset: {manifest.get('dataset_path')} ({manifest.get('dataset_fingerprint_id')})")
    print(f"Universe: {manifest.get('universe_fingerprint_id')}")
    print(f"Corporate Actions: {manifest.get('corporate_actions_fingerprint')}")
    print(f"Workers: {manifest.get('workers_resolved')} (requested {manifest.get('workers_requested')})")
    print(f"Created: {manifest.get('created_at')} Completed: {manifest.get('completed_at')}")
    print(f"Tags: {manifest.get('tags')}")
    print(
        f"Git: {manifest.get('git_commit')} dirty={manifest.get('git_dirty')} branch={manifest.get('git_branch')} repro={manifest.get('reproducibility')}"
    )
    print(f"Config Digest: {manifest.get('config_digest_id')}")
    print(f"Bundle: {store.run_dir}")
    print(
        f"Trials: {manifest.get('trial_count')} Completed: {info['completed']} Failed: {info['failed']} Pending: {info['pending']}"
    )
    if info.get("best"):
        b = info["best"]
        print(
            f"Best: {b.get('label')} (id {b.get('trial_id')}) Sharpe {b.get('sharpe'):.4f} CAGR {b.get('cagr'):.4f} Drawdown {b.get('max_drawdown'):.4f} Trials {b.get('label')}"
        )
    # Baseline trial if present
    baseline = manifest.get("baseline")
    if baseline:
        print(f"Baseline: {baseline}")
    print("Trials:")
    for t in info["trials"]:
        status = t.get("status")
        label = t.get("label")
        sharpe = t.get("sharpe")
        cagr = t.get("cagr")
        trial_id = t.get("trial_id", "")[:8]
        if status == "ok":
            print(
                f"  - {label} [{trial_id}]: {status} Sharpe {sharpe:.4f} CAGR {cagr:.4f} Equity {t.get('final_equity'):.2f} elapsed {t.get('elapsed_s'):.2f}s"
            )
        else:
            print(f"  - {label} [{trial_id}]: {status} error {str(t.get('error'))[:120]}")
    return 0


def _run_research_compare(config: Config, args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from parayabuck.research.experiment import compare_experiments
    from parayabuck.research.persistence import ExperimentStore

    store_a = ExperimentStore.load(Path(args.run_a))
    store_b = ExperimentStore.load(Path(args.run_b))
    comp = compare_experiments(store_a, store_b)
    print(f"Compare: {args.run_a} vs {args.run_b}")
    print(f"Common trials: {comp['common_trials']}")
    if comp["only_in_a"]:
        print(f"Only in A: {comp['only_in_a']}")
    if comp["only_in_b"]:
        print(f"Only in B: {comp['only_in_b']}")
    for d in comp["deltas"]:
        print(f"{d['label']} (id {d['trial_id']}):")
        print(f"  CAGR delta {d['cagr_delta']:+.4f} (A {d['a']['cagr']:.4f} -> B {d['b']['cagr']:.4f})")
        print(f"  Sharpe delta {d['sharpe_delta']:+.4f} (A {d['a']['sharpe']:.4f} -> B {d['b']['sharpe']:.4f})")
        print(f"  Sortino delta {d['sortino_delta']:+.4f} MaxDD delta {d['max_drawdown_delta']:+.4f}")
        print(
            f"  Turnover delta {d['turnover_delta']:+.4f} Costs delta {d['costs_delta']:+.4f} Fills {d['a']['fill_count']}->{d['b']['fill_count']}"
        )
    if getattr(args, "output", None):
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(comp, indent=2, default=str), encoding="utf-8")
        print(f"Wrote compare to {out}")
    return 0


def _run_research_list(config: Config, args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    root = Path(getattr(args, "root", None) or "research_runs")
    if not root.exists():
        print(f"Root {root} does not exist — no runs")
        return 0
    # Discover run dirs: any immediate child containing manifest.json, or recursive one level
    candidates: list[Path] = []
    for child in root.iterdir():
        if child.is_dir() and (child / "manifest.json").exists():
            candidates.append(child)
        elif child.is_dir():
            # Check one level deeper for nested research_runs (e.g., tmp)
            for sub in child.iterdir():
                if sub.is_dir() and (sub / "manifest.json").exists():
                    candidates.append(sub)
    # Also support root being a single run dir itself
    if (root / "manifest.json").exists():
        candidates.append(root)
    rows: list[dict[str, object]] = []
    for run_dir in candidates:
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        # Prefer summary.json for trial stats (fast), fall back to manifest
        summary_path = run_dir / "summary.json"
        trials_completed = 0
        trials_failed = 0
        best_sharpe = None
        best_cagr = None
        trial_count = manifest.get("trial_count", 0)
        status = manifest.get("status", "unknown")
        # Load summary if exists (do not load full trials.jsonl for 500 runs)
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                trials = summary.get("trials", []) if isinstance(summary, dict) else []
                trials_completed = sum(1 for t in trials if t.get("status") == "ok")
                trials_failed = sum(1 for t in trials if t.get("status") == "failed")
                ok_trials = [t for t in trials if t.get("status") == "ok"]
                if ok_trials:
                    # Best by sharpe, then cagr
                    try:
                        best = max(
                            ok_trials, key=lambda x: (x.get("sharpe") or float("-inf"), x.get("cagr") or float("-inf"))
                        )
                        best_sharpe = best.get("sharpe")
                        best_cagr = best.get("cagr")
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            # Fallback: try to count via trials.jsonl existence without loading all? Just use manifest counts
            pass
        # Filters
        if getattr(args, "status", None) and str(status) != str(args.status):
            continue
        if getattr(args, "strategy", None) and str(manifest.get("strategy_name")) != str(args.strategy):
            continue
        if getattr(args, "market", None) and str(manifest.get("market")) != str(args.market):
            continue
        if getattr(args, "tag", None):
            tags = manifest.get("tags") or []
            if str(args.tag) not in [str(t) for t in tags]:
                continue
        rows.append(
            {
                "created": manifest.get("created_at") or "",
                "status": status,
                "experiment": str(manifest.get("experiment_id", ""))[:8],
                "experiment_full": manifest.get("experiment_id", ""),
                "run_id": str(manifest.get("run_id", ""))[:8],
                "strategy": manifest.get("strategy_name", ""),
                "market": manifest.get("market", ""),
                "tier": manifest.get("tier", ""),
                "trials": trial_count,
                "completed": trials_completed,
                "failed": trials_failed,
                "best_sharpe": best_sharpe,
                "best_cagr": best_cagr,
                "run_dir": str(run_dir),
                "tags": ",".join(str(t) for t in (manifest.get("tags") or [])),
            }
        )
    # Sorting
    sort_key = getattr(args, "sort", "created")
    if sort_key == "created":
        rows.sort(key=lambda r: str(r.get("created") or ""), reverse=True)
    elif sort_key == "best_sharpe":
        rows.sort(
            key=lambda r: r.get("best_sharpe") if r.get("best_sharpe") is not None else float("-inf"),  # type: ignore[arg-type, return-value]
            reverse=True,
        )
    elif sort_key == "best_cagr":
        rows.sort(key=lambda r: r.get("best_cagr") if r.get("best_cagr") is not None else float("-inf"), reverse=True)  # type: ignore[arg-type, return-value]
    elif sort_key == "status":
        rows.sort(key=lambda r: str(r.get("status") or ""))
    # Limit
    limit = getattr(args, "limit", None)
    if limit is not None:
        rows = rows[: int(limit)]
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("No runs match filters")
        return 0
    # Table header
    header = f"{'created':<20} {'status':<10} {'exp':<8} {'strat':<28} {'mkt':<6} {'tier':<8} {'tri':<3} {'ok':<3} {'fail':<4} {'sharpe':<7} {'cagr':<7} run_dir"
    print(header)
    print("-" * len(header))
    for r in rows:
        created = str(r.get("created") or "")[:19]
        sharpe = f"{r['best_sharpe']:.3f}" if r.get("best_sharpe") is not None else "-"
        cagr = f"{r['best_cagr']:.3f}" if r.get("best_cagr") is not None else "-"
        print(
            f"{created:<20} {str(r['status']):<10} {str(r['experiment']):<8} {str(r['strategy']):<28} {str(r['market']):<6} {str(r['tier']):<8} {r['trials']:<3} {r['completed']:<3} {r['failed']:<4} {sharpe:<7} {cagr:<7} {r['run_dir']}"
        )
    # Experiment grouping summary (presentation only)
    from collections import Counter

    exp_counts = Counter(r["experiment_full"] for r in rows if r.get("experiment_full"))
    grouped = {k: v for k, v in exp_counts.items() if v > 1}
    if grouped:
        print("\nExperiment grouping (runs sharing experiment_id):")
        for exp_id, count in sorted(grouped.items(), key=lambda x: -x[1]):
            short = str(exp_id)[:8]
            latest = max((r for r in rows if r["experiment_full"] == exp_id), key=lambda x: str(x["created"]))
            print(f"  {short} ({exp_id}): runs={count} latest={latest['status']} dir={latest['run_dir']}")
    return 0


def _run_research_top(config: Config, args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    run_dir = Path(args.run_dir)
    metric = getattr(args, "metric", "sharpe")
    limit = int(getattr(args, "limit", 10) or 10)
    allowed = {"sharpe", "sortino", "cagr", "calmar", "max_drawdown", "turnover"}
    if metric not in allowed:
        print(f"Unsupported metric {metric!r}; choose from {sorted(allowed)}")
        return 2
    # Load summary or trials
    summary_path = run_dir / "summary.json"
    trials: list[dict[str, object]] = []
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            trials = summary.get("trials", []) if isinstance(summary, dict) else []
        except Exception:
            trials = []
    if not trials:
        # Fallback to trials.jsonl (rare, but for completeness)
        trials_path = run_dir / "trials.jsonl"
        if trials_path.exists():
            try:
                for line in trials_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        trials.append(json.loads(line))
            except Exception:
                pass
    ok_trials = [t for t in trials if t.get("status") == "ok"]
    if not ok_trials:
        print(f"No completed trials in {run_dir}")
        return 0
    # Sorting: max_drawdown is better when smaller (less negative? Actually max_drawdown is negative, smaller magnitude better? Use most closest to 0)
    # For max_drawdown, higher (less negative) is better; for turnover, lower is better. Define ranking accordingly.
    if metric == "max_drawdown":
        # Higher (e.g., -0.05 better than -0.20) → max
        sorted_trials = sorted(
            ok_trials,
            key=lambda t: t.get(metric) if t.get(metric) is not None else float("-inf"),  # type: ignore[arg-type, return-value]
            reverse=True,
        )
    elif metric == "turnover":
        # Lower turnover better
        sorted_trials = sorted(ok_trials, key=lambda t: t.get(metric) if t.get(metric) is not None else float("inf"))  # type: ignore[arg-type, return-value]
    else:
        sorted_trials = sorted(
            ok_trials,
            key=lambda t: t.get(metric) if t.get(metric) is not None else float("-inf"),  # type: ignore[arg-type, return-value]
            reverse=True,
        )
    top = sorted_trials[:limit]
    if getattr(args, "json", False):
        print(json.dumps(top, indent=2, default=str))
        return 0
    print(f"Top {len(top)} trials by {metric} — {run_dir}")
    print(f"{'rank':<4} {'label':<28} {metric:<10} {'sharpe':<7} {'cagr':<7} {'drawdown':<9} {'turnover':<8}")
    print("-" * 80)
    for idx, t in enumerate(top, start=1):
        label = str(t.get("label", ""))[:28]
        val = t.get(metric)
        val_str = f"{val:.4f}" if isinstance(val, (int, float)) else str(val)
        sharpe = f"{t.get('sharpe'):.3f}" if isinstance(t.get("sharpe"), (int, float)) else "-"
        cagr = f"{t.get('cagr'):.3f}" if isinstance(t.get("cagr"), (int, float)) else "-"
        dd = f"{t.get('max_drawdown'):.3f}" if isinstance(t.get("max_drawdown"), (int, float)) else "-"
        to = f"{t.get('turnover'):.2f}" if isinstance(t.get("turnover"), (int, float)) else "-"
        print(f"{idx:<4} {label:<28} {val_str:<10} {sharpe:<7} {cagr:<7} {dd:<9} {to:<8}")
    return 0
