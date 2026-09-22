"""Rebuild observed-only diagnostics, failing closed on parity and verification."""

from __future__ import annotations

from pathlib import Path

from research.crypto_exit_giveback_audit_v2.canonical_replay import read, write
from research.crypto_exit_giveback_audit_v2.canonical_verify import verify_payload
from research.crypto_exit_giveback_audit_v2.episodes import reconstruct, summarize
from research.crypto_exit_giveback_audit_v2.hysteresis import analyze
from research.crypto_exit_giveback_audit_v2.parity import main as parity_gate
from research.crypto_exit_giveback_audit_v2.verify_episodes import verify, verify_summary

OUT = Path(__file__).resolve().parent


def main() -> None:
    parity_gate()
    raw = read(OUT / "CANONICAL_RAW.json.gz")
    canonical = verify_payload(raw)
    write(OUT / "CANONICAL_INDEPENDENT_VERIFICATION.json", canonical)
    d = reconstruct(raw)
    for e in d["episodes"]:
        e["hysteresis"] = analyze(e, raw)
    receipt = verify(raw, d)
    write(OUT / "INDEPENDENT_VERIFICATION.json", receipt)
    if receipt["status"] != "PASS":
        raise ValueError(receipt)
    net = d["reconciliation"]["account_profit"]
    eps = d["episodes"]
    natural = [e for e in eps if not e["censored"]]
    summary = {
        "natural": summarize(natural, net_strategy_pnl=net),
        "all": summarize(eps, net_strategy_pnl=net),
        "censored": summarize([e for e in eps if e["censored"]], net_strategy_pnl=net),
        "annual_exit_cohorts": {
            str(y): summarize([e for e in natural if e["exit_timestamp"].startswith(str(y))], net_strategy_pnl=net)
            for y in range(2021, 2027)
        },
        "exit_reasons": {
            r: summarize([e for e in eps if e["hysteresis"]["exit_reason"] == r], net_strategy_pnl=net)
            for r in sorted({e["hysteresis"]["exit_reason"] for e in eps})
        },
        "post_peak_retention_groups": {
            str(b): summarize(
                [e for e in natural if e["hysteresis"]["retained_only_after_peak"] == b], net_strategy_pnl=net
            )
            for b in (True, False)
        },
    }
    cohorts = {"natural": natural, "all": eps, "censored": [e for e in eps if e["censored"]]}
    receipt["summary_verification"] = {
        key: verify_summary(group, summary[key], net_strategy_pnl=net) for key, group in cohorts.items()
    }
    if any(item["status"] != "PASS" for item in receipt["summary_verification"].values()):
        receipt["status"] = "FAIL"
    write(OUT / "INDEPENDENT_VERIFICATION.json", receipt)
    if receipt["status"] != "PASS":
        raise ValueError(receipt)
    write(OUT / "CRYPTO_EPISODE_DIAGNOSTICS.json", d)
    write(OUT / "SUMMARY.json", summary)
    print("CANONICAL_AND_ALL_EPISODE_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
