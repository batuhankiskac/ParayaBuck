"""Regenerate the H01-H05 diagnostic against a pin-expanded local evidence set."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUTPUT = REPO / "data/research/equity_h01_h05_causal_input_gate_2026_09_08_r16"
MANIFEST = REPO / "data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r16.json"
REPORT = REPO / "docs/research/equity_h01_h05_causal_input_gate_2026_09_09_r16.md"
SOURCE_CATALOG = REPO / "data/research/external/equity_h01_h05_sec_filings_2026_09_09/filing_fact_inventory.json"
SOURCE_CAUSAL = REPO / "data/research/external/equity_h01_h05_sec_filings_2026_09_09/causal_fact_inventory.json"
SOURCE_CLOSURE = REPO / "data/research/external/equity_h01_h05_route_closure_2026_09_09/route_closure_receipt.json"
SOURCE_RECEIPT = REPO / "data/research/external/equity_h01_h05_sec_filings_2026_09_09/acquisition_receipt.json"
SOURCE_VERIFIER = REPO / "scripts/research/verify_equity_h01_h05_sec_filing_evidence_2026_09_09.py"
BASE_RUNNER = REPO / "scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location("h01_h05_r11", BASE_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("BASE_RUNNER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extra_inventory(module: Any, base_inventory: Any) -> list[dict[str, Any]]:
    entries = base_inventory()
    receipt = json.loads(SOURCE_RECEIPT.read_text(encoding="utf-8"))
    for item in receipt["requests"]:
        path = REPO / str(item["output"])
        entries.append(
            {
                "relative_path": str(path.relative_to(REPO)),
                "size": path.stat().st_size,
                "sha256": sha256(path),
                "provider": "sec_edgar",
                "kind": "sec_filing_body",
                "cik": str(item["issuer_cik"]).zfill(10),
            }
        )
    for path, kind in (
        (SOURCE_RECEIPT, "sec_filing_receipt"),
        (SOURCE_CATALOG, "sec_filing_fact_inventory"),
        (SOURCE_CAUSAL, "sec_causal_fact_inventory"),
        (SOURCE_CLOSURE, "public_route_closure_receipt"),
    ):
        entries.append(
            {
                "relative_path": str(path.relative_to(REPO)),
                "size": path.stat().st_size,
                "sha256": sha256(path),
                "provider": "sec_edgar",
                "kind": kind,
                "cik": None,
            }
        )
    return sorted(entries, key=lambda item: item["relative_path"])


def _write_report(receipt: dict[str, Any], manifest: dict[str, Any]) -> None:
    summary = manifest["summary"]
    text = f"""# H01-H05 causal input gate — 2026-09-09 r16

This research-only revision retains the frozen eight-symbol, 36 decision
month-end grid and adds a verified EDGAR filing-evidence bundle to the source
inventory plus a deterministic numeric causal-fact inventory. The r11/r12 diagnostics remain preserved. No production, PAPER, LIVE,
return, portfolio, or promotion run was started.

The bounded acquisition contains {json.loads(SOURCE_RECEIPT.read_text())["request_count"]} frozen-universe 10-K/10-Q filings dated 2022–2024. Each body returned HTTP 200,
was revalidated with HTTP metadata, and is bound by accession, report date,
acceptance timestamp, source URL, local path, size, and SHA-256. The fact
inventory records exact-tag XBRL numeric values only with matching context,
unit, period, issuer identity, accepted_at, amendment status, and body/receipt
hashes. Facts remain separate evidence and are not silently substituted into
the frozen row runner; paired formula rows still require all frozen controls.

Coverage is unchanged at H01 {summary["feature_coverage"]["h01_book_to_market"]},
H02 {summary["feature_coverage"]["h02_gross_profitability"]}, H03
{summary["feature_coverage"]["h03_accruals"]}, H04
{summary["feature_coverage"]["h04_asset_growth"]}, and H05
{summary["feature_coverage"]["h05_net_payout_yield"]} of {summary["row_count"]} rows.
The resulting statuses remain H01 blocked on historical common-security and
raw-price/share equivalence, H02 and H04 blocked on breadth, H03 schema-only
with two period-mismatch rows and unresolved continuity, and H05 blocked on
price/share basis and breadth. No candidate is eligible for economic testing.

Pinned evidence:

* filing receipt: `{SOURCE_RECEIPT.relative_to(REPO)}` SHA-256 `{sha256(SOURCE_RECEIPT)}`;
* filing fact inventory: `{SOURCE_CATALOG.relative_to(REPO)}` SHA-256 `{sha256(SOURCE_CATALOG)}`;
* numeric causal fact inventory: `{SOURCE_CAUSAL.relative_to(REPO)}` SHA-256 `{sha256(SOURCE_CAUSAL)}`;
* public route closure receipt: `{SOURCE_CLOSURE.relative_to(REPO)}` SHA-256 `{sha256(SOURCE_CLOSURE)}`;
* filing receipt/fact-inventory verifier: `{SOURCE_VERIFIER.relative_to(REPO)}` SHA-256 `{sha256(SOURCE_VERIFIER)}`;
* diagnostic output: `{receipt["output"]}` SHA-256 `{receipt["output_sha256"]}`;
* source inventory canonical SHA-256 `{receipt["source_inventory_sha256"]}`;
* diagnostic receipt SHA-256 is recorded in the r16 manifest after this report is written.
"""
    REPORT.write_text(text, encoding="utf-8")


def main() -> int:
    if (OUTPUT.exists() or MANIFEST.exists() or REPORT.exists()) and "--force" not in __import__("sys").argv:
        raise RuntimeError("REFUSE_EXISTING_EQUITY_R16_OUTPUT")
    if not SOURCE_CATALOG.exists() or not SOURCE_RECEIPT.exists() or not SOURCE_CAUSAL.exists() or not SOURCE_CLOSURE.exists():
        raise RuntimeError("MISSING_VERIFIED_SEC_FILING_EVIDENCE")
    module = _load_base()
    module.OUTPUT = OUTPUT
    module.MANIFEST = MANIFEST
    base_inventory = module.source_inventory
    module.source_inventory = lambda: _extra_inventory(module, base_inventory)
    module.main()
    receipt_path = OUTPUT / "diagnostic_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.update(
        {
            "mission": "equity_h01_h05_causal_input_gate_2026_09_08_r16",
            "runner_sha256": sha256(Path(__file__)),
            "filing_acquisition_receipt": str(SOURCE_RECEIPT.relative_to(REPO)),
            "filing_acquisition_receipt_sha256": sha256(SOURCE_RECEIPT),
            "filing_evidence_inventory": str(SOURCE_CATALOG.relative_to(REPO)),
            "filing_evidence_inventory_sha256": sha256(SOURCE_CATALOG),
            "filing_causal_fact_inventory": str(SOURCE_CAUSAL.relative_to(REPO)),
            "filing_causal_fact_inventory_sha256": sha256(SOURCE_CAUSAL),
            "public_route_closure_receipt": str(SOURCE_CLOSURE.relative_to(REPO)),
            "public_route_closure_receipt_sha256": sha256(SOURCE_CLOSURE),
            "filing_evidence_verifier": str(SOURCE_VERIFIER.relative_to(REPO)),
            "filing_evidence_verifier_sha256": sha256(SOURCE_VERIFIER),
        }
    )
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_report(receipt, json.loads(MANIFEST.read_text(encoding="utf-8")))
    receipt["report"] = str(REPORT.relative_to(REPO))
    receipt["report_sha256"] = sha256(REPORT)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest.update(
        {
            "mission": "equity_h01_h05_causal_input_gate_2026_09_08_r16",
            "runner_sha256": sha256(Path(__file__)),
            "filing_acquisition_receipt": str(SOURCE_RECEIPT.relative_to(REPO)),
            "filing_acquisition_receipt_sha256": sha256(SOURCE_RECEIPT),
            "filing_evidence_inventory": str(SOURCE_CATALOG.relative_to(REPO)),
            "filing_evidence_inventory_sha256": sha256(SOURCE_CATALOG),
            "filing_causal_fact_inventory": str(SOURCE_CAUSAL.relative_to(REPO)),
            "filing_causal_fact_inventory_sha256": sha256(SOURCE_CAUSAL),
            "public_route_closure_receipt": str(SOURCE_CLOSURE.relative_to(REPO)),
            "public_route_closure_receipt_sha256": sha256(SOURCE_CLOSURE),
            "filing_evidence_verifier": str(SOURCE_VERIFIER.relative_to(REPO)),
            "filing_evidence_verifier_sha256": sha256(SOURCE_VERIFIER),
            "diagnostic_receipt": str(receipt_path.relative_to(REPO)),
            "diagnostic_receipt_sha256": sha256(receipt_path),
            "report": str(REPORT.relative_to(REPO)),
            "report_sha256": sha256(REPORT),
        }
    )
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"EQUITY_H01_H05_R16_COMPLETE rows={manifest['summary']['row_count']} inventory={manifest['source_inventory_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
