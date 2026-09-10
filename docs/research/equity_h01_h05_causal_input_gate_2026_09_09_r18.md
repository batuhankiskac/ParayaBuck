# H01-H05 causal input gate — 2026-09-09 r18

This research-only revision retains the frozen eight-symbol, 36 decision
month-end grid and adds a verified EDGAR filing-evidence bundle to the source
inventory plus a deterministic numeric causal-fact inventory. The r11/r12 diagnostics remain preserved. No production, PAPER, LIVE,
return, portfolio, or promotion run was started.

The bounded acquisition contains 98 frozen-universe 10-K/10-Q filings dated 2022–2024. Each body returned HTTP 200,
was revalidated with HTTP metadata, and is bound by accession, report date,
acceptance timestamp, source URL, local path, size, and SHA-256. The fact
inventory records exact-tag XBRL numeric values only with matching context,
unit, period, issuer identity, accepted_at, amendment status, and body/receipt
hashes. Facts remain separate evidence and are not silently substituted into
the frozen row runner; paired formula rows still require all frozen controls.

Coverage is unchanged at H01 288,
H02 72, H03
286, H04
19, and H05
32 of 288 rows.
A fresh bounded Yahoo chart/download retry covered all eight frozen symbols and both chart hosts. Seven symbols produced schema-valid chart evidence on both hosts (16 valid route attempts total); A required event-key versus declared-date handling, and AA returned 2,264 rows versus the pinned V3 count of 2,202. Download variants returned HTTP 401. All retry evidence remains excluded from admission because PIT availability and common-security continuity are unproven.

The resulting statuses remain H01 blocked on historical common-security and
raw-price/share equivalence, H02 and H04 blocked on breadth, H03 schema-only
with two period-mismatch rows and unresolved continuity, and H05 blocked on
price/share basis and breadth. No candidate is eligible for economic testing.

Pinned evidence:

* filing receipt: `data/research/external/equity_h01_h05_sec_filings_2026_09_09/acquisition_receipt.json` SHA-256 `cc06a2031aebc53342a82c3ea60034ffc674ea9c622b6eec5577aeeff0734ec3`;
* filing fact inventory: `data/research/external/equity_h01_h05_sec_filings_2026_09_09/filing_fact_inventory.json` SHA-256 `b52c16dbacddafc540f227e6e29179324139324efefb66ddd33eb6ceee7dfece`;
* numeric causal fact inventory: `data/research/external/equity_h01_h05_sec_filings_2026_09_09/causal_fact_inventory.json` SHA-256 `7d1f193bf864dbdcafba3b4517b4f512ea17e7d0dd439199395af4d98f035486`;
* public route closure receipt: `data/research/external/equity_h01_h05_route_closure_2026_09_09/route_closure_receipt.json` SHA-256 `8d737ac9726274e1cebc9a840c5a4a3ba13492c032e42999fc693d53b9fb5d84`;
* price/security retry receipt: `data/research/external/equity_h01_h05_price_security_retry_2026_09_09/retry_receipt.json` SHA-256 `7f6c3083f77760f07eb4651fadb9c1c304a8328134a6a009bd506786f4a1166e`;
* price/security retry runner: `scripts/research/acquire_equity_h01_h05_price_security_retry_2026_09_09.py` SHA-256 `84e2c20f69464cac23985a06d0f486b94a0b49b541e3057d510e2b4d63078106`;
* filing receipt/fact-inventory verifier: `scripts/research/verify_equity_h01_h05_sec_filing_evidence_2026_09_09.py` SHA-256 `2242823fad3447acc35ac95ba1ae531db5b2066e5d96fe867984b260b7dd4b8b`;
* diagnostic output: `data/research/equity_h01_h05_causal_input_gate_2026_09_08_r18/causal_input_rows.jsonl` SHA-256 `ced463b9f8889d66d5322fa97fa59b1c2c46a413a90981eab0a1639176890806`;
* source inventory canonical SHA-256 `89c6654b696c1cce1dff34ac4b15ddc05bc8f474f1e1cfcd1fd0bda46ebc4852`;
* diagnostic receipt SHA-256 is recorded in the r18 manifest after this report is written.
