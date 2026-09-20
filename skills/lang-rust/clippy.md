# Clippy configuration

Use the repository's existing policy and pinned toolchain. Read this reference
when lint selection or configuration is part of the task.

## Run only the relevant checks

```bash
cargo clippy -p <package>
cargo clippy -p <package> -- --no-deps
```

Use repository-supported features and targets. Run `cargo clippy -- -D warnings`
when CI or the requested policy treats warnings as errors; it includes compiler
warnings, not just Clippy diagnostics. There is no mandatory `cargo check` step
before Clippy when another appropriate check already covers that state.

## Select lints deliberately

Keep default and project-configured groups unless changing lint policy is in scope.
Pedantic lints are opt-in and can flag intentional choices. Select restriction
lints individually; enabling the whole restriction group can produce conflicting
requirements.

For example, `cargo clippy -- -W clippy::unwrap_used` requests one lint without
rewriting the project's configuration. When a lint does not fit an intentional
implementation, prefer a narrow, explained allowance over disabling a group.
Use `-A`, `-W`, or `-D` for command-line allow, warn, or deny levels as needed.

## Automatic fixes

`cargo clippy --fix` modifies source and implies `--all-targets`. Use it only for
authorized implementation or lint-fix work, with an appropriate package/feature
scope. Review the diff and rerun affected checks. Do not use dirty-tree override
flags simply to bypass protections around unrelated work.

## Sources

- [Clippy usage](https://doc.rust-lang.org/clippy/usage.html)
- [Lint reference](https://rust-lang.github.io/rust-clippy/master/index.html): match
  the selected lint to the project's Clippy version before changing code or policy.
