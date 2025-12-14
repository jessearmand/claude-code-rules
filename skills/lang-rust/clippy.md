# Clippy Configuration

Detailed guide for Rust's Clippy linter.

## Running Clippy

```bash
cargo clippy
```

## Lint Groups

### Default (`clippy::all`)

Enabled by default. Contains well-tested, low false-positive lints.

### Pedantic (`clippy::pedantic`)

Opinionated lints, may have intentional false positives:

```rust
#![warn(clippy::pedantic)]
```

Expect to sprinkle `#[allow(..)]` throughout code. Clippy itself uses this group.

### Restriction (`clippy::restriction`)

Lints that restrict language features:

```rust
// Cherry-pick specific lints, don't enable entire group
#![warn(clippy::unwrap_used)]
#![warn(clippy::expect_used)]
```

> Some lints contradict each other. Never enable the whole group.

### Style (`clippy::style`)

Most opinionated warn-by-default group. Can disable and cherry-pick:

```rust
#![allow(clippy::style)]
#![warn(clippy::redundant_else)]
```

## Command Line Configuration

```bash
# Allow a lint
cargo clippy -- -Aclippy::style

# Warn on a lint
cargo clippy -- -Wclippy::box_default

# Deny (error) on a lint
cargo clippy -- -Dclippy::perf

# Combine multiple
cargo clippy -- -Aclippy::style -Wclippy::box_default -Dclippy::perf
```

## CI Configuration

Fail build on any warning:

```bash
cargo clippy -- -Dwarnings
```

> This includes rustc warnings like `dead_code`.

## Source Configuration

```rust
// Crate-level (lib.rs or main.rs)
#![allow(clippy::style)]
#![warn(clippy::pedantic)]

// Item-level
#[allow(clippy::too_many_arguments)]
fn complex_function(...) {}
```

## Auto-Fix

Apply Clippy's suggestions automatically:

```bash
cargo clippy --fix
```

> Note: `--fix` implies `--all-targets`.

## Workspace Options

Run on specific crate:

```bash
cargo clippy -p example
```

Run only on that crate (exclude dependencies):

```bash
cargo clippy -p example -- --no-deps
```

## Without Cargo

For non-Cargo projects:

```bash
clippy-driver --edition 2018 -Cpanic=abort foo.rs
```

> `clippy-driver` is not a replacement for `rustc`. Artifacts may not be optimized.

## Lint Reference

Full list of lints: [Clippy Lint List](https://rust-lang.github.io/rust-clippy/master/index.html)

## Tips

- Use `#[allow(..)]` liberally if you disagree with a lint
- Report false positives to the Clippy team
- Pedantic and restriction groups are opt-in for a reason
- Run `cargo check` before `cargo clippy` to catch type errors first
