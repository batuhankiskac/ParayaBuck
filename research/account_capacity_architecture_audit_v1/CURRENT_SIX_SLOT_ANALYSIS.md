# Current six-slot consistency

This is a static upper-bound analysis of the configured PAPER architecture, not an observation of holdings or strategy outcomes. Source values are `config/settings.yaml:143-184`. V3 selects up to eight ranked equities, applies inverse-volatility sizing, caps each raw target at 0.10 without redistributing the residual (`src/parayabuck/portfolio/equity_residual_momentum.py:583-622`, `src/parayabuck/portfolio/momentum.py:49-69`), and passes targets to the account risk overlay (`src/parayabuck/execution/portfolio.py:216-375`). Crypto targets up to two equally weighted names within its target, then the overlay applies the 0.05 crypto sleeve.

| Crypto names already claiming slots | Equity slots left | Equity upper bound | Crypto sleeve upper bound | Gross upper bound | Count-only unused equity sleeve |
|---:|---:|---:|---:|---:|---:|
| 0 | 6 | 0.55 | 0 | 0.55 | 0 |
| 1 | 5 | 0.50 | 0.05 | 0.55 | 0.05 |
| 2 | 4 | 0.40 | 0.05 | 0.45 | 0.15 |

The six cap therefore cannot represent the configured 0.55 equity sleeve when two distinct crypto symbols claim slots. With one crypto name, it cannot represent the final 0.05 of equity capacity. The table assumes sufficient qualifying equities and adequate cash; actual inverse-vol weights, other risk gates, reservations, immutable external inventory, and order state can only lower realized targets. It does not assert that any sleeve should be fully deployed.

The config comment explains the 0.55 + 0.05 = 0.60 gross/cash invariant, but does not explain the simultaneous six-slot geometry (`config/settings.yaml:145-148`). The code permits a lower target and leaves unallocated weight as cash; the risk overlay trims names before exposure scaling (`src/parayabuck/execution/portfolio.py:315-375`). Thus unused sleeve is mechanically accepted, but no source found so far establishes that 0.15 count-only underdeployment with two crypto positions was an intentional account allocation policy.

The 0.40 cash setting is an enforced **minimum** in the overlay, not a fixed cash target: `maximum_gross <= 1 - minimum_cash` is checked at configuration construction (`src/parayabuck/config.py:700-716`), and the overlay uses both limits (`src/parayabuck/execution/portfolio.py:343-375`). Count-limited target cash may exceed 0.40.

Crypto claims are considered in the account-wide position limit. V3 itself ranks eight eligible names without allocating a crypto slot budget: its `top_n` and sizing are strategy-local; the overlay retains ranked names within the remaining account slots. The repository does not establish that eight was chosen *because* the account cap would later truncate it. Position count and exposure caps address different dimensions, but the present values conflict as a full-sleeve expression contract. Details of live snapshot, open-order and reservation semantics are in `POSITION_CAP_ENFORCEMENT_AUDIT.md`.
