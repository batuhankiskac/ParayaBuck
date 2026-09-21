# Security mapping reassessment

## Verdict

`INSIDER_SECURITY_MAPPING_FEASIBLE_FAIL_CLOSED`

The old blanket blocker is narrowed, not erased. A defensible subset can be
constructed without current-ticker lookahead, but the repository still lacks a
complete official issuer-CIK/security-class bridge for every historical event.

## Causal bridge

1. Preserve as-filed issuer CIK, symbol, accession, and filing date.
2. Match the exact uppercase as-filed symbol only to a lifetime row valid on the
   filing date. Never substitute today's symbol.
3. Require one lifetime candidate.
4. Maintain the observed CIK/entity bridge in filing-date order. At each event,
   use only mappings public on or before that filing date; exclude a CIK mapped
   to multiple entities or an entity observed under multiple CIKs as of then.
5. Require an explicitly common-like full security-title pattern. Convertible,
   preferred, option, warrant, unit, note, right, RSU, phantom, and unrecognized
   titles fail closed.
6. Require exactly one reporting owner for CMP attribution. Joint/missing-owner
   rows are not copied onto every co-filer.

## Measured coverage

From 53 official archives, 399,104 original-Form-4 code-P/acquired/positive
shares-and-price rows remain after amendment/basic-field gates. Of these:

- 103,681 have a unique exact symbol/date lifetime match;
- 81,312 also have a common-like title;
- 15,043 common-like mapped rows fail the as-of CIK/entity collision gate;
- 9,026 more fail the single-owner gate;
- 57,243 pass every fail-closed structural mapping/ownership gate (14.34% of
  all qualifying SEC purchase rows).

The low all-SEC denominator is expected because the repository lifetime file is
a bounded equity universe, not the EDGAR issuer universe. It must not be called
full-universe coverage. Exact counts and archive hashes are in
`STRUCTURAL_MAPPING_COUNTS.json`.

## Remaining gaps

Generic “Common Stock” does not prove exchange/listing identity by itself;
historical CIK-to-class identifiers and corporate reorganizations still need
curated evidence for broader coverage. CIK collisions and ticker reuse are
excluded rather than guessed. Thus mapping is sufficient for a conservative
structural opportunity audit, but insufficient to claim complete candidate
coverage or authorize an economic test.
