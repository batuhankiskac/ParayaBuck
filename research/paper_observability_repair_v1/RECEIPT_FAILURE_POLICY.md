# Receipt failure policy

A PAPER receipt failure logs a warning and emits a `paper_incident`; it does not change the order path or weaken any safety check. A final BUY admission observation failure likewise emits an incident and does not alter submission. Such a cycle cannot pass S7/S10, making forward-observation evidence invalid. The operator must investigate, repair the audit trail and restart a fresh frozen observation window. Runtime safety and observation validity are separate decisions.
