---
name: lang-typescript
description: TypeScript/JavaScript development with Vitest testing, React patterns, and functional programming best practices. Use when writing or reviewing TypeScript/JavaScript code, or when choosing among Oxlint, Oxfmt, and Biome.
---

# TypeScript/JavaScript Development

Write functional, type-safe TypeScript/JavaScript code.

## Package Managers

`npm`, `bun`, and `pnpm` are managed by [mise](https://mise.jdx.dev/). Do not install them with Homebrew, a standalone installer, or Corepack unless the project already does.

Detect the project manager from lockfiles and config, then use that CLI:

| Signal | Manager |
|--------|---------|
| `pnpm-lock.yaml`, `pnpm-workspace.yaml` | `pnpm` |
| `bun.lock`, `bun.lockb` | `bun` |
| `package-lock.json` | `npm` |
| `mise.toml` / `.tool-versions` lists `pnpm` / `bun` / `npm` | that tool |

If none of those exist, check `packageManager` in `package.json`, then fall back to whatever mise already provides on `PATH`.

Typical mise pins:

```toml
[tools]
node = "24"
pnpm = "10"
# or: bun = "1"
# or: npm = "11"
```

Run project binaries through the manager (`pnpm exec`, `bunx`, `npx`). Prefer `package.json` scripts over ad-hoc tool invocations.

## Validation Workflow

Before submitting changes:

1. Review `package.json` for available scripts
2. Build the repository
3. Run all tests
4. Check for type errors
5. Lint and format with the project's toolchain (see below)

## Lint and Format

Follow the project's existing toolchain. Do not add a second linter or formatter beside the one already configured.

| Project signals | Toolchain | Reference |
|-----------------|-----------|-----------|
| `.oxlintrc.json`, `.oxlintrc.jsonc`, `oxlint.config.ts`, `oxlint.config.mts` | Oxlint | [references/oxlint.md](references/oxlint.md) |
| `.oxfmtrc.json`, `.oxfmtrc.jsonc`, `oxfmt.config.ts`, `oxfmt.config.mts` | Oxfmt | [references/oxfmt.md](references/oxfmt.md) |
| `biome.json`, `biome.jsonc` | Biome | [references/biome.md](references/biome.md) |

Oxlint and Oxfmt are separate tools and often appear together. Biome covers lint + format in one binary.

When adding a toolchain to a new project:

- Dedicated linter + dedicated formatter: Oxlint + Oxfmt
- Single integrated tool: Biome

Stay on ESLint or Prettier only when the project still depends on plugin behavior the replacements do not cover.

Quick commands once the matching reference is loaded:

```bash
# Oxlint / Oxfmt
oxlint
oxlint --fix
oxfmt
oxfmt --check

# Biome
pnpm exec biome check ./src
pnpm exec biome check --write ./src
```

## Core Principles

### Prefer Plain Objects over Classes

- Use TypeScript interfaces/types with plain objects
- Classes add complexity that doesn't fit React's model
- Plain objects are easier to serialize, test, and reason about

```typescript
// Prefer this
interface User {
    id: string;
    name: string;
}
const user: User = { id: '1', name: 'Alice' };

// Avoid this
class User {
    constructor(public id: string, public name: string) {}
}
```

### ES Module Encapsulation

Use `import`/`export` for public API definition instead of class members:

- Exported = public API
- Not exported = private to module
- Test public APIs, not internals

### Type Safety

**Avoid `any`**:
- Loses type safety
- Masks underlying issues
- Reduces readability

**Prefer `unknown` over `any`**:

```typescript
function processValue(value: unknown) {
    if (typeof value === 'string') {
        console.log(value.toUpperCase()); // Type narrowed
    }
}
```

**Use type assertions sparingly**:
- `as Type` bypasses compiler checks
- Only use with external libraries or when you have more info than compiler

### Functional Array Operations

Prefer array methods over loops:

```typescript
// Prefer
const doubled = items.map(x => x * 2);
const evens = items.filter(x => x % 2 === 0);
const sum = items.reduce((acc, x) => acc + x, 0);

// Avoid imperative loops for transformations
```

## Comments Policy

Only write high-value comments. Avoid talking to the user through comments.

## Detailed Guides

- [Testing](testing.md) - Vitest patterns and mocking conventions
- [React](react.md) - React best practices with React Compiler focus
- [Oxlint](references/oxlint.md) - dedicated JS/TS linter
- [Oxfmt](references/oxfmt.md) - dedicated Prettier-compatible formatter
- [Biome](references/biome.md) - integrated lint + format toolchain
