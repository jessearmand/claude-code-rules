---
name: check
description: Perform comprehensive code quality and security checks. Use when running linting, type checking, tests, or build verification.
---

# Check

Run the project's check command and resolve any resulting errors. Follow the matching language skill for tool choice and flags.

Important:
- DO NOT commit any code during this process
- DO NOT change version numbers
- Focus only on fixing issues identified by checks

Common Checks Include:
1. **Linting**: Code style and syntax errors
2. **Type Checking**: TypeScript or language type errors
3. **Unit Tests**: Failing test cases
4. **Security Scan**: Vulnerability detection
5. **Code Formatting**: Style consistency
6. **Build Verification**: Compilation errors

## Process

1. Prefer project scripts (`check`, `lint`, `test`, `build`) over ad-hoc tool invocations
2. Run the check command
3. Analyze output for errors and warnings
4. Fix issues in priority order:
   - Build-breaking errors first
   - Test failures
   - Linting errors
   - Warnings
5. Re-run checks after each fix
6. Continue until all checks pass

## For Different Project Types

- **JavaScript/TypeScript**: lockfile package manager (`pnpm`, `bun`, or `npm`; all managed by mise) plus `lang-typescript` (Oxlint, Oxfmt, or Biome)
- **Python**: see `lang-python` for `uv`/`ruff`/`ty`; fall back to the repo's own linter/type checker
- **Rust**: `cargo check`, `cargo clippy` (see `lang-rust`)
- **Go**: `go vet`, `staticcheck`
- **Swift**: `swift-format`, `swiftlint` (see `lang-swift` / `xcode-build`)
