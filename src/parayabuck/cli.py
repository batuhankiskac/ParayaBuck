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
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
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
            help="also run 2x and 3x slippage/spread/crypto-fee/equity-regulatory-fee assumptions",
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
                PortfolioExecutionPolicy(
                    rebalance_band_pct=config.execution.rebalance_band_pct,
                    minimum_order_notional=config.execution.minimum_order_notional,
                ),
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
