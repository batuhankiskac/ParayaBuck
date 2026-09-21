# Causality audit

## Paper-faithful timing

The faithful default is the paper's six-month accounting lag and annual June
formation. For each June decision year, use the prior calendar year's eligible
fiscal-year observations, provided the exact accession was accepted before the
cutoff. Do not accelerate merely because SEC acceptance was earlier.

Conceptual execution contract:

`completed final NYSE session of June -> frozen score/rank -> first tradable July open`

The existing exchange-calendar primitive represents those boundaries exactly;
the generic scheduler does not natively implement annual June cadence.

## PIT invariants

- raw header acceptance precedes decision;
- same-accession comparative facts form working-capital deltas;
- later 10-Q/10-K/A/restatement never changes an earlier decision;
- current Company Facts “latest” state and Frames are not historical inputs;
- non-December and 52/53-week years retain actual periods;
- transition reports fail closed;
- security mapping and filing-time SIC are as-of the accession;
- no midyear rerank; lifecycle exits leave cash until the next annual decision.

No local economic series was produced, so no timing choice was selected from
outcomes.
