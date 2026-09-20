---
name: lang-typescript
description: Write or review TypeScript and JavaScript using the project's architecture, package manager, and validation tools.
---

# TypeScript/JavaScript Development

Follow the project's runtime, framework, and architectural conventions. Keep tool
or framework migrations separate unless the task requires them.

## Package manager and tooling

Use repository instructions, `packageManager` in `package.json`, lockfiles, and
CI scripts to identify the package manager and pinned version. Resolve conflicting
signals before installing dependencies or changing lockfiles.

| Lockfile | Manager |
|----------|---------|
| `pnpm-lock.yaml` | pnpm |
| `bun.lock` or `bun.lockb` | Bun |
| `package-lock.json` | npm |

`npm`, `bun`, and `pnpm` are managed by mise. Use existing mise configuration;
do not install package managers through another mechanism unless the project
explicitly requires it. If the repository specifies no manager, use an appropriate
one already provided by mise.

Prefer project scripts and installed project binaries. Do not introduce another
linter, formatter, test framework, or package manager during unrelated work.
Keep existing ESLint and Prettier setups when that is the repository's toolchain.

For a requested new toolchain, consider Oxlint with Oxfmt or Biome, accounting for
required plugins, framework support, and repository preferences. Read only the
matching reference when configuring or troubleshooting that tool:

- [Oxlint](references/oxlint.md): `.oxlintrc.json`, `.oxlintrc.jsonc`, or `oxlint.config.*`.
- [Oxfmt](references/oxfmt.md): `.oxfmtrc.json`, `.oxfmtrc.jsonc`, or `oxfmt.config.*`.
- [Biome](references/biome.md): `biome.json` or `biome.jsonc`.

## Validation

Inspect project scripts and required repository checks. Run affected tests and
appropriate type, lint, and format checks for the changed package or behavior.
Run a build when bundling, packaging, configuration, or integration behavior needs
verification. Broaden to the full suite when shared changes, failures, or repository
requirements justify it; do not require it for every edit.

Reuse passing checks for unchanged state. After a fix, rerun affected checks.
Distinguish pre-existing failures and environment limitations from regressions;
do not fix unrelated issues or claim checks passed when they did not run.

## Implementation choices

- Prefer precise types and narrow `unknown` at untrusted boundaries. Use `any` or
  assertions only when justified by an API constraint or an invariant the type
  system cannot express; keep those exceptions localized.
- Prefer plain objects for data and module exports for module APIs. Use classes
  where framework contracts, encapsulated behavior, or existing architecture call
  for them; do not rewrite classes merely to follow a stylistic preference.
- Choose array methods or loops for clarity, control flow, and performance in the
  actual code. Avoid unnecessary intermediate collections or clever reductions.
- Test observable behavior through supported interfaces. Do not export internals
  or introduce abstractions solely to make implementation details easier to mock.

## Framework-specific references

- For Vitest projects, use [Testing](testing.md) for mocking and environment details.
  Retain another existing test runner when present.
- For React work, use [React](react.md) for hooks, effects, and state guidance.
  Apply compiler-specific advice only when that compiler is enabled. Preserve
  framework requirements and necessary existing behavior when applying preferences.
