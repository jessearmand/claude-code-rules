# Oxlint

Dedicated high-performance linter for JavaScript and TypeScript. Defaults to high-signal correctness checks. Pair with Oxfmt for formatting; Oxlint does not format.

Use this reference when the project has `.oxlintrc.json`, `.oxlintrc.jsonc`, `oxlint.config.ts`, or `oxlint.config.mts`.

Docs: https://oxc.rs/docs/guide/usage/linter.html

## Install

Add as a project dev dependency. Do not install globally; npm/bun/pnpm come from mise.

```bash
pnpm add -D oxlint
# bun add -D oxlint
# npm add -D oxlint
```

Type-aware rules need an extra package:

```bash
pnpm add -D oxlint-tsgolint
```

```json
{
    "scripts": {
        "lint": "oxlint",
        "lint:fix": "oxlint --fix"
    }
}
```

## Commands

```bash
# Lint cwd
oxlint

# Safe fixes only
oxlint --fix

# Suggestions may change behavior
oxlint --fix-suggestions

# Aggressive fixes; review the diff
oxlint --fix-dangerously

# Type-aware rules (needs oxlint-tsgolint)
oxlint --type-aware

# Type-aware + TypeScript diagnostics (can replace tsc --noEmit)
oxlint --type-aware --type-check

# Init .oxlintrc.json
oxlint --init

# Inspect effective config for a file
oxlint --print-config path/to/file.ts

# List registered rules
oxlint --rules

# CI: fail on warnings
oxlint --deny-warnings
oxlint --quiet
```

CLI severity overrides apply left to right: `-A` allow, `-W` warn, `-D` deny.

```bash
oxlint -D correctness -D suspicious -A no-console
```

## Configuration

One config file per directory. JSON and TypeScript configs cannot coexist. TypeScript configs need the Node-based `oxlint` package (Node 22.18+ or 24+).

`.oxlintrc.json` (comments allowed):

```json
{
    "$schema": "./node_modules/oxlint/configuration_schema.json",
    "categories": {
        "correctness": "error",
        "suspicious": "warn"
    },
    "rules": {
        "typescript/no-explicit-any": "error",
        "no-console": "error"
    }
}
```

`oxlint.config.ts`:

```typescript
import { defineConfig } from 'oxlint';

export default defineConfig({
    categories: {
        correctness: 'error',
        suspicious: 'warn',
    },
    rules: {
        'typescript/no-explicit-any': 'error',
    },
});
```

Common fields: `rules`, `categories`, `plugins`, `jsPlugins`, `overrides`, `extends`, `ignorePatterns`, `env`, `globals`, `settings`, `options`.

Categories: `correctness` (default), `suspicious`, `pedantic`, `perf`, `style`, `restriction`, `nursery`.

Setting `plugins` replaces the default plugin set; include every plugin that should stay enabled.

```json
{
    "plugins": ["unicorn", "typescript", "oxc", "react", "vitest"]
}
```

`options.typeAware` and `options.typeCheck` belong only in the root config. CLI flags override config.

```json
{
    "options": {
        "typeAware": true,
        "typeCheck": true
    }
}
```

In monorepos, install deps and build packages that emit `.d.ts` before `--type-aware`.

## Suppressions

Prefer `oxlint-*`. `eslint-*` works during migration.

```typescript
// oxlint-disable-next-line no-console
console.log('debug');

console.log(x++); // oxlint-disable-line no-console, no-plusplus

/* oxlint-disable typescript/no-floating-promises */
```

Ignore comments cannot change rule options. Unused disable comments: `oxlint --report-unused-disable-directives`.

## Migrate from ESLint

```bash
npx @oxlint/migrate
npx @oxlint/migrate --type-aware
```

Incremental: run Oxlint first, keep ESLint for unsupported rules, disable overlap with `eslint-plugin-oxlint`.

```bash
oxlint && eslint
```

JS plugins (`jsPlugins`) are alpha. Native plugin names (`react`, `unicorn`, `typescript`, `import`, `jest`, `vitest`, `jsx-a11y`) are reserved; alias the JS package if both are needed.

## Editor

Install the Oxc extension (`oxc.oxc-vscode`). The editor talks to the project's `oxlint --lsp`.

```json
{
    "editor.codeActionsOnSave": {
        "source.fixAll.oxc": "always"
    }
}
```

Optional: `"oxc.typeAware": true` in editor settings, or `options.typeAware` in the root Oxlint config.
