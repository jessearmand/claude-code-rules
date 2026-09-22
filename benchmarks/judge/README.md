# Judge benchmark

Scores the `bash_risk_judge.py` questions with several judges on one fixed corpus and compares them:
TypeSafe's Jev (the hook's real path, `ai evaluate` through the AI Gateway), `Mapika/decider-2b` and
`Mapika/decider-4b` served locally, and SemIf (Qwen3.5-4B) through its own scorer. Results and the write-up for
each run are under `results/<date>/`.

## Corpus

`corpus.py` builds it from two sources, 255 cases as of 2026-09-22:

| corpus | n | labelled question | expected from |
|---|---:|---|---|
| rm_rf_guard | 34 | recursive_force | rm_rf_guard_test SHOULD_BLOCK / SHOULD_ALLOW |
| secrets_guard.commands | 109 | secret_access | secrets_guard_test COMMAND_CASES |
| secrets_guard.paths | 35 | secret_path | secrets_guard_test PATH_CASES |
| file_protection.paths | 54 | generated_path | file_protection_test PATH_CASES |
| hook_log | 23 | none | commands that reached Jev in real sessions (`~/.claude/hooks-logs`, untruncated only); Jev's logged answers are kept as `reference` |

Every command case also carries the live hook questions (`destructive`, `exfiltrates`, `scope`), so the hook's
own decision rule can be scored per judge. The live corpus is frozen with `JUDGE_LOG_UNTIL` (default set in
`corpus.py`) because the benchmark's own shell commands are logged by the hook while it runs.

## Run

```sh
# Jev: needs HOOK_JEV_ENABLE=1 and the gateway key via fnox, exactly as the hook does
python3 benchmarks/judge/run.py jev --out scratchpad/judge/jev.jsonl

# decider on Apple Silicon: decider.serve is CUDA-only, so serve decider.infer.Decider behind the same route
(cd ~/Develop/decider && .venv312/bin/python ~/Develop/claude-code-rules/benchmarks/judge/decider_mps_server.py Mapika/decider-2b --port 8000)
python3 benchmarks/judge/run.py decider --url http://127.0.0.1:8000 --name decider-2b --out scratchpad/judge/decider-2b.jsonl

# SemIf: export one row per (case, question) with warm-up rows on top, score with semif-score, import the rows back
python3 benchmarks/judge/semif_bridge.py export --out scratchpad/judge/corpus_semif.jsonl
(cd ~/Develop/SemIf && HF_HUB_OFFLINE=1 .venv/bin/semif-score --backend mlx --mode direct --model Qwen/Qwen3.5-4B \
   --revision 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a --input .../corpus_semif.jsonl --output .../semif_raw.jsonl)
python3 benchmarks/judge/semif_bridge.py import scratchpad/judge/semif_raw.jsonl --out scratchpad/judge/semif-4b.jsonl

python3 benchmarks/judge/report.py scratchpad/judge/*.jsonl --disagreements
```

`run.py` is sequential by default (`--workers 1`) so latency is per request, the way the hook sees it; two unrecorded
warm-up calls precede the timed run. Run one judge at a time: the local models share the GPU.

## What the report shows

* errors and client-side latency per judge (one call answers every question of a case);
* per corpus, agreement with the deterministic label at the hook threshold (0.7) and at 0.5, AUROC, ECE, Brier, log loss;
* the hook rule (ask when `destructive` or `exfiltrates` is at or above the threshold): how many commands each judge
  would prompt on, and the commands where two judges disagree;
* mean absolute probability difference between judges per boolean question.

Result files are one JSON object per case: `backend, key, corpus, subject, question, expected, latency_ms, answers`
(normalised to the `ai evaluate` shape: `{"probability": p}`, `{"choice": c, "probabilities": {...}}`) and, for the
live cases, `reference.jev_logged`.

## Files

* `corpus.py`: the cases; `backends.py`: Jev and decider clients; `metrics.py`, `report.py`: scoring and tables.
* `decider_mps_server.py`: `/v1/systemone` shim over `decider.infer.Decider` for machines without CUDA.
* `semif_bridge.py`: export to and import from SemIf's row format.
