"""Offline structural and no-peek validation for the V4 discovery artifacts."""

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REQUIRED = {
    "DISCOVERY_RUBRIC_V4.md",
    "DISCOVERY_RUBRIC_V4.json",
    "PRIOR_RESEARCH_EXCLUSION_REGISTER.md",
    "PRIOR_RESEARCH_EXCLUSION_REGISTER.json",
    "DISCOVERY_SEARCH_PLAN.md",
    "LONG_LIST_V4.csv",
    "LONG_LIST_V4.json",
    "STAGE_A_SCREEN.md",
    "STAGE_A_SCREEN.json",
    "STAGE_B_METHOD_SCREEN.md",
    "STAGE_B_METHOD_SCREEN.json",
    "STAGE_C_FEASIBILITY_SCREEN.md",
    "STAGE_C_FEASIBILITY_SCREEN.json",
    "ANTI_REDUNDANCY_AUDIT.md",
    "LONG_HIGH_EVIDENCE_MATRIX.csv",
    "MODERNITY_MATRIX.csv",
    "PUBLIC_SOURCE_CAPABILITY_INDEX_V4.csv",
    "CONCENTRATION_MATRIX.csv",
    "SLOT_EFFICIENCY_MATRIX.csv",
    "FINAL_SHORTLIST.md",
    "FINAL_SHORTLIST.json",
    "NEGATIVE_KNOWLEDGE_V4.json",
    "NO_PEEK_RECEIPT.json",
    "COUNT_RECONCILIATION.json",
    "DISCOVERY_DECISION.json",
    "FINAL_PUBLIC_DATA_ALPHA_DISCOVERY_V4_REPORT.md",
    "VALIDATION_RECEIPT.md",
}
FORBIDDEN_FIELDS = {
    "future_return",
    "forward_return",
    "pnl",
    "cagr",
    "sharpe",
    "sortino",
    "calmar",
    "drawdown",
    "alpha_return",
    "realized_future_return",
}


def load_json(name: str) -> Any:
    return json.loads((ROOT / name).read_text())


def scan_keys(value: Any, location: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert str(key).lower() not in FORBIDDEN_FIELDS, (location, key)
            scan_keys(item, location)
    elif isinstance(value, list):
        for item in value:
            scan_keys(item, location)


def main() -> None:
    assert REQUIRED <= {path.name for path in ROOT.iterdir()}
    for path in ROOT.iterdir():
        if path.suffix == ".json":
            scan_keys(json.loads(path.read_text()), path.name)
        elif path.suffix == ".csv":
            with path.open(newline="") as stream:
                reader = csv.DictReader(stream)
                assert reader.fieldnames
                assert not FORBIDDEN_FIELDS.intersection(field.lower() for field in reader.fieldnames)
                list(reader)

    rows = load_json("LONG_LIST_V4.json")
    with (ROOT / "LONG_LIST_V4.csv").open(newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert len(rows) == len(csv_rows) >= 50
    assert [row["candidate_id"] for row in rows] == [row["candidate_id"] for row in csv_rows]
    assert len({row["candidate_id"] for row in rows}) == len(rows)
    assert len({row["family"] for row in rows}) == len(rows)
    for row in rows:
        assert row["public_source"] and row["hard_reject_reason"] and row["disposition"]
        assert all(isinstance(row[f"R{i}"], int) and 0 <= row[f"R{i}"] <= 3 for i in range(1, 15))
        assert sum(row[f"R{i}"] for i in range(1, 15)) == row["rubric_total"]
        assert not row["literature_url"] or row["literature_url"].startswith("https://")
    stages = [
        load_json(f"STAGE_{stage}_{suffix}.json")
        for stage, suffix in (("A", "SCREEN"), ("B", "METHOD_SCREEN"), ("C", "FEASIBILITY_SCREEN"))
    ]
    stage_ids = [{row["candidate_id"] for row in stage} for stage in stages]
    assert stage_ids[2] <= stage_ids[1] <= stage_ids[0]
    assert len(stages[0]) <= 15 and len(stages[1]) <= 8 and len(stages[2]) <= 5
    assert all(row["literature_url"] for row in stages[0])
    closed = set(load_json("PRIOR_RESEARCH_EXCLUSION_REGISTER.json")["closed"])
    assert not closed.intersection(row["family"] for row in stages[0])
    shortlist = load_json("FINAL_SHORTLIST.json")
    assert len(shortlist) <= 3
    counts = Counter(row["disposition"] for row in rows)
    receipt = load_json("COUNT_RECONCILIATION.json")
    assert receipt["status"] == "PASS"
    assert receipt["long_list_total"] == len(rows) == sum(counts.values())
    assert receipt["disposition_counts"] == dict(counts)
    assert [receipt[f"stage_{stage}_count"] for stage in "abc"] == [len(stage) for stage in stages]
    assert receipt["final_shortlist_count"] == len(shortlist)
    assert len(load_json("NEGATIVE_KNOWLEDGE_V4.json")) == len(rows)
    with (ROOT / "PUBLIC_SOURCE_CAPABILITY_INDEX_V4.csv").open(newline="") as stream:
        sources = list(csv.DictReader(stream))
    assert sources and all(source["source_url"] and source["publication_semantics"] for source in sources)
    assert load_json("NO_PEEK_RECEIPT.json")["local_future_economics"] == "NONE"
    assert load_json("DISCOVERY_DECISION.json")["candidate_backtests"] == "NONE"
    print(f"PASS: {len(rows)} families, stages {[len(s) for s in stages]}, {len(shortlist)} shortlist")


if __name__ == "__main__":
    main()
