# Causality and signal availability

No EAR value, entry date or candidate return was calculated.

## Two clocks must remain separate

An event needs both a proven **public instant** and a deterministic **event session**. An SEC acceptance timestamp is useful evidence for a filing, but SEC guidance says filings are often available on sec.gov one to three minutes later and provides no exact first-availability timestamp. A press release or wire publication before the 8-K makes that earlier instant controlling. A later SEC acceptance may conservatively delay trading, but it cannot redefine the information arrival or recreate Compustat RDQ.

For a future causal convention, retain release, wire, SEC acceptance, SEC dissemination bound, observation and ingestion times separately. Date-only issuer pages, report periods and 8-K signature dates are not intraday timestamps. Late EDGAR submissions may receive next-business-day dissemination treatment. Unknown or conflicting provenance fails closed.

The event session is the first regular trading session capable of reflecting the release: same date for a verified pre-open release, next session for an after-close release, and the active session for an intraday release. Weekend and holiday releases map to the next session. This definition is conceptually causal but is not claimed to reproduce Compustat RDQ or the primary paper until external alignment evidence is established.

## Full-window availability

If the accepted EAR definition uses sessions `t−1`, `t`, and `t+1`, the complete statistic becomes known only after the `t+1` bar closes and its benchmark return is available. The earliest compatible daily-engine order is therefore created after that close and filled at the `t+2` open. Entering after `t` or at the `t+1` open would use a partial statistic and violate the definition.

The repository's completed-bar/next-open mechanics can represent the `t+2` sequence. They do not prove that the paper forms portfolios at the same instant, that sufficient continuation remains after this executable open, or that benchmark publication is timely enough. Those are external evidence gates. A daily signal based on SEC acceptance also needs a session calendar, halt handling, exact timestamp normalization and an ingestion-readiness rule.

## Duplicates, amendments and concurrent information

The same earnings release attached to multiple filings is one information event. Hashes and accession/exhibit lineage should preserve copies without double counting. Amendments become separate document versions at their own public times; they do not rewrite the earlier information set. A new quarter is a new episode, but no refresh/replacement rule is frozen in this campaign.

EAR intentionally captures the whole announcement package, including actual results, guidance and qualitative news. Decomposing that package would change the hypothesis. Extraordinary M&A, bankruptcy, financing, split or reorganization events must be flagged for diagnostics; exclusion is allowed only when the chosen literature specification provides a pre-outcome rule. Security mapping, benchmark membership and event timing are independent gates, so success in one cannot repair another.

## Entry verdict

`EAR_NEXT_OPEN_IMPLEMENTATION_CAUSALLY_COMPATIBLE`

The repository can wait for the full three-session statistic and place a next-open order. This verdict means the information sequence is causal and overlaps the paper's post-window interval beginning on `t+2`; it does not claim that Brandt et al. tested a `t+2` opening fill or that modern net drift remains after that price. Those distinct evidence gates fail elsewhere in this campaign.
