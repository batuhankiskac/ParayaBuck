# research/

No research code lives on `main` (see `docs/research/INDEX.md`): every study's code, inputs and
results are preserved in its annotated tag `archive/research/<name>-<yyyy-mm>`.

The directories that used to sit here (`crypto_sleeve_decision_v1`, `execution_model_audit_v1`,
`paper_config_v2`, `rule_variants_v1`, `signal_cross_check_v1`, `stress_robustness_v1`,
`trend_robustness_v2`) were removed on 2026-10-16 with the move to the Numba research engine;
they are preserved unchanged in tag `archive/research/main-research-dir-2026-10`:

    git checkout archive/research/main-research-dir-2026-10 -- research/<name>

Paths such as `research/rule_variants_v1/...` in older reports refer to that tag. New studies are
built on `research/fast_engine` (tag `archive/research/fast-engine-v1-2026-10`,
`docs/research/fast_engine_v1.md`, `docs/research/WORKER_PROTOCOL.md`).
