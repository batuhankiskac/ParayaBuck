# Known limitations V2

1. Both completed daily series carry availability-early labels. Equity uses New York session midnight; crypto aggregation labels a full UTC day at bucket start. The isolated controls consume completed bars at those labels. In a combined clock, crypto UTC-midnight and equity NY-midnight labels can order decisions earlier than physically possible. This can change cross-market slot priority; no immateriality proof exists yet.
2. Historical bars cannot reproduce broker acknowledgement, rejects, queue position, asynchronous refresh or non-marginable buying-power updates. V2 uses deterministic synthetic fills and one shared cash balance.
3. The combined daily-risk epoch uses New York calendar date; runtime restart/operator epochs are not reconstructed.
4. The frozen crypto hourly history lacks source acquisition and historical broker tradability metadata. The configured universe and causal trailing bars are used as provided.
5. Realized gross may drift after marks; current policy limits new target gross but does not create invented corrective trades.

These limits are recorded before any migration economics. In particular, item 1 is potentially material to a future position-cap comparison and prevents a readiness claim until bounded or corrected.
