# Oxfmt

Dedicated high-performance formatter for the JavaScript ecosystem. Prettier-compatible workflow; CLI options such as `--no-semi` are not supported — put options in the config file.

Use this reference when the project has `.oxfmtrc.json`, `.oxfmtrc.jsonc`, `oxfmt.config.ts`, or `oxfmt.config.mts`.

Docs: https://oxc.rs/docs/guide/usage/formatter.html

## Install

Add as a project dev dependency. Do not install globally; npm/bun/pnpm come from mise.

```bash
pnpm add -D oxfmt
# bun add -D oxfmt
# npm add -D oxfmt
```

```json
{
    "scripts": {
        "fmt": "oxfmt",
        "fmt:check": "oxfmt --check"
    }
}
```

## Commands

```bash
# Format the current directory (writes files)
oxfmt

# Check without writing
oxfmt --check

# List files that would change
oxfmt --list-different

# Init .oxfmtrc.json
oxfmt --init

# Migrate Prettier config
oxfmt --migrate prettier

# Format stdin
echo 'const   x   =   1' | oxfmt --stdin-filepath test.ts
```

Quote globs so the shell does not expand them: `oxfmt "src/**/*.ts"`.

## Configuration

One config file per directory. Nearest config wins. `--disable-nested-config` or `-c` skips per-file lookup.

`.oxfmtrc.json`:

```json
{
    "$schema": "./node_modules/oxfmt/configuration_schema.json",
    "printWidth": 100,
    "tabWidth": 4,
    "singleQuote": true,
    "semi": true,
    "trailingComma": "all"
}
```

`oxfmt.config.ts`:

```typescript
import { defineConfig } from 'oxfmt';

export default defineConfig({
    printWidth: 100,
    tabWidth: 4,
    singleQuote: true,
    semi: true,
});
```

Common fields:

- `printWidth` — default 100 (Prettier is 80)
- `tabWidth` — default 2; use 4 to match this repo's indent rule
- `useTabs`, `semi`, `singleQuote`, `trailingComma`
- `ignorePatterns`
- `sortImports` — off by default
- `sortTailwindcss` — off by default
- `sortPackageJson` — on by default
- `insertFinalNewline` — default true

Overrides:

```json
{
    "printWidth": 100,
    "overrides": [
        {
            "files": ["*.test.ts", "*.spec.ts"],
            "options": { "printWidth": 120 }
        }
    ]
}
```

Oxfmt also reads the nearest `.editorconfig` for `end_of_line`, `indent_style`, `indent_size`, `max_line_length`, and `insert_final_newline`. Nested `.editorconfig` files are not merged.

## Suppressions

JS/TS: `oxfmt-ignore` (also accepts `prettier-ignore`).

```typescript
// oxfmt-ignore
const a    = 42;

const b    = 1; // oxfmt-ignore
```

Non-JS regions (HTML, Vue templates/styles, Markdown) use `prettier-ignore`. TOML has no ignore comments.

## Editor

Install the Oxc extension (`oxc.oxc-vscode`). The editor talks to the project's `oxfmt --lsp`.

```json
{
    "editor.defaultFormatter": "oxc.oxc-vscode",
    "editor.formatOnSave": true
}
```
