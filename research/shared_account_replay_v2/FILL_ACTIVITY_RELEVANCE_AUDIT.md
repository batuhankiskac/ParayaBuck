# FILL activity relevance audit

Decision: `FILL_PAGINATION_IRRELEVANT_TO_SHARED_REPLAY_V2`.

`SharedAccountReplay` constructs `SharedAccount(starting_nav, starting_nav)`, requires no positions or pending orders at entry, and uses only historical bar and corporate-action CSV inputs. Fills arise from `PortfolioBacktestEngine._fill_side` inside the synthetic event loop. The runner imports no Alpaca client, production ledger, broker activities, account positions, or credentials. No broker account reconstruction is performed. If a future change imports any broker activity or state, this decision expires and replay must stop until activity completeness is proven.
