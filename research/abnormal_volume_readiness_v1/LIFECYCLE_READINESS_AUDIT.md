# Lifecycle readiness audit

Canonical lifetime SHA-256: `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`.

The strict loader reports 9,010 identities: 2,151 explicit terminals, 6,858 horizon-censored active identities, one rename chain, and one unresolved inactive identity (`EOSEW`, entity `dcc4e738-c754-48fe-b52d-4567363611fd`). Terminal mapping SHA-256 is `2dc07522b9b4e9acabfd8b57e5fd60702b4169cd831e3becbb733a369b1bce25`.

Required readiness behavior:

- resolve one stable entity identity and reject ambiguous/unresolved identities fail-closed;
- require validity on the formation session without using future `current_status` to pre-exclude an earlier valid observation;
- recheck the explicit lifecycle at fill;
- invalidate a pending target when its last tradable session precedes the scheduled fill, with no next-ranked replacement;
- do not infer intraday untradability when the terminal session equals the fill session;
- persist a risk-reducing terminal exit on the real terminal bar during the holding month, without price or P&L fields;
- require 50 valid dollar-volume observations and the additional prior close, so a new listing needs 51 valid closes;
- preserve FB→META as one entity history.

The existing ordinary CLI does not pass v5 terminal lifecycles into the backtest engine. A future candidate runner must load them explicitly.

