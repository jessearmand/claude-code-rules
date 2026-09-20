---
name: commit
description: Create a focused Git commit from the requested changes while preserving unrelated work. Use when asked to commit working-tree or staged changes.
---

# Commit Changes

Commit the change authorized by the user, preserving unrelated work and deriving
the message from the final staged diff. Honor an explicitly staged-only request;
otherwise use the task context to identify related tracked and new files.

## Scope and staging

- Inspect Git operation state, status, and staged and unstaged diffs. Do not create
  an ordinary commit during a merge, rebase, cherry-pick, or revert, or with
  unmerged entries; use the operation-specific workflow when authorized.
- Include files and hunks that clearly belong to the requested change. Existing
  authorization covers related new files, including already-staged additions;
  do not ask for the same approval again. Ask when the intended scope is unclear.
- Leave unrelated unstaged and untracked files untouched without asking how to
  handle them. If unrelated content is already staged, resolve that scope conflict
  before committing; obtain authorization before changing the user's selection.
- Stage by explicit path, using partial staging for mixed-purpose files. Avoid
  broad commands such as `git add .`, `git add -A`, and `git commit -a`.
- Review the final staged diff for completeness, unrelated changes, secrets, and
  generated or machine-local state that does not belong in the repository.

## Validation

Use the repository's required checks and the relevant language guidance. Select
checks that exercise the changed behavior; a documentation-only commit does not
inherently require a full build or test suite.

Working-tree checks are sufficient when they exercise the same source and
configuration that will be committed. Reuse checks already run against that
state. If partial staging or excluded source/configuration could change the
result, validate the staged tree in isolation. Repository requirements or an
explicit request for exact-index validation also require isolation.

### Verify without unrelated untracked files

When isolation is needed, follow [staged-tree validation](references/staged-tree-validation.md).
This is also the exact-index procedure used by the `commit-staged` skill; retain
its staged-only scope when following that workflow.

Fix check failures within the authorized scope, review and stage the fixes, and
rerun affected checks. Ask before expanding the change. If required validation
fails or cannot run, report the failure or limitation and do not commit unless the
user explicitly authorizes proceeding. Never describe unrun checks as passing.

## Conventional Commit message rules

Use this format:

```text
<type>[optional scope][!]: <description>

[optional body]

[optional trailers]
```

Apply these rules:

- Use `feat` for a new user-visible capability and `fix` for a bug fix.
- Use `docs`, `refactor`, `chore`, `test`, `perf`, `ci`, `build`, or `style` when one of those describes the change more accurately.
- Add a scope in parentheses only when it clarifies the affected subsystem, for example `fix(parser):`.
- Keep the description specific, imperative, and within 100 columns. Do not end it with a period.
- Add a body when the reason, behavior, tradeoff, or migration impact is not clear from the subject. Separate it with one blank line and wrap it within 120 columns.
- Explain why the change exists and what behavior changed; do not merely list filenames.
- Add trailer-style footers after another blank line, for example `Refs: #123`.
- Mark breaking changes with `!` before the colon or a `BREAKING CHANGE:` footer.

## Commit and report

- Reinspect the staged diff after validation. If the candidate changed, review it
  and rerun affected checks. Record the final index tree identifier before committing.
- Commit the index without bypassing hooks unless explicitly authorized. Do not
  amend, squash, push, or alter earlier commits without authorization.
- Compare `HEAD^{tree}` with the recorded candidate tree. If a hook or concurrent
  operation changed the committed content, report the discrepancy and inspect it;
  do not automatically amend or push.
- Report the commit identifier and subject, validation results and limitations,
  and any relevant work left outside the commit.
