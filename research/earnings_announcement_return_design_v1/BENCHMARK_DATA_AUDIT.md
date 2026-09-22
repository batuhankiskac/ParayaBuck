# Benchmark data audit

## Gate

`PRIMARY_EAR_BENCHMARK_REQUIRES_LICENSED_DATA`

The published construction is identifiable and reproducible in principle, but the repository does not contain the historical firm characteristics or security links required to assign an event stock to the correct benchmark portfolio. The freely downloadable portfolio returns solve only the second half of the problem.

## Exact benchmark checked

The January 22, 2008 version of Brandt, Kishore, Santa-Clara, and Venkatachalam, *Earnings Announcements are Full of Surprises*, defines

\[
EAR_{i,q}=\prod_{j=t-1}^{t+1}(1+R_{i,j})-\prod_{j=t-1}^{t+1}(1+FF_{i,j}).
\]

`t` is the Compustat quarterly earnings report date (`RDQE` in the paper), `R` is the stock return from CRSP, and `FF` is the daily return on the size/book-to-market portfolio to which the stock belongs. The paper explicitly says it uses six portfolios formed by the intersection of two size groups and three book-to-market groups. It is therefore a 2 x 3 matched-portfolio adjustment, not a factor-model residual, a 5 x 5 match, or a market adjustment. The paper's data section uses Compustat Industrial Quarterly and CRSP over 1987-2004.

Primary-paper source: January 22, 2008 `santa_clara_surprises.pdf`, locally inspected SHA-256 `d55a0f2cc116437957f282345e31699a0e50598256e53fca12d1d3fc5b9e5b10`; retrievable copy: [CiteSeerX PDF](https://citeseerx.ist.psu.edu/document?doi=e7d0a7dd62354ade2b883284c382b0e3b892c37c&repid=rep1&type=pdf); bibliographic record: [SSRN 909563](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=909563). The earlier June 2007 [UCLA-hosted draft](https://www.anderson.ucla.edu/documents/areas/fac/finance/ear.pdf), SHA-256 `73c4bf1ea2fbdcfeaeac085a6301f39adb93920134f8429e06c97de89c25396f`, is corroborative only. Its three-day compounded formula and six-portfolio 2 x 3 benchmark are unchanged in the January 2008 version.

## What the French files do and do not provide

The Kenneth French Data Library publishes daily returns for the six U.S. portfolios. Its current construction page states that the portfolios are formed at each June end using:

- the NYSE median June market equity for the small/big split;
- the 30th and 70th NYSE percentiles of book equity divided by December market equity;
- book equity for the last fiscal year ending in calendar year `t-1`;
- December `t-1` and June `t` market equity; and
- positive book equity, with membership held from July `t` through June `t+1`.

Sources: [six-portfolio construction](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/six_portfolios.html), [daily return library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html), and [historical archives](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/six_portfolios_archive.html).

The return file has six dated return columns. It does **not** say which portfolio contains an arbitrary security on an arbitrary date. Subtracting a row from that file therefore requires a separately established historical membership. Choosing a column from ticker, current market cap, current price-to-book, or the event-date cross-section would not reconstruct Brandt's benchmark.

## Minimum faithful input contract

| Input | Why it is required | Acceptable provenance |
|---|---|---|
| CRSP daily total return and historical PERMNO | Stock leg, exchange/share-code eligibility, delist and identifier continuity | Licensed CRSP daily stock file |
| December and June market equity | French annual size and B/M assignment | Licensed CRSP price and shares outstanding |
| Prior-calendar-year positive book equity | B/M numerator with the paper's annual lag | Licensed Compustat annual fundamentals; PIT snapshot/unrestated history for the user's stricter PIT requirement |
| NYSE breakpoint population | Exact median and 30/70 percentiles | Full licensed CRSP/Compustat eligible universe or vendor-supplied membership |
| GVKEY-PERMNO link history | Join issuer accounting data to the traded security at the event date | Licensed CRSP/Compustat Merged link history |
| Six daily portfolio returns | Benchmark return leg | Kenneth French Data Library, with downloaded vintage and hash frozen |

The French page uses information lags designed to avoid using the current fiscal year's accounting result. That does not by itself make a modern, revised Compustat extract point-in-time. S&P advertises Compustat point-in-time snapshots beginning in 1987, and WRDS lists Compustat Snapshot and Unrestated Quarterly as separate products. For a strict 2016-2026 PIT reconstruction, the accepted acquisition is CRSP + Compustat + CCM together with Snapshot/Unrestated history or equivalent immutable vintages. Sources: [S&P Compustat Financials](https://www.marketplace.spglobal.com/en/datasets/compustat-financials-%288%29), [WRDS/S&P product inventory](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/sp-global-market-intelligence/), and [CRSP/Compustat Merged guide](https://wrds-www.wharton.upenn.edu/documents/402/CRSP-Compustat_Merged_Database_Data_Guide_9efDcmD.pdf).

## Repository capability result

CodeGraph inspection found only the repository's generic OHLCV `MarketDataset` and V3 return processing. A targeted repository search found no Compustat, Kenneth French, Fama-French membership, book-equity, or historical market-equity loader. One issuer whose ticker text happens to be `CRSP` in the entity-lifetime file is not the CRSP database. Existing OHLCV does not contain shares outstanding or book equity.

No entitlement, credential, mounted extract, or validated vendor join was supplied for this campaign. This is not evidence that the products cannot be obtained; it is why the exact benchmark is currently licensed-data dependent.

## Acceptance test before any economic experiment

The benchmark gate may change to `PRIMARY_EAR_BENCHMARK_RECONSTRUCTIBLE` only after a frozen input package can, without fallback substitutions:

1. reproduce annual 2 x 3 membership for every eligible event security;
2. expose the source vintage and point-in-time/restatement policy for every accounting observation;
3. retain inactive securities and dated CCM links;
4. match the six French daily series by the same July-June membership convention;
5. report unmatched and ambiguous events rather than guessing; and
6. hash the membership, breakpoint, link, and return inputs.

No SPY, raw-return, V3-residual, market-factor, or current-price-to-book replacement is approved by this audit.
