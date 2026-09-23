# Execution proxy review — frozen before corrected outcomes

Candidate A: first pinned source-hour **OPEN** with `hourly_bar.timestamp > order_created_at`, once per UTC day for each pending order. It is observable in the pinned source, maintains strict temporal ordering, and can use the existing daily positive-volume lookback and daily participation cap. This is selected. It delays a market-order proxy by up to roughly one hour relative to an immediate live order; the delay is determined by data resolution, not performance.

Candidate B: the 00:00 source-hour opening print. Rejected because it coincides with complete daily signal availability and an order generated from that signal did not exist beforehand.

Candidate C: hourly close or fabricated intrahour midpoint/VWAP. Rejected because the pinned source does not prove a new market order can capture an exact closing print, while midpoint/VWAP would be a different unobserved execution price. Hourly close is a completed-bar observation, not a contemporaneous executable quote.

No corrected replay economics or alternative-proxy performance was inspected before this choice.
