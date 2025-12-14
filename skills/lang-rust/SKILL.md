---
name: lang-rust
description: Rust development with cargo check, clippy linting, and lint configuration. Use when writing or reviewing Rust code.
---

# Rust Development

Write idiomatic Rust code with proper linting and validation workflow.

## Validation Workflow

After implementing Rust code, always run in this order:

```bash
# 1. Type check first
cargo check

# 2. If no errors, run clippy
cargo clippy

# 3. Build when ready
cargo build
```

## Quick Clippy Commands

```bash
# Default lints
cargo clippy

# Fail on warnings (CI)
cargo clippy -- -Dwarnings

# Auto-fix suggestions
cargo clippy --fix
```

## Lint Configuration

Configure lint levels on command line:

```bash
# -A = Allow, -W = Warn, -D = Deny
cargo clippy -- -Aclippy::style -Wclippy::box_default -Dclippy::perf
```

Configure in source:

```rust
#![allow(clippy::style)]

#[warn(clippy::box_default)]
fn main() {
    // ...
}
```

## Detailed Reference

- [Clippy configuration](clippy.md) - Lint groups, pedantic/restriction lints, workspace options
