"""``parayabuck tax`` subcommands: rates-fetch, import-activities, export.

``import-activities`` reads Alpaca through the existing :class:`AlpacaClient`
adapter and only ever issues GET requests. ``export`` opens the runtime database
read-only and writes files; it never changes the ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from parayabuck.tax.dividends import (
    NOT_IMPORTED_INCOME_TYPES,
    OTHER_TYPES_NOTE,
    ensure_table,
    fetch_account_id,
    fetch_activities,
    parse_activity,
    record_coverage,
    store_activities,
)
from parayabuck.tax.errors import TaxDataError
from parayabuck.tax.export import build_year_report, export_year
from parayabuck.tax.ledger import TURKEY_TIME
from parayabuck.tax.rates import (
    DEFAULT_USDTRY_PATH,
    DEFAULT_YIUFE_PATH,
    EVDS_KEY_ENV,
    fetch_rates_to_files,
    load_rate_book,
)


def add_tax_parser(commands: Any) -> None:
    """Register the ``tax`` command group on the top-level subparsers."""
    tax = commands.add_parser("tax", help="TL tax ledger: rates, activities, yearly export (estimates)")
    sub = tax.add_subparsers(dest="tax_cmd")

    fetch = sub.add_parser("rates-fetch", help="download USD/TRY and Yİ-ÜFE from TCMB EVDS (needs PARAYABUCK_EVDS_KEY)")
    fetch.add_argument("--start", default="2020-01-01", help="first day to fetch (YYYY-MM-DD)")
    fetch.add_argument("--end", default=None, help="last day to fetch (YYYY-MM-DD, default today)")
    fetch.add_argument("--usdtry", default=str(DEFAULT_USDTRY_PATH))
    fetch.add_argument("--yiufe", default=str(DEFAULT_YIUFE_PATH))
    fetch.add_argument(
        "--confirm-yiufe-series",
        action="store_true",
        help="accept Yİ-ÜFE months that do not overlap the existing file, after checking the series code",
    )

    importer = sub.add_parser("import-activities", help="store DIV/DIVNRA/INT activities from Alpaca (idempotent)")
    importer.add_argument("--since", required=True, help="first activity date to import (YYYY-MM-DD)")
    importer.add_argument("--live", action="store_true", help="use the LIVE credential pair (default PAPER)")

    export = sub.add_parser("export", help="write the year's disposal CSV and summary JSON")
    export.add_argument("--year", type=int, required=True)
    export.add_argument("--out", required=True, help="CSV path; the summary is written next to it")
    export.add_argument("--account-id", default=None, help="Alpaca account id (default: latest in the ledger)")
    export.add_argument("--live", action="store_true", help="read the LIVE ledger (default: the config's ledger)")
    export.add_argument("--usdtry", default=str(DEFAULT_USDTRY_PATH))
    export.add_argument("--yiufe", default=str(DEFAULT_YIUFE_PATH))


def run_tax(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``tax`` command; returns the process exit code."""
    command = getattr(args, "tax_cmd", None)
    try:
        if command == "rates-fetch":
            return _rates_fetch(args)
        if command == "import-activities":
            return _import_activities(args)
        if command == "export":
            return _export(args)
    except (TaxDataError, OSError) as exc:
        print(f"tax {command}: {exc}", file=sys.stderr)
        return 1
    print("usage: parayabuck tax {rates-fetch,import-activities,export} ...", file=sys.stderr)
    return 2


def _database_path(config_path: str | None, *, live: bool = False, writing: bool = False) -> Path:
    from parayabuck.config import load_config

    return _ledger_for_mode(load_config(config_path).runtime, live=live, writing=writing)


def _ledger_for_mode(runtime: Any, *, live: bool, writing: bool) -> Path:
    """The ledger an import or export of one credential mode may use.

    ``--live`` targets the LIVE ledger, resolved the way the runtime resolves a LIVE
    configuration (``parayabuck.config.RuntimeConfig``). A PAPER configuration whose
    ledger is that same path refuses, because LIVE activities would land in the
    PAPER ledger. A PAPER import (``writing``) into a LIVE configuration's ledger
    also refuses, for the reverse reason. Refusals raise before any network call.
    """
    from parayabuck.config import DEFAULT_LIVE_DECISION_DB_PATH, RuntimeConfig, RuntimeMode

    configured = Path(runtime.decision_db_path)
    if live:
        target = Path(RuntimeConfig(mode=RuntimeMode.LIVE, decision_db_path=str(configured)).decision_db_path)
        if runtime.mode != RuntimeMode.LIVE and os.path.normpath(str(target)) == os.path.normpath(str(configured)):
            raise TaxDataError(
                f"the config's ledger {configured} is the PAPER ledger; LIVE activities need the LIVE ledger, "
                "so use a LIVE configuration"
            )
        return target
    if writing and runtime.mode == RuntimeMode.LIVE:
        raise TaxDataError(
            f"the config is LIVE, so its ledger {configured} is the LIVE ledger; PAPER activities are not "
            "stored there (use --live)"
        )
    if writing and os.path.normpath(str(configured)) == os.path.normpath(DEFAULT_LIVE_DECISION_DB_PATH):
        raise TaxDataError(
            f"the config's ledger {configured} is the LIVE ledger path; PAPER activities must not be stored "
            "there (use a PAPER configuration with its own ledger)"
        )
    return configured


def _rates_fetch(args: argparse.Namespace) -> int:
    from parayabuck.config import load_env_file

    load_env_file()
    key = os.getenv(EVDS_KEY_ENV, "").strip()
    if not key:
        print(f"tax rates-fetch: set {EVDS_KEY_ENV} to an EVDS API key first", file=sys.stderr)
        return 1
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else datetime.now(TURKEY_TIME).date()
    counts = fetch_rates_to_files(
        start,
        end,
        usdtry_path=Path(args.usdtry),
        yiufe_path=Path(args.yiufe),
        api_key=key,
        confirmed_series=bool(args.confirm_yiufe_series),
    )
    print(json.dumps({"start": start.isoformat(), "end": end.isoformat(), **counts}, indent=2))
    return 0


def _import_activities(args: argparse.Namespace) -> int:
    from parayabuck.config import load_env_file
    from parayabuck.execution.alpaca_client import AlpacaClient
    from parayabuck.live_safety import mode_credentials

    load_env_file()
    # Resolve the ledger before any network call, so a mode mismatch fails closed.
    database = _database_path(args.config, live=bool(args.live), writing=True)
    if not database.is_file():
        # F2-9-6: the engine creates the ledger; an import must not create an empty one that a
        # later start would take for an existing ledger. Refused before any network call.
        print(
            f"tax import-activities: ledger {database} does not exist; nothing was created. Point --config at the "
            "configuration whose ledger the engine writes.",
            file=sys.stderr,
        )
        return 1
    key, secret = mode_credentials(live=bool(args.live))
    if not key or not secret:
        print("tax import-activities: Alpaca credentials are not configured for this mode", file=sys.stderr)
        return 1
    since = date.fromisoformat(args.since)
    imported_on = datetime.now(TURKEY_TIME).date()
    adapter = AlpacaClient(key, secret, paper=not args.live)
    try:
        # The wrapper has no activities call; its raw authenticated GET is used for reads only.
        get = adapter._client.get
        account_id = fetch_account_id(get)
        raw_rows = fetch_activities(get, since)
    finally:
        adapter.close()
    parsed = [parse_activity(row, account_id) for row in raw_rows]
    raw_by_id = {str(row.get("id")): row for row in raw_rows}
    connection = sqlite3.connect(str(database), timeout=5.0)
    try:
        ensure_table(connection)
        inserted, existing = store_activities(connection, parsed, raw_by_id=raw_by_id)
        covered_through = record_coverage(connection, account_id, since, imported_on)
        connection.commit()
    finally:
        connection.close()
    print(
        json.dumps(
            {
                "since": since.isoformat(),
                "covered_through": covered_through.isoformat(),
                "account_suffix": account_id[-4:],
                "fetched": len(raw_rows),
                "inserted": inserted,
                "already_present": existing,
                "database": str(database),
                "live": bool(args.live),
                "not_imported_types": list(NOT_IMPORTED_INCOME_TYPES),
                "completeness_note": (
                    "capital-gain distributions and return of capital are not imported; "
                    "check the broker's year-end tax statement for them"
                ),
                "other_types_note": OTHER_TYPES_NOTE,
            },
            indent=2,
        )
    )
    return 0


def _latest_account(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT account_id FROM portfolio_runtime_sessions "
        "WHERE account_id IS NOT NULL AND account_id != '' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row is None or not row[0]:
        raise TaxDataError("no account found in the ledger; pass --account-id")
    return str(row[0])


def _export(args: argparse.Namespace) -> int:
    if args.year < 2000 or args.year > 2100:
        raise TaxDataError(f"--year {args.year} is out of range")
    database = _database_path(args.config, live=bool(args.live))
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise TaxDataError(f"cannot open ledger read-only at {database}: {exc}") from exc
    try:
        account_id = args.account_id or _latest_account(connection)
        rates = load_rate_book(Path(args.usdtry), Path(args.yiufe))
        report = build_year_report(connection, account_id, args.year, rates)
    finally:
        connection.close()
    csv_path, summary_path = export_year(report, Path(args.out))
    summary = report.to_dict()
    print(
        json.dumps(
            {
                "csv": str(csv_path),
                "summary": str(summary_path),
                "net_gain_tl": summary["net_gain_tl"],
                "estimated_tax_tl": summary["estimated_tax_tl"],
                "notes": summary["notes"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


__all__ = ["add_tax_parser", "run_tax"]
