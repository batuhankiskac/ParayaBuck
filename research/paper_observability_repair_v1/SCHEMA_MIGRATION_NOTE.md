# Receipt schema boundary

V1 `PaperReceipt` and its parser/writer remain for historical equity receipts, but ongoing runtime emission has moved to V2. Old V1 files may contain projected `positions_after` and `actual_gross_exposure`; they must never be treated as broker-confirmed state. The new auditor requires `.v2.json` for every persisted decision in the new observation period. Start the frozen forward window only after deploying the V2 writer, admission observation table and two-market auditor. Historical V1 reports are not retroactively upgraded.

The operator records the V2 deployment timestamp and passes it as `--start` to avoid mixing old V1 decisions from the same durable database into the new window.
