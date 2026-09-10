# H01-H05 causal input gate — 2026-09-09 r15

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
The resulting statuses remain H01 blocked on historical common-security and
raw-price/share equivalence, H02 and H04 blocked on breadth, H03 schema-only
with two period-mismatch rows and unresolved continuity, and H05 blocked on
price/share basis and breadth. No candidate is eligible for economic testing.

Pinned evidence:

* filing receipt: `data/research/external/equity_h01_h05_sec_filings_2026_09_09/acquisition_receipt.json` SHA-256 `cc06a2031aebc53342a82c3ea60034ffc674ea9c622b6eec5577aeeff0734ec3`;
* filing fact inventory: `data/research/external/equity_h01_h05_sec_filings_2026_09_09/filing_fact_inventory.json` SHA-256 `02ce6d5a46fa71757170b18165bb7395a3aba2427504d91572edbd6ab571aeef`;
* numeric causal fact inventory: `data/research/external/equity_h01_h05_sec_filings_2026_09_09/causal_fact_inventory.json` SHA-256 `3efc97edfb6092793ef91e351b62f8fb4ef5b1f8fd8fef207b84327fa6074da0`;
* public route closure receipt: `data/research/external/equity_h01_h05_route_closure_2026_09_09/route_closure_receipt.json` SHA-256 `9e22dd093f25766eb545ac219918074335412b3f1d0431b68f42e42cd62fe40c`;
* filing receipt/fact-inventory verifier: `scripts/research/verify_equity_h01_h05_sec_filing_evidence_2026_09_09.py` SHA-256 `6de3b875bf2cf491f489da3db4980848c783c00bff906837e65e70b79aae735a`;
* diagnostic output: `data/research/equity_h01_h05_causal_input_gate_2026_09_08_r15/causal_input_rows.jsonl` SHA-256 `ced463b9f8889d66d5322fa97fa59b1c2c46a413a90981eab0a1639176890806`;
* source inventory canonical SHA-256 `9f2b11177c2c9b0811786f5ce1ff96bd9b5627241ee20da772c5e5f70a0a9a1f`;
* diagnostic receipt SHA-256 is recorded in the r15 manifest after this report is written.
