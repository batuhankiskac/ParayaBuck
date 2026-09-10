"""Build and verify the frozen, pre-outcome H01-H16 research protocol.

This package freezes definitions and testability only.  It does not run an
economic backtest, change V3, or touch production/PAPER/LIVE configuration.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PROTOCOL_MD = REPO / "docs/research/equity_h01_h16_standalone_overlay_protocol_2026_09_09_v1.md"
MANIFEST = REPO / "data/research_manifests/equity_h01_h16_standalone_overlay_2026_09_09_v1.json"
RESULTS = REPO / "data/research_runs/equity_h01_h16_standalone_overlay_2026_09_09/protocol_results.json"
RECEIPT = REPO / "data/research_runs/equity_h01_h16_standalone_overlay_2026_09_09/protocol_receipt.json"
SCRIPT = REPO / "scripts/research/build_equity_h01_h16_protocol_2026_09_09.py"
TEST = REPO / "tests/research/test_equity_h01_h16_protocol_2026_09_09.py"
FULL_REPORT = REPO / "docs/research/strategy_queue_full_report_2026_09_06.md"
EQUITY_REVIEW = REPO / "docs/research/strategy_queue_equity_review_2026_09_06.md"
CURRENT_CLOSURE = REPO / "data/research_runs/all_34_research_closure_2026_09_09/closure_record.json"
H14_RESULT = REPO / "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json"
PENDING = "PENDING_FREEZE"
STATUS = "FROZEN_PRE_OUTCOME_DEFINITIONS"
EXPECTED_IDS = [f"H{i:02d}" for i in range(1, 17)]
UNSPECIFIED = "NOT_APPLICABLE_TO_FROZEN_MECHANISM"

COMMON = {
    "decision_timezone": "UTC",
    "decision_boundary": "official publication/acceptance timestamp at or before decision_time; use the next supported NYSE regular-session open",
    "universe": "all US common equities with a frozen PIT security/CIK identity and eligible evidence at decision_time; no survivorship-selected current ticker list",
    "security_continuity": "issuer CIK, security identifier, ticker/exchange lifetime, corporate actions and delisting state must be bound at every decision; unknown continuity excludes the row",
    "missing_data": "missing, stale, non-finite, revised-without-vintage, or identity-unbound inputs exclude the observation and are counted; no forward fill across an unavailable publication boundary",
    "rebalance": "monthly on the last supported NYSE session after all eligible information is available",
    "holding": "21 supported NYSE sessions unless the candidate contract below specifies an event horizon",
    "selection": "long-only top decile of finite eligible scores; ties resolved by permanent security identifier ascending; fewer than 10 eligible names records a shortfall and does not lower the threshold",
    "costs": {"multipliers": [1, 2, 3], "base": "frozen current equity execution-cost model", "fitting": False},
    "windows": {
        "full": {"start": "2022-01-01T00:00:00Z", "end": "2025-12-31T23:59:59Z"},
        "recent": {"start": "2024-01-01T00:00:00Z", "end": "2025-12-31T23:59:59Z"},
        "true_holdout": {"start": "2026-01-01T00:00:00Z", "end": "2026-07-31T23:59:59Z"},
    },
    "holdout_policy": "true_holdout is never used for parameter, threshold, direction, universe, or variant selection; report it only after the frozen full/recent run",
    "standalone": "candidate sleeve is evaluated alone with cash for excluded/missing rows; no incumbent signal or outcome filtering",
    "overlay": {
        "weights": "within the 55% equity sleeve: 95% current V3 and 5% candidate; candidate therefore receives 2.75% of total account before any cash shortfall",
        "equity_cap": 0.55,
        "gross_cap": 0.60,
        "cash_floor": 0.40,
        "positions_cap": 6,
        "candidate_selection": "candidate rows are frozen before the overlay; no spare-capacity or outcome-based exclusion except the separately named H14b variants",
        "controls": ["current V3 alone", "candidate standalone", "cash/passive matched exposure", "calendar or label permutation control where applicable", "fixed 95/5 shared-capital overlay"],
    },
    "robustness": {
        "subperiods": ["full", "recent", "true_holdout"],
        "regimes": "predeclared calendar-year and market-state buckets from lagged incumbent market return/volatility only",
        "drawdown_tail": "maximum drawdown, worst 5% daily returns, worst 20-session return, and common-down-day overlap on the same net NAV dates",
        "correlation": "Pearson and Spearman on overlapping net daily returns after exact UTC/session join; no OHLCV proxy for missing causal fields",
        "turnover": "gross traded notional divided by lagged sleeve NAV, with 1x/2x/3x costs",
        "bootstrap": {"method": "moving block bootstrap", "block_sessions": 21, "resamples": 10000, "seed": 20260909},
        "permutation": {"method": "within-window label permutation preserving issuer/event counts", "resamples": 10000, "seed": 20260910},
        "placebo": "fixed calendar or next-event placebo declared per candidate below; placebo dates never selected after observing returns",
    },
}

SOURCES = {
    "H01": {"title": "Value; Asness, Moskowitz, Pedersen", "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2174501"},
    "H02": {"title": "Gross profitability; Novy-Marx", "url": "https://www.nber.org/papers/w15940"},
    "H03": {"title": "Accrual anomaly; Sloan", "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2598"},
    "H04": {"title": "Asset growth; Cooper, Gulen, Schill", "url": "https://doi.org/10.1111/j.1540-6261.2008.01370.x"},
    "H05": {"title": "Total payout yield; Boudoukh et al.", "url": "https://www.nber.org/papers/w10651"},
    "H06": {"title": "Routine versus opportunistic insider trades; Cohen, Malloy, Pomorski", "url": "https://www.nber.org/papers/w16454"},
    "H07": {"title": "Analyst forecast revisions; Chan, Jegadeesh, Lakonishok", "url": "https://www.nber.org/papers/w5375"},
    "H08": {"title": "Post-earnings-announcement drift; Bernard and Thomas", "url": "https://doi.org/10.2307/2491062"},
    "H09": {"title": "Customer-supplier information links; Cohen and Frazzini", "url": "https://doi.org/10.1111/j.1540-6261.2008.01379.x"},
    "H10": {"title": "Filing language changes; Cohen, Malloy, Nguyen", "url": "https://www.nber.org/papers/w25084"},
    "H11": {"title": "Fund-flow price pressure; Coval and Stafford", "url": "https://doi.org/10.1016/j.jfineco.2006.09.007"},
    "H12": {"title": "Index effects; Greenwood and Sammon", "url": "https://doi.org/10.1111/jofi.13410"},
    "H13": {"title": "Overnight versus intraday returns; Lou, Polk, Skouras", "url": "https://doi.org/10.1016/j.jfineco.2019.03.011"},
    "H14": {"title": "Turn of the month; McConnell and Xu", "url": "https://doi.org/10.2469/faj.v64.n2.11"},
    "H15": {"title": "Risk arbitrage; Mitchell and Pulvino", "url": "https://doi.org/10.1111/0022-1082.00401"},
    "H16": {"title": "Two-step spinoffs; Amihud, Huh, Subrahmanyam", "url": "https://www.sciencedirect.com/science/article/pii/S1386418125000400"},
}


def _candidate(candidate_id: str, mechanism: str, formula: str, direction: str, holding: str, required: list[str], blocker: str, placebo: str, *, priority: str = "B", source_note: str | None = None) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "mechanism": mechanism,
        "formula": formula,
        "direction": direction,
        "universe_override": "",
        "rebalance": COMMON["rebalance"],
        "holding": holding,
        "missing_data": COMMON["missing_data"],
        "security_continuity": COMMON["security_continuity"],
        "required_fields": required,
        "costs": COMMON["costs"],
        "windows": COMMON["windows"],
        "priority": priority,
        "placebo": placebo,
        "minimum_breadth": "at least 100 issuer-month observations in full and 50 in recent; otherwise diagnostic-only",
        "blocker_if_unavailable": blocker,
        "source": {**SOURCES[candidate_id], **({"note": source_note} if source_note else {})},
        "test_plan": ["standalone", "1x/2x/3x cost stress", "full/recent/true_holdout", "calendar-year/regime", "drawdown/tail", "turnover", "bootstrap/permutation", "fixed control", "95/5 shared-capital overlay"],
    }


def definitions() -> dict[str, dict[str, Any]]:
    cards = {
        "H01": _candidate("H01", "Value", "BM_t = PIT book_equity_t / market_cap_t; market_cap_t = PIT shares_outstanding_t * decision_price_t", "long highest BM decile", "21 NYSE sessions", ["book_equity", "shares_outstanding", "decision_price", "fiscal_period", "accepted_at"], "PIT book/market fields, restatement vintage, and security continuity", "within-month random date placebo with the same eligible issuer count", priority="A"),
        "H02": _candidate("H02", "Gross profitability", "GP/TA_t = (revenue_t - COGS_t) / mean(total_assets_t, total_assets_t-1), using first accepted vintage", "long highest GP/TA decile", "21 NYSE sessions", ["revenue", "cost_of_goods_sold", "total_assets", "fiscal_period", "accepted_at"], "PIT accounting concepts, fiscal alignment, and restatement policy", "fiscal-period label permutation within issuer-month", priority="A"),
        "H03": _candidate("H03", "Low accruals", "ACCR_t = (net_income_t - operating_cash_flow_t) / mean(total_assets_t, total_assets_t-1)", "long lowest accrual decile", "21 NYSE sessions", ["net_income", "operating_cash_flow", "total_assets", "accepted_at"], "PIT income/cash-flow concepts, period matching, and identity history", "within-window issuer label permutation preserving fiscal periods", priority="A"),
        "H04": _candidate("H04", "Low asset growth", "AG_t = total_assets_t / total_assets_t-1 - 1; use the first accepted annual report for each fiscal year", "long lowest asset-growth decile", "252 NYSE sessions or next annual rebalance", ["total_assets", "fiscal_year", "accepted_at", "merger/spinoff identity"], "annual PIT balance sheets and M&A/spinoff continuity", "one-year shifted fiscal-year placebo", priority="B"),
        "H05": _candidate("H05", "Net payout yield", "NPY_t = (cash_dividends_t + issuer_buybacks_t - primary_issuance_t) / market_cap_t, trailing 12 months through decision_time", "long highest NPY decile", "21 NYSE sessions", ["dividends", "buybacks", "issuance", "market_cap", "action_time", "accepted_at"], "single PIT action feed for dividends, buybacks, issuance, and market-cap basis", "same issuer-month with payout labels shifted one year", priority="A"),
        "H06": _candidate("H06", "Opportunistic public insider purchases", "score_i,t = sum(open-market non-derivative direct purchase value by eligible owners in prior 21 sessions) / market_cap_t; eligible only when frozen routine/10b5-1/exclusion taxonomy passes", "long highest positive score decile", "63 NYSE sessions from next open after EDGAR accepted_at", ["Form4/4-A ownership", "transaction code", "owner/issuer CIK", "accepted_at", "10b5-1/routine flags", "price", "shares"], "complete Form 4/4-A history, taxonomy, routine history, acceptance time, and CIK/security continuity", "event-date permutation by issuer preserving owner/event counts", priority="A"),
        "H07": _candidate("H07", "Analyst forecast revisions", "REV_i,t = (new_consensus_eps_i,t - prior_consensus_eps_i,t) / abs(prior_consensus_eps_i,t); horizon is 1 fiscal year and prior vintage is the latest older vintage available at publication", "long highest positive revision decile", "63 NYSE sessions from next open after forecast publication", ["consensus EPS", "prior vintage", "horizon", "analyst/date identity", "publication_at", "issuer identity"], "licensed or authorized historical forecast vintages and stale-estimate policy", "publication-date permutation within forecast horizon", priority="B"),
        "H08": _candidate("H08", "Post-earnings-announcement drift", "SURP_i,t = (actual_eps_i,t - pre_release_consensus_eps_i,t) / abs(pre_release_consensus_eps_i,t); one event per issuer per 63 sessions", "long highest positive surprise decile", "21 NYSE sessions from next open after release_at", ["actual EPS", "pre-release consensus", "release_at", "accepted_at", "revision vintage", "event identity"], "typed release timestamp and pre-release consensus vintage with revision/collision policy", "fixed calendar-day placebo matched to release weekday", priority="B"),
        "H09": _candidate("H09", "Customer-supplier information spillover", "SPILL_supplier,t = customer_surprise_t * relationship_weight_supplier,customer,t; relationship must be PIT named and directional", "long highest positive supplier spillover decile", "21 NYSE sessions from next open after linked customer event", ["named customer/supplier link", "direction", "relationship vintage", "termination", "customer event publication_at", "security IDs"], "PIT relationship vintages, direction, termination, and linked-firm event mapping", "same supplier and weekday with customer event labels permuted", priority="B"),
        "H10": _candidate("H10", "Filing language change / Lazy Prices adaptation", "CHANGE_i,t = 1 - cosine(tfidf(section_text_latest), tfidf(section_text_prior)); sections are MD&A and risk factors, same form and issuer", "long lowest CHANGE quintile (predeclared non-changer adaptation); no short leg", "63 NYSE sessions from next open after EDGAR accepted_at", ["original 10-K/10-Q pair", "same-CIK/report-period linkage", "accepted_at", "MD&A/risk-factor text", "version/amendment identity"], "complete PIT filing-version breadth, acceptance times, text normalization, and frozen direction", "same issuer/form with prior-version order permuted", priority="B"),
        "H11": _candidate("H11", "Fund-flow forced selling pressure", "PRESSURE_i,t = sum_f(-net_flow_f,t * PIT_ownership_weight_i,f,t / median_dollar_volume_20d_i,t); buy after positive pressure", "long highest positive pressure decile", "21 NYSE sessions from next open after flow/holding publication lag", ["fund flow", "holdings vintage", "fund CIK", "ownership weight", "publication_at", "ADV"], "PIT NPORT/flow histories, ownership weights, publication lag, and rebalance identity", "fund-event labels permuted within reporting month", priority="A"),
        "H12": _candidate("H12", "Index-change price pressure", "event_score_i,t = +1 for a PIT named-index addition announced before implementation; removals are recorded but not traded by this long-only contract", "long additions only", "21 NYSE sessions from next open after effective_at", ["named index", "methodology vintage", "membership", "announcement_at", "effective_at", "implementation notice", "security ID"], "dated PIT membership/change archive and methodology version", "fixed non-event index-calendar dates matched by weekday/month", priority="C"),
        "H13": _candidate("H13", "Overnight versus intraday decomposition", "INTRA_i,t = close_i,t-1 / open_i,t-1 - 1; rank at t-1 close, enter at t open, exit at t close; previous overnight return is a control", "long highest prior-session intraday-return decile", "one regular session open-to-close", ["raw and adjusted open/close", "session calendar", "quote/trade timestamps", "auction/fill semantics", "feed identity"], "historical SIP/IEX parity, auction semantics, and executable intraday quotes/trades", "session labels permuted within month with identical trading-session count", priority="B"),
        "H14": _candidate("H14", "Turn-of-month ETF exposure", "preserve frozen SPY rule: P0 decision, L0 open entry, F3 close decision, F4 open exit; explicit four-session tuple", "long SPY during frozen turn-of-month tuple", "exact frozen L0/F1/F2/F3 open-to-open intervals", ["SPY split-adjusted bars", "NYSE calendar", "cash dividends", "P0/L0/F1/F2/F3/F4 tuple"], "preserve existing 18-cell result; no rewriting of the frozen H14 outcome", "preserve frozen fixed-calendar control", priority="A", source_note="existing frozen H14 result remains provenance-only; this protocol adds H14b definitions separately"),
        "H15": _candidate("H15", "Cash merger arbitrage adaptation", "SPREAD_i,t = (cash_offer_per_share_t - target_price_t) / target_price_t; cash-only offers, no stock consideration", "long target after public announcement", "until close/cancel/break/delist terminal event; missing terminal event is non-admissible", ["cash offer", "target ID", "announcement_at", "amendments", "close/cancel/break/delist", "consideration currency"], "complete official deal lifecycle, amendments, terminal events, identity, and delisting mapping", "fixed calendar dates matched to deal announcement weekday", priority="C"),
        "H16": _candidate("H16", "Two-step spinoff ownership conversion", "event_i,t = first effective date with explicit parent/recipient ownership conversion; signal is a binary event, no inferred return sign", "long recipient security after effective date; parent/recipient identity must be explicit", "63 NYSE sessions from next supported open after effective_at", ["announcement_at", "effective_at", "distribution", "conversion ratio", "successor IDs", "adjusted prices", "lifecycle"], "official spinoff distribution and successor mapping with lifecycle completeness", "effective-date weekday/calendar placebo with matched event count", priority="C", source_note="direction is a frozen falsifiable adaptation; the paper does not establish a generic sign"),
    }
    for card in cards.values():
        card["overlay"] = COMMON["overlay"]
        card["control_policy"] = COMMON["overlay"]["controls"]
        card["status_before_data"] = "DEFINITION_FROZEN_DATA_GATE_PENDING"
    return cards


def protocol_payload() -> dict[str, Any]:
    return {
        "schema": "equity-h01-h16-standalone-overlay-protocol-v1",
        "status": STATUS,
        "frozen_at": "2026-09-09T00:00:00Z",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "candidate_ids": EXPECTED_IDS,
        "candidate_count": 16,
        "source_policy": "primary papers and official SEC/NYSE documentation support hypotheses and field semantics only; they are not local performance evidence",
        "common": COMMON,
        "candidates": definitions(),
        "h14b": {
            "status": "FROZEN_DIAGNOSTIC_VARIANTS_NOT_PART_OF_H14_RESULT",
            "base_result_path": str(H14_RESULT.relative_to(REPO)),
            "base_result_sha256": sha256(H14_RESULT),
            "variants": [
                {"id": "H14b_spare_slot_only", "rule": "admit the frozen H14 sleeve only on dates current V3 has fewer than 6 positive positions; otherwise hold candidate sleeve in cash", "required": ["current V3 dated weight history", "H14 marked positions", "same NAV/fill dates"], "status": "NOT_RUN_MISSING_JOINED_CAPACITY_INPUT"},
                {"id": "H14b_lowest_ranked_slot_substitution", "rule": "when V3 has 6 positions and H14 is active, replace only the lowest-ranked V3 slot using a predeclared V3 rank snapshot; otherwise preserve V3", "required": ["current V3 dated rank snapshot", "H14 marked positions", "replacement fill/cost ledger"], "status": "NOT_RUN_MISSING_DATED_V3_RANK_INPUT"},
            ],
            "preservation": "the existing H14 18-cell result and current-V3 parity artifact are immutable inputs and are not rewritten by this package",
        },
        "research_boundaries": ["no OHLCV substitution for causal fields", "no retrieval-time or mutable-label substitution", "no economic admission from definition or literature alone", "no automatic promotion, PAPER, LIVE, production, registry, or configuration change"],
        "references": SOURCES,
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(payload: dict[str, Any], field: str) -> str:
    value = dict(payload)
    value[field] = PENDING
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _md(payload: dict[str, Any]) -> str:
    lines = ["# Equity H01–H16 standalone/overlay protocol — v1 (2026-09-09)", "", f"Status: `{payload['status']}`. This is a pre-outcome definition freeze; it records no economic result and cannot change V3, PAPER, LIVE, production, registry, or configuration.", "", "## Common frozen contract", "", "- Cutoff: `2026-07-31T23:59:59Z`.", "- Universe: all US common equities with point-in-time security/CIK identity and eligible evidence at the decision boundary; a current ticker list is not a PIT universe.", "- Decision: official UTC publication/acceptance time, then the next supported NYSE regular-session open.", "- Windows: full `2022-01-01..2025-12-31`, recent `2024-01-01..2025-12-31`, true holdout `2026-01-01..2026-07-31`; holdout is never used for selection.", "- Costs: current equity execution-cost model at `1x`, `2x`, and `3x`, fixed before any run.", "- Shared overlay: relative to the 55% equity sleeve, `95% current V3 + 5% candidate`; account caps are equity `<=55%`, gross `<=60%`, cash `>=40%`, positions `<=6`.", "- Missing fields, stale/revised values without vintage, identity failures, and unavailable timestamps exclude rows. OHLCV/current labels do not replace causal fields.", "- Robustness plan is frozen at calendar/regime, drawdown/tail, turnover, exact-date correlation, 10,000 moving-block bootstrap (21-session blocks, seed 20260909), 10,000 within-window permutations (seed 20260910), and fixed placebo controls.", "", "## Candidate definitions", "", "| ID | Mechanism | Exact formula | Direction | Rebalance/holding | Required causal fields |", "|---|---|---|---|---|---|"]
    for cid in payload["candidate_ids"]:
        card = payload["candidates"][cid]
        lines.append(f"| {cid} | {card['mechanism']} | {card['formula']} | {card['direction']} | {card['rebalance']} / {card['holding']} | {', '.join(card['required_fields'])} |")
    lines.extend(["", "## H14b diagnostic variants", "", "H14's existing frozen 18-cell result is preserved. H14b variants are separate definitions and have no result until their dated current-V3 capacity/rank inputs are available.", "", "| Variant | Frozen rule | Status |", "|---|---|---|"])
    for variant in payload["h14b"]["variants"]:
        lines.append(f"| {variant['id']} | {variant['rule']} | `{variant['status']}` |")
    lines.extend(["", "## Data and decision boundary", "", "Every candidate requires a receipt-bound source matrix, immutable publication/acceptance time, source vintage, security continuity, explicit missing-data disposition, and a complete full/recent/true-holdout inventory before any economic run. Standalone and shared-capital results must be reported separately; a valid literature mechanism is not local evidence.", "", "## Primary references", ""])
    lines.extend(f"- **{cid}** {item['title']}: {item['url']}" for cid, item in payload["references"].items())
    lines.extend(["", "No candidate is economically admitted or promoted by this protocol.", ""])
    return "\n".join(lines)


def _result_rows(payload: dict[str, Any], closure: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for cid in EXPECTED_IDS:
        candidate = closure["candidates"][cid]
        rows.append({"candidate_id": cid, "definition_status": "FROZEN", "economic_status": "NOT_RUN", "standalone_status": "BLOCKED_BY_CAUSAL_DATA" if cid != "H14" else "EXISTING_FROZEN_DIAGNOSTIC_ONLY", "joint_status": "NOT_RUN_CAPACITY_OR_DATA_GATE", "current_authoritative_status": candidate["status"], "current_evidence_source": {"path": str(CURRENT_CLOSURE.relative_to(REPO)), "sha256": sha256(CURRENT_CLOSURE), "pointer": ["candidates", cid]}, "economic_admitted": False, "integration_candidate": False, "terminal_blocker": payload["candidates"][cid]["blocker_if_unavailable"], "tested": cid == "H14", "result_claim": "existing H14 diagnostic is retained as provenance; no new economic result is claimed" if cid == "H14" else "no economic result; required PIT causal fields are not locally complete"})
    return rows


def build(repo: Path = REPO) -> dict[str, Any]:
    payload = protocol_payload()
    closure = json.loads(CURRENT_CLOSURE.read_text(encoding="utf-8"))
    results = {"schema": "equity-h01-h16-protocol-results-v1", "status": "DEFINITION_AND_DATA_GATE_ONLY", "candidate_count": 16, "economic_run": False, "integration_candidate_count": 0, "rows": _result_rows(payload, closure), "h14_result": {"path": str(H14_RESULT.relative_to(REPO)), "sha256": sha256(H14_RESULT), "status": "PRESERVED_EXISTING_DIAGNOSTIC"}}
    protocol_bytes = (_md(payload)).encode("utf-8")
    results_bytes = (json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    manifest = {"schema": "equity-h01-h16-standalone-overlay-manifest-v1", "status": STATUS, "protocol": {"path": str(PROTOCOL_MD.relative_to(REPO)), "sha256": hashlib.sha256(protocol_bytes).hexdigest()}, "results": {"path": str(RESULTS.relative_to(REPO)), "sha256": hashlib.sha256(results_bytes).hexdigest()}, "script": {"path": str(SCRIPT.relative_to(REPO)), "sha256": sha256(SCRIPT)}, "test": {"path": str(TEST.relative_to(REPO)), "sha256": sha256(TEST)}, "inputs": {str(path.relative_to(REPO)): {"sha256": sha256(path)} for path in (FULL_REPORT, EQUITY_REVIEW, CURRENT_CLOSURE, H14_RESULT)}, "candidate_count": 16, "candidate_ids": EXPECTED_IDS, "economic_run": False, "integration_candidate_count": 0, "no_auto_promotion": True, "no_ohlcv_proxy": True, "manifest_sha256": PENDING}
    manifest["manifest_sha256"] = canonical_hash(manifest, "manifest_sha256")
    receipt = {"schema": "equity-h01-h16-protocol-receipt-v1", "status": STATUS, "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": hashlib.sha256((json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()).hexdigest(), "canonical_sha256": manifest["manifest_sha256"]}, "protocol": {"path": str(PROTOCOL_MD.relative_to(REPO)), "sha256": hashlib.sha256(protocol_bytes).hexdigest()}, "results": {"path": str(RESULTS.relative_to(REPO)), "sha256": hashlib.sha256(results_bytes).hexdigest()}, "candidate_count": 16, "economic_run": False, "integration_candidate_count": 0, "receipt_sha256": PENDING}
    receipt["receipt_sha256"] = canonical_hash(receipt, "receipt_sha256")
    files = {PROTOCOL_MD: protocol_bytes, RESULTS: results_bytes, MANIFEST: (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(), RECEIPT: (json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()}
    _atomic(files)
    verify(repo)
    return {"status": STATUS, "candidate_count": 16, "protocol": str(PROTOCOL_MD), "results": str(RESULTS), "manifest": str(MANIFEST), "receipt": str(RECEIPT), "manifest_sha256": manifest["manifest_sha256"], "receipt_sha256": receipt["receipt_sha256"]}


def _atomic(files: dict[Path, bytes]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            staged.append((temporary, path))
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def verify(repo: Path = REPO) -> dict[str, Any]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if payload.get("status") != STATUS or payload.get("candidate_count") != 16 or payload.get("candidate_ids") != EXPECTED_IDS or payload.get("economic_run") is not False or payload.get("integration_candidate_count") != 0:
        raise ValueError("protocol_manifest_policy_or_inventory_invalid")
    canonical = dict(payload)
    declared = canonical.pop("manifest_sha256")
    if declared != hashlib.sha256(json.dumps({**canonical, "manifest_sha256": PENDING}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest():
        raise ValueError("protocol_manifest_self_hash_mismatch")
    for section in ("protocol", "results", "script", "test", "inputs"):
        values = payload.get(section, {})
        values = values if section == "inputs" else {values.get("path"): values}
        for relative, declaration in values.items():
            path = repo / relative
            if not path.is_file() or sha256(path) != declaration.get("sha256"):
                raise ValueError(f"protocol_hash_mismatch:{relative}")
    results = json.loads((repo / payload["results"]["path"]).read_text(encoding="utf-8"))
    if results.get("candidate_count") != 16 or len(results.get("rows", [])) != 16 or results.get("economic_run") is not False or results.get("integration_candidate_count") != 0:
        raise ValueError("protocol_results_inventory_or_policy_invalid")
    if any(row.get("integration_candidate") is not False or row.get("economic_admitted") is not False for row in results["rows"]):
        raise ValueError("protocol_results_economic_policy_invalid")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("receipt_sha256") != canonical_hash(receipt, "receipt_sha256") or receipt.get("manifest", {}).get("canonical_sha256") != declared or receipt.get("results", {}).get("sha256") != payload["results"]["sha256"]:
        raise ValueError("protocol_receipt_binding_invalid")
    return {"status": "FROZEN_PROTOCOL_VERIFIED", "candidate_count": 16, "economic_run": False, "integration_candidate_count": 0, "manifest_sha256": declared}


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True))
