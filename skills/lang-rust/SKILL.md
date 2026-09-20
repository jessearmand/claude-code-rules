---
name: lang-rust
description: Write or review Rust code using the project's toolchain, Cargo workspace, and validation requirements.
---

# Rust Development

Follow repository instructions, the pinned Rust toolchain, edition, minimum
supported Rust version, and existing error-handling and concurrency patterns.
Do not upgrade the toolchain or introduce lint-policy changes during unrelated work.

## Validation

Inspect workspace manifests, Cargo aliases, and CI scripts to identify required
checks and supported feature/target combinations. Select checks for the affected
package and behavior rather than running a fixed sequence after every edit.

| Need | Starting point |
|------|----------------|
| Type-check a package | `cargo check -p <package>` |
| Exercise changed behavior | `cargo test -p <package>` with relevant test filters |
| Lint a package | `cargo clippy -p <package>` |
| Check formatting | `cargo fmt --all -- --check` |
| Verify build artifacts or linking | `cargo build -p <package>` |

Adapt these commands to repository scripts and the relevant features, targets,
and profiles. A full build is not a prerequisite for every change. Broaden to
workspace checks when shared APIs, feature interactions, failures, or repository
requirements justify it. Do not assume `--all-features` represents a supported
configuration; some features are mutually exclusive.

Reuse checks already passed against unchanged state. After fixes, rerun affected
checks and distinguish pre-existing failures or unavailable targets from regressions.
For a read-only review, run checks when they help resolve a finding; do not apply
formatter or lint fixes without implementation scope.

## Lints and formatting

Preserve existing lint levels and formatting configuration. Match CI's warning
policy when required; do not enable pedantic/restriction groups or suppress warnings
just to make a check pass. Review automatic fixes for scope and behavior changes.

Use [Clippy configuration](clippy.md) when diagnosing a lint, changing lint policy,
or choosing targeted Clippy options. Consult the installed tool's help and
[Clippy documentation](https://doc.rust-lang.org/clippy/usage.html) for version-specific behavior.
