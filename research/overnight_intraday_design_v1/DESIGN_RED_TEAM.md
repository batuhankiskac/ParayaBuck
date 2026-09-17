# Independent design red-team

The red-team reviewed the frozen specification after the capability and
literature audits. It did not run a backtest or calculate any local economic
outcome.

| Challenge | Finding | Resolution in the frozen design |
| --- | --- | --- |
| Lookahead | The raw overnight move is first observable at the current session open, but the current daily strategy path decides only after the completed bar. | The score is read only after `Close(d)` and the order is created for `Open(n(d))`. Same-session open execution is explicitly prohibited. |
| Unavailable execution information | A published same-day overnight-to-intraday trade would need an open-time decision and usually a same-day close exit. | The candidate is a one-session-lagged hypothesis with the existing next-open fill and daily target replacement. Its holding interval is reported honestly as engine open-to-next-open exposure, not as a pure open-to-close fill. |
| Daily-open quality | SIP daily OHLC is built from qualifying regular-sale trades. It is not evidence that the value equals the NYSE/Nasdaq official opening-auction clearing price. | The candidate's fill is labeled `provider_daily_open_proxy`; no MOO/LOO or auction-specific claim is made. Opening slippage and 2x/3x cost gates are mandatory. |
| Microcap dependence | Some reversal evidence is stronger in illiquid names, while other evidence covers the largest 3,000 stocks and modern large/liquid stocks. | The current `$5`, `$20m` median-dollar-volume, exchange, completeness, and spread gates are retained. The candidate cannot weaken them, and coverage/turnover/cost gates can reject an effect that only survives in excluded tails. |
| Cost erosion | Overnight-related strategies can be highly sensitive to opening spreads and low-liquidity execution; the direct overnight leg may be especially expensive. | B is long-only, uses the existing participation and next-open model, and must be positive in validation at 2x cost with a defined 3x stress bound. |
| Split artifacts | The corporate-action inventory contains 1,477 split rows, so a raw unadjusted price series could create false overnight moves. | The v5 manifest and provider contract identify split-adjusted OHLC; the structural split QC found no material split-event pair near the unadjusted factor. The product loader rejects reapplication of split actions. The future run must fail closed on basis/normalization conflicts. |
| Dividend treatment | A price-component overnight signal can move on an ex-dividend date, while explicit dividend cash also exists for later accounting. | The signal is frozen as price-based split-adjusted OHLC with no dividend addition. The later P&L run must apply explicit dividend cash exactly once; it must not turn this into a second total-return hypothesis. |
| Previous residual-reversal campaign | The user-defined closed short-term residual-reversal campaign must not be reopened or used as an outcome benchmark. | No deleted research or campaign result was inspected. The candidate is frozen from its definition: one close-to-open component, simple SPY excess, no rolling close-to-close residual, no old parameters, no old results. Any conceptual overlap remains a documented research risk, not a reason to mine history. |
| Distinction from current V3 | V3 is 252/21 residual momentum with a 273-bar beta window, 63-bar residual volatility, positive 12-1 gate, 6/9/12 consensus, IVOL rejection, inverse-vol sizing, and weekly cadence. | B uses one overnight session, simple SPY excess, contrarian direction, six long names, fixed equal weights, and daily cadence. It shares only the account-level scanner/risk/execution boundaries. |
| Six-slot fit | A separate candidate sleeve could silently consume extra capital or positions. | B is an alternative configuration, not V3+B. It selects at most six names, has no short leg, uses no more than 0.55 equity allocation, and remains under the global six-position overlay. |
| Horizon cherry-picking | A literature search could select the best reported lookback after seeing results. | Horizon `1` is the irreducible definition of one close-to-open interval and is fixed before the economic test. No horizon sweep or skip choice is permitted. |
| Residualization overbuild | Reusing the V3 273-day beta machinery would add estimation error and blur the mechanism. | The candidate uses exactly `ON_i,d - ON_SPY,d`; no beta, sector, Fama–French, machine-learning, or multi-factor residual model is allowed. |
| Same hypothesis simpler? | A raw overnight sort is simpler but would leave broad market overnight pressure in the score; a full factor model is more complex than the evidence requires. | Simple SPY excess is the smallest explicit market adjustment that preserves the opening-pressure interpretation and uses an already pinned, non-traded reference. |
| Data aliasing | The v5 file has `1Day` and `1d` spellings, while the current loader filters exact timeframe strings. | A deterministic read-only alias normalization is a precondition and G1 gate. No silent partial dataset run is allowed. |
| Missing sessions | Symbol rows are pairable by prior observation, but not every prior observation is the immediately prior exchange session. | The signal uses the NYSE/SPY common-session predecessor and never subtracts one calendar day or blindly uses the previous CSV row. |

## Red-team disposition

The material concerns are resolved by narrowing the claim rather than by adding
data or tuning parameters. The final candidate is not a claim of same-day
auction capture; it is a causal, one-session-lagged testable hypothesis under
the existing daily engine. If the later implementation cannot preserve that
lag, the provider-open label, the normalized input, or the common-session/PIT
joins, it must stop before producing economic results.

The design remains eligible for a separate frozen economic test because the
mechanism is supported by multiple U.S. studies, the local data contains the
required OHLC/reference/lifecycle structure, and the current engine has an
honest next-open execution path. This is a design-readiness statement only; it
is not evidence that the candidate will pass any pre-frozen economic gate.

## No local economic peek

During this audit, no strategy P&L, candidate return, CAGR, Sharpe, Sortino,
Calmar, drawdown, profit factor, NAV, trade profitability, hit rate,
quintile/decile forward return, signal/subsequent-return relation, correlation
with subsequent returns, or parameter-performance surface was calculated or
inspected. The only numerical price-related check was the explicitly requested
structural split-basis QC around corporate-action dates; it was not used as a
signal, forecast, or economic selection criterion.
