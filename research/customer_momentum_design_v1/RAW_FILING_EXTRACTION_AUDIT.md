# Raw filing extraction status

No parser was trained, run, or manually validated against a deterministic filing corpus. Source search examples prove heterogeneous language and presentation, but they cannot establish false-positive/false-negative rates. Potential anchors include `major customer`, `customer accounted for`, `represented ...%`, `sales to`, and `revenues from`; a valid extraction must bind the percentage to the supplier and the **same** fiscal period and named legal customer. It must distinguish customer from end customer, reseller/distributor, government aggregation, historical comparative columns, and accounts-receivable concentration. Exact text and raw hash must accompany a proposed edge.

Accuracy: `NOT_MEASURED`. No automated edge is accepted.
