# Long list — public-data alpha discovery v3 — 2026-09-22 (pre-outcome)

32 families screened across three scouts (13 SEC filing events + 1 FINRA short-interest +
18 corp-action/mechanics/text/opportunistic). No local economics computed.

| Candidate | Mechanism | Public/free source | Natural ranking | Horizon | Modern long leg | Concentration | PIT feasibility | Main blocker | Disposition |
|---|---|---|---|---|---|---|---|---|---|
| S-A Insider ownership level | Managerial ownership alignment | EDGAR insider ZIPs (free) | Ownership % / level | 6–12M | No tradable long leg (MSV 1988; McConnell-Servaes 1990, old) | Diversified | Feasible | No long leg | REJECT_EVIDENCE |
| S-B Insider cluster buys | Informed consensus across insiders | EDGAR insider ZIPs (free) | Cluster size | 1–6M | No modern isolating paper (John-Lang 1991; Lakonishok-Lee 2001 old) | Unproven | Feasible | Stale evidence | REJECT_EVIDENCE |
| S-C CEO/CFO buys | Senior-insider information | EDGAR insider ZIPs (free) | Role-weighted buys | 1–6M | Old small-tilted only (LL 2001; JMZ 2003; SVW 2013); repo 2026-09-21 already insufficient | Unproven top-one | Feasible | Modern liquid buy-only missing | REJECT_EVIDENCE |
| S-D Opportunistic buys (CMP) | Routine-vs-opportunistic classifier | EDGAR insider ZIPs (free) | Opportunistic flag | 1–6M | 82bp is buy-minus-sell, not long leg | Closed family | Feasible | Closed; long leg absent | REJECT_REDUNDANT |
| S-E 8-K 5.02 leadership changes | Leadership repricing | EDGAR 8-K (free) | Event type | Days–3M | Announcement-only, old (Huson et al. 2004); no post-2010 drift | Event study | Feasible | No drift | REJECT_EVIDENCE |
| S-F 8-K 1.01/1.02 agreements | Material agreement value | EDGAR 8-K (free) | Item code | Days–3M | No item drift (Lerman-Livnat 2010, diversified pre-2010) | Unrankable | Feasible | No drift | REJECT_EVIDENCE |
| S-G 8-K 2.01 M&A completion | Deal synergy | EDGAR 8-K (free) | Deal size | 1–12M | Acquirer long-run neutral-negative; no long drift | Diversified | Feasible | Wrong sign | REJECT_EVIDENCE |
| S-H 8-K catch-all (8.01/2.02/7.01) | Mixed material news | EDGAR 8-K (free) | None ex ante | Days–3M | Unrankable; equals closed volume logic | Unrankable | Feasible | No ranking | REJECT_EVIDENCE |
| S-I Debt retirement | Deleveraging re-rating | EDGAR 8-K (free) | Retirement scale | 1–6M | Issuance effect is short-leg; no retirement long drift | Heterogeneous | Feasible | No long leg | REJECT_EVIDENCE |
| S-J Filing delays (NT/late) | Delay = bad news | EDGAR NT forms (free) | Days-late / repeat | 1–6M | Short-leg only (Impink 2012; GRZ 2013); no long leg | Long unselectable | Feasible | Wrong side | REJECT_EVIDENCE |
| S-K Restatements (4.02) | Accounting revelation | EDGAR 8-K 4.02 (free) | Restatement type | 1–6M | Short-leg only (Hribar-Jenkins 2004; Palmrose 2004; Desai 2006) | Long = market | Feasible | Wrong side | REJECT_EVIDENCE |
| S-L Auditor changes (4.01) | Audit-risk revelation | EDGAR 8-K 4.01 (free) | Resign vs dismiss | 1–6M | Announcement-only short-leg, old (Knechel 2007) | Long = market | Feasible | No long leg | REJECT_EVIDENCE |
| S-M Going-concern withdrawal | Distress resolution | EDGAR 8-K/audit opinion (free) | Withdrawal flag | 6–12M | Tiny distressed pre-2010 (Kausar-Taffler-Tan 2009) | Micro distress | Feasible | No liquid evidence | REJECT_EVIDENCE |
| F-1 Negative SI surprise / low-SI long | Informed-short unwinding / absence of pessimism | FINRA biweekly bulk + NasdaqTrader mirror (free) | Ascending %Chg; low level within high-ADV | 1–6M | NO: VW insignificant (−5bp vs −53bp EW); subperiod instability; OOS deterioration; Han-2023 unverified | Deciles only; no top-6/top-1 | YES-WITH-CAVEATS (pub-gated, in-file fields, vintage-archived) | VW insignificance + staleness + momentum overlap | REJECT_EVIDENCE |
| A1 Spin-child post-distribution drift | Conglomerate-discount release + neglect + focus improvement | EDGAR 10-12B/A + 8-K distribution detail (free) | Recency × liquidity; optional frozen focus flag | 6–12M | Pre-2010-weighted (CMW 1993; Desai-Jain 1999; Veld surveys); post-2015 liquid update REQUIRED | 10–30 concurrent → top-6 fillable | Causal (acceptance datetime + regular-way dates; WI excluded) | Liquid-subset premium may vanish; spin-ratio loader path | SHORTLIST |
| A2 Issuer self-tender post-expiration | Undervaluation signal | EDGAR SC-TO-I/T + 8-K (free) | Oversubscription / premium | 1–6M | Old small-N (Vermaelen 1981; CJ 1991; PV 2005); no modern liquid study | 5–15/yr; cannot sustain slots | Definable but awkward | Sparse; overlaps closed repurchase | REJECT_CONCENTRATION |
| A3 Debt tender deleveraging | Distress reduction | EDGAR 8-K + Ex-99.1 (free) | Debt-reduction scale | 1–6M | Small confounded announcement; no drift study | No natural rank | NLP flag needed | REJECT_EVIDENCE |
| A4 Special distributions | Cash-richness repricing | EDGAR 8-K + Ex-99.1 (free) | Special yield | Days–3M | Pop-then-flat; ex-date dominates | Sporadic | Feasible | REJECT_EVIDENCE |
| A5 Leveraged recap | Capital-structure signal | EDGAR 8-K + exhibits (free) | None natural | 3–12M | Rare, levered, mixed; no long-only evidence | Handful/yr | Scattered terms | REJECT_CONCENTRATION |
| A6 Exchange offers | Restructuring signal | EDGAR S-4/SC-TO/8-K (free) | None (bespoke) | 1–6M | Rare bespoke; no evidence | Too rare | Exhibit-bound | REJECT_CONCENTRATION |
| B1 Exchange-tier uplisting | Visibility/liquidity repricing | Nasdaq dir (current-only) + 8-K; tier history license-gated | Tier-jump size | Days–3M | Old (SM 1986; JK 2006); modern drift ~zero/negative | Wrong sign | FAILS (Daily List history licensed) | REJECT_DATA_PROVENANCE |
| B2 Delisting-warning resolution | Distress-resolution bounce | Nasdaq notices + 8-K (no bulk history) | Deficiency type | Days–2M | No modern liquid study; microcap distress | Unownable | No effective-dated archive | REJECT_EXECUTION |
| B3 Halt/status resolution | Information gap | Nasdaq halts (transient) + 8-K | None | Minutes–days | Gap only, no persistence | Single gaps | Transient | REJECT_EXECUTION |
| B4 Index inclusion | Index demand + attention | Press (no bulk archive); 13F 45-day lag | Announcement list | Days–1M | Modern pop decayed/crowded | Few/yr; horizon mismatch | No PIT feed | REJECT_EVIDENCE |
| C1 10-K risk/MD&A additions | Obfuscation / emerging risk | EDGAR 10-K full-text (free) | YoY new-word share | 1–12M | Short-side/vol only; long leg = market | Annual cluster | Feasible | REJECT_EVIDENCE |
| C2 NT late filing (text side) | Delay = bad news | EDGAR NT forms (free) | Days-late / repeat | 1–6M | Wrong side; long unselectable | Long = market | Feasible | REJECT_EVIDENCE |
| C3 Auditor/non-reliance (text) | Fraud revelation | EDGAR 8-K (free) | Resign vs dismiss | 1–6M | Short-side only | Long = market | Feasible | REJECT_EVIDENCE |
| C4 New disclosures (1C/PvP/clawback) | Governance/cyber repricing | EDGAR full-text/XBRL (free) | Tag deltas | Unknown | <3yr history; no long-horizon evidence can exist | Sparse | Feasible but unevidenced | REJECT_EVIDENCE |
| C5 LLM/FinBERT tone (unfrozen) | Sentiment predicts returns | EDGAR + current LLM weights | Model score | Days–3M | Hindsight: weights embed future | Varies | FAILS PIT | REJECT_DATA_PROVENANCE |
| D1 Patent-grant efficiency | Innovation underreaction | USPTO bulk (free) + EDGAR prices | Patents/cites per R&D$ | 6–24M | HHL 2013; Kogan 2017 (thru 2010); post-2015 liquid update unverified | Broad rankable | FAILS mapping (assignee→ticker; Kogan file ends 2010) | DEFER_EXTERNAL_DATA_BURDEN |
| D2 FDA-approval holder continuation | Pipeline de-risking drift | openFDA + sponsor 8-K (free) | NME/BLA novelty × size | 1–6M | Anticipated gap; thin holder drift; small-biotech | No 6 liquid slots | Fuzzy mapping; gap entry | REJECT_CONCENTRATION |
| D3 SI/fails squeeze longs | Crowded-short bounce | FINRA SI + SEC fails (free, delayed) | Delta SI / fails | Wks–3M | Published alpha is short-side; low-SI long = market | Illiquid HTB | Usable vintages | REJECT_EXECUTION |
| D4 Macro timing (FRED/Treasury) | Rates/credit regime | FRED API (free key; ALFRED vintages) | None (market-level) | Wks–quarters | Macro timing, not stock selection | Not rankable | PIT-feasible, irrelevant | REJECT_CONCENTRATION |

Counts: 32 rows (S-A…S-M = 13, F-1, A1–A6, B1–B4, C1–C5, D1–D4). Dispositions:
SHORTLIST 1 (A1) · DEFER 1 (D1) · REJECT_EVIDENCE 20 · REJECT_CONCENTRATION 6 ·
REJECT_EXECUTION 3 (S/H-count: B2, B3, D3) · REJECT_DATA_PROVENANCE 2 (B1, C5) ·
REJECT_REDUNDANT 1 (S-D).

Note: web search was unavailable during parent verification (all providers blocked);
scout citations are training-knowledge + prior repo reviews and MUST be re-verified with
full text before any design freeze — recorded as the decisive gating item for A1.
