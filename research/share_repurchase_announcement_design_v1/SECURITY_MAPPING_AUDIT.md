# Security mapping audit

Verdict: `REPURCHASE_SECURITY_MAPPING_BLOCKED` for a validated historical
research corpus. A fail-closed mapping specification is feasible, but that is
not the same as a verified CIK/security/entity bridge.

## Local evidence

`src/parayabuck/backtest/lifetimes.py` defines 16 canonical fields and checks
unique entity/provider/symbol IDs and ordered rename intervals. The 9,010-row
v5 lifetime metadata file has no CIK or security-title field. Read only
identity metadata; no bars, future outcomes or candidate return calculations.
The existing symbols and lifetime bounds are useful constraints, not issuer
identity proof. A present ticker match alone is insufficient.

## Required deterministic join

1. Retain accession and as-filed CIK, issuer legal name, registered security
   title, exchange and ticker as disclosed in that historical filing.
2. Establish that the authorization concerns that specific ordinary common
   share class. Reject preferred shares, units, ADR/ordinary-share ambiguity,
   multiple-class ambiguity, and issuer-versus-subsidiary mismatch.
3. Use a provenance-backed effective-dated CIK/security-to-provider/entity
   bridge. Check the historical ticker within its rename interval and entity
   lifetime at causal public availability, not today's alias.
4. Require exactly one compatible canonical entity/security. Quarantine zero
   matches, multiple matches, missing boundaries or contradictory evidence.
5. Persist the filing locator, title, bridge evidence/hash, interval and
   rejection reason. Never silently map an unknown security to ordinary stock.

The issuer sample is source validation only. It has not been joined to a
verified historical CIK bridge; unattempted joins are not reported as failed
matches or zero market opportunity. The structural JSON distinguishes these.

## Instrument contamination

An operating-company ordinary-share authorization requirement excludes most
fund products, but CIK alone does not: funds and issuers with multiple products
also have CIKs. Restrict form/registrant/security semantics and explicitly
exclude investment companies, ETFs/ETNs, tender offers and debt/preferred
repurchases. An operating-company issuer does not prove that every listed
security under its CIK is common stock. General security-master coverage
remains unresolved.

SEC's current ticker files are discovery aids, not historical truth:
[Accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).
The issuer sample and missing bridge do not warrant a mapping-feasible verdict
for the required 2016–2026 corpus.
