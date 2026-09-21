# Volume reconstruction audit

Canonical bars SHA-256: `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`. The manifest declares SIP and `adjustment=split`. Canonical corporate actions SHA-256: `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`.

The frozen field is `DV(t)=split_adjusted_close(t)*split_adjusted_volume(t)`. Reciprocal forward/reverse-split adjustment preserves this product and any uniform own-window rank or ratio. Synthetic forward/reverse transformations and real AAPL/NVDA/DRYS boundaries are the required later runner fixtures; no-action controls must remain unchanged.

Raw CSV validation must precede `load_csv_bars`, which converts blank volume to zero. Blank, nonnumeric, nonfinite, or negative volume fails the input. A zero is retained as a structural observation but makes every signal window containing it ineligible. There is no epsilon or interpolation.

Only rows through the common endpoint `2026-07-30` may be read by readiness reconstruction. Corporate-action rows after that date cannot affect G1. No future corporate action may create a decision-time exclusion; the frozen scale-invariance justification applies only to the retrospectively normalized research field.

Volume reconstruction itself is feasible. The unresolved blocker lies in the selection-relevant `Q0.90` estimator, not in price/volume adjustment.

