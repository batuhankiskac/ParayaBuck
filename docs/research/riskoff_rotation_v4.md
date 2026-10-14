# riskoff_rotation_v4 — independent confirmation of L2

Verdict: FAIL — independent fresh-instrument confirmation does not pass F2 (max drawdown).

Code audit: no look-ahead in monthly bond/gold choice; mid-month hold-to-month-end logic correct;
defensive resize band 10pp and 0.5pp risk resize implemented; cost charged on |Δw|; sells ~62/yr.
Main-window pre-tax delta +0.82 pp unchanged from v3.

## F-gates (fresh 21 ETFs)
| Gate | Result |
|---|---|
| F1 median Δ CAGR (cal, m=2) | +1.01 pp, 95% positive — pass |
| F2 Δ max DD <= +3pp | 76.2% (need 80%) — FAIL |
| F3 pooled block-20 bootstrap median Δ | +0.838 pp (42k paths) — pass |
| F4 doubled cost | +0.426 pp median, 61.9% positive — pass |

F2 failure: L2 worsens max drawdown by more than 3 pp vs C0 on 5 of 21 instruments.

After-tax check (report-only): after-tax model does not model defensive lots; pre-tax main-window
delta +0.82 pp, with expected additional tax drag from ~11/yr turnover.

Bottom line: v3's L2 edge on main window/S&P and OOS does not translate to a robust drawdown
improvement across fresh instruments. Do not promote.
