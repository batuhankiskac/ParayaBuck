# Protocol-conformance correction

The first execution attempt on 2026-09-17 is invalid evidence and is excluded
from the campaign decision. It exposed an implementation defect before final
verification: the defensive wrapper multiplied every raw V3 target by
`0.45 / 0.55`, but raw production V3 emits up to eight pre-risk weights while
the production risk overlay subsequently retains six and caps the sleeve.
Consequently Control B exceeded the frozen `0.45` V3 allocation on 58
defensive decisions and reached `0.4909090909`.

The correction does not change the signal, instrument, cadence, window, cost
model, thresholds, or the 10% SH allocation. Defensive V3 still receives the
same proportional `0.45 / 0.55` derating, with one additional fail-closed cap:
the scale may be reduced enough that the V3 weights which can survive the real
six-slot production limit never exceed `0.45`. It is never increased to fill
unused capacity. In Treatment C, SH still consumes a real slot, so the loss of
the sixth V3 name remains cash and is measured as deployability evidence.

The frozen protocol file and acceptance gates remain byte-for-byte unchanged.
All prior JSON outputs are overwritten by the corrected full replay and may
not be used in the final decision.
