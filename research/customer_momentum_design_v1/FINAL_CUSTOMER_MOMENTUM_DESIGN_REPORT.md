# Final design decision

`CUSTOMER_MOMENTUM_DESIGN_BLOCKED_DATA_OR_EXECUTION`

This is a **design-stage acquisition/execution block**, not an alpha rejection or a measured proof that every free-data graph is inadequate. A deterministic 2016–2026 SEC corpus with accession/accepted-time vintages, manual identity checks and historical security mappings was not acquired. The necessary PIT public graph therefore does not exist in this campaign, and its feasibility is unknown. No candidate or economic protocol is authorized. The first operationally material failed gate is G4; downstream G5–G12 cannot be measured honestly without edges. The narrower `BLOCKED_PUBLIC_GRAPH` label would imply a measured inadequacy that this run cannot support.

## Snapshot and repository context

- Starting `origin/main`: `be440821e34d859f2e693c45532566eb0dafb1f8` (fetched at task start). Research branch parent is the same SHA. During this campaign the shared remote-tracking ref advanced to `7724a2bc9e61ba325a6a252c8f518cabd99d6c69`; none of those commits were absorbed. Final research SHA is recorded in the handoff after commit.
- Search of remote-main `docs/research/research_register.md`, `research/`, and branch names found no completed local customer-momentum economic test. The incumbent equity and crypto strategy names in `config/settings.yaml` remain `equity_residual_momentum_v3` and `crypto_low_variance_hysteresis_v1`; the current account cap there is eight positions. The older canonical baseline document retains a six-position description and must not substitute for current config.
- Canonical equity endpoint is 2026-07-30. `docs/research/public_data_research_policy_v1.md` keeps licensed customer-chain data out of scope absent separate operator authorization.

## Primary hypothesis and long-only evidence

The [full Cohen–Frazzini paper](https://pages.stern.nyu.edu/~afrazzin/pdf/Economic%20Links%20and%20Predictable%20Returns%20-%20Cohen%20and%20Frazzini.pdf) uses annual Compustat Segment customer names, conservatively matches listed customers to CRSP PERMNO, delays use six months after fiscal-year end, equal-weights multiple customers in the base signal, ranks suppliers on completed prior-month customer returns, forms broad monthly quintiles, and holds for the following month. It excludes supplier prices <= $5. Sales-share weighting is robustness. The source return is CRSP monthly stock return, normally a holding-period return inclusive of dividends; separate delisting-return stitching is not specified in the article. Its exact stale-link expiration is not stated.

The published Table III high quintile has positive excess return but mixed alpha: VW five-factor 0.440%/month (t=1.60), EW five-factor 0.420% (t=2.11). The negative low leg accounts for roughly 64% (VW) and 69% (EW) of the reported five-factor high-minus-low alpha. This is `CUSTOMER_MOMENTUM_LONG_HIGH_LIMITED`, not zero high-leg evidence. Later [Pinchuk Tables 8–10](https://arxiv.org/html/2301.11394) give 2005–2018 high-decile FF5+UMD alpha 0.50% EW (t=2.32) and 0.61% VW (t=2.07), but the later long-short factor is weak; decile evidence does not establish a few-name portfolio. The 2025 [*Speed Matters* article](https://pubsonline.informs.org/doi/10.1287/mnsc.2023.00291) supports an attention mechanism, not this long-only public-data implementation. Modern classification: `LIMITED_MODERN_SUPPORT`. See `LONG_HIGH_EVIDENCE.md` and `MODERN_EVIDENCE_REVIEW.md`.

## Accounting, SEC law and Compustat provenance

[SFAS 131 paragraph 39](https://storage.fasb.org/fas131.pdf), the basis for ASC 280, requires disclosure of >=10% customer concentration, the amount from each major customer and affected segment(s), but explicitly **does not require customer identity**. The paper's contrary characterization is an error. Before 2020-11-09, historical Item 101(c)(1)(vii) conditionally required naming a >=10% customer if its loss would materially adversely affect the registrant; the smaller-reporting-company version did not impose the same naming rule, per the [SEC's 2016 explanation](https://www.sec.gov/rules/proposed/2016/33-10110.pdf). From [2020-11-09](https://www.sec.gov/rules-regulations/staff-guidance/compliance-disclosure-interpretations/transitional-faqs-regarding-amended-regulation-s-k-items-101-103-105), Item 101 became a material, principles-based customer-dependence topic without the old prescriptive naming condition ([final rule](https://www.sec.gov/files/rules/final/2020/33-10825.pdf)). The change is applied by filing date, not fiscal label. Regime coverage change is plausible but unmeasured.

Compustat adds collection, normalization, customer codes and annual historical panel fields to disclosures; Cohen–Frazzini then perform phonetic/manual identity matching. The [vendor manual](https://tevgeniou.github.io/EquityRiskFactors/CompustatManualChpt5.pdf) even distinguishes `Not Reported` names from reported revenue. It is not a raw SEC relationship field, and the reviewed documentation does not show that undisclosed identities are somehow reconstructible. This creates a source-equivalence question, not proof of vendor access to private relationships.

## Public graph and source audit

The [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) supply filings and entity-wide standard XBRL facts. They do not yield a complete named-customer graph: custom/dimensional customer identity can appear only in raw iXBRL/HTML and may be anonymous. [A rendered named XBRL table](https://www.sec.gov/Archives/edgar/data/57515/000005751523000017/R66.htm) demonstrates possible names; [an anonymous 2024 table](https://www.sec.gov/Archives/edgar/data/1769624/000121390026062503/ea029006003-ars_triller.pdf) demonstrates nonresolvable records. Selected examples are not coverage statistics.

A deterministic 330-row historical-lifetime candidate frame was frozen before inspecting relationships (`DETERMINISTIC_AUDIT_SAMPLE.csv/json`), but its CIKs, filings and PIT size/sector strata are absent. No substantial issuer-year **filing** corpus, accepted-at ledger, parser validation, named-versus-anonymous rate, public-company mapping rate, XBRL coverage series or graph-fidelity comparison was produced. `HISTORICAL_DISCLOSURE_COVERAGE.json` explicitly records zero audited issuer-years and null rates; this is **missing evidence**, not a zero relationship estimate. Raw filing extraction accuracy is `NOT_MEASURED`. SEC bulk collection was not started without a configured compliant contact User-Agent. The proposed construction spec keeps anonymous, private, government, unresolved and listed-pending observations distinct and refuses inferred identities.

Every future edge must carry supplier/customer, accession, fiscal period, exact quoted source/hash, `accepted_at`, first-public availability, share if stated, mapping evidence and supersession. Earliest use is no earlier than public disclosure and the paper's six-month lag. A later amendment/name revelation never rewrites earlier history. The paper does not specify a precise expiration clock; a conservative next-annual-or-12-month rule is documented as a **proposal**, not source-equivalent base. 10-Q accelerated edges would be a different signal. Historical supplier CIK/share class and named-customer public-parent/security mapping are `NOT_RUN`; current tickers cannot repair history.

## Concentration, liquidity, V3 and policy

Monthly eligible suppliers, high-tail supply, natural sparsity, slot demand, supplier overlap and V3 selection overlap are `NOT_MEASURED` because no accepted graph exists. There is no defensible `top_n` or claim that a broad published quintile fits eight shared-account positions. The paper's liquid and larger-stock robustness is primarily **long-short spread** evidence, not a verified liquid high-only leg. The public/free-data policy therefore cannot authorize economics. This conclusion does not reopen commercial procurement.

## Governance and next action

- Local future supplier economics calculated: `NONE`.
- Production changes: `NONE`.
- PAPER/LIVE changes: `NONE`.
- Candidate frozen: `NO`. Future standalone economic protocol authorized: `NO`.
- Exact next action: `KEEP_CUSTOMER_MOMENTUM_CLOSED_UNDER_PUBLIC_DATA_POLICY`. A new pre-outcome effort could resume only after an SEC-compliant deterministic corpus with accepted-time/identity validation and historic security mapping can be audited; then reassess G4–G13 **before** any supplier outcome.
- Concurrent work: this task touched only its dedicated worktree and research branch; no other branch or worktree was checked out, reset, rebased or modified.
