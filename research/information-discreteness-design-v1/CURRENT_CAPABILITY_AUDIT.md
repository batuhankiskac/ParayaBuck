# Current capability audit

Status: design-only audit; no candidate economics were computed.

## Starting state

- Repository: `/Users/batuhankiskac/Desktop/ParayaBuck`
- Design branch: `research/information-discreteness-design-v1`
- Clean corrected-main starting SHA: `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de`
- Local `main`: `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de`
- `origin/main`: `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de`
- Worktree at audit start: clean

The branch was created from the clean current `main`; no production files are
modified by this design package.

## PAPER/LIVE identity

The current equity PAPER identity is `equity_residual_momentum_v3`. It is
enabled in `config/settings.yaml`, with a 0.55 maximum equity allocation and
the existing weekly residual-momentum parameters. Runtime mode is `paper`.

LIVE is disabled and the live hard dollar cap is `0`. This design does not
change either setting and does not authorize promotion.

## Corrected canonical data path

The following semantics are available without adding an external dataset:

| Area | Current contract used by the design |
|---|---|
| Bars | Normalized regular-session `1d` equity bars; timestamps are timezone-aware; invalid sessions/weekend rows are rejected. Signal prices are split-adjusted closes. |
| Lifetimes | Strict entity-lifetime schema with unique identities and explicit terminal mapping. Horizon-censored identities remain censored; unresolved identities are never guessed into engine terminal events. |
| Corporate actions | The canonical loader retains cash dividends and applies them once to account P&L. Stock-dividend, forward-split, and reverse-split rows are skipped by the loader; splits are not reapplied to already split-adjusted prices. |
| PIT selection | The existing point-in-time scanner and observation-set path are retained. Historical status, availability, and eligibility are evaluated as of the completed decision bar; no future inactive/delisted status is used to score an earlier bar. |
| Costs | Existing adverse execution fills use slippage plus half spread, and effective-dated SEC/TAF/CAT equity fees. The configured cost multiplier remains the only future stress mechanism; no cost change is introduced here. |
| Roles | `SPY` is reference-only. `SH`, `PSQ`, and `DOG` are hedge-only. They are not ordinary alpha targets. |
| Execution | Decisions use completed bars and target the next tradable bar open proxy. Same-close execution is not available to this design. |
| Architecture | Equity maximum allocation is 0.55, per-position cap is 0.10, maximum positions is 6, and the streamed equity observation cap is 30. |

## Structural feasibility counts

These are dataset-shape counts only. No signal ranking, forward return, P&L,
NAV, IC, Sharpe, CAGR, drawdown, or candidate-versus-V3 comparison was
calculated.

| Check | Count or value |
|---|---:|
| Canonical equity `1d` bars | 11,686,832 |
| Distinct lifetime identities | 9,010 |
| Bar start | 2016-01-04T05:00:00+00:00 |
| Bar end | 2026-07-31T04:00:00+00:00 |
| Identities with at least 274 aligned closes | 7,432 |
| Identities below 274 closes | 1,578 |
| Lifetime rows | 9,010 |
| Explicit terminal events | 2,151 |
| Horizon-censored identities | 6,858 |
| Unresolved lifetime rows | 1 |
| Rename chains | 1 |
| Cash actions retained after loader | 141,751 |
| Action types retained after loader | `dividend` only |

The candidate needs exactly 274 aligned closes: 252 daily returns for the
12-1 formation interval plus 21 most-recent skipped returns. The count above
therefore establishes structural availability, not economic suitability.

## Dataset provenance

The hashes recorded by the canonical V3 protocol are retained as the data
identity for the later A/B test:

- bars: `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`
- lifetimes: `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`
- corporate actions: `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`

`docs/research/canonical_v3_baseline.md` contains a historical production SHA
that predates the corrected current main. This audit uses the verified current
`main` SHA above and does not rewrite that historical research record.

## Scope boundary

This file records capability and structural data facts only. It does not
claim that the candidate works, and it does not authorize a PAPER/LIVE
configuration change.
