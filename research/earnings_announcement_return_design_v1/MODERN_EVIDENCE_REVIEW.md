# Modern and alternative-definition evidence review

## Verdict

`MODERN_US_EAR_EVIDENCE_INSUFFICIENT`

This is an evidence verdict for the proposed 2017–2026 U.S. strategy, not a
claim that every earnings-related drift has vanished.  The literature supports
historical announcement-return continuation and an independently validated
market-adjusted alternative, but it does not establish that the exact Brandt,
Kishore, Santa-Clara and Venkatachalam (BKSV) three-day, characteristic-adjusted
EAR retains a tradeable positive long leg in modern large/liquid U.S. stocks
after the complete EAR is observable.

## What counts as exact EAR

BKSV define EAR as the compounded stock return over trading days `[-1,+1]`
around the quarterly announcement less the compounded return of the matched
Fama–French size/book-to-market portfolio over the same window.  Their sample
is 1987–2004.  That paper is the exact-specification evidence; SUE, analyst
forecast error, call text, guidance, and an announcement-day jump predicted
from SUE are different signals.

Source lock: this campaign uses the January 22, 2008
[BKSV author PDF](https://www.anderson.ucla.edu/documents/areas/fac/finance/santa_clara_surprises.pdf),
SHA-256 `d55a0f2cc116437957f282345e31699a0e50598256e53fca12d1d3fc5b9e5b10`,
especially §§3–4 and Tables 1–7.  The older June 2007 `ear.pdf` has materially
different Table 2 and Table 6 estimates and is not the numerical source for
this campaign.

## Externally supported alternatives

| Definition | Independent evidence | Assessment for this campaign |
|---|---|---|
| Characteristic-adjusted `[-1,+1]` EAR | The January 22, 2008 BKSV version uses a matched Fama–French size/book-to-market portfolio and reports subsequent continuation in 1987–2004. | Primary definition; no post-2010 exact-specification validation found. |
| Market-adjusted `[-2,+1]` announcement return | Chan, Jegadeesh and Lakonishok (CJL) subtract the equally weighted market return and show that this earnings-news proxy predicts subsequent returns separately from price momentum. Hou, Xue and Zhang (HXZ) later implement `sum(stock return - value-weighted market return)` over `[-2,+1]`. | A genuinely literature-supported alternative, not an invented SPY substitution. It changes both benchmark and event window from BKSV and must be named as a source adaptation. |
| Raw announcement return | No primary U.S. study located that validates raw return alone as the same future-return predictor while addressing market-wide announcement-window moves. | Not validated for selection merely because local OHLCV can compute it. |
| Market-model abnormal return | Event studies commonly use market-model CAR, but no primary source located here validates a market-model version as the proposed standalone future-return EAR signal. | Not validated for substitution. |
| V3 residual return | No external EAR paper located that uses ParayaBuck's long-horizon residual-momentum construction as the announcement benchmark. | Not an EAR alternative. |

CJL source: [published-paper copy](https://www.lsvasset.com/pdf/research-papers/Momentum-Strategies-Papers.pdf), equation (2) and the earnings-momentum tests.  HXZ source: [Review of Financial Studies article](https://academic.oup.com/rfs/article/33/5/2019/5236964), Table 3 and Internet Appendix A.1.2.

## Modern/post-publication evidence ledger

### Supportive, but not sufficient

HXZ extend their U.S. anomaly panel through December 2016.  Their CJL-style
`Abr` signal uses Compustat RDQ and market-adjusted `[-2,+1]` returns, monthly
decile sorts, and one-, six-, or twelve-month overlapping holdings.  With NYSE
breakpoints and value weights, the high-minus-low results are 0.70% per month
(`t=5.45`) for `Abr1`, 0.33% (`t=3.41`) for `Abr6`, and 0.23% (`t=2.99`) for
`Abr12`.  This is useful evidence that a simpler announcement-return variable
is not merely a convenience proxy.  It does not isolate 2010–2016, does not use
BKSV's benchmark/window, and does not report a concentrated positive long leg.

Chen and Zimmermann's open-source replication program also includes
`AnnouncementReturn`, but its maintainers later issued an explicit lookahead-
bias patch.  Consequently, downstream web dashboards showing results through
2024 are not treated as decisive evidence without a version-pinned,
post-fix audit.  The campaign did not import those returns or run an economic
test.  Sources: [Open Source Asset Pricing repository](https://github.com/OpenSourceAP/CrossSection) and [release noting the AnnouncementReturn patch](https://github.com/OpenSourceAP/CrossSection/releases).

### Modern challenges

Martineau (2022) studies SUE-conditioned PEAD, not EAR.  In the U.S. sample,
analyst surprises cease to predict 60-day post-announcement returns for
non-microcaps after 2005; recent residual drift is concentrated in microcaps
and short horizons.  This cannot directly reject exact EAR because EAR embeds
the entire disclosure bundle rather than earnings forecast error.  It does
raise the evidentiary bar for any liquid-stock continuation claim.  Source:
[Critical Finance Review/SSRN record](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3111607).

Christensen, Timmermann and Veliyev (2025) use tick-level after-hours data for
the 50 most liquid S&P 100 constituents during 2008–2020.  Their SUE-predicted
immediate-return strategy is not a test of sorting on an already observed
three-day EAR.  Still, it is directly relevant to the proposed large/liquid
implementation: after 2016, a strategy paying the bid–ask spread is flat and
latency variants lose money, consistent with much faster price discovery.
Source: [Journal of Financial Economics publication record](https://pure.au.dk/portal/en/publications/warp-speed-price-moves-jumps-after-earnings-announcements/) and [open manuscript record](https://arxiv.org/abs/2601.08962).

The broader PEAD literature includes modern survival results based on SUE,
text, attention, or other accounting variables.  Those do not validate exact
EAR and therefore are not promoted into the positive evidence column.

## Decision implication

The market-adjusted CJL/HXZ definition passes the narrow “externally validated
alternative exists” test.  The modern gate still fails: the strongest broad
replication ends in 2016 and pools the entire history, while modern direct
large/liquid evidence points toward faster incorporation and does not test the
same observed-EAR continuation.  This record supports a research rejection on
modern-evidence grounds; it is not a data-blocked inference and is not an
empirical null estimated from ParayaBuck data.
