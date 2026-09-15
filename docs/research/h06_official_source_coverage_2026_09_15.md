# H06 official source coverage

This milestone is acquisition-only. It does not classify owners, resolve the
population amendment graph, admit H06 signals, calculate scores, or access
economic outcomes.

## July accession identity

The canonical identity is the SEC accession number. A master-index accession
group must agree on form and filing date, and every route basename must be
`<accession>.txt`. Participant CIK directories and full archive paths may
differ. Those paths are retained as ordered official aliases to one accepted
submission.

The deterministic canary receipt compares two distinct official routes for
three size-two groups, two larger groups, and the first maximum-size group.
Mass acquisition is admitted only when each pair has the same accession,
acceptance time, conformed submission type, and exact SHA-256.

## Fair access and resume

Quarter/index discovery uses one configured request start per second. July
filing acquisition uses one global limiter capped at four request starts per
second across four workers. Submissions metadata uses one request start per
second. All routes use bounded retry/backoff; the configured policy is
reported, not a fabricated measured rate. Only the User-Agent source enum is
persisted.

July processing is accession-sorted. Valid cache entries are revalidated and
never requested again. A new body is validated and atomically renamed before
it enters the inventory, and the inventory is atomically checkpointed at
least every 25 completed attempts.

## Acceptance metadata

The relevant quarterly target is derived only from structured SEC transaction
fields. Routine-history targets contain a 2019-2025 non-derivative P or S row.
Signal-structural targets are 2022+ non-derivative P/A/direct rows with positive
shares and price. No owner classification or outcome field participates.

Bulk `acceptanceDateTime` becomes an authority only after exact-instant
comparison with receipt-bound complete submissions. The resolver examines
recent arrays, then official referenced historical JSON, and finally exact
complete-submission headers only for true metadata gaps. Duplicate participant
records that contain both a UTC timestamp and an Eastern wall-clock value
mislabelled with `Z` are resolved only when America/New_York conversion proves
that the two representations denote the same instant; deterministic EST, EDT,
and Form 4/A canaries bind that rule back to complete-submission headers.

Three quarterly target accessions absent from `submissions.zip` are recovered
only from their exact-date official SEC Oldloads archives. Exact-date Feed
archives were inspected first, but their `.nc` members do not contain the PDS
acceptance timestamp and are therefore rejected as acceptance authority. Each
accepted fallback row binds the Oldloads URL, full `.gz` SHA-256, extracted
submission SHA-256, accession, form, filing date, raw Eastern acceptance
timestamp from the PDS control block, and normalized UTC instant. One bulk
filing-date conflict is quarantined and resolved from its directly accessible
complete-submission participant route.

## Local-only artifacts

Large SEC ZIPs, exact-date Feed archives, July bodies, alias canary bodies,
referenced history JSON, extracted fallback submissions, and acceptance tables remain below
`data/research/local/h06_full_acquisition_2026_09_14/` and are not staged in
Git. Tracked manifests contain hashes, counts, source URLs, policy metadata,
and self-integrity seals only.

## Sealed result

The final accession-level source state is 30/30 quarterly ZIPs, 22/22 July
indexes, and 11,242/11,242 July complete submissions with zero failures. The
outcome-free quarterly target contains 379,493 routine-history accessions,
45,942 signal-structural accessions, and 384,068 accessions in the union. The
acceptance map contains all 384,068 quarterly targets plus 11,242 July bodies
(395,310 total rows), with zero unresolved records and zero conflicts. Source
authority is 384,064 validated bulk records, one direct complete-submission
correction record, and three exact-date Oldloads records. The offline rebuild
matches the sealed stable inventory hashes.
