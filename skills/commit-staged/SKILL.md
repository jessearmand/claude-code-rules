---
name: commit-staged
description: This skill should be used when the user asks to "commit staged changes", "commit what is staged", "write a commit message for the index", or explicitly wants a commit limited to the current Git index. It validates and commits the exact staged tree without including unstaged or untracked work.
disable-model-invocation: true
allowed-tools: Bash(git diff:*), Bash(git status:*), Bash(git log:*), Bash(git commit:*), Bash(git add:*), Read, Glob, Grep, Skill(check)
---

# Commit Staged Changes

Commit only the change already represented by the Git index. Preserve all unstaged and untracked work, and validate the exact staged tree before committing it.

## Invariants

- Treat the current index as the complete commit scope.
- Never stage an unstaged or untracked file merely because a check reads or rewrites it.
- Never use `git add .`, `git add -A`, or `git commit -a`.
- Validate the exact index tree in isolation so unstaged, untracked, and ignored source files cannot make checks pass.
- Stop when a merge, rebase, cherry-pick, or revert is active, or when the index contains unmerged entries. Use the operation-specific continuation workflow instead of creating an ordinary commit.
- Follow the `check` skill for repository validation.
- Follow the `commit` skill's “Verify without unrelated untracked files” procedure and Conventional Commit message rules. Retain this skill's staged-only scope.

## Process

1. Inspect repository operation state, `git status --short`, and `git diff --staged`.
2. Stop if the staged diff is empty.
3. Stop on an active merge, rebase, cherry-pick, or revert. Report the operation and use its specific continuation workflow only when the user requests it.
4. Confirm that the staged hunks form one coherent change. If the index mixes unrelated concerns, ask whether to split it before changing the user's staged selection.
5. Follow the `commit` skill's exact-index validation procedure with the current index as the complete candidate. Do not run checks against the dirty original worktree.
6. If validation requires a source rewrite, do not extend the staged candidate. Report the exact rewrite and stop. If the user wants to include it, transition to the general `commit` workflow.
7. Write the current index to a tree object again. If its identifier differs from the validated tree, inspect and validate the new tree before committing.
8. Reinspect `git diff --staged`, then derive the subject and optional body from that final diff.
9. Commit only the index after validation passes. Do not bypass hooks with `--no-verify`, amend, squash, or push unless explicitly requested.
10. After committing, compare `HEAD^{tree}` with the validated tree. If they differ, report the hook-induced or concurrent change; do not amend or push automatically.
11. Report the commit identifier and subject, validation commands that passed, and unstaged or untracked paths deliberately left untouched.
