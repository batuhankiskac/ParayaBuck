# X01 maturity bucket approximation protocol — 2026-09-08

This is a frozen research protocol only. It defines a diagnostic mapping for
the case where historical ETF duration and holdings vintages remain absent;
it authorizes no return calculation, candidate ranking, V3 change, PAPER/LIVE
action, or promotion.

| ETF | Frozen maturity bucket | Treasury proxy columns |
|---|---|---|
| SHY | 1–3 years | `1 Yr`, `2 Yr`, `3 Yr` |
| IEF | 7–10 years | `7 Yr`, `10 Yr` |
| TLT | 20+ years | `20 Yr`, `30 Yr` |

The proxy is a maturity-label diagnostic, not historical ETF duration. It does
not infer holdings weights, effective duration, turnover, distributions,
expenses, or available-at timing. Treasury observations are used only at the
published daily date and are cutoff at 2026-07-31. A future economic run would
need an independently reviewed rule for combining the columns and an explicit
ETF accounting ledger.

Status: `FROZEN_PROXY_PIT_BACKING_UNAVAILABLE`; economic admission remains
blocked until historical duration/holdings vintages and source reconciliation
are available.
