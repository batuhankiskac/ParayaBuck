# Current capability audit

The frozen design is validly committed at `4f454f5deb4c77151b574f87c29228c859cfc884`, exactly one commit above production `main` at `86123374e41735d8d163c7fbed5bf5edfc43468d`. The design branch and its remote both resolve to that SHA. The readiness branch was created directly from it.

The repository has the required canonical bars, lifetimes, corporate actions, PIT scanner, reserved-role configuration, NYSE calendar dependency, strict terminal-lifecycle engine behavior, and cost semantics. It does not have a candidate-specific abnormal-volume harness. The generic monthly policy is unsuitable: it recognizes the first completed bar of a new month, while pending fills occur before the decision loop, producing a fill on the second tradable bar.

The proposed narrow research-only architecture would use a schedule-derived month-end close and next-month first open, a strict raw-data/lifecycle loader, a pure candidate reconstruction, persisted structural output, and an independently implemented verifier. It must not use `run_product_backtest` or emit portfolio/economic reports.

Implementation is intentionally stopped before target reconstruction because the frozen selection rule contains an unresolved selection-relevant quantile convention. Building a runner by choosing a convention would mutate the frozen candidate in code.

