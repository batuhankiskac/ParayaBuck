# Cross-asset strategy queue review — 2026-09-06

This review covers exact cards X01–X06 from the pasted queue. It records
mechanism evidence and feasibility gates only. No short, futures, perpetual,
or economic execution was performed. The fixed V3 and crypto incumbent
contracts remain unchanged.

## X01 — unlevered bond ETF yield-curve carry / roll-down

The primary carry paper defines carry as an ex-ante, model-free characteristic
and finds cross-sectional and time-series predictability across bonds,
commodities, equities, and other assets. It also documents exposure to
recession, liquidity, and volatility risk; this is a risk premium hypothesis,
not a guaranteed yield. See [Koijen, Moskowitz, Pedersen and Vrugt,
“Carry”](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2632697).

The proposed unlevered ETF version is not the paper's direct futures or bond
implementation. It would require a frozen ETF universe, historical curve
levels or ETF-implied duration/yields, a fixed carry-plus-roll-down formula,
distribution treatment, and a rule for selecting or allocating among maturities.
The local repository has daily equity ETF OHLCV and corporate actions, but no
historical bond yield curves, duration history, or bond-ETF carry inputs.

Minimal future spec: fixed long-only bond ETF set; one predeclared carry and
roll-down estimator; monthly calendar rebalance; next NYSE-session open;
split/dividend handling; 1x/2x/3x cost stress; no leverage, shorting, or
post-result maturity search. Current verdict: `DATA_BLOCKED`; theoretical
carry is not an ETF implementation result. Complementarity is plausible as a
rate/term-structure mechanism distinct from V3 residual momentum, but untested.

## X02 — unlevered commodity ETF futures-curve carry

The carry paper applies the same broad concept to commodities, but a commodity
carry signal requires the relative prices of multiple dated futures contracts.
CME documents that contracts expire, may be offset or rolled, and that the
roll choice changes realized return. SEC-filed ETF prospectuses likewise state
that contango can create negative roll yield and backwardation positive roll
yield; an ETF's managed roll is therefore an implementation layer. See [CME's
expiration and roll explanation](https://www.cmegroup.com/education/courses/introduction-to-futures/understanding-futures-expiration-contract-roll)
and the [SEC-filed iShares Bloomberg Roll Select Commodity Strategy ETF
prospectus](https://www.sec.gov/Archives/edgar/data/1524513/000119312526064208/R55.htm).

No local commodity ETF OHLCV, futures contract-month history, contract
specifications, roll calendar, collateral yield, or fund roll receipts were
found. A commodity ETF price series alone cannot reconstruct the underlying
curve carry or distinguish spot, roll, collateral, and fee returns.

Minimal future spec: frozen liquid ETF universe and provider roll methodology;
dated futures settlement/volume/open interest and contract multipliers;
predeclared curve carry measure; monthly roll/rebalance; long-only ETF orders
at next session open; explicit distributions and expense ratio; cost stress.
Current verdict: `DATA_BLOCKED`. Complementarity could come from commodity
curve states, but no local executable evidence exists.

## X03 — country ETF point-in-time value

Asness, Moskowitz and Pedersen document value and momentum premia across
countries and asset classes. That evidence supports investigating a value
mechanism; it does not supply point-in-time country ETF valuation histories or
validate an ETF-level long-only implementation. See [“Value and Momentum
Everywhere,” Journal of Finance](https://doi.org/10.1111/jofi.12021).

The repository has SPY and QQQ daily histories and a separate normalized IWM
daily sidecar, but no country-ETF valuation vintages, historical P/B or P/E,
FX series, country ETF accounting, or point-in-time country membership. iShares
pages expose current holdings and valuation snapshots, illustrating the
provider data needed but not historical PIT vintages; see the [iShares ACWX
product page](https://www.ishares.com/us/products/239594/ishares-msci-acwi-ex-us-etf?periodCd=m).

Minimal future spec: frozen country ETF list; one valuation ratio and exact
available-at timestamp; long-only lowest-price/highest-yield ranking; monthly
rebalance; FX and ETF distributions; next NYSE open; survivorship and successor
identity gates; no momentum filter. Current verdict: `DATA_BLOCKED`. Value may
complement V3's residual momentum, but this is an H01-related mechanism and
requires a genuine PIT data set.

## X04 — equity pairs / statistical arbitrage

Gatev, Goetzmann and Rouwenhorst match stocks by normalized price distance and
trade a self-financing long/short pair. Their paper is direct evidence for the
relative-value mechanism, not for a long-only substitute. See the [NBER Working
Paper 7032](https://www.nber.org/papers/w7032).

The card explicitly needs a long cheap leg and short expensive leg, borrow,
recall handling, two-leg order state, spread exposure, and financing. The
current portfolio target contract and engine are long-only; they do not model
negative quantities, borrow fees/availability, atomic two-leg fills, or pair
margin. Alpaca's own documentation confirms that short selling is a margin
feature with account and borrow requirements ([Margin and Short
Selling](https://docs.alpaca.markets/us/v1.1/docs/margin-and-short-selling)).

Minimal future spec: frozen pair formation universe and distance window;
point-in-time pair membership; entry/exit z-score and maximum age frozen in a
manifest; borrow/recall/financing series; synchronized two-leg execution;
gross/net and pair-level risk; next tradable opens. A long-only one-leg version
would be a different strategy and must receive a new ID. Current verdict:
`OUT_OF_SCOPE` and `CAPABILITY_BLOCKED`.

## X05 — crypto futures cash-and-carry

BIS Working Paper 1087 defines crypto carry as going long spot while selling
short a futures contract, and documents large, time-varying basis plus margin
and liquidation risk. The paper's premium is not a risk-free return. See [BIS
Crypto carry](https://www.bis.org/publications/working-paper-1087-crypto-carry)
and its [revised PDF](https://www.bis.org/publ/work1087.pdf).

The local crypto data is spot 1h OHLCV only. It lacks dated futures marks and
settlements, contract multipliers, expiries, basis snapshots, collateral
yield, margin, liquidation, and two-leg fill records. CME's roll documentation
also makes clear that futures positions must be offset or rolled before expiry
([CME futures expiration and contract roll](https://www.cmegroup.com/education/courses/introduction-to-futures/understanding-futures-expiration-contract-roll)).

Minimal future spec: one venue and frozen contract universe; PIT spot and dated
futures bid/ask/mark/settlement; basis definition; collateral and financing;
margin/liquidation rules; synchronized spot-long/futures-short execution;
explicit expiry/roll; cost stress and venue outage handling. A long-only spot
adaptation loses the cash-and-carry mechanism and must not be presented as X05.
Current verdict: `OUT_OF_SCOPE`, `CAPABILITY_BLOCKED`, and `DATA_BLOCKED`.

## X06 — spot/perpetual funding carry

The intended position is spot long plus perpetual short, earning or paying
periodic funding while remaining exposed to basis, mark, margin, and
liquidation risk. Binance documents that funding transfers between long and
short holders, is based on position notional and the funding rate, normally
settles every eight hours, and may have contract-specific interval/cap changes.
See [Binance Introduction to Futures Funding
Rates](https://www.binance.com/en/support/faq/detail/360033525031). BIS's
crypto-carry research provides the broader evidence that carry varies strongly
and can rise before crashes ([BIS Working Paper 1087](https://www.bis.org/publications/working-paper-1087-crypto-carry)).

The repository has no historical funding rates, perpetual mark/index prices,
premium-index snapshots, margin schedules, liquidation records, or spot/perp
two-leg fills. The native engine cannot represent a short derivative leg or
funding cash flows. Funding is not a fixed risk-free yield: the exchange
documents rate and interval changes, and collateral can be consumed in
volatile conditions.

Minimal future spec: one frozen venue/contract set; PIT funding and mark/index
history; funding payment timestamps and rate formula; spot and perp fills;
margin, liquidation, collateral, and outage rules; synchronized hedge
creation/exit; fixed rebalance/roll policy; cost stress. A long-only spot
signal is not X06. Current verdict: `OUT_OF_SCOPE`, `CAPABILITY_BLOCKED`, and `DATA_BLOCKED`.

## Queue conclusions

X01–X03 are economically meaningful hypotheses but cannot be honestly
replayed from current local inputs. X04–X06 additionally require a new
short/derivative execution model. None justifies automatic capital allocation
or changes to V3, the crypto incumbent, risk ceilings, or production
configuration. A candidate can proceed only after its data, PIT, capacity,
and frozen execution contract are independently satisfied.
