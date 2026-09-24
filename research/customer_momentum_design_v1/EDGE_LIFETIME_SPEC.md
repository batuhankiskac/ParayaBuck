# Edge lifetime is an unresolved design gate

Cohen–Frazzini's “last available supplier–customer link” documents use of the last annual Compustat observation but does not precisely say whether a link expires after one year, at the next annual observation, or after another interval. Table I gives observed link duration, not a carry-forward algorithm. We will not attribute a particular clock to the paper.

A conservative proposed PIT rule would activate a verified annual edge only after the **later of** its `accepted_at` and six months after supplier fiscal-year-end, carry it only to the next available annual filing or 12 months, whichever comes first, and stop at an explicit public termination. A later omission would only act from its own filing time. **This is a proposal, not a frozen or tested base rule**; the difference from Compustat's last-available semantics is a graph-fidelity issue. No local lifetime tuning or return testing occurred.
