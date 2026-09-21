# Design red team

The objections below are resolved before any local economic output is
inspected.

## 1. Is Frog in the Pan merely momentum repackaged?

Partly overlapping, yes. The candidate uses a positive momentum gate. Da,
Gurun, and Warachka report residual-ID evidence after conventional
characteristic controls, while later work finds both common and distinct
components. That supports a bounded experiment, not a claim of orthogonality.
The displacement gates are deliberately stronger than “positive return”.

## 2. Does the effect survive momentum controls externally?

The primary paper and later continuity/TSMOM evidence condition or residualize
against conventional momentum-related variables. Huang et al. extend the
mechanism to linked-firm lead-lag relations. The evidence is encouraging but
not a guarantee for a six-name long-only account, so the local protocol uses a
fresh V3 control and a validation gate.

## 3. Does long-only implementation retain enough of the published effect?

The literature mainly uses cross-sectional or time-series long/short portfolios
with broader breadth and different holding periods. A six-name long-only
implementation is therefore a translation. This is acceptable for one test
only because the translation is explicit, simple, and compared against the
same account architecture; it is not labeled as a replication.

## 4. Is ID dependent on microcaps or illiquidity?

Momentum-cost studies warn that small, illiquid names can make paper profits
untradeable, and the historical product taxonomy is broad. The current PIT
scanner’s $20m median-dollar-volume floor, 50bp spread ceiling, regular-session
completeness, and future 1x/2x/3x cost gates are retained. If those gates fail,
the candidate is rejected rather than rescued by a new liquidity filter.

## 5. Does V3 already capture continuous momentum?

V3 captures residual momentum magnitude, multi-horizon agreement, IVOL, and
inverse-vol sizing. It may capture some continuity indirectly. Embedding ID
inside V3 would make that question unanswerable; a separate alternative and a
slot-displacement test provide the cleaner attribution.

## 6. Would adding ID to V3 simply overfit V3?

Yes, that is a material risk. The design therefore selects architecture A,
not an ID filter or blend. The candidate is frozen once, with no coefficient,
percentile, cadence, or threshold search.

## 7. Does raw-return ID conflict with residual-momentum selection?

The candidate does not use residual-momentum selection. Raw daily price returns
are faithful to the published ID mechanism. The paper’s residual-ID analysis
is a cross-sectional residual of the ID characteristic; it is not V3-style
market-return residualization. Mixing those would create a new hypothesis and
is out of scope.

## 8. Does monthly/next-open execution match published evidence?

The source evidence is monthly formation/holding oriented, while this engine
decides after a completed bar and fills at the next tradable open proxy. That
one-session operational delay is documented and is not hidden as same-close
execution. Monthly cadence is chosen to match the slow information-diffusion
hypothesis and to avoid unnecessary turnover.

## 9. Can dividends or splits distort sign counts?

Signals use split-adjusted closes, and split/stock-dividend rows are not
reapplied. Cash dividends are account-P&L events and are excluded from the
price-path signal, matching the published price-return construction. The
canonical loader applies cash actions once, preventing duplicate account
effects.

## 10. Does the broad `US_EQUITY` universe undermine interpretation?

It limits generalization. Historical bars do not provide a complete PIT
common-stock taxonomy, so an ETP can technically enter the broad pool. The
limitation is acceptable for a fair A/B test because both arms share the same
pool and reserved roles. Results must be described as broad-universe
implementation evidence, not common-stock-only evidence.

## 11. Is the chosen construction arbitrary?

The construction is the canonical two-stage logic: positive 12-1 winner
qualification followed by the published low-ID continuity direction. A zero
gate, 252-return interval, 21-return skip, six slots, equal weight, and current
allocation/cap are fixed from literature fidelity and architecture. No local
outcome was used to choose them.

## 12. Is the effect strong enough post-publication to deserve one test?

McLean and Pontiff provide a general warning that predictors weaken after
publication, especially where liquidity and idiosyncratic risk are poor. That
warning argues for one sealed, cost-stressed, validation-controlled experiment,
not for rejecting an otherwise defensible hypothesis without testing. It does
not justify production promotion.

## Red-team disposition

The objections reduce the claim to a narrow implementation hypothesis, but do
not make it non-defensible or untestable. Proceed to exactly one frozen
economic experiment only under `FUTURE_TEST_PROTOCOL.md`; no alternative
candidate is proposed.
