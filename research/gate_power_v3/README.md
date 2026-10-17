# research/gate_power_v3 — audit of the batch screen's power (third and final pass)

This is an **audit study**, not a trading-idea search. It answers the owner's question — *can the
`research/batch_screen` two-stage screen detect a real improvement, or is it too strict / buggy?* —
with (a) synthetic known-edge arms driven from the mandatory engine, (b) a positive-control ladder
through the campaign's own alternate-control machinery, (c) an audit of the multiple-testing
(deflation) step, and (d) a tally of the 303 real verdicts from the committed records.

It builds on the campaign's own framework, copied verbatim from `research/batch_screen/` (`api.py`
`boot.py` `deflation.py` `extra.py` `registry.py` `rules.py` `stages.py` `windows.py`,
`EXTRA_DATA.json`), with exactly one behavioural change: `api.py`'s synthetic legs are pinned **by
leg name** in `SYNTH_LEGS` and selected by an arm's `"leg"`, replacing `gate_power_v2`'s env-global
`GATE_POWER_DRIFT` that made `syn_null0` a copy of `syn_edge`. The copied framework files are the
campaign's bytes; the campaign copy is never edited or run in place.

Run it from the worktree root:

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.gate_power_v3.screen --harness   # parity --quick + K anchors + legs
PYTHONPATH=. $R -m research.gate_power_v3.screen --run       # controls + synthetic + deflation
PYTHONPATH=. $R -m research.gate_power_v3.screen --tally
```

* `engine.py` — the stage-1/stage-2 orchestration (`stages` + `boot`) without the campaign ledger.
* `dsr_audit.py` — the framework's deflated Sharpe vs the null-sampling-variance one.
* `tally.py` — the Stage-2 reject tally from `research/batch_screen/results/`.
* `screen.py` — the CLI (`--harness`, `--run`, `--tally`).
* `PREREGISTRATION.md` — the fixed protocol and the proposed deflation amendment (§7.2).
* `DATA_MANIFEST.json` — the SHA-256 of every input this study reads.
