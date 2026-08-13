# Biome

Integrated linter, formatter, and assist actions for JavaScript and TypeScript.

Use this reference when the project already has `biome.json` / `biome.jsonc`, or when adding Biome as a single-tool toolchain.

Docs: https://biomejs.dev/

## Commands

Prefer project scripts (`check`, `lint`, `format`). Otherwise invoke the local binary through the project's package manager:

```bash
# Check lint + format
pnpm exec biome check ./src
bunx biome check ./src
npx @biomejs/biome check ./src

# Fix safe issues
pnpm exec biome check --write ./src

# Fix including unsafe fixes (review the diff)
pnpm exec biome check --write --unsafe ./src

# Lint only
pnpm exec biome lint ./src

# Format only
pnpm exec biome format --write ./src
```

## Configuration

Create `biome.json` in the project root:

```json
{
    "$schema": "https://biomejs.dev/schemas/2.0.5/schema.json",
    "linter": {
        "enabled": true,
        "rules": {
            "recommended": true,
            "suspicious": {
                "noExplicitAny": "error"
            }
        }
    },
    "formatter": {
        "enabled": true,
        "indentStyle": "space",
        "indentWidth": 4
    },
    "assist": {
        "enabled": true,
        "actions": {
            "source": {
                "organizeImports": "on"
            }
        }
    },
    "javascript": {
        "formatter": {
            "quoteStyle": "single",
            "semicolons": "always"
        }
    }
}
```

Pin `$schema` to the installed Biome major/minor, not a remembered version.

## Rule Groups

- **correctness**: guaranteed bugs (enabled by default)
- **suspicious**: likely bugs or useless code
- **style**: consistent style (warnings by default)
- **complexity**: overly complex code
- **security**: potential security flaws
- **a11y**: accessibility rules for React/HTML

## Assist Actions

Assist actions always offer a code fix. `source` group:

- **organizeImports**: sort and group imports
- **useSortedKeys**: sort object keys (JSON/config files)

```bash
# Assist only
pnpm exec biome check --formatter-enabled=false --linter-enabled=false

# All checks including assist
pnpm exec biome check --write ./src
```

## Suppressions

```typescript
// biome-ignore lint/suspicious/noExplicitAny: external API requires any
const data: any = externalApi.getData();

// biome-ignore format: matrix alignment
const matrix = [
    [1, 0, 0],
    [0, 1, 0],
];
```

## Editor

```json
{
    "editor.defaultFormatter": "biomejs.biome",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.fixAll.biome": "explicit",
        "source.organizeImports.biome": "explicit",
        "source.action.useSortedKeys.biome": "explicit"
    }
}
```

- `source.fixAll.biome` — safe lint fixes
- `source.organizeImports.biome` — sort imports
- `source.action.useSortedKeys.biome` — sort object keys

## Migrate from ESLint

```bash
pnpm exec biome migrate eslint
pnpm exec biome lint --write --unsafe --suppress="suppressed due to migration"
```
