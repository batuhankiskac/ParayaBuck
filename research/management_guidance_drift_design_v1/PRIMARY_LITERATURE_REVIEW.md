# Primary literature: final publication verification

Reviewed 2026-09-22. Ng, Tuna and Verdi (2013), *Management forecast credibility and underreaction to news*, Review of Accounting Studies 18:956–986. [Publisher PDF](https://link.springer.com/content/pdf/10.1007/s11142-012-9217-4.pdf), [publication record](https://link.springer.com/article/10.1007/s11142-012-9217-4).

The entire final, typeset 31-page article was read, including tables and notes; Table 1 was also rendered and inspected. Temporary copy: `/tmp/ng_tuna_verdi_2013_publisher.pdf`; SHA256 `aacd7bd0991b5243dbeb1df794908eef8ff094b0c963236be3addbb52414c7de`. The copyrighted PDF is not a repository artifact. No author code or candidate economics was run.

## Compact evidence ledger

| Final-paper location | Verified observation |
|---|---|
| pp.961–962 | First Call CIG; 23,822 forecasts, 1996–2008; excludes forecasts within three days of earnings announcements. |
| Table 1, p.966 | Three-month Q1–Q5: −0.95, 0.58, −0.16, 0.35, 3.65***%; hedge 4.60***%. |
| Table 1, p.966 | Twelve-month Q1/Q5/hedge: −3.26*/4.03***/7.29***%. |
| Figure 2, p.968 | Equal-weighted. No final-paper value-weighted result found. |
| Table 4, pp.975–976 | Controlled surprise slope 3.13*** (t=4.48); firm/quarter clustered inference. |
| Table 5, p.977 | Fama–MacBeth inference for PEAD×surprise sorts. |
| Table 7, pp.981–982 | Quarterly/annual surprise slopes 3.58***/2.69***; annual Good News 0.40 (t=0.56). |

`***`/`*`: 1%/10% two-sided significance. No Table 1 t-values reported. Its bad-news three-month result is unstarred. Middle ranks are nonmonotonic. Earnings-date/non-earnings-date panels were not found.

Clock inconsistency: pp.963,966–968 use third day; pp.973,976 use second day. The final article does not establish first-next-open execution.

## Maintainer interpretation

A hedge estimate cannot be credited to a long-only portfolio. Likewise, a regression slope spanning the surprise distribution is not the return of buying its highest-ranked issuer. A pooled historical sample is not evidence about the present regime. The publication is a reason to investigate the disclosure hypothesis, but does not settle concentrated deployment, slippage or brokerage economics.

The visibly nonmonotonic middle ranks caution against assuming every increase in surprise improves expected return. Even a significant extreme-quintile result cannot establish an optimal top-one cutoff. A period label in a paper is not an implementation-ready trading calendar.

Do not repair inconsistencies by combining a draft's methods with final-table results. Missing source detail must remain missing. See [surprise specification](FORECAST_SURPRISE_SPEC.md) for the corresponding reconstruction boundary. No candidate, holding rule or future economic test is frozen here.
