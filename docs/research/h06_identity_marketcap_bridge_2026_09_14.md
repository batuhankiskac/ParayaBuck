# H06 identity and market-cap bridge — 2026-09-14

Status: `H06_IDENTITY_MARKETCAP_BLOCKED`.

This is an outcome-free structural audit. No H06 signal, ranking, return, P&L, holdout result, or strategy change was produced.

## Local recovery

All nine persisted H06 SEC probe files and all three canonical V5 inputs were present and matched their frozen SHA-256 values. No V5 file was rebuilt and no tracked probe needed Git restoration. The optional Form 4 PDF was not needed and remains untracked.

## Identity path

The SEC-side anchor is issuer CIK. A decision-safe security state must come from accepted filing-specific cover XBRL and retain accession, form, accepted time, title, symbol, exchange, context, dimensions, URL, and source hash. A Form 4 row must agree on issuer CIK, symbol, and transaction class. The V5 join then requires exactly one active symbol interval and no unresolved ticker reuse.

V5 cannot complete that proof as frozen: it has provider UUID, symbol, observed-bar lifetime, exchange, and build-time status, but no CIK, security name, or share-class identifier. META/FB is one META interval with an embedded rename string, not two exact intervals.

## Shares and split basis

`dei:EntityCommonStockSharesOutstanding` is usable only from accepted filing-specific 10-K/10-Q contexts. The frozen protocol does not authorize latest-accepted carry-forward, so the verdict is `LATEST_ACCEPTED_REPORTED_SHARES_WOULD_BE_A_PROTOCOL_CHANGE`.

V5 acquisition code and manifests prove the request label `adjustment=split`, but not the exact per-row factor or normalization horizon. Corporate actions are internally consistent with `new_rate / old_rate` (AAPL/NVDA forward, GE reverse), yet the provider schema semantics are not pinned. Therefore the exact basis status is `V5_SPLIT_BASIS_NOT_PROVEN` and future-action normalization remains blocked.

## Structural probes

| Case | Class | Identity | Market-cap basis |
|---|---|---|---|
| COMMON_EQUITY_NO_SPLIT | AMBIGUOUS_SECURITY_CLASS | IDENTITY_MISSING | BLOCKED_SECURITY_IDENTITY |
| FORWARD_SPLIT | AMBIGUOUS_SECURITY_CLASS | IDENTITY_MISSING | BLOCKED_SPLIT_BASIS |
| REVERSE_SPLIT | AMBIGUOUS_SECURITY_CLASS | IDENTITY_MISSING | BLOCKED_SPLIT_BASIS |
| SAME_CIK_TICKER_CHANGE | AMBIGUOUS_SECURITY_CLASS | IDENTITY_AMBIGUOUS | BLOCKED_SECURITY_IDENTITY |
| MULTIPLE_COMMON_CLASSES | AMBIGUOUS_SECURITY_CLASS | IDENTITY_MISSING | BLOCKED_SHARES_CLASS_AMBIGUOUS |
| NON_COMMON_LISTED_SECURITY | AMBIGUOUS_SECURITY_CLASS | IDENTITY_MISSING | BLOCKED_SECURITY_IDENTITY |

## SEC acquisition limitation

The official filing pages were readable in a browser, but direct bounded HTTP downloads returned 403. Browser content was not persistable through the available interface. The receipt records source pointers and the transport limitation with `persisted=false`; it deliberately supplies no SHA, treats the page facts as non-evidence, and cannot upgrade those cases to exact status.

## Remaining blockers

- `new bounded SEC filing bodies could not be persisted and SHA-bound because direct SEC HTTP returned 403`
- `Form 4 symbol/title was not receipt-bound for all structural cases`
- `V5 has no issuer CIK or share-class identifier and does not represent META old/new as exact intervals`
- `LATEST_ACCEPTED_REPORTED_SHARES_WOULD_BE_A_PROTOCOL_CHANGE`
- `V5_SPLIT_BASIS_NOT_PROVEN`
- `split ratio provider-schema semantics and V5 normalization horizon are not frozen`
- `H06_BLOCKED_FUTURE_SPLIT_NORMALIZATION`

## Boundary

Economics, H06 scores, ranking, returns, P&L, holdout economics, tuning, V3, PAPER, LIVE, registry, and canonical V5 bytes were untouched.
