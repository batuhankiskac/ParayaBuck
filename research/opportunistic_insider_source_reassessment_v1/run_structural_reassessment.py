#!/usr/bin/env python3
"""Structural-only SEC Form 4 reassessment; deliberately computes no returns.

The program reads the SEC quarterly Form 3/4/5 bulk archives and the frozen
ParayaBuck entity-lifetime file.  It emits only provenance, mapping,
classification, filing-latency, and opportunity-arrival counts.  Price/bar
files are neither accepted nor opened.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import statistics
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = ROOT / "data/research/external/opportunistic_insider_source_reassessment_v1"
LIFETIMES = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
OUTPUT_DIR = Path(__file__).resolve().parent
START_MONTH = "2017-01"
END_MONTH = "2026-03"


@dataclass(frozen=True)
class Lifetime:
    entity_id: str
    symbol: str
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True)
class Submission:
    accession: str
    filing_date: date
    document_type: str
    issuer_cik: str
    issuer_symbol: str


@dataclass(frozen=True)
class PurchaseEvent:
    accession: str
    transaction_key: str
    filing_date: date
    transaction_date: date
    issuer_cik: str
    issuer_symbol: str
    entity_id: str | None
    title_class: str
    owners: tuple[str, ...]


def parse_sec_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.upper(), fmt).date()
        except ValueError:
            pass
    return None


def parse_iso_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def tsv_rows(archive: zipfile.ZipFile, member: str) -> Iterable[dict[str, str]]:
    with archive.open(member) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="") as text:
            yield from csv.DictReader(text, delimiter="\t")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_lifetimes() -> tuple[dict[str, list[Lifetime]], str]:
    by_symbol: dict[str, list[Lifetime]] = defaultdict(list)
    with LIFETIMES.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = row["historical_symbol"].strip().upper()
            valid_from = parse_iso_date(row["valid_from"])
            if not symbol or valid_from is None:
                continue
            by_symbol[symbol].append(
                Lifetime(
                    entity_id=row["entity_id"].strip(),
                    symbol=symbol,
                    valid_from=valid_from,
                    valid_to=parse_iso_date(row["valid_to"]),
                )
            )
    return dict(by_symbol), sha256(LIFETIMES)


def candidates_for(
    lifetimes_by_symbol: dict[str, list[Lifetime]], symbol: str, as_of: date
) -> tuple[Lifetime, ...]:
    return tuple(
        lifetime
        for lifetime in lifetimes_by_symbol.get(symbol.upper(), ())
        if lifetime.valid_from <= as_of and (lifetime.valid_to is None or as_of <= lifetime.valid_to)
    )


def security_title_class(title: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", " ", title.upper()).strip()
    non_common_terms = (
        "OPTION",
        "WARRANT",
        "UNIT",
        "NOTE",
        "DEBENTURE",
        "BOND",
        "RIGHT",
        "CONVERTIBLE",
        "PHANTOM",
        "RESTRICTED STOCK UNIT",
        "RSU",
        "PREFERRED",
        "PARTNERSHIP INTEREST",
    )
    if any(term in normalized for term in non_common_terms):
        return "non_common"
    common_patterns = (
        r"^COMMON STOCK$",
        r"^COMMON SHARES?$",
        r"^CLASS [A-Z0-9]+ COMMON STOCK$",
        r"^ORDINARY SHARES?$",
        r"^AMERICAN DEPOSITARY SHARES?$",
        r"^ADRS?$",
    )
    if any(re.fullmatch(pattern, normalized) for pattern in common_patterns):
        return "common_like"
    return "ambiguous"


def month_range(start: str, end: str) -> list[str]:
    year, month = map(int, start.split("-"))
    end_year, end_month = map(int, end.split("-"))
    result: list[str] = []
    while (year, month) <= (end_year, end_month):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def percentile(values: list[int], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def owner_status_by_year(
    owner_history: dict[str, list[tuple[int, int, date]]],
    owner_amendments: dict[str, list[date]],
    years: Iterable[int],
) -> dict[tuple[str, int], str]:
    result: dict[tuple[str, int], str] = {}
    for owner, records in owner_history.items():
        absorbed_routine = False
        for year in sorted(years):
            cutoff = date(year, 1, 1)
            if any(amended_at < cutoff for amended_at in owner_amendments.get(owner, ())):
                # The quarterly tables do not provide a reliable original-row
                # replacement key.  Once an amendment is public, fail closed
                # rather than combine original and corrected rows.
                status = "amendment_ambiguous"
            elif absorbed_routine:
                status = "routine"
            else:
                months_by_year: dict[int, set[int]] = defaultdict(set)
                for transaction_year, transaction_month, filed_at in records:
                    if filed_at < cutoff:
                        months_by_year[transaction_year].add(transaction_month)
                prior = [months_by_year.get(year - offset, set()) for offset in (3, 2, 1)]
                if not all(prior):
                    status = "unclassified"
                elif set.intersection(*prior):
                    status = "routine"
                    absorbed_routine = True
                else:
                    status = "opportunistic"
            result[(owner, year)] = status
    return result


def main() -> None:
    lifetimes, lifetime_hash = load_lifetimes()
    archives = sorted(INPUT_DIR.glob("????q?_form345.zip"))
    if not archives:
        raise SystemExit(f"No SEC archives found under {INPUT_DIR}")

    totals: Counter[str] = Counter()
    title_counts: Counter[str] = Counter()
    mapping_counts: Counter[str] = Counter()
    owner_history: dict[str, list[tuple[int, int, date]]] = defaultdict(list)
    owner_amendments: dict[str, list[date]] = defaultdict(list)
    events: list[PurchaseEvent] = []
    cik_entities: dict[str, set[str]] = defaultdict(set)
    entity_ciks: dict[str, set[str]] = defaultdict(set)
    symbol_ciks: dict[str, set[str]] = defaultdict(set)
    archive_receipts: list[dict[str, object]] = []
    sample_candidates: list[dict[str, str]] = []
    seen_purchase_keys: set[tuple[str, str]] = set()

    for path in archives:
        archive_receipts.append(
            {
                "file": path.name,
                "quarter": path.name.removesuffix("_form345.zip").upper(),
                "publisher": "U.S. Securities and Exchange Commission",
                "source_url": (
                    "https://www.sec.gov/files/structureddata/data/"
                    f"insider-transactions-data-sets/{path.name}"
                ),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "cache_mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
            }
        )
        with zipfile.ZipFile(path) as archive:
            submissions: dict[str, Submission] = {}
            for row in tsv_rows(archive, "SUBMISSION.tsv"):
                totals["all_submissions"] += 1
                document_type = row["DOCUMENT_TYPE"].strip().upper()
                if document_type not in {"4", "4/A"}:
                    continue
                totals["form4_and_4a_submissions"] += 1
                if document_type == "4/A":
                    totals["form4a_submissions"] += 1
                filing_date = parse_sec_date(row["FILING_DATE"])
                if filing_date is None:
                    totals["invalid_filing_date"] += 1
                    continue
                accession = row["ACCESSION_NUMBER"].strip()
                symbol = row["ISSUERTRADINGSYMBOL"].strip().upper()
                issuer_cik = row["ISSUERCIK"].strip().lstrip("0") or "0"
                submissions[accession] = Submission(
                    accession=accession,
                    filing_date=filing_date,
                    document_type=document_type,
                    issuer_cik=issuer_cik,
                    issuer_symbol=symbol,
                )
                if symbol:
                    totals["submissions_with_symbol"] += 1

            owners_by_accession: dict[str, list[str]] = defaultdict(list)
            for row in tsv_rows(archive, "REPORTINGOWNER.tsv"):
                accession = row["ACCESSION_NUMBER"].strip()
                if accession in submissions:
                    owner = row["RPTOWNERCIK"].strip().lstrip("0") or "0"
                    owners_by_accession[accession].append(owner)
            for accession, owners in owners_by_accession.items():
                submission = submissions[accession]
                if submission.document_type == "4/A":
                    for owner in set(owners):
                        owner_amendments[owner].append(submission.filing_date)

            for row in tsv_rows(archive, "NONDERIV_TRANS.tsv"):
                accession = row["ACCESSION_NUMBER"].strip()
                submission = submissions.get(accession)
                if submission is None:
                    continue
                code = row["TRANS_CODE"].strip().upper()
                acquired = row["TRANS_ACQUIRED_DISP_CD"].strip().upper()
                transaction_date = parse_sec_date(row["TRANS_DATE"])
                history_owners = set(owners_by_accession.get(accession, ()))
                if (
                    code in {"P", "S"}
                    and transaction_date is not None
                    and submission.document_type == "4"
                    and len(history_owners) == 1
                ):
                    owner = next(iter(history_owners))
                    owner_history[owner].append(
                        (transaction_date.year, transaction_date.month, submission.filing_date)
                    )
                if code != "P" or acquired != "A" or transaction_date is None:
                    continue
                totals["form4_purchase_rows_including_amendments"] += 1
                try:
                    shares = float(row["TRANS_SHARES"])
                    price = float(row["TRANS_PRICEPERSHARE"])
                except ValueError:
                    totals["purchase_rows_missing_positive_shares_or_price"] += 1
                    continue
                if shares <= 0 or price <= 0:
                    totals["purchase_rows_missing_positive_shares_or_price"] += 1
                    continue
                if submission.document_type != "4":
                    totals["amendment_purchase_rows_excluded"] += 1
                    continue

                event_key = (accession, row["NONDERIV_TRANS_SK"].strip())
                if event_key in seen_purchase_keys:
                    totals["duplicate_accession_transaction_keys_excluded"] += 1
                    continue
                seen_purchase_keys.add(event_key)

                title_class = security_title_class(row["SECURITY_TITLE"])
                title_counts[title_class] += 1
                if not submission.issuer_symbol:
                    mapping_counts["missing_as_filed_symbol"] += 1
                    entity_id = None
                else:
                    matches = candidates_for(lifetimes, submission.issuer_symbol, submission.filing_date)
                    if not matches:
                        mapping_counts["no_lifetime_match"] += 1
                        entity_id = None
                    elif len(matches) > 1:
                        mapping_counts["ambiguous_lifetime_match"] += 1
                        entity_id = None
                    else:
                        mapping_counts["unique_exact_symbol_date_match"] += 1
                        entity_id = matches[0].entity_id
                        cik_entities[submission.issuer_cik].add(entity_id)
                        entity_ciks[entity_id].add(submission.issuer_cik)
                        symbol_ciks[submission.issuer_symbol].add(submission.issuer_cik)
                event = PurchaseEvent(
                    accession=accession,
                    transaction_key=row["NONDERIV_TRANS_SK"].strip(),
                    filing_date=submission.filing_date,
                    transaction_date=transaction_date,
                    issuer_cik=submission.issuer_cik,
                    issuer_symbol=submission.issuer_symbol,
                    entity_id=entity_id,
                    title_class=title_class,
                    owners=tuple(sorted(set(owners_by_accession.get(accession, ())))),
                )
                events.append(event)
                if entity_id is not None and title_class == "common_like" and len(sample_candidates) < 20:
                    sample_candidates.append(
                        {
                            "accession_number": accession,
                            "filing_date": submission.filing_date.isoformat(),
                            "issuer_cik": submission.issuer_cik,
                            "issuer_trading_symbol": submission.issuer_symbol,
                            "entity_id": entity_id,
                            "transaction_key": event.transaction_key,
                        }
                    )

    years = range(2013, 2027)
    classifications = owner_status_by_year(owner_history, owner_amendments, years)
    classification_counts: Counter[str] = Counter(classifications.values())
    months = month_range(START_MONTH, END_MONTH)
    monthly_events: dict[str, list[PurchaseEvent]] = defaultdict(list)
    latency_days: list[int] = []
    owner_opportunity_counts: Counter[str] = Counter()
    issuer_opportunity_counts: Counter[str] = Counter()

    identity_collision_rows = 0
    joint_owner_rows = 0
    post_amendment_rows = 0
    identity_ok: dict[tuple[str, str], bool] = {}
    observed_cik_entities: dict[str, set[str]] = defaultdict(set)
    observed_entity_ciks: dict[str, set[str]] = defaultdict(set)
    events_by_date: dict[date, list[PurchaseEvent]] = defaultdict(list)
    for event in events:
        if event.entity_id is not None:
            events_by_date[event.filing_date].append(event)
    for filing_date in sorted(events_by_date):
        dated_events = events_by_date[filing_date]
        for event in dated_events:
            assert event.entity_id is not None
            observed_cik_entities[event.issuer_cik].add(event.entity_id)
            observed_entity_ciks[event.entity_id].add(event.issuer_cik)
        for event in dated_events:
            assert event.entity_id is not None
            identity_ok[(event.accession, event.transaction_key)] = (
                len(observed_cik_entities[event.issuer_cik]) == 1
                and len(observed_entity_ciks[event.entity_id]) == 1
            )

    for event in events:
        if event.entity_id is None or event.title_class != "common_like":
            continue
        if not identity_ok[(event.accession, event.transaction_key)]:
            identity_collision_rows += 1
            continue
        # The quarterly tables do not link transaction rows to one owner in a
        # multi-owner filing.  Do not attribute the row to every co-filer.
        if len(event.owners) != 1:
            joint_owner_rows += 1
            continue
        owner = event.owners[0]
        if any(amended_at <= event.filing_date for amended_at in owner_amendments.get(owner, ())):
            post_amendment_rows += 1
            continue
        month = event.filing_date.strftime("%Y-%m")
        if month not in months:
            continue
        opportunistic_owners = [
            owner for owner in event.owners if classifications.get((owner, event.filing_date.year)) == "opportunistic"
        ]
        if not opportunistic_owners:
            continue
        monthly_events[month].append(event)
        issuer_opportunity_counts[event.entity_id] += 1
        owner_opportunity_counts.update(opportunistic_owners)
        latency_days.append((event.filing_date - event.transaction_date).days)

    monthly = []
    for month in months:
        month_events = monthly_events.get(month, [])
        monthly.append(
            {
                "month": month,
                "opportunistic_purchase_transaction_rows": len(month_events),
                "distinct_entities": len({event.entity_id for event in month_events}),
                "distinct_opportunistic_owners": len(
                    {
                        owner
                        for event in month_events
                        for owner in event.owners
                        if classifications.get((owner, event.filing_date.year)) == "opportunistic"
                    }
                ),
                "one_slot_occupancy_indicator": int(bool(month_events)),
            }
        )

    total_mapped_common = sum(
        1 for event in events if event.entity_id is not None and event.title_class == "common_like"
    )
    total_strict = sum(
        1
        for event in events
        if event.entity_id is not None
        and event.title_class == "common_like"
        and identity_ok[(event.accession, event.transaction_key)]
        and len(event.owners) == 1
    )
    total_purchase_rows = len(events)
    totals.setdefault("duplicate_accession_transaction_keys_excluded", 0)
    mapping_payload = {
        "schema_version": 1,
        "scope": "structural_only_no_market_price_bars_no_returns",
        "as_of_cached_data": "2026-Q1",
        "requested_period": "2013-Q1 through 2026-Q2",
        "source_archives_present": len(archives),
        "source_archives_expected": 54,
        "missing_archive": "2026q2_form345.zip",
        "lifetime_file": str(LIFETIMES.relative_to(ROOT)),
        "lifetime_sha256": lifetime_hash,
        "totals": dict(sorted(totals.items())),
        "qualifying_original_form4_purchase_rows": total_purchase_rows,
        "title_classification": dict(sorted(title_counts.items())),
        "mapping": dict(sorted(mapping_counts.items())),
        "exact_mapped_common_like_rows_before_identity_and_owner_gates": total_mapped_common,
        "identity_collision_rows_excluded": identity_collision_rows,
        "joint_or_missing_owner_rows_excluded": joint_owner_rows,
        "post_owner_amendment_rows_excluded": post_amendment_rows,
        "strict_mapped_common_like_single_owner_rows": total_strict,
        "strict_mapping_rate_of_qualifying_rows": (total_strict / total_purchase_rows if total_purchase_rows else 0.0),
        "issuer_ciks_mapping_to_multiple_entities": sum(len(values) > 1 for values in cik_entities.values()),
        "entities_observed_with_multiple_issuer_ciks": sum(len(values) > 1 for values in entity_ciks.values()),
        "symbols_observed_with_multiple_issuer_ciks": sum(len(values) > 1 for values in symbol_ciks.values()),
        "deterministic_sample_candidates": sorted(sample_candidates, key=lambda row: (row["accession_number"], row["transaction_key"]))[:10],
        "archive_receipts": archive_receipts,
        "method_notes": [
            "Mapping is exact uppercase as-filed ISSUERTRADINGSYMBOL plus filing-date lifetime validity; no current-ticker alias is used.",
            "Only original Form 4 rows with code P, acquired flag A, positive shares and positive price enter structural purchase events.",
            "Form 4/A rows are counted but excluded from event and owner-history construction to prevent amendments from becoming duplicate causal events.",
            "Security-title classification is fail closed: only an enumerated common-like full-string pattern passes.",
        ],
    }

    event_counts = [row["opportunistic_purchase_transaction_rows"] for row in monthly]
    opportunity_payload = {
        "schema_version": 1,
        "scope": "structural_opportunity_only_no_market_bars_no_economic_outcomes",
        "window": {"start_month": START_MONTH, "end_month": END_MONTH, "months": len(months)},
        "classification": {
            "rule": "At each calendar-year start, use only original-Form-4 P/S transaction months from the prior three complete calendar years whose filing date is before the cutoff. Require activity in all three; common month in all three => routine and absorbing, otherwise opportunistic; insufficient history => unclassified; after a public Form 4/A => amendment_ambiguous.",
            "status_cell_counts": dict(sorted(classification_counts.items())),
            "owners_with_any_history": len(owner_history),
            "owners_with_any_form4a": len(owner_amendments),
        },
        "monthly": monthly,
        "summary": {
            "total_opportunistic_purchase_transaction_rows": sum(event_counts),
            "distinct_opportunistic_purchase_accessions": len(
                {event.accession for values in monthly_events.values() for event in values}
            ),
            "distinct_entity_month_opportunities": len(
                {
                    (event.entity_id, event.filing_date.strftime("%Y-%m"))
                    for values in monthly_events.values()
                    for event in values
                }
            ),
            "months_with_zero_events": sum(value == 0 for value in event_counts),
            "months_with_at_least_one_event": sum(value > 0 for value in event_counts),
            "one_slot_structural_occupancy_rate": sum(value > 0 for value in event_counts) / len(event_counts),
            "events_per_month_min": min(event_counts),
            "events_per_month_median": statistics.median(event_counts),
            "events_per_month_p90": percentile(event_counts, 0.90),
            "events_per_month_max": max(event_counts),
            "distinct_entities": len(issuer_opportunity_counts),
            "distinct_opportunistic_owners": len(owner_opportunity_counts),
            "entities_with_repeat_events": sum(value > 1 for value in issuer_opportunity_counts.values()),
            "share_of_transaction_rows_from_repeat_entities": (
                sum(value for value in issuer_opportunity_counts.values() if value > 1) / sum(event_counts)
                if sum(event_counts)
                else 0.0
            ),
            "filing_latency_calendar_days": {
                "count": len(latency_days),
                "min": min(latency_days) if latency_days else None,
                "median": statistics.median(latency_days) if latency_days else None,
                "p90": percentile(latency_days, 0.90),
                "p99": percentile(latency_days, 0.99),
                "max": max(latency_days) if latency_days else None,
                "negative_count": sum(value < 0 for value in latency_days),
            },
        },
        "method_notes": [
            "Owner history admits a transaction month only when its original filing date is earlier than the annual classification cutoff; transaction date alone is never treated as public availability.",
            "Filing date is the bulk data's day-level public-availability proxy; acceptance timestamp is unavailable in the quarterly tables.",
            "A transaction is counted once by accession plus NONDERIV_TRANS_SK.",
            "Because the bulk table does not link a transaction row to one co-filer, multi-owner and missing-owner filings are excluded from both owner-history construction and CMP opportunity attribution rather than copied to every owner.",
            "After an owner's first public Form 4/A, that owner fails closed as amendment_ambiguous because the bulk schema lacks a reliable original-row replacement key.",
            "An event filed on or after the owner's first Form 4/A is excluded immediately, including within the same calendar year.",
            "CIK/entity collision checks use only mappings observed on or before each event's filing date; future collisions do not alter earlier eligibility.",
            "The sole associated owner must be classified opportunistic as of that filing year.",
            "The one-slot indicator measures only whether a month offers at least one structurally eligible event; it does not rank names or test returns.",
        ],
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("STRUCTURAL_MAPPING_COUNTS.json", mapping_payload),
        ("STRUCTURAL_OPPORTUNITY_AUDIT.json", opportunity_payload),
    ):
        target = OUTPUT_DIR / name
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
