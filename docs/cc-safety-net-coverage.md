# PR #1 review and cc-safety-net coverage comparison

Prepared against agent-config commit `f31b4ef3b6d934bf1c32feb0e6b640e7ecca8a87`,
cc-safety-net 2.6.11 at `0626be7a791c17a0daa67ae90a899372c667056d`, and its official
rulebooks at `d1fc329f00f1c8068f0ce57a855508043981f7c7`.

The objective remains a classifier/decision-model layer above the hooks, with
deterministic hooks owning hard denials, the model only asking for confirmation,
and unavailable model results falling back to the host's normal permission flow.
Broad guard-family coverage is already present. Reproducing an entire safety-net
product or implementing complete Bash semantics would move beyond that objective.

The PR had six unresolved review threads when inspected. They were addressed on
the same branch in `f31b4ef`: S3 sync deletion; forced branch creation/reset;
forced branch reset/move/copy; forced tag replacement; update-ref stdin
transactions; and full-line redaction of unquoted prose passphrases. The newer
`a7639cc` changes to executed SQL strings and rsync link options were retained.
All 12 guard test scripts passed. The transaction tests passed again after the
final stdin-versus-filename controls. New lint diagnostics: none. Type checking
the touched runtime modules still reports two existing nullable-Git diagnostics
in unchanged loops in dangerous_git.py; those are not new runtime failures.

## What is covered

| Area | cc-safety-net | Current hooks | Assessment |
|---|---|---|---|
| Destructive Git | 26 named core entries: discards, force push, ref deletion, stash loss, worktree removal, overrides | Matching operation families in dangerous_git; some local removals ask; new force branch/tag and stdin-ref checks extend this | Broad family coverage, with different decision policies; not proof of every spelling |
| Filesystem destruction | 15 named core entries: dangerous rm targets, find deletion, devices, formatting, shredding | rm_rf_guard asks for recursive forced rm; dangerous_system and config_guard cover devices, deletion and protected config | Broad coverage; ours prompts on some project/temp deletions upstream allows |
| Nested execution | 11 named core execution entries: shell/interpreter nesting, awk, xargs, parallel, dynamic sources | Shared shell parser, wrappers, scripts, Make/package sources, interpreter strings and model routing | Strong static handling; incomplete/dynamic analysis still has gaps |
| Secret access | Basenames, extensions, home credential trees and coding-CLI paths; shell and file-tool input | secrets_guard handles these families and nested/interpreter references; judge_redact protects model-bound context | Significant overlap; exact credential-path and tool-adapter parity has not been exhaustively measured |
| Self-protection | Own policy/application guard and general Git metadata protection | Wider agent-config coverage across Claude, Codex, omp/pi, opencode and persistence locations | Different protected assets; general Git metadata coverage is not identical |
| Cloud/IaC | Four opt-in official rulebooks, 50 rules | Terraform, AWS, gcloud and Azure tables, plus generic deletion rules | All 81 upstream block-labelled pack fixtures intercepted |
| Additional controls | Built-in core and extensible rulebooks | SQL/database, Kubernetes/container, publishing, fetch-and-execute, security-control and persistence checks | Our deterministic scope already extends beyond upstream core |
| Model decisions | The runtime gate is deterministic allow/deny; no network model classifier was found in the inspected gate | Optional Jev/AI Gateway decision models with typed concerns, contextual prompts, thresholds and redaction | This is the main intended differentiator |
| Policy/platform product | Standard/Strict/Paranoid presets, custom rulebooks, shared policy, GUI, many hosts, Windows shells | Fixed Python tables, tiers, environment settings, Claude/omp/pi integration, Linux/macOS Bash | Product parity is unnecessary; PowerShell/CMD remain outside this PR |

The named core counts describe upstream metadata entries, not a percentage of
equivalent implementation. Eighteen other entries are PowerShell/CMD-specific.
The official cloud packs are a separate repository; they are not all enabled by
cc-safety-net's default core policy.

## Measured replay

I replayed all 6,965 upstream harvested literals through the four local
deterministic hooks, then checked model eligibility only for deferred commands.
The saved comparison column is `work/standard`; local checks used an empty
workspace, default/high policy, and normal interactive permission mode. No
literal was executed, no decision model was called, and there were no guard
exceptions. Reproduce from this checkout with
[`scripts/replay_ccsafety.py`](../scripts/replay_ccsafety.py); the upstream
checkout is not vendored, so clone it at the pinned commit first:

```sh
git clone https://github.com/kenryu42/cc-safety-net.git scratchpad/cc-safety-net
git -C scratchpad/cc-safety-net checkout 0626be7a791c17a0daa67ae90a899372c667056d
python3 scripts/replay_ccsafety.py
```

Re-run on 2026-10-15 at claude-code-rules `a8d7345` (agent-config `d3d89bb`),
the deny-labelled row was 696 deny / 254 ask / 486 defer (380 model candidates,
106 neither) and the allow-labelled row 69 / 215 / 5,240 (1,381 candidates),
still with no guard exceptions. The tables below keep the `f31b4ef` figures.

| Upstream label | Inputs | Our deny | Our ask | Our defer |
|---|---:|---:|---:|---:|
| Deny | 1,436 | 693 (48.3%) | 251 (17.5%) | 492 (34.3%) |
| Allow | 5,524 | 70 (1.3%) | 212 (3.8%) | 5,242 (94.9%) |
| Fail closed | 5 | 1 | 0 | 4 |

Of the 492 deferred deny-labelled inputs, 386 pass the classifier prefilter and
106 do not. Thus 65.7% are deterministically intercepted, another 26.9% are
eligible for a model decision if enabled, and 7.4% reach neither layer in this
replay. Eligibility does not mean the model would prompt or successfully answer.
Of the upstream allow-labelled inputs, 1,383 deferred inputs are also model
candidates, so candidate volume and false-prompt rate matter independently.

This is a label comparison, not a vulnerability rate or a live test of
cc-safety-net. The harvested corpus includes non-command prose, path/patch
fragments, Windows examples, and product-specific policy operations. Some
path-sensitive cases differ with the filesystem, home, repository and policy.
For example, a patch header naming `.env` is labelled as sensitive by upstream's
raw-text handling; treating that same literal as Bash does not exercise our
actual patch/file-tool adapter. This prevents interpreting 7.4% as 106 genuine
Bash bypasses. The replay writes the per-literal results to
`scratchpad/cc-safety-net-replay.jsonl` and the aggregate to
`scratchpad/cc-safety-net-replay-summary.json` (both gitignored; pass `--out`
to write elsewhere).

Official rulebook fixture comparison, using the same deterministic workflow:

| Pack | Block-labelled fixtures | Our deny | Our ask | Missed |
|---|---:|---:|---:|---:|
| Terraform | 24 | 24 | 0 | 0 |
| AWS | 15 | 13 | 2 | 0 |
| gcloud | 27 | 27 | 0 | 0 |
| Azure | 15 | 15 | 0 | 0 |
| Total | 81 | 79 | 2 | 0 |

Across the 96 pack allow-labelled controls, our hooks defer 64, ask about 24,
and deny 8. Some are intentional policy differences: our Azure table blocks
Key Vault deletion that this curated pack excludes. Others are avoidable noise:
Terraform `--dry-run` spelling is not recognized by the current `-dry-run`
prefix test, and AWS skeleton-generation exemptions in the specific rule are
not honored by the generic deletion rule. These are pre-existing follow-up
items, not additional changes slipped into this PR review fix.

## Keep the next work focused

1. **Make uncertain analysis reach the decision layer.** The prefilter does not
   explicitly include parsed truncation/unparseability or skipped sources.
   Dynamic dot-sourcing such as `. "$FILE"` and opaque command-stream runners
   can receive no meaningful model review. Add a small uncertainty-routing
   contract instead of continuing to emulate every shell construct. Decide
   separately when unresolved execution requires an ask without a model.
2. **Keep decisions monotonic.** The omp adapter currently returns any
   deterministic deny first, then a deterministic ask, and consults the model
   only when neither applies. Preserve this ordering across hosts. Model
   confidence must not turn a deterministic denial into permission.
3. **Preserve evidence and redact before sending it.** Supply task intent,
   relevant recent commands, sources, provenance, uncertainty and resolved
   targets within explicit budgets. Use the actual model payload as the
   acceptance boundary for redaction and truncation tests.
4. **Reduce unnecessary prompts before adding more rules.** Normalize safe
   previews consistently across specific/generic rules; evaluate paired risky
   and benign cases. Separate false denials from false prompts, candidate
   routing from model recall, and model timeouts from safe outcomes.
5. **Set a review boundary and stop expanding parser fidelity by default.**
   cc-safety-net itself records accepted residual families and prefers bounded
   conservative checks or strict-mode handling to endless standard-mode parser
   expansion. Keep a short scope/residual document for this PR. Use realistic
   incidents and regression pairs to select deterministic additions.

Live model accuracy, latency, repeated-run stability, and real pi/omp sessions
were not tested in this turn. The PR description contains earlier calibration
claims; this comparison does not independently revalidate them. Current
threshold configuration is 0.7, with model failure returning to normal host
permissions. A deferred result is not evidence that the command is safe.

Sources inspected: upstream `src/core/rules/destructive.ts`,
`src/core/rules/secret.ts`, `src/gate/pipeline.ts`, Git analyzer and policy
modules, harvested verdict fixture and loader, `SECURITY.md`, the residual-risk
registry guidance, and the four official rulebooks. Locally: deterministic
rule tables, shell/source parsing, omp aggregation, bash_risk_judge,
judge_context, judge_redact, jev_client, judge_probe and regression tests.

Pinned upstream evidence: [core rule metadata](https://github.com/kenryu42/cc-safety-net/blob/0626be7a791c17a0daa67ae90a899372c667056d/src/core/rules/destructive.ts), [gate pipeline](https://github.com/kenryu42/cc-safety-net/blob/0626be7a791c17a0daa67ae90a899372c667056d/src/gate/pipeline.ts), [harvested labels](https://github.com/kenryu42/cc-safety-net/blob/0626be7a791c17a0daa67ae90a899372c667056d/tests/fixtures/gate/harvested-verdicts.jsonl), and [official rulebooks](https://github.com/cc-safety-net/rulebooks/tree/d1fc329f00f1c8068f0ce57a855508043981f7c7).
