# Portfolio Expansion Batch — Independent Scope and Source Review

Date: 2026-09-05

Scope reviewed:
- docs/research/portfolio_expansion_crypto_batch_2026_09_05.md
- docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md
- docs/research/portfolio_expansion_batch_2026_09_05_protocol.md

No backtest, test, production, configuration, or frozen-artifact change was made.

## Decision

PASS with one P1 protocol wording risk; the previously open crypto citation notes are now resolved. The reports preserve equity V3 logic and existing allocation ceilings, distinguish standalone REJECT from portfolio UNTESTED, and avoid claims of profitability or approval. The equity report correctly treats the archived nine-ETF C1 recipe as an exact duplicate while leaving broader cross-asset diversification and risk-overlay questions as hypotheses.

The 8-item equity inventory plus the 8-item crypto inventory contains 16 listed entries. At exact-rule level they are 16 entries. At broader-mechanism level, equity cross-asset trend and crypto time-series trend share one time-series-momentum family, so the defensible broad-mechanism count is 15; this overlap is disclosed. Low-beta versus low-variance are related risk-selection ideas but not the same score. Overnight decomposition versus crypto loser reversal are related behavioral themes but not the same information/timing rule. Optional crypto pairs and Amihud/cointegration extensions are outside the eight-row count.

## P1 — protocol target weights

The protocol specifies a 0.025 incumbent plus 0.025 candidate crypto mixture, total 0.05. This is within the configured crypto ceiling and is described as a research mixture. Every occurrence must be interpreted as a test target, not a realized allocation or production target. The protocol also says caps are ceilings, and the reports state that V3 and its allocation are unchanged. A later implementation must preserve the 0.05 ceiling, shared risk overlay, max-two crypto positions, and fail-closed union handling. This is an interpretation risk, not an observed unsafe edit.

## Source and transfer audit

The equity sources are appropriately primary and transfer limits are mostly explicit:

- Frazzini and Pedersen, Betting Against Beta, [JFE DOI](https://doi.org/10.1016/j.jfineco.2013.10.005), supports low-beta evidence but uses leveraged long/short construction; the report correctly limits transport to capped long-only positions.
- Lou, Polk, and Skouras, [JFE DOI](https://doi.org/10.1016/j.jfineco.2019.03.011), supports overnight/intraday decomposition and cross-period behavior; the report correctly requires execution and adjustment verification.
- Novy-Marx, [NBER working paper](https://www.nber.org/papers/w15940), and Asness, Frazzini, and Pedersen, [Springer DOI](https://doi.org/10.1007/s11142-018-9470-2), support profitability/quality mechanisms; the report correctly marks PIT fundamentals blocked.
- Bernard and Thomas, [JSTOR](https://www.jstor.org/stable/2491062), supports the historical PEAD reference; the report correctly requires release time and consensus vintages.
- Moskowitz, Ooi, and Pedersen, [JFE DOI](https://doi.org/10.1016/j.jfineco.2011.11.003), supports futures time-series momentum; the equity report limits transfer to long-only ETFs.
- Moreira and Muir, [Journal of Finance DOI](https://doi.org/10.1111/jofi.12513), supports volatility management as an overlay; the report does not call it new alpha or authorize V3 changes.
- Gatev, Goetzmann, and Rouwenhorst, [NBER paper](https://www.nber.org/papers/w7032), supports classic pairs; the report correctly marks current long-only execution incapable.

The crypto report uses MAX, reversal, volume, Amihud, and pairs sources as mechanism evidence with transfer caveats. The previously open crypto citations are now verified: Brown, Cohen, Tang & Yam, “Taylor’s law of fluctuation scaling for semivariances” ([PNAS DOI](https://doi.org/10.1073/pnas.2108031118)) supports semivariance measurement properties only; Fieberg, Liedtke, Metko & Zaremba, “Cryptocurrency factor momentum” ([publisher page](https://www.tandfonline.com/doi/full/10.1080/14697688.2023.2269999)) studies more than 3,900 coins over 2014–2022; and Hsieh, Liu & Liu, “State transitions and momentum effect in cryptocurrency market” ([JFRL DOI](https://doi.org/10.1016/j.frl.2025.108356)) studies weekly crypto momentum conditional on market states. Their samples and constructions do not establish this bot’s long-only daily outcome.

The 52-week-high source is a primary Wiley article, but its study concerns insider transactions and US equities, not the crypto proximity rule. The report presents it as mechanism context and retains the local negative result. Alpaca fee links are official but time-sensitive and should be receipt-pinned if costs enter economics.

## Scope correctness

The reports do not make the standalone-REJECT-equals-portfolio-REJECT error. C3/C4 explicitly retain standalone results while labeling portfolio contribution untested; equity C2 does the same. Quality, value, PEAD, and fundamental/event families are blocked rather than filled with price proxies.

The equity report preserves V3 logic and the configured ceiling; it does not propose splitting or replacing the 0.55 sleeve. The protocol's C2 exclusion is consistent with the fixed-equity constraint, while the equity report keeps C2 as a future complementary question. The crypto protocol's 0.025 values are bounded research targets and must remain separate from configured realized exposure.

The reports correctly distinguish volatility targeting from alpha, pairs from current long-only capability, and data presence from point-in-time/tradable evidence. No report claims that the shared portfolio has already been proven.

## Shared-account limitation

The protocol accurately says that independent sleeve-curve blending can screen correlation but cannot model shared cash, order competition, caps, fees on changed quantities, or risk feedback. Decision-grade evidence requires one shared ledger and persisted signals, targets, orders, fills, fees, cash, positions, equity curve, and terminal marks. This is a research-infrastructure limitation, not evidence against any candidate.

## Required disposition

- PASS: scope language, V3/allocation preservation, no auto-promotion, standalone versus portfolio distinction, exact C1 duplicate treatment, and shared-ledger limitation.
- P1: label protocol 0.025/0.025 as research test targets everywhere they are surfaced to an operator.
- BLOCKED until data/capability receipt: PIT quality/value, PEAD events/consensus, overnight corporate-action/open execution, futures/carry, orderflow, and pairs short/borrow/two-leg execution.

The reports are suitable as research inventories after these provenance notes are carried into the next manifest. They do not authorize production, PAPER/LIVE, or economic conclusions.


## Native screen validation closure

The native validation receipt is now present in `docs/research/portfolio_expansion_crypto_screen_native_validation_2026_09_05.md`. The current focused test file has 27 passing tests, including one real subprocess invocation of the public screen CLI against a temporary hourly CSV. That invocation exercised `full/low_variance22/1x` as shard 0 of 42, with a real buy fill, nonzero crypto fees, a later execution bar, a nonempty cash ledger, final marked positions, and cash-plus-marked-position reconciliation. Checkpoints and the invocation receipt were written under the temporary directory. This proves one native cell and the CLI serialization path; it does not prove completion or economics for all 42 cells.

The draft manifest intentionally retains `manifest_sha256 = DRAFT_COMPUTE_AFTER_FREEZE`. `--validate-inputs` can therefore report the pin inventory without claiming a canonical digest, while `--run` fails closed with `BLOCKED_MANIFEST_DIGEST_PLACEHOLDER`. The immutable draft currently has 9 source pins, the protocol/settings pins, the temporary-input hash substitution is tested, and the canonical digest excludes only its own digest field. Current hashes at review: runner `ca623c3fc79dc67a4e655237cc85fdab3384f1edd0868ea08112d0b6c70df0da`, protocol `39213f8aabed1a1268f36179d4cabe9cb79e678274ba6a4317b2b14911543a52`, draft manifest `53354f3598ea812f4cbc197525e45750a92516c6923caacc849549fc11807919`, native validation report `5657759a019322289be5ef8aea99d9e07278d0324cf90c166b3c2176dcb9537d`, and test file `4d6a45106cec2db3848b3f006efe97031d30ebce132249996c371367fc1551ee`.

Disposition remains: native one-cell proof PASS; full 42-cell screen and any economic/promotion conclusion remain untested.
