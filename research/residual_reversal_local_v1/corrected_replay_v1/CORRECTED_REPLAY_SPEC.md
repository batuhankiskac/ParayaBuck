# Corrected replay mechanics: `equity_short_term_residual_reversal_v1`

This document is the pre-outcome mechanics freeze for the one corrected replay
of the already frozen residual-reversal hypothesis. It corrects only the five
implementation defects recorded in the parent forensic audit. It does not
change the candidate, data, chronology, costs, risk limits, allocation, or
acceptance gates.

## Scope and non-goals

- One replay only, on the existing local v5 price panel, v5 corporate-action
  table, and v5 entity-lifetime table.
- Research branch only. No production registration, PAPER/LIVE change, broker
  order, or main-branch change.
- No parameter search, retuning, alternative allocation, alternative cost
  model, or corrected-result rerun after outcomes are observed.
- The original campaign directory and its result files remain immutable.

## Frozen economics

The candidate is exactly `equity_short_term_residual_reversal_v1`:

- US equities, daily bars, SPY benchmark, and the current V3 residualization;
- 273 aligned-return beta window;
- exactly 21 completed residual returns and zero skip;
- most-negative residual return ranks strongest, with no positive-score gate;
- long-only, weekly, `top_n=6`;
- V3 idiosyncratic-volatility rejection quantile `0.70`;
- inverse residual-volatility weighting, residual-volatility window `63`;
- per-position cap `0.10`.

The standalone reversal sleeve and V3 control each retain the configured
maximum equity allocation `0.55`. The treatment is one account ledger with
frozen intended sleeves:

- V3: `0.4125` (`0.75 * 0.55`);
- reversal: `0.1375` (`0.25 * 0.55`);
- total intended equity sleeve: `0.55`; remainder is cash.

The existing six-position account limit, `0.10` per-symbol cap, gross limit,
cash buffer, daily-loss behavior, drawdown shadow behavior, order band,
minimum order notional, and 1x/2x/3x cost multipliers are unchanged.

## 1. Entity lifecycle and terminal accounting

The replay loads `equity_entity_lifetimes_full_v5.csv` and builds a
point-in-time terminal map for inactive canonical symbols. Each entry carries
the lifetime metadata and the exact `last_observed_bar` timestamp. Active
symbols are not assigned a terminal entry; the engine's existing default is
active when no terminal entry exists.

The corrected replay uses a research-only subclass of the existing historical
engine. It assigns the terminal map before the event loop. A managed inactive
symbol is observable on its final legitimate bar (`last_bar >= timestamp`) and
is removed from the completeness-required observation set after that bar. At
the final legitimate bar, after the normal strategy decision/target cycle,
any held terminal position is closed using the real bar and the existing
`_close_position` accounting path. Pending orders for that symbol are removed,
the event is labeled `TERMINAL_BAR`, and no synthetic price is created. After
that event the position cannot remain stale inventory. If a terminal bar is
missing from the streamed panel, the replay fails closed rather than inventing
one.

## 2. Decision-coverage contract

The engine records every weekly schedule attempt separately from fills. The
schedule is the first available completed bar of each ISO week. An attempt
becomes an eligible opportunity once the strategy's declared minimum history
is available. An eligible attempt is recorded as either a genuine engine
decision or a skip with a structural reason, including incomplete observation
batch. A decision with zero fills is still a decision.

The frozen coverage gates are:

- validation contains at least one genuine scheduled decision for every run;
- eligible validation decision coverage is at least `90%`;
- the longest unexplained gap is at most `8` scheduled weeks;
- the last eligible-period decision is no more than `8` scheduled weeks before
  the final eligible schedule opportunity;
- decisions and fills are persisted as separate records.

The verifier recomputes the schedule from the persisted curve/attempt ledger,
checks skipped-attempt reasons, checks validation activity and recency, and
fails closed before any economic gate is interpreted.

## 3. Strategy-specific observation sets

The scanner's broad point-in-time eligible pool is shared infrastructure. Each
strategy then owns its alpha ranking:

- V3: scanner eligible pool -> V3 alpha rank -> V3 observation set;
- reversal: the same scanner eligible pool ->
  `EquityShortTermResidualReversalV1.rank_candidates` -> reversal observation
  set;
- treatment: the same scanner eligible pool -> separate V3 and reversal
  rankings -> the deterministic combined treatment observation set below.

The reversal run never consumes the V3 observation set. Every persisted
snapshot records its observation strategy name/version and the verifier checks
that identity.

The treatment ranker is `treatment_combined_observation_v1`. It keeps required
reference/pinned symbols first, then produces a deterministic de-duplicated
round-robin sequence `V3 rank 1, reversal rank 1, V3 rank 2, reversal rank 2,
...`, with each source ranked by its own alpha. It truncates only at the
existing maximum stream capacity (`30`). This gives both components meaningful
access without using economic results or the larger V3 sleeve to choose stream
membership. The treatment verifier requires at least six reversal-ranked
symbols in the stream whenever six eligible reversal candidates exist.

## 4. Sleeve-aware treatment position policy

Each component first uses its own strategy-produced target. If its positive
raw target exposure exceeds the reference `0.55` sleeve, it is capped at
`0.55` without upward renormalization; then it is multiplied by `0.75` or
`0.25`. Thin/risk-off component targets remain thin.

For each symbol, scaled V3 and reversal contributions are preserved separately.
Their sum is the account target. If a summed symbol exceeds the unchanged
`0.10` per-symbol cap, both source contributions are proportionally reduced to
the cap and the reduction is recorded. The merged account target is then
selected under the unchanged six-position limit using this predeclared
sleeve-aware policy:

1. With both sleeves nonempty, apportion six slots as V3 `4` and reversal `2`.
   This is largest-remainder apportionment of `6 * (0.75, 0.25)` with the
   exact-half tie assigned to the smaller reversal sleeve. If one sleeve has
   no candidates, its quota is zero; its budget remains cash.
2. Select each sleeve's highest alpha ranks up to its quota. An overlapping
   symbol occupies one account slot and satisfies both source sleeves.
3. If overlap leaves spare account slots, fill them by alternating the next
   unselected reversal and V3 alpha ranks, starting with reversal; if one
   source is exhausted, continue with the other. This uses spare slots only
   for that source's already-scaled target contributions and never transfers
   capital between sleeves.
4. Every unselected symbol is retained in attribution with reason
   `position_capacity_sleeve_aware`. The final target has at most six symbols,
   so the unchanged production overlay still processes it and remains the
   final authority for all risk limits and execution mechanics.

The output target retains a composite rank for compatibility, while metadata
retains every source rank and contribution. It records raw source names,
overlap, merged pre-cap names, selected post-cap names, intended and realized
source exposures, unused allocation, and every dropped-symbol reason. The
overlay result is one shared target and one shared ledger; standalone NAVs are
never blended.

## 5. Required synthetic policy tests

Before the replay, deterministic no-return tests must prove:

- disjoint targets retain nonzero reversal representation when feasible;
- an overlap is one slot, is capped at `0.10`, and keeps auditable source
  contributions;
- an infeasible disjoint target leaves the unrepresented sleeve allocation in
  cash and reports capacity shortfall;
- no reversal candidates leave the full reversal budget in cash rather than
  reallocating it to V3.

The lifecycle test must reproduce an ESRX/DVMT/KSU-style terminal wedge and
prove that the terminal map closes the position at its real final bar and that
later completeness attempts are not blocked by it.

## 6. Independent verifier invariants

The corrected verifier is separate from the runner and checks, before economic
interpretation:

- main SHA, frozen protocol SHA, corrected-spec SHA, price SHA, lifetime SHA,
  and corporate-action SHA;
- candidate identity and exact 21/zero-skip/negative-first/no-positive-gate
  semantics, plus unchanged V3 source identity;
- V3, reversal, and treatment observation strategy identity and treatment
  combined-policy identity;
- terminal-map loading, terminal fixture behavior, and no inactive pinned
  symbol blocking future decisions;
- scheduled opportunities, decisions, coverage, skipped reasons, validation
  decisions, maximum gaps, and last-decision recency;
- source-sleeve attribution, no cross-sleeve reallocation, six-position and
  per-symbol caps, gross/cash limits, and cash-held infeasible remainder;
- next-bar fills, cost arithmetic, NAV reconciliation, and nonnegative cash.

Only a verifier status of `CORRECTED_REPLAY_VALID` permits the frozen economic
gates to be evaluated. Any failed replay-validity invariant produces
`CORRECTED_REPLAY_INVALID` and the final decision
`RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT`.

## Freeze record

No corrected economic result may be generated until this exact file exists and
its SHA-256 is recorded in `replay_manifest.json` and the corrected verifier.
