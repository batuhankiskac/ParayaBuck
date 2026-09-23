# RR-02 pre-fix reproduction

The audit pinned at `3551322b9ac609ed2ee52c0343a14f2dcd408f37` showed that a repeated 100-item FILL page and a non-list response could be reported as successful. The new regression `test_repeated_fill_page_is_failure_without_watermark` failed before production edits: the old path stopped after a repeated cursor and returned success. It could apply staged page-1 activity and advance the timestamp watermark despite no proof of complete traversal.

The fixed test requires `False`, zero applied activity, and no watermark update. Startup treats `False` as a hard reconciliation failure.
