# H01-H05 causal input gate — 2026-09-09 r12

This research-only revision retains the frozen eight-symbol, 36 decision
month-end grid and adds a verified EDGAR filing-evidence bundle to the source
inventory. The r11 diagnostic remains preserved. No production, PAPER, LIVE,
return, portfolio, or promotion run was started.

The bounded acquisition contains 98 frozen-universe 10-K/10-Q filings dated 2022–2024. Each body returned HTTP 200,
was revalidated with HTTP metadata, and is bound by accession, report date,
acceptance timestamp, source URL, local path, size, and SHA-256. The fact
inventory records XBRL tag coverage without persisting filing values as
admitted features. The existing causal runner intentionally consumes the
frozen Companyfacts/submissions inputs; therefore this acquisition admits no
new H01–H05 rows until a separately reviewed context, unit, period, and
restatement parser is approved.

Coverage is unchanged at H01 288,
H02 72, H03
286, H04
19, and H05
32 of 288 rows.
The resulting statuses remain H01 blocked on historical common-security and
raw-price/share equivalence, H02 and H04 blocked on breadth, H03 schema-only
with two period-mismatch rows and unresolved continuity, and H05 blocked on
price/share basis and breadth. No candidate is eligible for economic testing.

Pinned evidence:

* filing receipt: `data/research/external/equity_h01_h05_sec_filings_2026_09_09/acquisition_receipt.json` SHA-256 `cc06a2031aebc53342a82c3ea60034ffc674ea9c622b6eec5577aeeff0734ec3`;
* filing fact inventory: `data/research/external/equity_h01_h05_sec_filings_2026_09_09/filing_fact_inventory.json` SHA-256 `b62356a37205311410efc0ded7c64e1cba324974d453e8ded7e6e2be7a472341`;
* filing receipt/fact-inventory verifier: `scripts/research/verify_equity_h01_h05_sec_filing_evidence_2026_09_09.py` SHA-256 `8096879bb33ff9e9f6a7c63c5f6da479ccbe0a2d24ea8f93a4acd2a02e98c4e8`;
* diagnostic output: `data/research/equity_h01_h05_causal_input_gate_2026_09_08_r12/causal_input_rows.jsonl` SHA-256 `ced463b9f8889d66d5322fa97fa59b1c2c46a413a90981eab0a1639176890806`;
* source inventory canonical SHA-256 `18bf652df0b17004f1f8c26a98e92760ac059c89e38033cfd3da472f65d9ffdc`;
* diagnostic receipt SHA-256 is recorded in the r12 manifest after this report is written.
